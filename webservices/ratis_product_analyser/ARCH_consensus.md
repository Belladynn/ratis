---
type: sub-arch
service: ratis_product_analyser
parent: ARCH_PRODUCT_ANALYSER
related: [ARCH_BATCH_CONSENSUS]
status: production
tags: [consensus, price, trust-score, product-analyser]
updated: 2026-04-24
---

# ratis_product_analyser — Price Consensus

> Multi-user price consensus on `(store_id, product_ean)`: `UNIQUE` per pair, `trust_score`, `price_consensus_history` on every change, `frozen_until` when score ≥95%. Parameters in `ratis_settings.json`.
> @tags: consensus price trust-score product-analyser price_consensus price_consensus_history frozen-until snapshot V1
> @status: LIVRÉ V0
> @subs: auto

> Parent: [[ARCH_PRODUCT_ANALYSER]] · Relations: [[ARCH_BATCH_CONSENSUS]]

> Status: 📸 Snapshot V1 — to be refined with real data. Parameters in `ratis_core/config/ratis_settings.json`.
> Branch: `main`

---

## Implementation Checklist

**Base checklist — to keep in every ARCH:**
- [x] Alembic migration created and verified
- [x] SQLAlchemy models updated
- [x] Repository — CRUD functions
- [x] Service — business logic + edge cases
- [x] Route — endpoint + error codes
- [x] Tests written (TDD — before the code)
- [x] `conftest.py` updated if new `require_env()`
- [x] `ratis_settings.json` updated if new parameters
- [x] `pg_dump > db/schema.sql` after migration
- [x] `ruff check --fix` clean
- [x] CI pipeline green

