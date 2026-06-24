# Security Policy

Thank you for taking the time to help keep Ratis secure. This document explains
how to report a vulnerability and what you can expect in return.

## Supported versions

Ratis is developed as a single active line of work. There is no long-term
support matrix: only the latest state of the default branch (`main`) is
supported and receives security fixes. Older commits and unmerged branches are
not maintained.

| Version            | Supported          |
| ------------------ | ------------------ |
| `main` (latest)    | :white_check_mark: |
| Any earlier commit | :x:                |

## Reporting a vulnerability

**Please do not open a public GitHub issue, pull request, or discussion for a
security vulnerability.** Public disclosure before a fix is available puts users
at risk.

Use one of the two private channels instead:

1. **GitHub private vulnerability reporting** (preferred) — open a report from
   the repository's **Security → Report a vulnerability** tab. This keeps the
   discussion private between you and the maintainer and is tracked as a GitHub
   Security Advisory. (Enable *Settings → Code security and analysis → Private
   vulnerability reporting* on the repository to expose the form.)
2. **Email** — send the details to **contact@ratis.app** with a subject line
   starting `[SECURITY]`.

Please include, where possible:

- a description of the issue and its potential impact;
- the affected component (service, endpoint, script, infra module);
- reproduction steps or a proof of concept;
- any suggested remediation.

### What to expect

This is a solo-maintained portfolio project, so timelines are best-effort
rather than contractual:

- **Acknowledgement** within a few business days.
- **Triage and an initial assessment** shortly after.
- **Coordinated disclosure** — a fix is prepared and released before any public
  write-up, and credit is given to the reporter unless anonymity is requested.

## Scope

In scope: the application code in this repository — the five FastAPI services
under `webservices/`, the shared `ratis_core` library, the Expo/React-Native
client under `ratis_client/`, the batch jobs under `batch/`, the agent tooling
under `tools/agent-mcp/`, the Terraform under `infra/`, and the CI/CD
definitions under `.github/`.

Out of scope: third-party providers integrated by the project (Google/Apple
OAuth, Stripe, Cloudflare R2, Expo, OpenStreetMap/OSRM, Anthropic, Runa) —
report those to their respective vendors. Findings that require physical or
privileged access to the maintainer's host machine, social-engineering, or
volumetric denial-of-service are also out of scope.

## Security posture

Security is treated as a first-class design constraint here, not an
afterthought. The controls below already exist in the repository and are
documented in the [Architecture Decision Log](docs/adr/README.md); they are
listed as evidence of intent rather than a guarantee of perfection.

- **Blast-radius isolation in authentication.** Authentication uses asymmetric
  **RS256 JWTs with a single issuer** (the auth service holds the private key)
  and many verifiers (the other services hold only the public key). A
  compromise of a downstream service cannot mint tokens. See
  [ADR-0001](docs/adr/0001-rs256-jwt-single-issuer.md).
- **The model never sees raw secrets.** Agent automation goes through
  `agent-mcp`, a **Keychain-backed control plane** that exposes typed tools to
  the LLM; provider tokens live in the macOS Keychain and are read at call time,
  so a credential string never enters the model's context. Two scoped caller
  tokens (`admin` / `ops`) enforce least privilege, rejecting out-of-scope calls
  before any provider traffic. See
  [ADR-0011](docs/adr/0011-agent-mcp-keychain-control-plane.md) and
  [`tools/agent-mcp/`](tools/agent-mcp/).
- **Just-in-time secrets vault.** Short-lived credentials are leased on demand
  and revoked automatically (`ratis-secret use ... --cmd`, the `secret_with`
  context manager), so secrets are not left lying in the environment.
- **HMAC-chained, append-only audit log.** Every privileged tool call and every
  secrets-vault operation is written to a tamper-evident, hash-chained JSONL
  audit trail for forensic traceability.
- **Confined agent-to-production-DB writes.** An agent can only *propose* a
  database write; a **7-layer confinement pipeline** (frozen stored procedures,
  human-curated manifests verified by the real Postgres parser, a typed human
  gate, a `REVOKE`-restricted role, and DB-floor caps) bounds the worst case
  structurally. See
  [ADR-0012](docs/adr/0012-db-write-pipeline-7-layer-confinement.md).
- **Secret-scanning CI/pre-commit gates.** `gitleaks` and `detect-secrets` run
  both as pre-commit hooks and as required CI jobs (mirrored so `--no-verify`
  cannot bypass them), blocking credentials at the door.
- **No plaintext secrets in infrastructure.** The Terraform task definitions
  inject secrets via AWS Secrets Manager `valueFrom` references rather than
  plaintext environment variables, and secret-bearing values are marked
  `sensitive`.
- **GDPR-grade data handling.** Account deletion is in-place anonymization,
  receipt images are purged on a short TTL, and location data is treated as PII
  and never logged. See
  [ADR-0008](docs/adr/0008-gdpr-in-place-anonymization.md).

For the deeper rationale behind these controls, see the
[Architecture Decision Log](docs/adr/README.md) and
[`docs/agents/README.md`](docs/agents/README.md).
