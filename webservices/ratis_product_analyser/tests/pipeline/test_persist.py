"""Phase 4 persist tests — DB-integration via the PA conftest db fixture.

Covers :

- ``parsed_tickets`` upsert + idempotence on ``parsed_jsonb_hash``
- 1-to-1 ItemMatch → scans (no drop, count invariant)
- ``scans.parsed_ticket_id`` linkage to the upserted parsed_tickets row
- ``scans.match_method`` / ``status`` / ``rejected_reason`` mapping per
  cascade outcome
- ``store_status='matched'`` → receipts.store_id + scans.store_id set
- ``store_status='suggested'`` → store_candidates row created
- ``store_status='unresolved'`` → no candidate, no store_id
- ``pipeline_audit_log`` final ``persist_completed`` event written
- ``PersistError`` on count-mismatch invariant violation
- Audit log is best-effort (failure does not abort the persist)
- Receipt barcode persistence (PR-B) :
  - ``receipt_barcode`` set from ``footer.barcode.raw``
  - ``barcode_fields`` jsonb populated from decoded fields
  - ``purchased_at`` priority : barcode date+time → OCR → SENTINEL
  - ``handle_barcode_rescan`` supersedes prior receipts on same barcode

Cf. ``ARCH_receipt_pipeline.md`` § Phase 4 Persister.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from ratis_core.models.product import Product
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text
from worker.pipeline.persist import PersistError, persist_pipeline_result
from worker.pipeline.types import (
    Candidate,
    DecisionInputs,
    ItemMatch,
    MatchedTicket,
    ParsedFooter,
    ParsedHeader,
    ParsedItem,
    ParsedReceiptBarcode,
    ParsedTicket,
    RawTicket,
)

# Anchored to "now - 1 day" so the PR4 age check
# (``consensus.ticket_max_age_days = 7``) never triggers in the
# default builder. Tests that need an out-of-window date pin it inline.
CAPTURED_AT = datetime.now(UTC) - timedelta(days=1)


# ── Builders ──────────────────────────────────────────────────────────────


def _build_raw(receipt_id: uuid.UUID | None = None) -> RawTicket:
    return RawTicket(
        receipt_id=receipt_id or uuid.uuid4(),
        blocks=(),
        barcodes=(),
        image_hash="a" * 64,
        ocr_engine_version="paddleocr-test-fr",
        captured_at=CAPTURED_AT,
    )


def _build_parsed(
    *,
    receipt_id: uuid.UUID,
    items: tuple[ParsedItem, ...] = (),
    image_hash: str = "a" * 64,
    barcode: ParsedReceiptBarcode | None = None,
    purchased_at: datetime | None = CAPTURED_AT,
) -> ParsedTicket:
    header = ParsedHeader(
        brand="INTERMARCHE",
        address_line="1 RUE DE PARIS",
        postcode="92400",
        city="COURBEVOIE",
        phone=None,
        siret=None,
        source_block_ids=(),
    )
    footer = ParsedFooter(
        total_cents=1234,
        vat_breakdown=(),
        payment_method="CB",
        item_count_declared=len(items),
        barcode=barcode,
        source_block_ids=(),
    )
    return ParsedTicket(
        receipt_id=receipt_id,
        items=items,
        header=header,
        footer=footer,
        purchased_at=purchased_at,
        raw_ticket_image_hash=image_hash,
    ).with_jsonb_hash()


def _make_item(
    raw_label: str = "BANANE",
    total_cents: int = 199,
    quantity: int = 1,
    barcode: str | None = None,
    parsing_issues: tuple[str, ...] = (),
) -> ParsedItem:
    return ParsedItem(
        raw_label=raw_label,
        normalized_label=raw_label.upper(),
        quantity=quantity,
        unit_price_cents=total_cents,
        total_cents=total_cents,
        barcode=barcode,
        source_block_ids=(),
        parsing_issues=parsing_issues,
    )


def _matched_for(parsed_item: ParsedItem, *, ean: str) -> ItemMatch:
    return ItemMatch(
        parsed_item_id=parsed_item.id,
        status="matched",
        product_ean=ean,
        match_method="barcode",
        match_confidence=1.0,
        rejected_reason=None,
        top_candidates=(Candidate(product_ean=ean, product_label="X", score=1.0, source="barcode"),),
        decision_inputs=DecisionInputs(
            normalized_label=parsed_item.normalized_label,
            barcode_used=ean,
            knowledge_lookup_hit=False,
            consensus_state=None,
            candidates_considered=1,
        ),
    )


def _unresolved_for(parsed_item: ParsedItem, reason: str = "no_fuzzy_candidate") -> ItemMatch:
    return ItemMatch(
        parsed_item_id=parsed_item.id,
        status="unresolved",
        product_ean=None,
        match_method=None,
        match_confidence=None,
        rejected_reason=reason,
        top_candidates=(),
        decision_inputs=DecisionInputs(
            normalized_label=parsed_item.normalized_label,
            barcode_used=None,
            knowledge_lookup_hit=False,
            consensus_state=None,
            candidates_considered=0,
        ),
    )


def _make_store(db, *, name: str = "Intermarche Courbevoie") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer="intermarche",
        address="1 RUE DE PARIS",
        city="COURBEVOIE",
        postal_code="92400",
        lat=Decimal("48.89"),
        lng=Decimal("2.25"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_user(db) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"persist-test-{uid.hex[:8]}@ratis.fr",
        display_name="PersistTester",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def _make_product(db, *, ean: str, name: str = "Test Product") -> Product:
    p = Product(ean=ean, name=name, source="off")
    db.add(p)
    db.flush()
    db.commit()
    return p


# ── Tests ─────────────────────────────────────────────────────────────────


def test_persist_inserts_parsed_ticket(db):
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    row = db.execute(
        text(
            "SELECT id, parsed_jsonb_hash, raw_ticket_image_hash, ocr_engine_version FROM parsed_tickets WHERE id = :id"
        ),
        {"id": result["parsed_ticket_id"]},
    ).first()
    assert row is not None
    assert row.parsed_jsonb_hash == parsed.parsed_jsonb_hash
    assert row.raw_ticket_image_hash == raw.image_hash
    assert row.ocr_engine_version == "paddleocr-test-fr"


def test_persist_idempotent_same_hash(db):
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    r1 = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()
    r2 = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    assert r1["parsed_ticket_id"] == r2["parsed_ticket_id"]
    count = db.execute(
        text("SELECT count(*) FROM parsed_tickets WHERE parsed_jsonb_hash = :h"),
        {"h": parsed.parsed_jsonb_hash},
    ).scalar_one()
    assert count == 1


def test_persist_inserts_one_scan_per_item_match(db):
    user = _make_user(db)
    items = (
        _make_item("BANANE", total_cents=149),
        _make_item("LAIT", total_cents=199),
        _make_item("PAIN", total_cents=99),
    )
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=items)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=tuple(_unresolved_for(it) for it in items),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    assert len(result["scan_ids"]) == 3
    rows = db.execute(
        text(
            "SELECT id, scanned_name, status, rejected_reason "
            "FROM scans WHERE parsed_ticket_id = :pt ORDER BY scanned_name"
        ),
        {"pt": result["parsed_ticket_id"]},
    ).all()
    assert len(rows) == 3
    names = sorted(r.scanned_name for r in rows)
    assert names == ["BANANE", "LAIT", "PAIN"]
    for row in rows:
        assert row.status == "unresolved"
        assert row.rejected_reason == "no_fuzzy_candidate"


def test_persist_links_scans_to_parsed_ticket(db):
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    rows = db.execute(
        text("SELECT parsed_ticket_id FROM scans WHERE parsed_ticket_id = :pt"),
        {"pt": result["parsed_ticket_id"]},
    ).all()
    assert len(rows) == 1
    assert rows[0].parsed_ticket_id == result["parsed_ticket_id"]


def test_persist_propagates_match_method_and_confidence(db):
    user = _make_user(db)
    _make_product(db, ean="3017620422003", name="Nutella")
    item = _make_item("NUTELLA", total_cents=499, barcode="3017620422003")
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_matched_for(item, ean="3017620422003"),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    row = db.execute(
        text("SELECT status, match_method, match_confidence, product_ean, rejected_reason FROM scans WHERE id = :id"),
        {"id": result["scan_ids"][0]},
    ).first()
    assert row.status == "matched"
    assert row.match_method == "barcode"
    assert float(row.match_confidence) == pytest.approx(1.0)
    assert row.product_ean == "3017620422003"
    assert row.rejected_reason is None


def test_persist_writes_completed_audit_event(db):
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    rows = db.execute(
        text("SELECT phase, event FROM pipeline_audit_log WHERE phase = 'persist' ORDER BY created_at")
    ).all()
    events = {r.event for r in rows}
    assert "parsed_ticket_persisted" in events
    assert "persist_completed" in events
    assert result["audit_event_count"] >= 2


def test_persist_creates_store_candidate_when_suggested(db):
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="suggested",
        store_rejected_reason="store_low_confidence_0.700",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    assert result["store_candidate_id"] is not None
    row = db.execute(
        text("SELECT raw_header, retailer_guess, postal_code, status FROM store_candidates WHERE id = :id"),
        {"id": result["store_candidate_id"]},
    ).first()
    assert row is not None
    assert row.retailer_guess == "INTERMARCHE"
    assert row.postal_code == "92400"
    assert row.status == "pending"
    assert "INTERMARCHE" in row.raw_header


def test_persist_sets_store_id_when_matched(db):
    user = _make_user(db)
    store = _make_store(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=store.id,
        store_status="matched",
        store_rejected_reason=None,
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    receipt = db.execute(
        text("SELECT store_id, store_status FROM receipts WHERE id = :id"),
        {"id": raw.receipt_id},
    ).first()
    assert receipt.store_id == store.id
    assert receipt.store_status == "confirmed"

    scan = db.execute(
        text("SELECT store_id, store_status FROM scans WHERE id = :id"),
        {"id": result["scan_ids"][0]},
    ).first()
    assert scan.store_id == store.id
    assert scan.store_status == "confirmed"


def test_persist_no_store_candidate_when_unresolved(db):
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    assert result["store_candidate_id"] is None
    count = db.execute(text("SELECT count(*) FROM store_candidates")).scalar_one()
    assert count == 0


def test_persist_raises_persist_error_on_count_mismatch(db):
    """Invariant : len(matched.item_matches) MUST equal len(parsed.items).

    We bypass Pydantic by hand-building a MatchedTicket where the item_matches
    references a parsed_item_id that doesn't exist — simulates a Phase 3 bug.
    """
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    # MatchedTicket is well-formed (Pydantic invariants would block a count
    # mismatch via subclassing — easier to forge a referential mismatch).
    bogus = ItemMatch(
        parsed_item_id=uuid.uuid4(),  # NOT in parsed.items
        status="unresolved",
        rejected_reason="bogus",
        decision_inputs=DecisionInputs(
            normalized_label="X",
            barcode_used=None,
            knowledge_lookup_hit=False,
            consensus_state=None,
            candidates_considered=0,
        ),
    )
    # Need exactly 1 item_match to pass the count check, then trip the
    # ID-lookup check.
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(bogus,),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    with pytest.raises(PersistError, match="parsed_item_id"):
        persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)


def test_persist_raises_when_jsonb_hash_missing(db):
    """ParsedTicket.with_jsonb_hash() must be called before persist — invariant."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    # NO with_jsonb_hash() — parsed_jsonb_hash stays None.
    header = ParsedHeader(brand="X", source_block_ids=())
    footer = ParsedFooter(
        total_cents=100,
        vat_breakdown=(),
        payment_method=None,
        item_count_declared=None,
        barcode=None,
        source_block_ids=(),
    )
    parsed = ParsedTicket(
        receipt_id=raw.receipt_id,
        items=(item,),
        header=header,
        footer=footer,
        purchased_at=CAPTURED_AT,
        raw_ticket_image_hash=raw.image_hash,
    )
    assert parsed.parsed_jsonb_hash is None

    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    with pytest.raises(PersistError, match="parsed_jsonb_hash"):
        persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)


