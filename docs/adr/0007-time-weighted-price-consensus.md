# ADR-0007: Multi-user price consensus with time-weighted trust score

**Status:** Accepted

## Context and Problem Statement

A given `(store_id, product_ean)` price must be derived from many user scans of varying recency and reliability. It must react to genuine price changes, but must not be thrashed by OCR outliers or a burst of identical scans. How is a single trustworthy price computed from noisy, bursty, time-distributed crowd input?

## Decision Drivers

- Recent scans should dominate so genuine price changes surface.
- A single OCR outlier must only dent confidence, not flip the price.
- A burst of identical scans must not cause write amplification or thrash.
- Thresholds must be tunable without a redeploy.
- The mechanism must be fail-safe: a batch outage may degrade freshness but must not lose data.

## Considered Options

- **Time-weighted trust score over a rolling window**, with a short freeze after corroboration and daily decay.
- **Simple latest-price or naive average.**
- **Hardcoded thresholds** baked into code.
- **Permanent freeze** once a price is corroborated.

## Decision Outcome

Chosen: `price_consensus` is `UNIQUE(store_id, product_ean)` with a `trust_score` (0–100) = time-weighted ratio of concordant scans over a 20-scan window, recomputed on each new scan and decayed daily by `ratis_batch_consensus`. **3 concordant scans within one day freeze** the consensus (`frozen_until = now() + 24h`) — during freeze new scans are recorded in `price_consensus_scans` but `trust_score` is not recomputed; the nightly batch clears the freeze. After `decay_grace_days` with no scan, `trust_score` decays `decay_rate_pct`/day down to `decay_floor`. Every price change writes `price_consensus_history`. All thresholds live in `ratis_settings.json`.

**Rejected:** simple latest-price or naive average (no outlier resistance, no change detection); hardcoded thresholds (per the no-hardcode rule — settings / `app_settings`); permanent freeze (in favor of a nightly-reset 24h window).

**Quality-attribute trade-off:** we bought **robustness and accuracy** (OCR-noise resistance, reactivity to real changes, auditable history, tunability) at the cost of **freshness/timeliness** under low traffic or batch outage — a 20-scan window means sparse store/product pairs stabilize slowly, and a multi-day batch outage degrades freshness via decay.

### Consequences

- **Good:** robust to OCR noise and bursty duplicates; reactive to real price changes (a real change drops the score to ~24%; a single outlier only to ~79%); auditable history; tunable; fail-safe under batch outage (a crashed freeze-clearing batch loses at most a day of recompute, not data).
- **Bad:** correctness depends on the nightly batch running (a multi-day outage degrades freshness via decay); a 20-scan window means low-traffic store/product pairs stabilize slowly; tuning the weights/thresholds is an ongoing calibration task.

**Source.** `webservices/ratis_product_analyser/ARCH_consensus.md`; `CLAUDE.md` tables (`price_consensus`). Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
