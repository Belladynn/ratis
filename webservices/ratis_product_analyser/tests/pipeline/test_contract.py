"""Contract test — oracle pour le pipeline.

Charge la fixture image, fait traverser les 4 phases, compare le DB state
final contre ``expected_<name>.json``.

⚠ **Opt-in** : le test ne run que si ``RUN_CONTRACT_TEST=1`` est exporté.
Raison : il appelle Anthropic (vrai LLM) + nécessite un seed DB + 5-15s
de cold-start PaddleOCR. Trop lourd pour la CI standard. À activer en
nightly ou en validation manuelle (cf. ARCH § Contract test).

Le test skip aussi si l'oracle JSON contient encore des ``FILL_ME``.

Format de l'oracle (cf. ``expected_intermarche_courbevoie.json``) :
- ``ticket_meta``: image_hash + ocr_engine_version (objectifs, pré-remplis)
- ``items_visibles_sur_le_ticket[]``: ce que l'humain VOIT sur le ticket
  (label, qty, unit_price_cents, total_cents). Le matcher (status,
  match_method) est libre — pas asserté ici.
- ``ticket_total_cents``: total imprimé en bas
- ``store_match.store_status_attendu``: 'matched' / 'suggested' / 'unresolved'
- ``pipeline_audit_log_events_attendus``: events (phase, event) qui DOIVENT
  être émis (set-subset accepté — events supplémentaires OK).
"""

from __future__ import annotations

import json
import os
import unicodedata
from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import text

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> tuple[bytes, dict]:
    image_path = FIXTURES_DIR / f"{name}.jpg"
    expected_path = FIXTURES_DIR / f"expected_{name}.json"
    image_bytes = image_path.read_bytes()
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    return image_bytes, expected


# Skip module if orchestrator missing (blocs 4-7 not yet merged)
orchestrator = pytest.importorskip(
    "worker.pipeline.orchestrator",
    reason="pipeline orchestrator not yet implemented",
)


def _expected_oracle_filled(expected_path: Path) -> bool:
    return "FILL_ME" not in expected_path.read_text(encoding="utf-8")


_INTERMARCHE_EXPECTED = FIXTURES_DIR / "expected_intermarche_courbevoie.json"
if not _expected_oracle_filled(_INTERMARCHE_EXPECTED):
    pytest.skip(
        "expected_intermarche_courbevoie.json contains FILL_ME — "
        "fill from visual inspection before running the contract test.",
        allow_module_level=True,
    )

# Opt-in via ENV var — sinon skip
if not os.environ.get("RUN_CONTRACT_TEST"):
    pytest.skip(
        "Contract test is opt-in. Set RUN_CONTRACT_TEST=1 to run "
        "(requires DB seed + ANTHROPIC_API_KEY + ~30s runtime).",
        allow_module_level=True,
    )


def _normalize_label(label: str) -> str:
    """UPPER + strip accents (best-effort match against scans.scanned_name)."""
    nfkd = unicodedata.normalize("NFKD", label)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.upper().strip()


def _strip_doc(payload):
    """Drop ``_doc`` / ``_help_*`` / ``_FILL_ME_*`` keys recursively."""
    if isinstance(payload, dict):
        return {
            k: _strip_doc(v)
            for k, v in payload.items()
            if not (k == "_doc" or k.startswith("_help") or k.startswith("_FILL_ME"))
        }
    if isinstance(payload, list):
        return [_strip_doc(x) for x in payload]
    return payload


@pytest.fixture(scope="module")
def intermarche_courbevoie_fixture():
    return _load_fixture("intermarche_courbevoie")


