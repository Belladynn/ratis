"""Tests for store_detector — header classifier and store matching pipeline."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from worker.ocr.store_detector import (
    _CFG,
    _candidate_intersection,
    detect_store,
    extract_store_signals,
    lookup_fingerprints,
    record_candidate,
    record_fingerprints,
    score_signals,
)

# ── extract_store_signals ─────────────────────────────────────────────────────


class TestExtractStoreSignals:
    """Pure classifier tests — no DB needed."""

    def test_detects_labelled_phone(self):
        lines = ["MONOPRIX COURBEVOIE 10", "12 RUE DE L'ABREUVOIR", "92400 COURBEVOIE", "Tél: 0149970970"]
        signals = extract_store_signals(lines)
        assert signals["phone"] == "0149970970"

    def test_detects_postal_code_and_city(self):
        lines = ["CARREFOUR MARKET", "5 AV DU GENERAL DE GAULLE", "75008 PARIS"]
        signals = extract_store_signals(lines)
        assert signals["postal_code"] == "75008"

    def test_detects_brand_from_uppercase_header(self):
        lines = ["LIDL PARIS 15", "3 RUE DU COMMERCE", "75015 PARIS"]
        signals = extract_store_signals(lines)
        assert signals["retailer"] == "LIDL PARIS 15"

    def test_detects_store_code_from_barcode_line(self):
        # Without barcode_formats, store_code must NOT be extracted (two-pass retailer-aware, no fallback)
        # See TestExtractStoreSignalsBrandAware for the retailer-aware extraction tests
        lines = ["MONOPRIX COURBEVOIE 10", "234103109975250624084122"]
        signals = extract_store_signals(lines)
        assert "store_code" not in signals

    def test_detects_unlabelled_phone(self):
        lines = ["Super U Bordeaux", "0556123456"]
        signals = extract_store_signals(lines)
        assert signals["phone"] == "0556123456"

    def test_ocr_errors_in_phone_corrected(self):
        # O→0, A→4, I→1
        lines = ["AUCHAN", "Tél: O1 A9 97 O9 7O"]
        signals = extract_store_signals(lines)
        assert signals["phone"] == "0149970970"

    def test_empty_lines_returns_empty(self):
        assert extract_store_signals([]) == {}

    def test_barcode_line_not_classified_as_brand(self):
        lines = ["234103109975250624084122"]
        signals = extract_store_signals(lines)
        assert "retailer" not in signals

    def test_barcode_min_digits_from_config(self, monkeypatch):
        """DP-07: barcode_min_digits should come from config, not hardcoded.
        Without barcode_formats no store_code is produced (two-pass retailer-aware contract).
        The threshold still controls whether the barcode is _recognised_ as a barcode line
        (i.e. not classified as retailer/phone/address).
        """
        monkeypatch.setattr("worker.ocr.store_detector._CFG", {**_CFG, "barcode_min_digits": 25})
        lines = ["12345678901234567890"]  # 20 digits — below 25, not recognised as barcode
        signals = extract_store_signals(lines)
        assert "store_code" not in signals

        monkeypatch.setattr("worker.ocr.store_detector._CFG", {**_CFG, "barcode_min_digits": 15})
        # 18 digits — above 15, recognised as barcode but no formats → still no store_code
        lines_with_retailer = ["MONOPRIX", "123456789012345678"]
        signals_no_fmt = extract_store_signals(lines_with_retailer)
        assert "store_code" not in signals_no_fmt
        # With a matching format, store_code IS extracted
        fmt = {"monoprix": {"length": 18, "fields": [{"name": "store_code", "start": 0, "end": 4}]}}
        signals_with_fmt = extract_store_signals(lines_with_retailer, barcode_formats=fmt)
        assert "store_code" in signals_with_fmt


# ── score_signals ─────────────────────────────────────────────────────────────


class TestScoreSignals:
    def test_empty_signals_returns_zero(self):
        assert score_signals({}) == 0

    def test_phone_only_scores_30(self):
        # 2026-04-27 — phone is now a retailer signal (was 80, dropped to 30
        # because a corporate standard phone really only narrows down to an
        # enseigne, not a single store). See
        # refactor/phone-as-retailer-signal.
        assert score_signals({"phone": "+33600000001"}) == 30

    def test_brand_with_postal_scores_50(self):
        # retailer alone = 20, retailer+postal = 50 (brand_postal takes priority)
        assert score_signals({"retailer": "MONOPRIX", "postal_code": "75001"}) == 50

    def test_brand_alone_scores_20(self):
        assert score_signals({"retailer": "MONOPRIX"}) == 20

    def test_combined_signals(self):
        signals = {
            "phone": "+33600000001",
            "retailer": "MONOPRIX",
            "postal_code": "75001",
            "address": "5 RUE DE LA PAIX",
        }
        # phone(30) + retailer_postal(50) + address_fuzzy(40) = 120
        # (phone weight reduced 2026-04-27 — refactor/phone-as-retailer-signal)
        assert score_signals(signals) == 120


# ── lookup_fingerprints ───────────────────────────────────────────────────────


class TestLookupFingerprints:
    def test_phone_fingerprint_is_ignored(self, db, store):
        """2026-04-27 — phone is no longer a fingerprint signal.

        Even if a legacy phone fingerprint row exists in the table (from
        before the retailer-signal refactor), lookup_fingerprints must NOT
        match it — a corporate phone is shared across franchises and using
        it as a store id risks Marseille-vs-Lille false matches.
        See refactor/phone-as-retailer-signal.
        """
        db.execute(
            text("""
            INSERT INTO store_fingerprints (id, store_id, signal_type, signal_value, confirmed_count)
            VALUES (gen_random_uuid(), :store_id, 'phone', '0149970970', 1)
        """),
            {"store_id": str(store.id)},
        )
        db.flush()

        signals = {"phone": "0149970970"}
        result = lookup_fingerprints(db, signals)
        assert result is None

    def test_no_match_returns_none(self, db):
        signals = {"phone": "0600000000"}
        assert lookup_fingerprints(db, signals) is None

    def test_match_on_brand_postal(self, db, store):
        db.execute(
            text("""
            INSERT INTO store_fingerprints (id, store_id, signal_type, signal_value, confirmed_count)
            VALUES (gen_random_uuid(), :store_id, 'retailer_postal', 'LIDL:75015', 1)
        """),
            {"store_id": str(store.id)},
        )
        db.flush()

        signals = {"retailer": "LIDL", "postal_code": "75015"}
        result = lookup_fingerprints(db, signals)
        assert result == store.id

    def test_roundtrip_record_then_lookup(self, db, store):
        """record_fingerprints then lookup_fingerprints must return the same store_id.

        Uses retailer+postal — phone is no longer fingerprintable
        (2026-04-27). See refactor/phone-as-retailer-signal.
        """
        signals = {"retailer": "CARREFOUR", "postal_code": "75001"}
        record_fingerprints(db, store.id, signals)
        db.flush()
        result = lookup_fingerprints(db, signals)
        assert result == store.id


# ── detect_store integration ──────────────────────────────────────────────────


class TestDetectStore:
    def test_phone_alone_does_not_auto_match(self, db, store):
        """2026-04-27 — phone alone is now a *retailer* signal (score 30),
        below threshold_confirm (40). It can no longer auto-match a single
        store on its own — that's the whole point of
        refactor/phone-as-retailer-signal (Marseille / Lille false-match
        prevention).
        """
        db.execute(
            text("UPDATE stores SET phone = '0149970970' WHERE id = :id"),
            {"id": str(store.id)},
        )
        db.flush()

        ocr_lines = ["Tél: 0149970970"]
        result = detect_store(db, ocr_lines)
        assert result is None

    def test_phone_combined_with_retailer_postal_matches(self, db, store):
        """Phone (30) + retailer (already 50 with postal) → 80 = threshold_auto.

        Phone now boosts an otherwise borderline retailer+postal candidate
        rather than identifying a store on its own. The store must have
        retailer_id set so phone can infer the retailer.
        """
        # Need a retailer row + retailer_id on the store so phone-inference works.
        db.execute(
            text(
                "INSERT INTO retailers (id, canonical_name, slug, is_verified)"
                " VALUES (gen_random_uuid(), 'SuperTest', 'supertest', false)"
            )
        )
        rid = db.execute(text("SELECT id FROM retailers WHERE slug = 'supertest'")).scalar()
        db.execute(
            text(
                "UPDATE stores SET phone = '0149970970', retailer = 'supertest', "
                "retailer_id = :rid, postal_code = '75001' WHERE id = :id"
            ),
            {"id": str(store.id), "rid": str(rid)},
        )
        db.flush()

        ocr_lines = ["SUPERTEST PARIS", "75001 PARIS", "Tél: 0149970970"]
        result = detect_store(db, ocr_lines)
        assert result is not None
        assert result.store_id == store.id
        assert result.auto is True

    def test_returns_none_when_no_match(self, db):
        ocr_lines = ["SOME UNKNOWN STORE", "0600000000", "99999 VILLE"]
        result = detect_store(db, ocr_lines)
        assert result is None

    def test_brand_alone_scores_20(self):
        """DP-08: Verify that retailer signal alone contributes exactly 20 points."""
        score = score_signals({"retailer": "BOUTIQUE"})
        assert score == 20

    def test_score_below_threshold_auto_returns_none(self, db, store, monkeypatch):
        """DP-08: Verify that when best candidate score < threshold_auto, detect_store returns None.

        Uses monkeypatch to set threshold_auto to an impossibly high value (100),
        ensuring even a perfect score would fall below it. The store has retailer+postal
        to make a valid candidate, but the patched threshold guarantees the result is None.
        """
        monkeypatch.setattr(
            "worker.ocr.store_detector._CFG",
            {**_CFG, "threshold_auto": 100},
        )
        db.execute(
            text("UPDATE stores SET retailer = 'SUPERTEST', postal_code = '75001' WHERE id = :id"),
            {"id": str(store.id)},
        )
        db.flush()
        ocr_lines = ["SUPERTEST PARIS", "75001"]
        result = detect_store(db, ocr_lines)
        assert result is None

    def test_soft_match_via_brand_postal(self, db, store):
        """retailer + postal gives score=50 — above threshold_confirm (40), below threshold_auto (80) → soft match."""
        db.execute(
            text("UPDATE stores SET retailer = 'supertest', postal_code = '75001', phone = NULL WHERE id = :id"),
            {"id": str(store.id)},
        )
        db.flush()
        # OCR: retailer alone → 20pts; retailer+postal → normalized to 50pts; no phone
        ocr_lines = ["SUPERTEST PARIS", "75001 PARIS"]
        result = detect_store(db, ocr_lines)
        assert result is not None
        assert result.store_id == store.id
        assert result.auto is False
        assert result.score == 50

    def test_barcode_store_code_matches_via_fingerprint(self, db, store):
        """Barcode-derived store_code should match an existing fingerprint."""
        record_fingerprints(db, store.id, {"store_code": "07879", "retailer": "INTERMARCHE"})
        db.commit()
        lines = ["INTERMARCHE"]
        result = detect_store(db, lines, barcode_store_code="07879")
        assert result is not None
        assert result.store_id == store.id
        assert result.auto is True
        assert result.score == 100

    def test_barcode_store_code_overrides_ocr_store_code(self, db, store):
        """Barcode store_code takes precedence over OCR-extracted store_code."""
        record_fingerprints(db, store.id, {"store_code": "07879", "retailer": "INTERMARCHE"})
        db.commit()
        lines = ["INTERMARCHE", "99990000111122223333"]
        result = detect_store(db, lines, barcode_store_code="07879")
        assert result is not None
        assert result.store_id == store.id

    def test_detect_store_forwards_known_retailers(self, db, store):
        """detect_store must accept known_retailers and forward it to extract_store_signals.

        Regression: previously known_retailers was not passed through, so the retailer-aware
        pre-scan in extract_store_signals was never used during store detection.

        After 2026-04-27 (refactor/phone-as-retailer-signal) the phone signal
        alone can no longer auto-match — we combine phone(30) with
        retailer_postal(50) so the total reaches threshold_auto(80) and the
        match is verified end-to-end.
        """
        # Need a retailer row + retailer_id wired so phone-inference works.
        db.execute(
            text(
                "INSERT INTO retailers (id, canonical_name, slug, is_verified)"
                " VALUES (gen_random_uuid(), 'Lidl', 'lidl', true)"
                " ON CONFLICT (slug) DO NOTHING"
            )
        )
        rid = db.execute(text("SELECT id FROM retailers WHERE slug = 'lidl'")).scalar()
        db.execute(
            text(
                "UPDATE stores SET phone = '0149970970', retailer = 'lidl', "
                "retailer_id = :rid, postal_code = '75001' WHERE id = :id"
            ),
            {"id": str(store.id), "rid": str(rid)},
        )
        db.flush()
        # Passing known_retailers must not raise TypeError (the bug) and the match must succeed.
        # Use bare "LIDL" so the prescan exact-match picks it up (any longer
        # variant like "LIDL TEST" would not normalise to "lidl").
        ocr_lines = ["EN CAISSE", "LIDL", "75001 PARIS", "Tél: 0149970970"]
        result = detect_store(db, ocr_lines, known_retailers=frozenset(["lidl"]))
        assert result is not None
        assert result.store_id == store.id
        assert result.auto is True


# ── record_fingerprints ───────────────────────────────────────────────────────


class TestRecordFingerprints:
    def test_does_not_insert_phone_fingerprint(self, db, store):
        """2026-04-27 — phone is no longer recorded as a fingerprint signal.

        A corporate standard phone is shared across franchises so a 1:1
        phone→store mapping is unsafe. record_fingerprints must skip it
        entirely, even when surrounding signals are present. See
        refactor/phone-as-retailer-signal.
        """
        signals = {"phone": "0149970970", "retailer": "MONOPRIX", "postal_code": "92400"}
        record_fingerprints(db, store.id, signals)
        db.flush()
        row = db.execute(
            text("SELECT store_id FROM store_fingerprints WHERE signal_type='phone' AND signal_value='0149970970'"),
        ).first()
        assert row is None
        # The retailer_postal fingerprint is still recorded — sanity check.
        rp = db.execute(
            text("SELECT signal_value FROM store_fingerprints WHERE signal_type='retailer_postal'"),
        ).first()
        assert rp is not None
        assert rp.signal_value == "MONOPRIX:92400"

    def test_inserts_brand_postal_fingerprint(self, db, store):
        signals = {"retailer": "LIDL PARIS 15", "postal_code": "75015"}
        record_fingerprints(db, store.id, signals)
        db.flush()
        row = db.execute(
            text("SELECT signal_value FROM store_fingerprints WHERE signal_type='retailer_postal'"),
        ).first()
        assert row is not None
        assert row.signal_value == "LIDL:75015"

    def test_increments_confirmed_count_on_conflict(self, db, store):
        # retailer_postal is now the smallest fingerprint we still record
        # (phone has been removed — see refactor/phone-as-retailer-signal).
        signals = {"retailer": "INCSTORE", "postal_code": "33000"}
        record_fingerprints(db, store.id, signals)
        db.flush()
        record_fingerprints(db, store.id, signals)
        db.flush()
        row = db.execute(
            text("SELECT confirmed_count FROM store_fingerprints WHERE signal_value='INCSTORE:33000'"),
        ).first()
        assert row.confirmed_count == 2


class TestRecordCandidate:
    def test_inserts_new_candidate(self, db):
        signals = {"retailer": "NEWSTORE", "postal_code": "33000", "phone": "0556000001"}
        header_text = "NEWSTORE\n3 RUE TEST\n33000 BORDEAUX\n0556000001"
        record_candidate(db, signals, header_text)
        db.flush()
        row = db.execute(
            text("SELECT retailer_guess, occurrence_count FROM store_candidates WHERE retailer_guess = 'NEWSTORE'"),
        ).first()
        assert row is not None
        assert row.occurrence_count == 1

    def test_increments_occurrence_on_second_call(self, db):
        signals = {"retailer": "DUPSTORE", "postal_code": "44000"}
        header_text = "DUPSTORE\n44000 NANTES"
        record_candidate(db, signals, header_text)
        db.flush()
        record_candidate(db, signals, header_text)
        db.flush()
        row = db.execute(
            text(
                "SELECT occurrence_count FROM store_candidates"
                " WHERE retailer_guess = 'DUPSTORE' AND postal_code = '44000'"
            ),
        ).first()
        assert row.occurrence_count == 2

    def _make_receipt(self, db) -> uuid.UUID:
        """Insert a minimal receipt row and return its id."""
        rid = uuid.uuid4()
        db.execute(
            text("INSERT INTO receipts (id, purchased_at, store_status) VALUES (:id, CURRENT_DATE, 'unknown')"),
            {"id": str(rid)},
        )
        return rid

    def test_inserts_with_receipt_id(self, db):
        """receipt_id is stored when provided on first insert."""
        receipt_id = self._make_receipt(db)
        signals = {"retailer": "RCVSTORE", "postal_code": "75001"}
        record_candidate(db, signals, "RCVSTORE 75001", receipt_id=receipt_id)
        db.flush()
        row = db.execute(
            text("SELECT receipt_id FROM store_candidates WHERE retailer_guess = 'RCVSTORE'"),
        ).first()
        assert row is not None
        assert str(row.receipt_id) == str(receipt_id)

    def test_increment_does_not_overwrite_receipt_id(self, db):
        """occurrence_count increment path leaves receipt_id unchanged."""
        receipt_id_first = self._make_receipt(db)
        receipt_id_second = self._make_receipt(db)
        signals = {"retailer": "INCSTORE", "postal_code": "13001"}
        record_candidate(db, signals, "INCSTORE", receipt_id=receipt_id_first)
        db.flush()
        record_candidate(db, signals, "INCSTORE", receipt_id=receipt_id_second)
        db.flush()
        row = db.execute(
            text("SELECT receipt_id, occurrence_count FROM store_candidates WHERE retailer_guess = 'INCSTORE'"),
        ).first()
        assert row.occurrence_count == 2
        assert str(row.receipt_id) == str(receipt_id_first)  # unchanged


# ── Changement A : extraction retailer-aware (deux passes, sans fallback) ─────────

_MONOPRIX_FORMATS = {
    "monoprix": {
        "length": 24,
        "fields": [
            {"name": "store_code", "start": 0, "end": 4},
            {"name": "caisse", "start": 4, "end": 7},
        ],
    }
}

_INTERMARCHE_FORMATS = {
    "intermarche": {
        "length": 24,
        "fields": [
            {"name": "date", "start": 0, "end": 8},
            {"name": "caisse", "start": 8, "end": 12},
            {"name": "transaction", "start": 12, "end": 19},
            {"name": "store_code", "start": 19, "end": 24},
        ],
    }
}

_BOTH_FORMATS = {**_MONOPRIX_FORMATS, **_INTERMARCHE_FORMATS}


class TestExtractStoreSignalsBrandAware:
    """Changement A — two-pass retailer-aware barcode extraction."""

    def test_monoprix_store_code_extracted_from_start(self):
        """Monoprix barcode: store_code is first 4 chars."""
        barcode = "2341" + "031" + "0" * 17
        lines = ["MONOPRIX COURBEVOIE", barcode]
        signals = extract_store_signals(lines, barcode_formats=_MONOPRIX_FORMATS)
        assert signals.get("store_code") == "2341"

    def test_intermarche_store_code_extracted_from_end(self):
        """Intermarche barcode: store_code is chars [19:24]."""
        barcode = "20260418" + "0001" + "0000001" + "12345"
        lines = ["INTERMARCHE", barcode]
        signals = extract_store_signals(lines, barcode_formats=_INTERMARCHE_FORMATS)
        assert signals.get("store_code") == "12345"

    def test_no_store_code_when_no_formats_provided(self):
        """Without formats, barcode present but store_code must NOT be extracted."""
        barcode = "234103109975250624084122"
        lines = ["MONOPRIX COURBEVOIE", barcode]
        signals = extract_store_signals(lines)
        assert "store_code" not in signals

    def test_no_store_code_when_brand_unknown_in_formats(self):
        """Brand not present in formats → no store_code extracted."""
        barcode = "234103109975250624084122"
        lines = ["LECLERC BORDEAUX", barcode]
        signals = extract_store_signals(lines, barcode_formats=_MONOPRIX_FORMATS)
        assert "store_code" not in signals

    def test_no_store_code_when_no_barcode_line(self):
        """No barcode line → no store_code even with formats."""
        lines = ["MONOPRIX COURBEVOIE", "12 RUE DE L'ABREUVOIR", "92400 COURBEVOIE"]
        signals = extract_store_signals(lines, barcode_formats=_MONOPRIX_FORMATS)
        assert "store_code" not in signals

    def test_no_store_code_when_barcode_too_short_for_field(self):
        """Barcode shorter than field end → no store_code extracted."""
        barcode = "20260418" + "0001" + "0000001" + "12"  # only 22 chars, end=24 requires 24
        lines = ["INTERMARCHE", barcode]
        signals = extract_store_signals(lines, barcode_formats=_INTERMARCHE_FORMATS)
        assert "store_code" not in signals

    def test_detect_store_loads_formats_from_db(self, db, store, monkeypatch):
        """detect_store should call _load_barcode_formats and pass result to extract_store_signals."""
        captured_formats = {}

        def mock_extract(lines, country_code="FR", barcode_formats=None, **kwargs):
            captured_formats["value"] = barcode_formats
            return {}

        monkeypatch.setattr("worker.ocr.store_detector.extract_store_signals", mock_extract)
        monkeypatch.setattr(
            "worker.ocr.store_detector._load_barcode_formats",
            lambda db: _BOTH_FORMATS,
        )
        detect_store(db, ["MONOPRIX COURBEVOIE"], country_code="FR")
        assert captured_formats.get("value") == _BOTH_FORMATS


# ── Changement B : backfill stores.store_code + slow path lookup (DP-05) ───────


class TestRecordFingerprintsBackfill:
    """Changement B — record_fingerprints must UPDATE stores.store_code when NULL."""

    def test_backfills_store_code_when_null(self, db, store):
        """record_fingerprints with store_code in signals → UPDATE stores SET store_code."""
        db.execute(
            text("UPDATE stores SET store_code = NULL WHERE id = :id"),
            {"id": str(store.id)},
        )
        db.flush()

        signals = {"store_code": "2341", "retailer": "MONOPRIX"}
        record_fingerprints(db, store.id, signals)
        db.flush()

        row = db.execute(
            text("SELECT store_code FROM stores WHERE id = :id"),
            {"id": str(store.id)},
        ).first()
        assert row.store_code == "2341"

    def test_does_not_overwrite_existing_store_code(self, db, store):
        """record_fingerprints must not overwrite an already-set store_code."""
        db.execute(
            text("UPDATE stores SET store_code = 'ORIG' WHERE id = :id"),
            {"id": str(store.id)},
        )
        db.flush()

        signals = {"store_code": "NEW1", "retailer": "MONOPRIX"}
        record_fingerprints(db, store.id, signals)
        db.flush()

        row = db.execute(
            text("SELECT store_code FROM stores WHERE id = :id"),
            {"id": str(store.id)},
        ).first()
        assert row.store_code == "ORIG"

    def test_no_backfill_when_no_store_code_signal(self, db, store):
        """record_fingerprints without store_code must not touch stores.store_code."""
        db.execute(
            text("UPDATE stores SET store_code = NULL WHERE id = :id"),
            {"id": str(store.id)},
        )
        db.flush()

        signals = {"phone": "0149970970"}
        record_fingerprints(db, store.id, signals)
        db.flush()

        row = db.execute(
            text("SELECT store_code FROM stores WHERE id = :id"),
            {"id": str(store.id)},
        ).first()
        assert row.store_code is None


class TestCandidateIntersectionStoreCode:
    """Changement B — _candidate_intersection must look up stores by store_code."""

    def test_store_code_lookup_adds_score(self, db, store):
        """store_code present in signals → store with matching store_code gets score."""
        db.execute(
            text("UPDATE stores SET store_code = 'SC42' WHERE id = :id"),
            {"id": str(store.id)},
        )
        db.flush()

        signals = {"store_code": "SC42"}
        candidates = _candidate_intersection(db, signals)
        matching = [c for c in candidates if c["store_id"] == store.id]
        assert matching, "Expected at least one candidate matching by store_code"
        assert matching[0]["score"] >= 70  # _SIGNAL_SCORES["store_code"]

    def test_unknown_store_code_returns_no_candidate(self, db):
        """store_code not present in any store → no candidates."""
        signals = {"store_code": "XXXX"}
        candidates = _candidate_intersection(db, signals)
        assert candidates == []


# ── Changement C : address keywords multi-pays (DP-06) ─────────────────────────


class TestAddressKeywordsMultiCountry:
    """Changement C — extract_store_signals must use country-specific address keywords."""

    def test_fr_rue_detected(self):
        lines = ["MONOPRIX", "5 RUE VICTOR HUGO", "75001 PARIS"]
        signals = extract_store_signals(lines, country_code="FR")
        assert "address" in signals

    def test_be_straat_detected(self):
        lines = ["DELHAIZE", "5 STRAAT LEUVEN", "3000 LEUVEN"]
        signals = extract_store_signals(lines, country_code="BE")
        assert "address" in signals

    def test_be_laan_detected(self):
        lines = ["CARREFOUR BELGIQUE", "12 LAAN VAN AALST", "9300 AALST"]
        signals = extract_store_signals(lines, country_code="BE")
        assert "address" in signals

    def test_ch_strasse_detected(self):
        lines = ["MIGROS", "8 STRASSE BERN", "3000 BERN"]
        signals = extract_store_signals(lines, country_code="CH")
        assert "address" in signals

    def test_ch_gasse_detected(self):
        lines = ["COOP SUISSE", "3 GASSE ZURICH", "8001 ZURICH"]
        signals = extract_store_signals(lines, country_code="CH")
        assert "address" in signals

    def test_unknown_country_falls_back_to_fr(self):
        """Unknown country_code → falls back to FR keywords."""
        lines = ["STORE", "5 RUE DU TEST", "75001 PARIS"]
        signals = extract_store_signals(lines, country_code="JP")
        assert "address" in signals

    def test_fr_keyword_not_matched_for_ch_only(self):
        """A CH-only keyword (GASSE) must not match when country_code='FR'."""
        lines = ["STORE", "3 GASSE ZURICH", "8001 ZURICH"]
        signals = extract_store_signals(lines, country_code="FR")
        assert "address" not in signals


# ── retailer DB cross-reference ──────────────────────────────────────────────────


class TestExtractStoreSignalsKnownBrands:
    """known_retailers validates retailer before falling back to the heuristic."""

    def test_known_brand_matched_exactly(self):
        """Line matching a known retailer → used as retailer signal."""
        lines = ["LIDL", "3 RUE DU COMMERCE", "75015 PARIS"]
        signals = extract_store_signals(lines, known_retailers=frozenset(["LIDL"]))
        assert signals["retailer"] == "LIDL"

    def test_known_brand_case_insensitive(self):
        """OCR may return mixed case — matching is accent+case normalised."""
        lines = ["Lidl", "3 RUE DU COMMERCE", "75015 PARIS"]
        signals = extract_store_signals(lines, known_retailers=frozenset(["lidl"]))
        assert signals.get("retailer", "").upper() in ("LIDL",)

    def test_known_brand_accent_normalised(self):
        """'INTERMARCHÉ' from OCR matches 'intermarche' stored in DB."""
        lines = ["INTERMARCHÉ", "5 AV DU PORT", "29200 BREST"]
        signals = extract_store_signals(lines, known_retailers=frozenset(["intermarche"]))
        # retailer is found; normalized key resolves to "intermarche"
        assert signals.get("retailer") is not None
        assert "INTERMARCH" in signals.get("retailer", "").upper()

    def test_unknown_brand_falls_back_to_heuristic(self):
        """No known retailer match → falls back to uppercase heuristic."""
        lines = ["NOUVELLE ENSEIGNE", "5 RUE DU TEST", "75001 PARIS"]
        signals = extract_store_signals(lines, known_retailers=frozenset(["lidl", "monoprix"]))
        assert signals.get("retailer") == "NOUVELLE ENSEIGNE"

    def test_empty_known_retailers_uses_heuristic(self):
        """Empty frozenset → heuristic still applies."""
        lines = ["LIDL", "3 RUE DU COMMERCE", "75015 PARIS"]
        signals = extract_store_signals(lines, known_retailers=frozenset())
        assert signals.get("retailer") == "LIDL"

    def test_none_known_retailers_uses_heuristic(self):
        """None (default) → heuristic applies as before."""
        lines = ["MONOPRIX", "5 RUE VICTOR HUGO", "75001 PARIS"]
        signals = extract_store_signals(lines)
        assert signals.get("retailer") == "MONOPRIX"

    def test_known_brand_prioritised_over_earlier_noise_line(self):
        """If a noise uppercase line appears before the real retailer, known_retailers picks the right one."""
        # "EN CAISSE" would be picked by heuristic (first uppercase ≥ 4 chars).
        # With known_retailers, "LIDL" should win.
        lines = ["EN CAISSE", "LIDL", "3 RUE DU COMMERCE"]
        signals = extract_store_signals(lines, known_retailers=frozenset(["lidl"]))
        assert signals.get("retailer", "").upper() in ("LIDL",)
