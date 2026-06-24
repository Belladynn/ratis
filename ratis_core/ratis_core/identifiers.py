"""Public, non-PII user identifiers.

This module exposes ``generate_support_id`` which produces a compact,
preview-friendly identifier of the shape ``RTS-XXXXXX`` for a user.

Why we need a third identifier
==============================
Users today are identified by :

- ``users.id`` — UUID v4 (36 chars). Stable but ugly to dictate over the
  phone or a Twitter DM.
- ``users.email`` — PII. Operators can ask for it but the user cannot
  share it publicly to claim or vouch for an account.

``support_id`` plugs that gap : the user can read it on their profile
screen, paste it on Twitter ("@RatisSupport coucou — RTS-A3K7XP") and an
operator can look the account up in seconds without ever touching PII.

Properties
----------
- **Public, non-PII** : random — never derived from email/phone/UUID.
- **Compact** : 10 chars total (``RTS-`` prefix + 6 chars).
- **Unambiguous** : the alphabet excludes the look-alikes ``I O 0 1`` so
  someone reading the ID over voice or copy-paste from a low-res screen
  cannot confuse them.
- **Cryptographically random** : built on ``secrets.choice`` so collisions
  are bounded by birthday math on 32^6 ≈ 1.07B values. For V1 (<<1M users)
  the practical collision probability per insertion is < 1e-3 — and we
  retry on UNIQUE violation in the repo, see
  ``webservices/ratis_auth/repositories/user_repository.create_user``.

The alphabet is a closed set of 32 characters : 24 uppercase letters
(A-Z minus I and O) plus 8 digits (2-9).
"""

from __future__ import annotations

import secrets

# 32-char alphabet : A-Z minus {I, O}, 2-9 (no 0, no 1).
# Sorted for readability — order does not matter for security.
SUPPORT_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Length of the random suffix (the ``RTS-`` prefix is constant).
SUPPORT_ID_SUFFIX_LENGTH = 6

# Static prefix — keeps the brand name visible and lets operators tell at
# a glance "this is a Ratis support code, not a Slack ID or a coupon".
SUPPORT_ID_PREFIX = "RTS-"


def generate_support_id() -> str:
    """Return a fresh ``RTS-XXXXXX`` identifier.

    Idempotent (no side effects). Each call draws independent random
    characters from :data:`SUPPORT_ID_ALPHABET` via ``secrets.choice``.

    Format guarantee : the returned string matches the regex
    ``^RTS-[A-HJ-NP-Z2-9]{6}$``.
    """
    suffix = "".join(secrets.choice(SUPPORT_ID_ALPHABET) for _ in range(SUPPORT_ID_SUFFIX_LENGTH))
    return f"{SUPPORT_ID_PREFIX}{suffix}"
