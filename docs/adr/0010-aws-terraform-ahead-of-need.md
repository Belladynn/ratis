# ADR-0010: AWS target as Terraform IaC ahead of need

**Status:** Accepted (POC) — committed; applied ephemerally to validate the topology, then torn down; deliberately not kept running in prod

## Context and Problem Statement

The migration path (ADR-0009) requires AWS to be "ready before saturation" so the cutover can happen in under a week. The team also wanted to learn AWS/Terraform (skills/CV goal) and prove the topology without incurring real production cost. How do we make the AWS cutover mechanical and de-risked without paying for AWS before it is needed?

## Decision Drivers

- The cutover must be mechanical and achievable in under a week.
- Defer real AWS spend until ~500+ active users justify it.
- Concrete AWS/Terraform learning value.
- Mirror the docker-compose topology one-to-one so nothing surprising is introduced at cutover.

## Considered Options

- **Terraform stack under `infra/aws`, applied ephemerally to validate then torn down (not kept in prod)**, mirroring the compose topology on managed primitives.
- **AWS CDK** instead of Terraform.
- **Lock-in managed services (DynamoDB/SQS/Lambda).**
- **Defer all AWS work until saturation actually hits.**

## Decision Outcome

Chosen: author a Terraform stack under `infra/aws` (region `eu-west-3`, profile `claude-agent`) provisioning: an ECS cluster with Service Connect (Cloud Map private namespace `ratis.local`); the 5 services as Fargate tasks (auth/product/list/rewards public behind an ALB, notifier internal); a single RDS Postgres 16 (`db.t3.micro`); an ElastiCache Redis 7.1 (`cache.t3.micro`); and Secrets Manager entries for `INTERNAL_API_KEY` / `DATABASE_URL` / `REDIS_URL`. A reusable Terraform module (`modules/service`) parameterizes each service. The stack was applied ephemerally to validate the topology (the full thing stood up), then torn down to €0; it is kept out of continuous prod — with "not applied in prod" comments — until 500+ active users justify the spend.

**Rejected:** AWS CDK (the listed alternative, not chosen); lock-in managed services (DynamoDB/SQS/Lambda explicitly excluded); deferring all AWS work (would make the cutover slow and risky). S3 vs keeping Cloudflare R2 left open ("S3 or keep R2").

**Quality-attribute trade-off:** we bought **time-to-cutover and reproducibility** (a credible <1-week mechanical migration, IaC, learning value) at the cost of **production-readiness** — this is deliberately a representative POC, not hardened: default `nginx:alpine` task images, plaintext secrets in task env, single-AZ `db.t3.micro` with `skip_final_snapshot=true` on the default VPC.

### Consequences

- **Good:** a credible <1-week cutover path; IaC reproducibility; concrete learning value.
- **Bad:** explicitly a POC, not hardened — tasks use a default `nginx:alpine` image (real Ratis images are a later phase); secrets are injected in plaintext into task env (proper ECS `valueFrom` + IAM is a TODO); RDS is single-AZ `db.t3.micro` with `skip_final_snapshot=true` and reuses the default VPC, so it would need sizing / HA / hardening before real traffic.

**Source.** `infra/aws/{main,cluster,network,secrets,data,services}.tf`; `PROD_CHECKLIST.md`. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
