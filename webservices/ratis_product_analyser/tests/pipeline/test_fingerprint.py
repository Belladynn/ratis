"""Unit tests for the dual-fingerprint compute helpers (anti-fraud PR3).

Covers :

- 10-component canonical concat — deterministic, NULL → "", ordering fixed.
- :func:`compute_fp_user` includes ``user_id`` ; ``compute_fp_global``
  does not (so 2 users posting the same physical ticket collide on
  ``fp_global`` while their ``fp_user`` diverge).
- :func:`validate_mandatory_signals` — hard rule per ARCH étape 4 :
  ``iso_date`` mandatory ; one of ``brand_normalized`` /
  ``address_normalized`` mandatory.

No DB / no IO — pure-function tests, run in ms.
"""

from __future__ import annotations

import uuid

from worker.pipeline.fingerprint import (
    FingerprintComponents,
    canonical_string,
    compute_fp_global,
    compute_fp_user,
    validate_mandatory_signals,
)

# ── Builders ──────────────────────────────────────────────────────────────


def _full_components(**overrides) -> FingerprintComponents:
    """Return a complete (non-null) :class:`FingerprintComponents`."""
    defaults = {
        "store_id": "11111111-1111-1111-1111-111111111111",
        "address_normalized": "1 RUE DE PARIS 92400 COURBEVOIE",
        "brand_normalized": "INTERMARCHE",
        "iso_date": "2026-04-30",
        "iso_time": "14:30:00",
        "time_precision": "second",
        "total_ttc_cents": 1234,
        "item_count_declared": 5,
        "payment_method": "cb",
        "tva_total_cents": 210,
    }
    defaults.update(overrides)
    return FingerprintComponents(**defaults)


# ── canonical_string ──────────────────────────────────────────────────────


class TestCanonicalString:
    def test_full_components_concat_order(self):
        """Canonical concat keeps the documented field order."""
        c = _full_components()
        out = canonical_string(c)
        assert out == (
            "11111111-1111-1111-1111-111111111111|"
            "1 RUE DE PARIS 92400 COURBEVOIE|"
            "INTERMARCHE|"
            "2026-04-30|"
            "14:30:00|"
            "second|"
            "1234|"
            "5|"
            "cb|"
            "210"
        )

    def test_null_components_render_as_empty_string(self):
        """A NULL field becomes "" — separators stay so positions hold."""
        c = FingerprintComponents(
            store_id=None,
            address_normalized=None,
            brand_normalized=None,
            iso_date=None,
            iso_time=None,
            time_precision=None,
            total_ttc_cents=None,
            item_count_declared=None,
            payment_method=None,
            tva_total_cents=None,
        )
        out = canonical_string(c)
        assert out == "|||||||||"  # 9 separators, 10 empty fields

    def test_int_components_are_str_coerced(self):
        """``total_ttc_cents`` / ``item_count_declared`` / ``tva_total_cents``
        render as decimal strings (no float, no leading zeros).
        """
        c = _full_components(
            total_ttc_cents=0,
            item_count_declared=0,
            tva_total_cents=0,
        )
        out = canonical_string(c)
        # "0" is a valid value (vs None which would be "")
        parts = out.split("|")
        assert parts[6] == "0"  # total
        assert parts[7] == "0"  # item_count
        assert parts[9] == "0"  # tva_total

    def test_int_zero_distinct_from_null(self):
        """An explicit ``0`` total must not collide with a missing total."""
        c_zero = _full_components(total_ttc_cents=0)
        c_null = _full_components(total_ttc_cents=None)
        assert canonical_string(c_zero) != canonical_string(c_null)

    def test_deterministic_repeat_same_input(self):
        """Same inputs → same canonical string (idempotence)."""
        c = _full_components()
        assert canonical_string(c) == canonical_string(c)


# ── compute_fp_user / compute_fp_global ───────────────────────────────────


