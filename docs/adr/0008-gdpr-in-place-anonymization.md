# ADR-0008: GDPR deletion by in-place anonymization (4-tier tombstones)

**Status:** Accepted

## Context and Problem Statement

`DELETE /account` must remove personal data while legally preserving financial records (cashback transactions/withdrawals, subscriptions are never-purge for 5–10 years) and keeping useful per-user analytics correlatable but not re-identifiable. A naive row delete would break financial-audit FKs and lose aggregate insight. How can GDPR erasure of PII coexist with mandatory financial retention and de-identified analytics?

## Decision Drivers

- GDPR erasure of PII is mandatory and must be irreversible.
- Financial records (`cashback_transactions`, `cashback_withdrawals`, `subscriptions`) are never-purge by law (5–10 years) and their FKs must survive.
- Behavioral analytics should stay groupable per user but not re-identifiable.
- The whole operation must be a single transaction.

## Considered Options

- **In-place anonymization (soft-delete) with a 4-tier tombstone policy.**
- **Hard row deletion.**
- **Per-table random anonymization** that loses per-user grouping (for the analytics tier).
- **Sentinel emails** for uniqueness collisions.

## Decision Outcome

Chosen: anonymize **in place** (soft-delete `is_deleted=true`) rather than hard-delete, in a single transaction, with a tiered tombstone policy:

- **(Tier 1/2)** tombstone the `users` row — `email → deleted_{uuid}@deleted.invalid`, `display_name`/`avatar_url`/`provider_id → NULL`, account state → `deleted`; delete the `user_identities` rows so OAuth no longer resolves to the tombstone; revoke refresh tokens.
- **(Tier 3)** behavioral analytics tables get a per-user anonymous UUID = `sha256(real_id || RGPD_ANONYMIZE_SALT)` — deterministic per user, irreversible without the salt, with `users.id` FKs dropped.
- **(Tier 4)** never-purge financial tables (`cabecoin_transactions`, `cashback_transactions`, `cashback_withdrawals`, `gift_card_orders`) repoint `user_id` to a single static sentinel UUID (`…0001`), breaking correlation while keeping rows for accounting audit.

**Rejected:** hard row deletion (breaks financial-table FKs and legal retention); per-table random anonymization (loses per-user grouping — Tier 3 uses salted-deterministic UUIDs instead); sentinel emails for collisions (rejected elsewhere, ADR-0002 / DA-45, as a workaround).

**Quality-attribute trade-off:** we bought **regulatory compliance** (GDPR erasure + legal financial retention coexisting, analytics surviving de-identified) at the cost of **a hard secret-management dependency and ongoing maintenance** — irreversibility hinges on protecting `RGPD_ANONYMIZE_SALT`, and the multi-tier mapping must be extended for every new PII/financial table or coverage silently regresses.

### Consequences

- **Good:** GDPR-compliant erasure + legal financial retention coexist; analytics survive de-identified (still groupable per anonymized user); audit ledger intact and any residual cashback recoverable by Ratis.
- **Bad:** irreversibility hinges on protecting `RGPD_ANONYMIZE_SALT` (a missing/leaked salt is a real risk — see KP-79 boot guard); subscriptions remain Stripe-customer-coupled with an accepted residual tombstone correlation (out of scope F-AU-3); the multi-tier mapping must be maintained as new PII/financial tables are added.

**Source.** `docs/product/PRIVACY.md`; `webservices/ratis_auth/ARCH_AUTH.md` (DELETE /account lifecycle); `CLAUDE.md` RGPD section. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