# ── Barcode persistence (PR-B) ────────────────────────────────────────────


def test_persist_writes_receipt_barcode_when_present(db):
    """``footer.barcode.raw`` written to ``receipts.receipt_barcode``."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    bc = ParsedReceiptBarcode(
        raw="20260430143067122010" + "0100",
        retailer_key="intermarche",
        store_code="00100",
    )
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=bc)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()
    row = db.execute(
        text("SELECT receipt_barcode FROM receipts WHERE id = :id"),
        {"id": raw.receipt_id},
    ).first()
    assert row.receipt_barcode == bc.raw


def test_persist_writes_barcode_fields_when_parsed(db):
    """Decoded barcode fields persisted as jsonb (raw excluded)."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    bc = ParsedReceiptBarcode(
        raw="20260430143067122010" + "0100",
        retailer_key="intermarche",
        store_code="00100",
        caisse="201",
        tx_id="6712",
        date=date(2026, 4, 30),
        time=time(14, 30),
    )
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=bc)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()
    row = db.execute(
        text("SELECT barcode_fields FROM receipts WHERE id = :id"),
        {"id": raw.receipt_id},
    ).first()
    fields = row.barcode_fields
    assert fields is not None
    assert fields["retailer_key"] == "intermarche"
    assert fields["store_code"] == "00100"
    assert fields["caisse"] == "201"
    assert fields["tx_id"] == "6712"
    assert fields["date"] == "2026-04-30"
    assert fields["time"] == "14:30:00"
    assert "raw" not in fields, "raw must live in receipt_barcode column"