class TestComputeFingerprints:
    def test_fp_user_is_sha256_hex_64chars(self):
        c = _full_components()
        out = compute_fp_user(c, user_id="user-A")
        assert len(out) == 64
        # sha256 hex is [0-9a-f]
        int(out, 16)  # raises if not hex

    def test_fp_global_is_sha256_hex_64chars(self):
        c = _full_components()
        out = compute_fp_global(c)
        assert len(out) == 64
        int(out, 16)

    def test_fp_user_differs_when_user_id_differs(self):
        """Same components, two distinct users → distinct fp_user."""
        c = _full_components()
        fp_a = compute_fp_user(c, user_id="user-A")
        fp_b = compute_fp_user(c, user_id="user-B")
        assert fp_a != fp_b

    def test_fp_global_equal_across_users(self):
        """Cross-user fraud detection : same physical ticket → same fp_global."""
        c = _full_components()
        # fp_global takes no user_id parameter — function is pure on components.
        assert compute_fp_global(c) == compute_fp_global(c)

    def test_fp_user_differs_when_one_component_changes(self):
        """A 1-cent total bump must flip the fingerprint (no hidden rounding)."""
        c1 = _full_components(total_ttc_cents=1234)
        c2 = _full_components(total_ttc_cents=1235)
        assert compute_fp_user(c1, "u") != compute_fp_user(c2, "u")
        assert compute_fp_global(c1) != compute_fp_global(c2)

    def test_fp_user_includes_user_id_in_input(self):
        """Sanity check : fp_user(components, user) != fp_global(components)
        even when ``user_id=""``-ish would collide (empty user case)."""
        c = _full_components()
        # An empty user_id is degenerate but should still be distinguishable
        # from "no user_id at all" (i.e. fp_global). We chose "user|..." vs
        # "..." as the concat shape, so they MUST differ.
        assert compute_fp_user(c, user_id="") != compute_fp_global(c)

    def test_fp_user_real_uuid_user_id(self):
        """Production input is a UUID — round-trip a real one."""
        c = _full_components()
        uid = str(uuid.uuid4())
        fp = compute_fp_user(c, user_id=uid)
        assert len(fp) == 64

    def test_fp_global_independent_of_user_id(self):
        """Any user posting the same ticket → same fp_global (by construction)."""
        c = _full_components()
        # fp_global signature : no user_id arg, so identical by construction.
        # Adding this test to lock the contract in place for future maintainers.
        same1 = compute_fp_global(c)
        same2 = compute_fp_global(c)
        assert same1 == same2


# ── validate_mandatory_signals ────────────────────────────────────────────


class TestValidateMandatorySignals:
    def test_full_components_valid(self):
        c = _full_components()
        ok, reason = validate_mandatory_signals(c)
        assert ok is True
        assert reason is None

    def test_missing_date_invalid(self):
        c = _full_components(iso_date=None)
        ok, reason = validate_mandatory_signals(c)
        assert ok is False
        assert reason == "missing_date"

    def test_empty_string_date_invalid(self):
        """Empty string counts as missing — guards against OCR ""-fallback."""
        c = _full_components(iso_date="")
        ok, reason = validate_mandatory_signals(c)
        assert ok is False
        assert reason == "missing_date"

    def test_missing_brand_only_valid_when_address_present(self):
        """Hard rule : ``brand`` OR ``address`` is enough."""
        c = _full_components(brand_normalized=None)
        ok, reason = validate_mandatory_signals(c)
        assert ok is True
        assert reason is None

    def test_missing_address_only_valid_when_brand_present(self):
        c = _full_components(address_normalized=None)
        ok, reason = validate_mandatory_signals(c)
        assert ok is True
        assert reason is None

    def test_missing_both_brand_and_address_invalid(self):
        c = _full_components(brand_normalized=None, address_normalized=None)
        ok, reason = validate_mandatory_signals(c)
        assert ok is False
        assert reason == "missing_brand_and_address"

    def test_missing_date_takes_precedence_over_brand_address(self):
        """When BOTH constraints fail, the date reason is reported first
        — most authoritative signal, drives UX message specificity."""
        c = _full_components(
            iso_date=None,
            brand_normalized=None,
            address_normalized=None,
        )
        ok, reason = validate_mandatory_signals(c)
        assert ok is False
        assert reason == "missing_date"

    def test_other_components_null_do_not_block(self):
        """Only date + (brand|address) are mandatory ; the other 7 may be
        None and the function returns valid (we still emit a fingerprint,
        components-on-empty-string concat)."""
        c = FingerprintComponents(
            store_id=None,
            address_normalized=None,
            brand_normalized="CARREFOUR",
            iso_date="2026-05-01",
            iso_time=None,
            time_precision=None,
            total_ttc_cents=None,
            item_count_declared=None,
            payment_method=None,
            tva_total_cents=None,
        )
        ok, reason = validate_mandatory_signals(c)
        assert ok is True
        assert reason is None


# ── Cross-property: validation + compute composability ────────────────────


class TestComposeValidateAndCompute:
    def test_a_validated_components_yields_stable_fingerprint(self):
        """A valid (mandatory-OK) but otherwise partial component set still
        yields a stable, deterministic fingerprint — useful for "partial but
        accepted" receipts (e.g. small store with no postcode printed)."""
        c = FingerprintComponents(
            store_id=None,
            address_normalized=None,
            brand_normalized="CARREFOUR",
            iso_date="2026-05-01",
            iso_time=None,
            time_precision=None,
            total_ttc_cents=None,
            item_count_declared=None,
            payment_method=None,
            tva_total_cents=None,
        )
        ok, _ = validate_mandatory_signals(c)
        assert ok
        # Idempotence still holds with mostly-null components.
        assert compute_fp_user(c, "u1") == compute_fp_user(c, "u1")
        assert compute_fp_global(c) == compute_fp_global(c)
