# ADR-0003: Monetary amounts as integer cents end-to-end

**Status:** Accepted

## Context and Problem Statement

Float/NUMERIC money handling risks rounding drift across DB, backend, and frontend. Early columns used `NUMERIC(10,2)` for cashback amounts, and OCR extracts prices as decimal strings from receipts. How should money be represented so financial arithmetic is exact and uniform across the whole stack?

## Decision Drivers

- Financial math must be exact — no floating-point rounding errors in balances or cashback.
- One uniform representation across DB, API, settings, and frontend.
- OCR ingestion of decimal strings must not introduce binary-float representation errors.
- The convention must be enforced, not left to memory.

## Considered Options

- **Integer cents end-to-end** for amounts, `NUMERIC` reserved only for rates.
- **`NUMERIC`/`DECIMAL` columns for amounts** (the early `NUMERIC(10,2)` approach).
- **`int(float * 100)`** conversion on OCR ingestion.

## Decision Outcome

Chosen: **integer cents** in backend and DB — never `NUMERIC`/`FLOAT`/`DECIMAL` for an amount. Rates (`cashback_rate`, multipliers) remain `NUMERIC` as the one documented exception. OCR conversion must go through `Decimal`: `int(round(Decimal(str(v)) * 100))`, never `int(float * 100)`. Migration `a8b9c0d1e2f3` (2026-04-13) converted 9 columns including `scans.price`, `receipts.total_amount`, `price_consensus.price`, and cashback balances/withdrawals. `ratis_settings.json` stores cents; the API returns integer cents directly and the frontend does display-time conversion. The convention is codified as known-problem guard KP-03.

**Rejected:** `NUMERIC`/`DECIMAL` for amounts (kept only for rates, where fractional precision is intrinsic); `int(float * 100)` (explicitly forbidden — it reintroduces float rounding before scaling).

**Quality-attribute trade-off:** we bought **correctness/data-integrity** (exact money arithmetic, one representation) at the cost of **developer ergonomics** — every developer and OCR path must remember the cents convention and the `Decimal` rule (a recurring footgun, KP-03), and two numeric conventions (cents vs rates) now coexist.

### Consequences

- **Good:** exact money arithmetic; one uniform representation across DB / API / settings.
- **Bad:** every developer and OCR path must remember the cents convention and the `Decimal` rule (KP-03); the rates exception means two numeric conventions coexist and must be kept straight.

**Source.** `docs/decisions/DECISIONS_ACTED.md` DA-02 and DA-07; `CLAUDE.md` money rule; KP-03. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
