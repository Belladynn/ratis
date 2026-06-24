"""Admin-only service helpers for ratis_rewards.

This sub-package isolates code paths gated behind ``ADMIN_API_KEY`` (and,
for financial-sensitive endpoints, ``X-Admin-TOTP``). Keeping admin services
in their own namespace makes the segregation explicit when reading the
service tree and avoids polluting user-facing services.
"""
