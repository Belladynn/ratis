"""Unit tests for ``services.product_attributes`` — pure helper, no DB IO.

Phase C-1 of the missions sprint introduces ``is_organic_product`` so the
PA worker can decorate ``trigger_action`` events with
``qualifier='attribute:organic'`` when a product is OFF-tagged organic.
The helper is intentionally side-effect-free : it inspects the
``products.labels_tags`` array and returns a bool. The actual emit logic
lives in ``services.reconciliation_service``.

Phase C-3 (2026-05-11) extends this module with
``derive_scan_distinct_qualifiers`` — builds the ``category:<slug>`` and
``store:<uuid>`` qualifier strings consumed by the 8 active
``scan_distinct`` missions in the V1 catalogue.

Phase C-2 (2026-05-11) adds ``is_french_product`` — mirror of
``is_organic_product`` that inspects ``products.origins_tags`` so the
PA worker can decorate ``trigger_action`` events with
``qualifier='attribute:french'`` once the prod backfill batch
populates the new column.
"""

from __future__ import annotations

import uuid

import pytest
from services.product_attributes import (
    derive_scan_distinct_qualifiers,
    is_french_product,
    is_organic_product,
)

# ── happy paths ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tags",
    [
        ["en:organic"],
        ["fr:bio"],
        ["en:eu-organic"],
        ["fr:agriculture-biologique"],
        # Real-world OFF tag list contains other labels too — the helper
        # must still detect the organic signal among them.
        ["en:fair-trade", "en:organic"],
        ["en:no-gluten", "fr:bio", "en:vegan"],
    ],
)
def test_returns_true_for_organic_signal(tags: list[str]) -> None:
    assert is_organic_product(tags) is True


def test_case_insensitive_match() -> None:
    """OFF normally lower-cases tags but defensive : any casing must match."""
    assert is_organic_product(["EN:Organic"]) is True
    assert is_organic_product(["FR:BIO"]) is True
    assert is_organic_product(["fr:Agriculture-Biologique"]) is True


# ── negative paths ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tags",
    [
        ["en:fair-trade"],
        ["en:vegan", "en:no-palm-oil"],
        ["fr:bio-suisse"],  # similar prefix but not a known signal
        ["en:organic-farming-something"],  # partial-string ≠ exact match
    ],
)
def test_returns_false_for_non_organic_tags(tags: list[str]) -> None:
    assert is_organic_product(tags) is False


def test_returns_false_for_empty_list() -> None:
    assert is_organic_product([]) is False


def test_returns_false_for_none() -> None:
    assert is_organic_product(None) is False


# ── derive_scan_distinct_qualifiers (Phase C-3) ────────────────────────


