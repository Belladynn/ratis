"""Tests for :mod:`ratis_core.identifiers`.

Properties asserted :
- Format strictly matches ``^RTS-[A-HJ-NP-Z2-9]{6}$``.
- Alphabet contains exactly 32 distinct characters and excludes the four
  look-alikes ``I O 0 1``.
- Random distribution over the alphabet is roughly uniform on a 1000-call
  sample (defensive sanity check against a regression on the alphabet).
"""

from __future__ import annotations

import re
from collections import Counter

from ratis_core.identifiers import (
    SUPPORT_ID_ALPHABET,
    SUPPORT_ID_PREFIX,
    SUPPORT_ID_SUFFIX_LENGTH,
    generate_support_id,
)

_FORMAT_RE = re.compile(r"^RTS-[A-HJ-NP-Z2-9]{6}$")


def test_alphabet_size_is_32() -> None:
    assert len(SUPPORT_ID_ALPHABET) == 32


def test_alphabet_has_no_duplicates() -> None:
    assert len(set(SUPPORT_ID_ALPHABET)) == 32


def test_alphabet_excludes_lookalikes() -> None:
    """I, O, 0, 1 are explicitly excluded — they look identical at low res."""
    for ch in "IO01":
        assert ch not in SUPPORT_ID_ALPHABET, f"{ch!r} must not be in the alphabet"


def test_generate_support_id_format() -> None:
    sid = generate_support_id()
    assert _FORMAT_RE.match(sid), f"unexpected shape : {sid!r}"


def test_generate_support_id_length() -> None:
    sid = generate_support_id()
    # ``RTS-`` (4) + 6 random chars = 10 total.
    assert len(sid) == 4 + SUPPORT_ID_SUFFIX_LENGTH
    assert sid.startswith(SUPPORT_ID_PREFIX)


def test_generate_support_id_alphabet_only() -> None:
    """Every char of the random suffix must come from the alphabet."""
    sid = generate_support_id()
    suffix = sid[len(SUPPORT_ID_PREFIX) :]
    for ch in suffix:
        assert ch in SUPPORT_ID_ALPHABET, f"{ch!r} not in alphabet"


def test_generate_support_id_distribution_roughly_uniform() -> None:
    """Sanity check : 1000 calls ⇒ every alphabet char should appear at
    least once across the produced suffixes (6000 draws uniform over 32
    options — the chance of any single char NOT appearing is vanishingly
    small ~ (31/32)^6000 < 1e-80).
    """
    counter: Counter[str] = Counter()
    for _ in range(1000):
        sid = generate_support_id()
        counter.update(sid[len(SUPPORT_ID_PREFIX) :])
    # Every alphabet char must be observed at least once.
    for ch in SUPPORT_ID_ALPHABET:
        assert counter[ch] > 0, f"alphabet char {ch!r} never appeared in 1000 draws"
    # And no char outside the alphabet must ever appear.
    for ch in counter:
        assert ch in SUPPORT_ID_ALPHABET, f"unexpected char {ch!r} produced"


def test_generate_support_id_is_random() -> None:
    """Two consecutive calls must (with overwhelming probability) differ.

    Probability of collision per pair = 1/32^6 ≈ 1e-9. We assert across
    100 calls that we get at least 95 distinct values — a huge safety
    margin that catches a regression like ``random.seed(0)`` somewhere.
    """
    seen = {generate_support_id() for _ in range(100)}
    assert len(seen) >= 95