def test_intermarche_courbevoie_pipeline_state(db, intermarche_courbevoie_fixture):
    """End-to-end: run pipeline sur l'image, assert le DB state."""
    image_bytes, expected_raw = intermarche_courbevoie_fixture
    expected = _strip_doc(expected_raw)

    # NB : signature réelle de run_pipeline — adapte selon le code mergé
    from datetime import datetime
    from uuid import uuid4

    user_id = uuid4()  # le test seede son user de test ailleurs si besoin
    orchestrator.run_pipeline(
        image_bytes,
        db=db,
        user_id=user_id,
        captured_at=datetime.now(UTC),
        log_level="normal",
    )

    # ── Ticket meta ──
    parsed = (
        db.execute(
            text(
                "SELECT raw_ticket_image_hash, ocr_engine_version FROM parsed_tickets ORDER BY created_at DESC LIMIT 1"
            )
        )
        .mappings()
        .one()
    )
    exp_meta = expected["ticket_meta"]
    assert parsed["raw_ticket_image_hash"] == exp_meta["raw_ticket_image_hash"]
    assert parsed["ocr_engine_version"] == exp_meta["ocr_engine_version"]

    # ── Items visibles : compte + chaque label visible apparaît ──
    scans = (
        db.execute(
            text(
                "SELECT scanned_name, total FROM scans "
                "WHERE parsed_ticket_id IS NOT NULL "
                "ORDER BY scanned_at, scanned_name"
            )
        )
        .mappings()
        .all()
    )
    expected_items = expected["items_visibles_sur_le_ticket"]
    assert len(scans) == len(expected_items), f"Expected {len(expected_items)} scans, got {len(scans)}"
    actual_labels = {_normalize_label(s["scanned_name"] or "") for s in scans}
    for exp_item in expected_items:
        exp_label = _normalize_label(exp_item["label"])
        # On vérifie que le label visible apparaît (substring tolérance OCR)
        matched = any(exp_label in al or al in exp_label for al in actual_labels)
        assert matched, f"Label visible {exp_item['label']!r} introuvable parmi les scans : {sorted(actual_labels)}"

    # ── Total cents ──
    receipt = (
        db.execute(
            text(
                "SELECT total_amount, store_status FROM receipts "
                "WHERE parsed_ticket_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            )
        )
        .mappings()
        .one()
    )
    assert receipt["total_amount"] == expected["ticket_total_cents"], (
        f"Total ticket {receipt['total_amount']} != attendu {expected['ticket_total_cents']}"
    )

    # ── Store match status ──
    exp_store = expected["store_match"]
    assert receipt["store_status"] == exp_store["store_status_attendu"]

    # ── Audit log : events attendus présents (set-subset) ──
    audit_events = (
        db.execute(
            text(
                "SELECT phase, event FROM pipeline_audit_log "
                "WHERE level IN ('normal', 'production') ORDER BY created_at"
            )
        )
        .mappings()
        .all()
    )
    expected_events = {(e["phase"], e["event"]) for e in expected["pipeline_audit_log_events_attendus"]}
    actual_events = {(e["phase"], e["event"]) for e in audit_events}
    missing = expected_events - actual_events
    assert not missing, f"Audit events manquants : {sorted(missing)}"


def test_pipeline_idempotent_on_same_image(db, intermarche_courbevoie_fixture):
    """Running pipeline twice doit NE PAS dupliquer parsed_tickets."""
    image_bytes, _ = intermarche_courbevoie_fixture
    from datetime import datetime
    from uuid import uuid4

    user_id = uuid4()
    orchestrator.run_pipeline(
        image_bytes,
        db=db,
        user_id=user_id,
        captured_at=datetime.now(UTC),
        log_level="normal",
    )
    orchestrator.run_pipeline(
        image_bytes,
        db=db,
        user_id=user_id,
        captured_at=datetime.now(UTC),
        log_level="normal",
    )

    count = db.execute(
        text(
            "SELECT count(*) FROM parsed_tickets pt "
            "WHERE pt.raw_ticket_image_hash = ("
            "  SELECT raw_ticket_image_hash FROM parsed_tickets "
            "  ORDER BY created_at DESC LIMIT 1)"
        )
    ).scalar_one()
    assert count == 1, f"Idempotence violée : {count} rows pour la même image"
