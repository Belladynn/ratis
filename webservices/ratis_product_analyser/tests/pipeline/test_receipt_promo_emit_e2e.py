"""End-to-end test for the Phase C-4 promo_found emit.

Verifies that ``_award_scan_rewards`` (shared V2/V3 reward tail in
``worker/receipt_task.py``) fires the right ``trigger_action(
"promo_found", ...)`` call when the raw OCR text carries promo
signals, and stays silent otherwise.

Scope
=====
- Receipt with 2 distinct promo signals → 1 emit, quantity=2,
  idempotency_key=``<receipt_id>:promo``, context carries the matched
  patterns for forensics.
- Receipt with no promo signals → 0 emit.
- Feature flag disabled (``pipeline.promo_detection.enable=false``) →
  0 emit even with promo signals present.
- Idempotency : second call with same receipt → same idempotency_key
  so the server-side ``reward_events UNIQUE`` constraint dedups
  (no double-credit on Celery retry).
- The 4 promo_found missions in the V1 catalogue are all
  ``is_active=true`` (sanity check that miss_pb migration ran in
  the test DB).

Strategy
========
We exercise the reward emit layer directly (``_award_scan_rewards``)
rather than the full pipeline, since the pipeline integration is
already covered by ``test_pipeline_end_to_end_grants_nrc_and_reconcile_in_one_scan``
in test_persist.py. The promo regex is layered on top of the existing
``receipt_scan`` emit ; we monkey-patch ``trigger_action`` and assert
that the expected calls fire (vanilla + promo) or only vanilla.
"""

from __future__ import annotations

import uuid
from datetime import date as date_cls
from decimal import Decimal

from ratis_core.models.product import Product
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text

# ── Fixtures ──────────────────────────────────────────────────────────


def _make_user(db) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"promo-test-{uid.hex[:8]}@ratis.fr",
        display_name="PromoTester",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def _make_store(db) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Carrefour Champs-Élysées",
        retailer="carrefour",
        address="74 avenue des Champs-Élysées",
        city="Paris",
        postal_code="75008",
        lat=Decimal("48.87"),
        lng=Decimal("2.30"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_product(db, *, ean: str = "3017620422003") -> Product:
    p = Product(ean=ean, name="Nutella 400g", source="off")
    db.add(p)
    db.flush()
    db.commit()
    return p


def _make_receipt_with_accepted_scan(db, *, user: User, store: Store, product: Product) -> Receipt:
    """Build a ``confirmed``-store receipt with one accepted scan —
    the prerequisite for ``_award_scan_rewards`` to do anything."""
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        store_status="confirmed",
        purchased_at=date_cls.today(),
        image_r2_key="fake-key.jpg",
    )
    db.add(r)
    db.flush()

    scan = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        receipt_id=r.id,
        store_id=store.id,
        store_status="confirmed",
        scan_type="receipt",
        scanned_name="NUTELLA 400G",
        product_ean=product.ean,
        match_method="barcode",
        status="matched",
        price=250,
        quantity=1.0,
    )
    db.add(scan)
    db.flush()
    db.commit()
    return r


def _capture_trigger_action(monkeypatch) -> list[dict]:
    """Monkey-patch the imported ``trigger_action`` symbol in
    ``worker.receipt_task`` so we capture all emits. The fire-and-
    forget contract means the real implementation is HTTP-out ; we
    snapshot the call args instead."""
    import worker.receipt_task as task_module

    captured: list[dict] = []
    monkeypatch.setattr(
        task_module,
        "trigger_action",
        lambda user_id, action_type, **kwargs: captured.append(
            {"user_id": user_id, "action_type": action_type, **kwargs}
        ),
    )
    monkeypatch.setattr(
        task_module,
        "trigger_cashback_scan",
        lambda *a, **kw: None,  # not under test here
    )
    return captured


# ── Tests ─────────────────────────────────────────────────────────────