def test_persist_no_barcode_fields_when_no_barcode(db):
    """No barcode → receipt_barcode + barcode_fields stay NULL."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=None)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()
    row = db.execute(
        text("SELECT receipt_barcode, barcode_fields FROM receipts WHERE id = :id"),
        {"id": raw.receipt_id},
    ).first()
    assert row.receipt_barcode is None
    assert row.barcode_fields is None


def test_persist_handles_rescan_supersedes_prior_receipt(db):
    """Two receipts with same barcode raw → old one's scans superseded,
    old.receipt_barcode cleared so unique index allows new insert."""
    user = _make_user(db)
    bc = ParsedReceiptBarcode(
        raw="20260430143067122010" + "0100",
        retailer_key="intermarche",
        store_code="00100",
    )

    # First receipt + scan.
    raw1 = _build_raw()
    item1 = _make_item("BANANE", total_cents=149)
    parsed1 = _build_parsed(receipt_id=raw1.receipt_id, items=(item1,), barcode=bc)
    matched1 = MatchedTicket(
        parsed_ticket_id=parsed1.id,
        item_matches=(_unresolved_for(item1),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    r1 = persist_pipeline_result(raw=raw1, parsed=parsed1, matched=matched1, db=db, user_id=user.id)
    db.commit()

    # Second receipt with same barcode.
    raw2 = _build_raw()
    item2 = _make_item("LAIT", total_cents=199)
    parsed2 = _build_parsed(receipt_id=raw2.receipt_id, items=(item2,), barcode=bc)
    matched2 = MatchedTicket(
        parsed_ticket_id=parsed2.id,
        item_matches=(_unresolved_for(item2),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    persist_pipeline_result(raw=raw2, parsed=parsed2, matched=matched2, db=db, user_id=user.id)
    db.commit()

    # Old receipt : barcode cleared.
    old = db.execute(
        text("SELECT receipt_barcode FROM receipts WHERE id = :id"),
        {"id": raw1.receipt_id},
    ).first()
    assert old.receipt_barcode is None

    # Old scans : status='rejected', rejected_reason='superseded_rescan'.
    old_scan = db.execute(
        text("SELECT status, rejected_reason FROM scans WHERE id = :id"),
        {"id": r1["scan_ids"][0]},
    ).first()
    assert old_scan.status == "rejected"
    assert old_scan.rejected_reason == "superseded_rescan"

    # New receipt : barcode set.
    new = db.execute(
        text("SELECT receipt_barcode FROM receipts WHERE id = :id"),
        {"id": raw2.receipt_id},
    ).first()
    assert new.receipt_barcode == bc.raw


def test_persist_uses_barcode_date_for_purchased_at_when_present(db):
    """``footer.barcode.date`` (+ optional time) wins over OCR ``parsed.purchased_at``."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    bc = ParsedReceiptBarcode(
        raw="20260415123412345678" + "9012",
        retailer_key="intermarche",
        date=date(2026, 4, 15),
        time=time(12, 34, 56),
    )
    # OCR-extracted date is different (2026-04-30) — barcode must win.
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=bc)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()
    row = db.execute(
        text("SELECT purchased_at, purchased_at_with_time FROM receipts WHERE id = :id"),
        {"id": raw.receipt_id},
    ).first()
    assert row.purchased_at == date(2026, 4, 15)
    # purchased_at_with_time combines barcode date + time when both present.
    assert row.purchased_at_with_time is not None
    assert row.purchased_at_with_time.date() == date(2026, 4, 15)
    assert row.purchased_at_with_time.time().replace(microsecond=0) == time(12, 34, 56)