class TestDeriveScanDistinctQualifiers:
    """``derive_scan_distinct_qualifiers`` emits the qualifier strings
    consumed by the ``scan_distinct`` mission family.

    Contract :
      * ``category:<categories_tags[0]>`` — the FIRST entry of the OFF
        ``categories_tags`` array (most-specific tag by OFF convention).
        Skipped when ``categories_tags`` is None or empty.
      * ``store:<store_uuid>`` — present iff ``store_id is not None``.
      * Empty list when no signal available (e.g. unmatched scan with no
        product and no resolved store).

    The mission runtime (``missions_repository.apply_action_event_to_user_missions``
    branch B) splits on the first colon, so the qualifier value itself
    can carry colons (``category:en:dairies``) without ambiguity.
    """

    def test_both_signals_present(self) -> None:
        store_id = uuid.uuid4()
        qs = derive_scan_distinct_qualifiers(
            categories_tags=["en:dairies", "en:fresh-milks"],
            store_id=store_id,
        )
        assert sorted(qs) == sorted(
            [
                "category:en:dairies",
                f"store:{store_id}",
            ]
        )

    def test_category_only(self) -> None:
        qs = derive_scan_distinct_qualifiers(categories_tags=["en:dairies"], store_id=None)
        assert qs == ["category:en:dairies"]

    def test_store_only(self) -> None:
        store_id = uuid.uuid4()
        qs = derive_scan_distinct_qualifiers(categories_tags=None, store_id=store_id)
        assert qs == [f"store:{store_id}"]

    def test_empty_categories_treated_as_none(self) -> None:
        store_id = uuid.uuid4()
        qs = derive_scan_distinct_qualifiers(categories_tags=[], store_id=store_id)
        # No category qualifier — empty array carries no signal.
        assert qs == [f"store:{store_id}"]

    def test_neither_signal(self) -> None:
        qs = derive_scan_distinct_qualifiers(categories_tags=None, store_id=None)
        assert qs == []

    def test_preserves_full_off_tag_with_colon(self) -> None:
        """OFF tags carry a ``<lang>:`` prefix that must survive
        verbatim — the mission runtime splits on the FIRST colon only,
        so ``category:en:dairies`` resolves to type-tag ``category`` and
        tracked value ``en:dairies`` (the full OFF tag)."""
        qs = derive_scan_distinct_qualifiers(
            categories_tags=["en:plant-based-foods-and-beverages"],
            store_id=None,
        )
        assert qs == ["category:en:plant-based-foods-and-beverages"]

    def test_takes_first_categories_tag_only(self) -> None:
        """Multi-tag arrays : only the first (most-specific) tag is
        emitted. Future C-3.1 may evolve to also emit a broader-tag
        qualifier, but V1 keeps the rule simple."""
        qs = derive_scan_distinct_qualifiers(
            categories_tags=[
                "en:apples",  # most specific — picked
                "en:fruits",
                "en:plant-based-foods-and-beverages",
            ],
            store_id=None,
        )
        assert qs == ["category:en:apples"]


# ── Phase C-2 — is_french_product (origins_tags matcher) ───────────────


class TestIsFrenchProduct:
    """``is_french_product`` mirrors ``is_organic_product`` but inspects
    ``products.origins_tags`` instead of ``labels_tags``.

    Contract :
      * Match is exact (no prefix slicing) — ``en:france`` matches,
        ``en:france-metropolitaine`` does NOT.
      * Match is case-insensitive — defensive (OFF lowercases tags).
      * Recognised signals : ``en:france``, ``fr:france``,
        ``en:made-in-france``. Hierarchy parsing (``en:european-union``
        ascendant) is NOT enabled — the catalogue explicitly targets
        French-made / French-origin products, not "European-anything".

    Live OFF observation (2026-05-11) : dominant shape is the plain
    ``en:france`` literal, often co-existing with broader tags like
    ``en:european-union``. The ``in`` check on the full array suffices.
    """

    @pytest.mark.parametrize(
        "tags",
        [
            ["en:france"],
            ["fr:france"],
            ["en:made-in-france"],
            # Real-world OFF data : France co-exists with broader origins.
            ["en:france", "en:european-union"],
            ["en:france", "fr:saint-martin-de-gurson"],
            ["en:france", "en:non-european-union"],
            # Bilingual — rare but observed.
            ["en:france", "fr:france"],
        ],
    )
    def test_returns_true_for_french_signal(self, tags: list[str]) -> None:
        assert is_french_product(tags) is True

    def test_case_insensitive_match(self) -> None:
        """OFF normally lowercases tags but defensive : any casing matches."""
        assert is_french_product(["EN:France"]) is True
        assert is_french_product(["FR:FRANCE"]) is True
        assert is_french_product(["en:Made-In-France"]) is True

    @pytest.mark.parametrize(
        "tags",
        [
            ["en:germany"],
            ["en:european-union"],
            ["en:non-european-union", "en:unspecified"],
            ["en:france-metropolitaine"],  # partial-string ≠ exact match
            ["fr:france-d-outre-mer"],  # derived sub-tag, not a signal
            ["en:spain", "en:italy"],
        ],
    )
    def test_returns_false_for_non_french_tags(self, tags: list[str]) -> None:
        assert is_french_product(tags) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert is_french_product([]) is False

    def test_returns_false_for_none(self) -> None:
        """``origins_tags`` column is nullable — pre-backfill rows + OFF
        items without origin metadata yield None. Must return False
        without raising (defensive default — no false positive)."""
        assert is_french_product(None) is False