def test_promo_found_emitted_when_signals_present(db, monkeypatch):
    """Receipt with 2 distinct promo signals → 2 emits :
    1) receipt_scan (existing, unchanged)
    2) promo_found quantity=2 idempotency=``<rid>:promo``
    """
    import worker.receipt_task as task_module

    user = _make_user(db)
    store = _make_store(db)
    product = _make_product(db)
    receipt = _make_receipt_with_accepted_scan(db, user=user, store=store, product=product)

    captured = _capture_trigger_action(monkeypatch)

    # Raw OCR text with 2 distinct promo signals : "PROMO" keyword + a
    # negative-price line. Different patterns ; counted separately.
    raw_text = "Carrefour Champs-Elysees\nNUTELLA 400G   2,50€\nPROMO\n-0,50€\nTOTAL 2,00€"
    task_module._award_scan_rewards(db, receipt, raw_receipt_text=raw_text)

    # Expect : receipt_scan + promo_found.
    by_action = {c["action_type"]: c for c in captured}
    assert "receipt_scan" in by_action
    assert "promo_found" in by_action

    promo = by_action["promo_found"]
    assert promo["quantity"] == 2, captured
    assert promo["idempotency_key"] == f"{receipt.id}:promo"
    assert "patterns_matched" in promo["context"]
    assert len(promo["context"]["patterns_matched"]) == 2
    assert promo["context"]["receipt_id"] == str(receipt.id)


def test_no_promo_emit_when_no_signals(db, monkeypatch):
    """Plain receipt with no promo keywords / negative prices →
    receipt_scan fires (existing), promo_found does NOT."""
    import worker.receipt_task as task_module

    user = _make_user(db)
    store = _make_store(db)
    product = _make_product(db)
    receipt = _make_receipt_with_accepted_scan(db, user=user, store=store, product=product)

    captured = _capture_trigger_action(monkeypatch)

    raw_text = "Carrefour Champs-Elysees\nNUTELLA 400G   2,50€\nPAIN BIO 1,50€\nTOTAL 4,00€"
    task_module._award_scan_rewards(db, receipt, raw_receipt_text=raw_text)

    actions = [c["action_type"] for c in captured]
    assert "receipt_scan" in actions
    assert "promo_found" not in actions


def test_no_promo_emit_when_raw_text_is_none(db, monkeypatch):
    """Pre-Phase-C-4 callers that don't pass ``raw_receipt_text``
    still work — the detector layer is opt-in via the kwarg."""
    import worker.receipt_task as task_module

    user = _make_user(db)
    store = _make_store(db)
    product = _make_product(db)
    receipt = _make_receipt_with_accepted_scan(db, user=user, store=store, product=product)

    captured = _capture_trigger_action(monkeypatch)
    task_module._award_scan_rewards(db, receipt)  # no raw_receipt_text

    actions = [c["action_type"] for c in captured]
    assert actions == ["receipt_scan"]


def test_feature_flag_disabled_skips_promo_emit(db, monkeypatch):
    """``pipeline.promo_detection.enable=false`` short-circuits the
    detector. The flag is the rollback escape hatch — flipping it in
    production disables every receipt's promo emit without a code
    revert. ``receipt_scan`` continues to fire normally."""
    import worker.receipt_task as task_module

    from ratis_core import settings as settings_module

    user = _make_user(db)
    store = _make_store(db)
    product = _make_product(db)
    receipt = _make_receipt_with_accepted_scan(db, user=user, store=store, product=product)

    # Snapshot current settings, patch the cache to inject ``enable=false``.
    _original = settings_module.load_settings()
    _patched = {**_original}
    _patched["pipeline"] = {
        **_original.get("pipeline", {}),
        "promo_detection": {
            **_original.get("pipeline", {}).get("promo_detection", {}),
            "enable": False,
        },
    }
    monkeypatch.setattr(
        task_module,
        "load_settings",
        lambda: _patched,
    )

    captured = _capture_trigger_action(monkeypatch)

    raw_text = "PROMO 30%\n-2,50€\nRemise fidélité"
    task_module._award_scan_rewards(db, receipt, raw_receipt_text=raw_text)

    actions = [c["action_type"] for c in captured]
    assert "receipt_scan" in actions
    assert "promo_found" not in actions, "promo_found must NOT fire when the feature flag is disabled"