def test_persist_falls_back_to_ocr_date_when_barcode_no_date(db):
    """Barcode has raw but no date → use OCR ``parsed.purchased_at``."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    bc = ParsedReceiptBarcode(
        raw="UNPARSEABLE12345",
        retailer_key=None,
        date=None,
    )
    ocr_date = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=bc, purchased_at=ocr_date)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()
    row = db.execute(
        text("SELECT purchased_at FROM receipts WHERE id = :id"),
        {"id": raw.receipt_id},
    ).first()
    assert row.purchased_at == date(2026, 4, 28)


def test_persist_falls_back_to_sentinel_when_no_date_anywhere(db):
    """No barcode date AND no OCR date.

    Pre anti-fraud PR3 the receipt would have INSERTed with the SENTINEL
    date and accepted scans regardless ; PR3 (cf. ARCH § étape 4
    `missing_mandatory_signals_for_dedup`) now rejects such receipts
    because the fingerprint cannot be computed without a date.

    The contract preserved here :

    - The skeleton receipt row still uses SENTINEL ``1970-01-01`` for
      ``purchased_at`` (the receipt row must exist to satisfy the
      ``scans.receipt_id NOT NULL`` FK for the rejected sentinel scan).
    - The pipeline does NOT raise — it persists the rejection through
      a single sentinel ``scans`` row with
      ``rejected_reason='missing_mandatory_signals_for_dedup:missing_date'``.
    """
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=None, purchased_at=None)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()
    row = db.execute(
        text("SELECT purchased_at FROM receipts WHERE id = :id"),
        {"id": raw.receipt_id},
    ).first()
    assert row.purchased_at == date(1970, 1, 1)
    # PR3 contract — the rejected sentinel scan carries the precise reason.
    scan_row = db.execute(
        text("SELECT status, rejected_reason FROM scans WHERE receipt_id = :rid"),
        {"rid": raw.receipt_id},
    ).first()
    assert scan_row is not None
    assert scan_row.status == "rejected"
    assert scan_row.rejected_reason == "missing_mandatory_signals_for_dedup:missing_date"


def test_persist_log_level_filters_audit_events(db):
    """log_level='production' drops verbose events from the audit log."""
    user = _make_user(db)
    items = (_make_item("A", total_cents=100), _make_item("B", total_cents=200))
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=items)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=tuple(_unresolved_for(it) for it in items),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    persist_pipeline_result(
        raw=raw,
        parsed=parsed,
        matched=matched,
        db=db,
        user_id=user.id,
        log_level="production",
    )
    db.commit()

    rows = db.execute(text("SELECT level FROM pipeline_audit_log WHERE phase = 'persist'")).all()
    # In production, scan_persisted (level=verbose) must be filtered out.
    levels = {r.level for r in rows}
    assert "verbose" not in levels


# ── F-PA-3 — NRC ledger record_resolution ─────────────────────────────────


def test_persist_records_nrc_ledger_for_barcode_match(db):
    """A V3 scan matched via ``barcode`` writes one row in
    ``product_name_resolutions`` so the NRC ledger sees the user's
    physical-scan vote (strongest signal). Mirrors V2 ``barcode_service``.

    F-PA-3 — pre-fix V3 never called ``record_resolution`` ; the ledger
    state machine (PENDING / VERIFIED / ...) never advanced from V3
    traffic and the shadow-ban anti-fraud weight_override never fired.
    """
    user = _make_user(db)
    store = _make_store(db)
    _make_product(db, ean="3017620422003", name="Nutella")
    item = _make_item("NUTELLA", total_cents=499, barcode="3017620422003")
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_matched_for(item, ean="3017620422003"),),
        store_match_id=store.id,
        store_status="matched",
        store_rejected_reason=None,
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    rows = db.execute(
        text(
            "SELECT scan_id, store_id, normalized_label, product_ean, "
            "       user_id, match_method, source_type, weight_override "
            "FROM product_name_resolutions "
            "WHERE scan_id = :sid"
        ),
        {"sid": result["scan_ids"][0]},
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.normalized_label == "NUTELLA"
    assert row.product_ean == "3017620422003"
    assert row.match_method == "barcode"
    assert row.source_type == "receipt"
    assert row.weight_override is None  # user not shadow-banned


def test_persist_skips_nrc_ledger_for_consensus_match(db):
    """When the matcher used ``consensus_match`` (i.e. the EAN came from
    the ledger itself), we MUST NOT re-write to the ledger : it would
    loop a row's own vote back into its own consensus computation.

    Matches V2 receipt_task lines 1946-1952 documented refonte 2026-05-02.
    """
    user = _make_user(db)
    store = _make_store(db)
    _make_product(db, ean="3017620422003", name="Nutella")
    item = _make_item("NUTELLA", total_cents=499)
    consensus_match = ItemMatch(
        parsed_item_id=item.id,
        status="matched",
        product_ean="3017620422003",
        match_method="consensus_match",
        match_confidence=0.95,
        rejected_reason=None,
        top_candidates=(),
        decision_inputs=DecisionInputs(
            normalized_label=item.normalized_label,
            barcode_used=None,
            knowledge_lookup_hit=False,
            consensus_state="VERIFIED",
            candidates_considered=1,
        ),
    )
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(consensus_match,),
        store_match_id=store.id,
        store_status="matched",
        store_rejected_reason=None,
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    count = db.execute(
        text("SELECT count(*) FROM product_name_resolutions WHERE scan_id = :sid"),
        {"sid": result["scan_ids"][0]},
    ).scalar_one()
    assert count == 0


def test_persist_skips_nrc_ledger_for_unresolved_scan(db):
    """An unresolved scan (no EAN) has nothing to record in the ledger —
    the (scan, label, ean) tuple is incomplete by definition.
    """
    user = _make_user(db)
    store = _make_store(db)
    item = _make_item("UNKNOWN_THING")
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=store.id,
        store_status="matched",
        store_rejected_reason=None,
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    count = db.execute(
        text("SELECT count(*) FROM product_name_resolutions WHERE scan_id = :sid"),
        {"sid": result["scan_ids"][0]},
    ).scalar_one()
    assert count == 0


def test_persist_skips_nrc_ledger_when_store_unresolved(db):
    """A barcode-matched scan with no store (store_status='unknown')
    cannot feed the ledger : the ledger is keyed on (store_id, label).
    Mirrors V2 ``barcode_service`` skip branch.
    """
    user = _make_user(db)
    _make_product(db, ean="3017620422003", name="Nutella")
    item = _make_item("NUTELLA", total_cents=499, barcode="3017620422003")
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_matched_for(item, ean="3017620422003"),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    count = db.execute(
        text("SELECT count(*) FROM product_name_resolutions WHERE scan_id = :sid"),
        {"sid": result["scan_ids"][0]},
    ).scalar_one()
    assert count == 0


# ── End-to-end V3 path integration (F-PA-1 / F-PA-3 / F-PA-5) ─────────────


def test_pipeline_end_to_end_grants_nrc_and_reconcile_in_one_scan(db, monkeypatch):
    """One V3 scan exercises all 3 audit fixes at once :

    - F-PA-1 : ``trigger_action`` + ``trigger_cashback_scan`` fire once
      with idempotency_key=receipt.id (CAB / XP not silently zeroed).
    - F-PA-3 : ``record_resolution`` writes the NRC ledger row for the
      barcode-matched scan (consensus state machine advances).
    - F-PA-5 : ``reconcile_unknown_scans_for_receipt`` attaches a prior
      unknown label scan within geo-radius (PII cleared).

    This is the smoke-test sealing the V3 path against the silent
    rollout-day catastrophe documented in the deep audit.
    """
    from datetime import date as date_cls
    from datetime import timedelta

    import worker.receipt_task as task_module
    from ratis_core.models.scan import Receipt, Scan
    from services.reconciliation_service import (
        reconcile_unknown_scans_for_receipt,
    )

    # ── Setup : user + store + product ─────────────────────────────────
    user = _make_user(db)
    store = _make_store(db)
    _make_product(db, ean="3017620422003", name="Nutella")

    # Pre-existing unknown label scan within 100m of the store.
    unknown_scan = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=None,
        store_status="unknown",
        scan_type="electronic_label",
        scanned_name="NUTELLA 400G",
        price=250,
        quantity=1.0,
        status="pending",
        user_lat=Decimal(str(float(store.lat) + 0.00005)),
        user_lng=Decimal(str(float(store.lng) + 0.00005)),
        scanned_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(unknown_scan)
    db.flush()
    db.commit()

    # Pre-create the receipt row (mirrors what the upload route does
    # before enqueueing the worker). The V3 persist UPSERTs it later.
    receipt = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,  # final state — V3 persist will keep this
        purchased_at=date_cls.today(),
        image_r2_key="fake-key.jpg",
    )
    db.add(receipt)
    db.flush()
    db.commit()

    # ── Run real persist_pipeline_result (Phase 4) for V3 ──────────────
    item = _make_item("NUTELLA 400G", total_cents=250, barcode="3017620422003")
    raw = _build_raw(receipt_id=receipt.id)
    parsed = _build_parsed(receipt_id=receipt.id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_matched_for(item, ean="3017620422003"),),
        store_match_id=store.id,
        store_status="matched",
        store_rejected_reason=None,
    )
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)

    # ── Phase B reconciliation (same pre-commit window as V2/V3 tail) ──
    db.refresh(receipt)
    reconcile_unknown_scans_for_receipt(db, receipt)

    db.commit()

    # F-PA-1 — post-commit grants (helper queries DB).
    actions: list[dict] = []
    cashback_calls: list[list[dict]] = []
    monkeypatch.setattr(
        task_module,
        "trigger_action",
        lambda uid, action_type, **kw: actions.append({"user_id": uid, "action_type": action_type, **kw}),
    )
    monkeypatch.setattr(
        task_module,
        "trigger_cashback_scan",
        lambda uid, lines: cashback_calls.append(lines),
    )
    db.refresh(receipt)
    task_module._award_scan_rewards(db, receipt)

    # ── Assertions ──────────────────────────────────────────────────────
    # F-PA-1 — grants fired once with the right shape.
    assert len(actions) == 1
    assert actions[0]["action_type"] == "receipt_scan"
    assert actions[0]["idempotency_key"] == str(receipt.id)
    assert len(cashback_calls) == 1
    assert cashback_calls[0][0]["ean"] == "3017620422003"
    assert cashback_calls[0][0]["price"] == 250

    # F-PA-3 — NRC ledger row written for the barcode-matched scan.
    matched_scan_id = db.execute(
        text("SELECT id FROM scans WHERE receipt_id = :rid AND status = 'matched'"),
        {"rid": str(receipt.id)},
    ).scalar_one()
    ledger_row = db.execute(
        text(
            "SELECT match_method, source_type, normalized_label, product_ean "
            "FROM product_name_resolutions WHERE scan_id = :sid"
        ),
        {"sid": matched_scan_id},
    ).first()
    assert ledger_row is not None
    assert ledger_row.match_method == "barcode"
    assert ledger_row.source_type == "receipt"
    assert ledger_row.normalized_label == "NUTELLA 400G"
    assert ledger_row.product_ean == "3017620422003"

    # F-PA-5 — prior unknown label scan got reconciled.
    db.refresh(unknown_scan)
    assert unknown_scan.store_id == store.id
    assert unknown_scan.store_status == "confirmed"
    assert unknown_scan.user_lat is None
    assert unknown_scan.user_lng is None


def test_persist_records_nrc_ledger_with_weight_override_for_shadow_banned(db):
    """Shadow-banned user → ``record_resolution`` sets ``weight_override=0``
    so the vote is preserved for audit but carries zero weight in
    consensus state computation. Anti-fraud V1 (NRC bloc A).
    """
    user = _make_user(db)
    db.execute(
        text("UPDATE users SET is_shadow_banned = true WHERE id = :uid"),
        {"uid": str(user.id)},
    )
    db.flush()
    store = _make_store(db)
    _make_product(db, ean="3017620422003", name="Nutella")
    item = _make_item("NUTELLA", total_cents=499, barcode="3017620422003")
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,))
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_matched_for(item, ean="3017620422003"),),
        store_match_id=store.id,
        store_status="matched",
        store_rejected_reason=None,
    )

    result = persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    row = db.execute(
        text("SELECT weight_override FROM product_name_resolutions WHERE scan_id = :sid"),
        {"sid": result["scan_ids"][0]},
    ).first()
    assert row is not None
    assert row.weight_override == 0


# ── Advisory lock — barcode race serialisation (KP-41) ────────────────────


def _capture_executed_sql(db, monkeypatch) -> list[str]:
    """Spy on ``db.execute`` and return a growing list of SQL text strings."""
    seen: list[str] = []
    real_execute = db.execute

    def _spy(statement, *args, **kwargs):
        try:
            seen.append(str(statement))
        except Exception:
            pass
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", _spy)
    return seen


def test_persist_takes_advisory_lock_when_barcode_present(db, monkeypatch):
    """KP-41 : two concurrent uploads of the same physical receipt race on
    ``uq_receipts_receipt_barcode``. Persist must serialise on a
    ``pg_advisory_xact_lock(hashtext(barcode))`` before touching the
    barcode, so the second transaction blocks instead of 500-ing."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    bc = ParsedReceiptBarcode(
        raw="20260430143067122010" + "0100",
        retailer_key="intermarche",
        store_code="00100",
    )
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=bc)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    seen = _capture_executed_sql(db, monkeypatch)
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    lock_stmts = [s for s in seen if "pg_advisory_xact_lock" in s]
    assert lock_stmts, "advisory lock not taken for a barcode-bearing ticket"
    # The lock must be acquired BEFORE any receipts INSERT/SELECT.
    first_lock = next(i for i, s in enumerate(seen) if "pg_advisory_xact_lock" in s)
    first_receipts = next((i for i, s in enumerate(seen) if "receipts" in s.lower()), len(seen))
    assert first_lock < first_receipts, "advisory lock taken after receipts access"


def test_persist_no_advisory_lock_when_barcode_absent(db, monkeypatch):
    """No barcode → fingerprint path drives dedup, no advisory lock needed."""
    user = _make_user(db)
    item = _make_item()
    raw = _build_raw()
    parsed = _build_parsed(receipt_id=raw.receipt_id, items=(item,), barcode=None)
    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(_unresolved_for(item),),
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )
    seen = _capture_executed_sql(db, monkeypatch)
    persist_pipeline_result(raw=raw, parsed=parsed, matched=matched, db=db, user_id=user.id)
    db.commit()

    assert not [s for s in seen if "pg_advisory_xact_lock" in s]
