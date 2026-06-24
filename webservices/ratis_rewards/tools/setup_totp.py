"""One-shot script — generate TOTP secret + provisioning URI for Google Authenticator.

Usage  : ``python -m tools.setup_totp``
Output : prints provisioning URI + base32 secret. Add the secret to ``.env``
         as ``ADMIN_TOTP_SECRET``. Then scan the URI (any QR generator)
         in Google Authenticator (or compatible app like Authy, 1Password).

Single-shot — re-running the script generates a new secret. Re-enrolment
is a manual operation (rotate ``ADMIN_TOTP_SECRET`` in env, re-add account
in Authenticator).
"""

from __future__ import annotations

import pyotp


def main() -> None:
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name="Ratis Admin", issuer_name="Ratis")
    print("=" * 60)
    print("ADMIN TOTP setup — one-shot")
    print("=" * 60)
    print()
    print("1. Add to .env :")
    print(f"   ADMIN_TOTP_SECRET={secret}")
    print()
    print("2. Open Google Authenticator → Add account → Scan QR")
    print("   or enter manually using :")
    print()
    print(f"   Otpauth URI : {uri}")
    print()
    print(f"   Manual entry secret (base32) : {secret}")
    print()
    print("3. Test current code :")
    print(f"   python -c \"import pyotp; print(pyotp.TOTP('{secret}').now())\"")
    print("=" * 60)


if __name__ == "__main__":
    main()