**Custom checklist — CC plans its items before coding:**
- [x] Temporally weighted trust_score
- [x] Freeze (frozen_until) on 3 concordant scans
- [x] Daily decay (decay_grace_days)
- [x] Price switch via inverse ratio
- [ ] Refine window_size, decay_rate with real data

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Principle](#principle) [L.37 - L.42]
- [Consensus creation](#consensus-creation) [L.44 - L.49]
- [trust_score calculation](#trust_score-calculation) [L.51 - L.101]
- [Freeze (frozen_until)](#freeze-frozen_until) [L.103 - L.110]
- [Sliding cap (decay)](#sliding-cap-decay) [L.112 - L.127]
- [Price switch](#price-switch) [L.129 - L.138]
- [Price precision](#price-precision) [L.140 - L.146]
- [Automatic rejections](#automatic-rejections) [L.148 - L.153]
- [Trust tiers](#trust-tiers) [L.155 - L.165]
- [`price_consensus_scans` purge](#price_consensus_scans-purge) [L.167 - L.181]
- [`ratis_settings.json` — consensus parameters](#ratis_settingsjson--consensus-parameters) [L.183 - L.200]
- [To refine (real data)](#to-refine-real-data) [L.202 - L.207]

---

## Principle

A `price_consensus` represents the most reliable price observed for a `(store_id, product_ean)`. Its reliability is measured by a `trust_score` (0-100), recalculated on each new scan and degraded daily by `ratis_batch_consensus`.

---

## Consensus creation

**Minimum required:**
- 2 concordant scans (same price)
- 2 distinct users minimum (anti-manipulation)

---

## trust_score calculation

The trust_score is a **temporally weighted ratio** over the window of the last 20 scans.

**Weight of a scan based on its age:**
```
poids = max(0.30, 1.0 - (age_jours × 0.10))

Jour 0  → 1.00
Jour 1  → 0.90
Jour 3  → 0.70
Jour 7  → 0.30  ← plancher
Jour 15 → 0.30  ← plancher maintenu
```

**Formula:**
```python
score_concordants = SUM(poids(s) for s in fenetre if s.price == consensus_price)
score_total       = SUM(poids(s) for s in fenetre)
trust_score       = score_concordants / score_total * 100
```

**Examples:**

*Stable consensus — regular scans:*
```
Scan J-5  : poids 0.50 × 3.50€ ✅
Scan J-3  : poids 0.70 × 3.50€ ✅
Scan J-1  : poids 0.90 × 3.50€ ✅
Scan J-0  : poids 1.00 × 3.50€ ✅

trust_score = 3.10 / 3.10 = 100% ✅
```

*1 isolated OCR error:*
```
Scan J-5  : poids 0.50 × 3.50€ ✅
Scan J-3  : poids 0.70 × 3.50€ ✅
Scan J-2  : poids 0.80 × 3.60€ ❌  ← erreur OCR
Scan J-1  : poids 0.90 × 3.50€ ✅
Scan J-0  : poids 1.00 × 3.50€ ✅

score_concordants = 0.50+0.70+0.90+1.00 = 3.10
score_total       = 3.10 + 0.80 = 3.90
trust_score       = 3.10 / 3.90 = 79% — impacted but not catastrophic ✅
```

*Price that actually changes:*
```
Scan J-15 : poids 0.30 × 3.50€ ✅
Scan J-10 : poids 0.30 × 3.50€ ✅
Scan J-5  : poids 0.50 × 3.50€ ✅
Scan J-3  : poids 0.70 × 3.60€ ❌  ← nouveau prix
Scan J-2  : poids 0.80 × 3.60€ ❌
Scan J-1  : poids 0.90 × 3.60€ ❌
Scan J-0  : poids 1.00 × 3.60€ ❌

score_concordants = 0.30+0.30+0.50 = 1.10
score_total       = 1.10+0.70+0.80+0.90+1.00 = 4.50
trust_score       = 1.10 / 4.50 = 24% → strong change signal ✅
```

*Abandoned consensus — recent divergent scan:*
```
5 × Scan J-15 : poids 0.30 × 3.50€ ✅  (all at floor)
1 × Scan J-0  : poids 1.00 × 3.60€ ❌  ← nouveau scan divergent

score_concordants = 5 × 0.30 = 1.50
score_total       = 1.50 + 1.00 = 2.50
trust_score       = 1.50 / 2.50 = 60% — doubt is well captured ✅
```

---

## Freeze (frozen_until)

**3 concordant scans in the same day → `frozen_until = now() + 24h`**

During the freeze: new scans are recorded in `price_consensus_scans` but the trust_score is not recalculated.

`ratis_batch_consensus` resets `frozen_until = NULL` every night. If the batch crashes — one day of recalculation is lost, not the data.

---

## Sliding cap (decay)

Applied daily by `ratis_batch_consensus` — all decay parameters are configurable in `ratis_settings.json`, no hardcoded values.

After `decay_grace_days` days without a new scan, the trust_score decreases by `decay_rate_pct` per day down to the floor `decay_floor`:

```
trust_score = max(decay_floor, trust_score - decay_rate_pct)  # par jour
```

*Example with decay_grace_days=5, decay_rate_pct=10, decay_floor=30:*
```
J+0 à J+5  → pas de decay (période de grâce)
J+6        → trust_score - 10%
J+7        → trust_score - 10%
...
Plancher 30% atteint et maintenu indéfiniment
```

---

## Price switch

The trust_score decreases naturally via temporal weighting. When the dominant price in the window **strictly** exceeds the score of the current price, a switch is triggered:

- The old consensus is archived in `price_consensus_history` (`first_seen_at` → `last_seen_at = now`)
- The consensus moves to the new price, `first_seen_at = now`, `frozen_until = NULL`

No explicit threshold — temporal weighting and the sliding cap are sufficient to degrade the score naturally.

---

## Price precision

No tolerance — `3.49€` ≠ `3.50€`. Every cent of difference is a new price.

**Quarantine:** price outside ±30% of the current consensus → manual validation.

---

## Automatic rejections

- Receipt dated more than 7 days ago → rejected
- Video not captured from the app → reduced weight (unreliable metadata)

---

## Trust tiers

| Trust score | Meaning |
|---|---|
| >= 95% | Very reliable |
| 70-95% | Reliable |
| 50-70% | Aging — CAB bonus for concordant scan |
| < 50% | Stale — high CAB bonus + return to 95% if identical concordant scan |
| < 30% | Floor — indication only |

Gamification detail → `ratis_rewards/ARCH_gamification.md`.

---

## `price_consensus_scans` purge

Daily purge in `ratis_batch_purge` — keeps only the last 20 scans per consensus:

```sql
DELETE FROM price_consensus_scans
WHERE consensus_id = :id
AND scan_id NOT IN (
    SELECT scan_id FROM price_consensus_scans
    WHERE consensus_id = :id
    ORDER BY created_at DESC
    LIMIT 20
)
```

`price_consensus_history` is the reference snapshot — `price_consensus_history_scans` removed (migration).

---

## `ratis_settings.json` — consensus parameters

```json
"consensus": {
    "min_scans_to_create": 2,
    "min_distinct_users": 2,
    "window_size": 20,
    "scan_weight_decay_per_day": 0.10,
    "scan_weight_floor": 0.30,
    "freeze_threshold_scans": 3,
    "freeze_duration_hours": 24,
    "decay_grace_days": 5,
    "decay_rate_pct": 10,
    "decay_floor": 30,
    "price_quarantine_pct": 30,
    "ticket_max_age_days": 7
}
```

---

## To refine (real data)

- `window_size` based on actual store traffic
- Weight decay rate (0.10/day) based on price volatility
- Sliding caps based on observed behaviour
