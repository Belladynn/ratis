# Scaling & Hosting Strategy

How Ratis hosting evolves from a near-zero-cost bootstrap to a horizontally-scaled
AWS topology — and the concrete signals that trigger each move. The guiding principle
is **stay cheap and portable until growth forces the next stage, but have the next
stage fully prepared so the switch takes less than a week, not a panic weekend.**

The whole stack is deliberately built on portable primitives — Postgres + Redis +
FastAPI behind Docker, multi-arch images, S3-compatible object storage — so no stage
is locked to a single provider.

---

## Staged hosting timeline

### Stage 1 — Bootstrap VPS (V0 alpha)

A single Hetzner Cloud VPS running the full `docker-compose.prod.yml` stack behind
Caddy (automatic HTTPS). One box, all five services, Postgres and Redis as containers.

- **Why:** cheapest credible way to ship an alpha. A few euros a month, one SSH target,
  identical to the local dev stack.
- **Constraint discovered early:** PaddleOCR (`paddlepaddle`) ships no `linux_aarch64`
  wheel, so the OCR path forces an **x86_64** VPS (Hetzner `CX*`), not the cheaper ARM
  `CAX*` line. This same ARM/OCR limitation follows the project to every later stage.
- OSRM routing needs ~3–5 GB RAM for the France graph; on a small VPS it points at the
  public OSRM endpoint instead of running locally until the box is ≥16 GB.

### Stage 2 — Mac mini self-hosted

Migrate the *same* docker-compose stack to a Mac mini (Apple Silicon, 48 GB RAM) running
at home, always on.

- **Why:** **0 €/month** of infra cost, full control, and hands-on ops learning. Because
  the images are multi-arch the move is config-free — same compose file, same containers.
- **Same ARM caveat:** the Mac mini is ARM64, so the PaddleOCR limitation reappears — OCR
  either runs on a dedicated x86 helper or is externalised.
- This stage also hosts the supporting tooling (CI runners, automation) on the same
  always-on machine.

### Stage 3 — AWS (managed, horizontally scalable)

When the Mac mini saturates, move to AWS. **Everything for this stage is prepared in
advance** (IaC, load-test baselines, DB migration plan) so the cutover is fast.

- **Why:** managed durability (Multi-AZ Postgres), horizontal scaling, and the
  operational guarantees a self-hosted box can't give — geo-replication, customer SLAs,
  absorbing unpredictable load spikes.
- **Cost trade-off is explicit:** AWS is *more* expensive than continuing to scale a VPS
  vertically. It is only justified by a real driver (see "When AWS is actually
  justified" below), not by raw load alone — moderate growth can still be met by a bigger
  single box far more cheaply.

---

## Saturation signals — when to leave the Mac mini

Alerting for these is wired from V0 onward, so saturation is *seen coming* rather than
discovered in production. Monitored continuously (Sentry / Netdata / Prometheus). Any one
sustained signal is a trigger to start the (already-prepared) AWS cutover:

| Signal | Threshold | What it means |
|---|---|---|
| **PaddleOCR queue depth** | > 10 jobs backlogged for > 5 min straight | OCR worker can't keep up |
| **Postgres CPU** | > 70 % sustained over 1h | DB is the bottleneck → needs read replicas |
| **Host CPU (avg)** | > 60 % over a rolling 24h | no headroom left to absorb peaks |
| **API latency p99** | > 800 ms on non-OCR routes | generalist bottleneck, not just OCR |
| **RAM used** | > 80 % sustained | swap risk → systemic slowdown |
| **5xx error rate** | > 0.5 % of requests over 1h | the box is under stress |

The thresholds are intentionally conservative (e.g. 60 % CPU, not 90 %) because the goal
is to migrate *with* headroom, completing the switch in under a week — not to ride the
machine until it falls over.

---

## What must be ready before the AWS switch

Prepared progressively after the alpha, so the cutover is mechanical:

- **AWS-ready images** — already multi-arch ARM64/amd64, so they run on Fargate (ARM
  Graviton or x86) unchanged.
- **Load-test baselines** — `k6`/`locust` scenarios (receipt scan, label scan, list CRUD,
  login) run against each stage so capacity is a measured number, not a guess.
- **AWS CI/CD pipeline** — GitHub Actions that push images to ECR and update ECS task
  definitions, added once the self-hosted stage is stable and triggerable on demand.
- **Infrastructure as code** — Terraform for the full target topology, initiated in
  `infra/aws/` (marked "not applied in prod") once there are 500+ active users.
- **DB migration plan** — `pg_dump` export → RDS import, tested end-to-end against a
  staging RDS *before* saturation. Estimated downtime ~15–30 min for a DB under ~50 GB.
- **OCR worker cluster plan** — keep the `product_analyser` API thin, push jobs to Redis,
  and let dedicated (spot) Fargate workers consume them, so OCR scales horizontally and
  independently of the API.

---

## Target AWS topology

Minimal target footprint, kept deliberately to portable building blocks:

- **ECS Fargate** for the five services — horizontal scaling per service (the OCR worker
  is its own task family on spot capacity, decoupled from the API via the Redis queue).
- **RDS Postgres (Multi-AZ)** — managed durability and failover; read replicas added when
  DB CPU is the bottleneck.
- **ElastiCache (Redis)** — managed cache + Celery broker.
- **ALB + Route 53 + ACM** — load balancing, DNS, TLS.
- **Object storage** — keep Cloudflare **R2** (S3-compatible API, free egress — a real
  cost advantage over S3); migrate to S3 + CloudFront only if R2 becomes limiting.

### Cost & lock-in posture

- **Indicative cost** at ~10k DAU: RDS `db.t4g.medium` (~80 €/mo) + 5 Fargate tasks
  (~150 €/mo) + ALB (~20 €/mo) + data transfer (~30 €/mo) ≈ **~280 €/mo**. Vertically
  scaling a single VPS at the same load is ~30–50 €/mo — so AWS costs roughly 5–10×.
- **When AWS is actually justified:** geo-replication, contractual customer SLAs,
  unpredictable load spikes, or a fundraise — *not* incremental growth, which a bigger
  box handles more cheaply.
- **No managed lock-in:** avoid AWS-proprietary managed services (DynamoDB, SQS, Lambda)
  unless scaling on AWS is an explicit, committed decision. Stay on
  **Postgres + Redis + FastAPI** as long as possible so any stage remains portable.

---

> Source of record: the "Stratégie hébergement" section of `docs/ops/PROD_CHECKLIST.md`,
> which tracks the live checklist items behind each preparation step.