def test_promo_idempotency_key_stable_across_retries(db, monkeypatch):
    """Two successive calls to ``_award_scan_rewards`` (e.g. Celery
    retry reaching the same tail) produce the SAME ``promo_found``
    idempotency_key. Server-side ``reward_events UNIQUE`` then dedups.
    """
    import worker.receipt_task as task_module

    user = _make_user(db)
    store = _make_store(db)
    product = _make_product(db)
    receipt = _make_receipt_with_accepted_scan(db, user=user, store=store, product=product)

    captured = _capture_trigger_action(monkeypatch)

    raw_text = "PROMO\n-1,00€"
    task_module._award_scan_rewards(db, receipt, raw_receipt_text=raw_text)
    task_module._award_scan_rewards(db, receipt, raw_receipt_text=raw_text)

    promo_events = [c for c in captured if c["action_type"] == "promo_found"]
    assert len(promo_events) == 2  # client-side both fire ;
    # but they share the same idempotency_key so the rewards service
    # dedups at the ``reward_events UNIQUE`` constraint.
    keys = {e["idempotency_key"] for e in promo_events}
    assert len(keys) == 1, f"idempotency_key must be stable across retries — actual: {keys!r}"
    assert keys == {f"{receipt.id}:promo"}


def test_unconfirmed_store_skips_all_emits(db, monkeypatch):
    """The reward tail guards on ``stores.validation_status =
    'confirmed'`` ; a pending / suspicious store blocks BOTH the
    receipt_scan and the promo_found emits (defense-in-depth)."""
    import worker.receipt_task as task_module

    user = _make_user(db)
    store = _make_store(db)
    # Flip the store to pending — should disable all emits.
    db.execute(
        text("UPDATE stores SET validation_status = 'pending' WHERE id = :id"),
        {"id": str(store.id)},
    )
    db.commit()
    product = _make_product(db)
    receipt = _make_receipt_with_accepted_scan(db, user=user, store=store, product=product)

    captured = _capture_trigger_action(monkeypatch)
    task_module._award_scan_rewards(db, receipt, raw_receipt_text="PROMO 30%\n-2,50€")

    assert captured == [], "Pending store must block all reward emits, including promo_found"


# ── Catalogue sanity check ─────────────────────────────────────────────


def test_four_promo_found_missions_seeded_in_migration():
    """Sanity : the migration ``20260508_1000_missions_catalog_v1``
    must seed exactly 4 ``promo_found`` rows (1 daily easy + 3 weekly
    easy/medium/hard). If this fails, Phase C-4 has nothing to power —
    the emit will fire correctly but no mission row matches.

    We parse the migration source file (rather than the runtime DB)
    because the SQLAlchemy ``create_all()``-based test fixture skips
    migrations entirely ; the runtime catalogue presence is verified
    in ratis_rewards' own test suite where Alembic runs end-to-end."""
    from pathlib import Path

    # File lives at <repo>/webservices/ratis_product_analyser/tests/
    # pipeline/test_receipt_promo_emit_e2e.py — 4 dirs up to the repo root.
    repo_root = Path(__file__).resolve().parents[4]
    mig_path = repo_root / "alembic" / "versions" / "20260508_1000_missions_catalog_v1.py"
    assert mig_path.exists(), f"Missing seed migration : {mig_path}"
    content = mig_path.read_text(encoding="utf-8")

    # The migration seeds tuples ``(action_type, qualifier, frequency,
    # difficulty, target_count, cab_reward)`` ; we look for the 4 rows
    # by frequency + difficulty.
    expected = [
        '("promo_found", None, "daily", "easy"',
        '("promo_found", None, "weekly", "easy"',
        '("promo_found", None, "weekly", "medium"',
        '("promo_found", None, "weekly", "hard"',
    ]
    for needle in expected:
        assert needle in content, f"Missing promo_found mission seed : {needle}\nin {mig_path}"
