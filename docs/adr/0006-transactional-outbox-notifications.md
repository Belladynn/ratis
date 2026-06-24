# ADR-0006: Transactional outbox for fire-and-forget notifications

**Status:** Accepted

## Context and Problem Statement

Ratis is fire-and-forget by doctrine: every user action triggers async side effects. `notify_user` is a synchronous `httpx` POST. Calling it after `db.commit()` in the route is fragile (a process crash loses the notification); calling it *inside* the transaction blocks the commit on network latency. How are user-facing notifications dispatched reliably without coupling route latency to the notifier?

## Decision Drivers

- No notification may be lost on a process crash — the intent must be durable.
- The user-facing route must not block on notifier network latency.
- Prefer Postgres-native machinery over standing up a message broker (no extra infra).
- Multiple drainers should be able to run without contention.

## Considered Options

- **Transactional outbox** — a `notification_outbox` table written in the same transaction as the event, drained by a background worker.
- **Direct `httpx` call after commit.**
- **Direct call inside the transaction.**
- **A full message broker (RabbitMQ/Kafka).**

## Decision Outcome

Chosen: introduce a `notification_outbox` table. `enqueue_notification(db, user_id, type, data)` inserts into the **same transaction** as the triggering event (atomic). An `asyncio` worker in `main.py` drains the outbox every 30s using `SELECT … FOR UPDATE SKIP LOCKED`, calls `notify_user`, and updates `sent_at`. Routes (events, streak, referral) and helpers (`handle_scan_accepted`, `maybe_increment_challenge`) no longer call `notify_user` directly.

**Rejected:** direct `httpx` after commit (lost on crash); direct call inside the transaction (blocks commit on network latency); a full broker (RabbitMQ/Kafka) — avoided in favor of a Postgres-backed outbox to add no extra infra.

**Quality-attribute trade-off:** we bought **reliability** (at-least-once delivery, atomic with the business event, route latency unaffected, no new infra) at the cost of **latency and consumer complexity** — up to 30s delivery delay, an in-process drainer that must keep running, and duplicate-send on retry that consumers must tolerate.

### Consequences

- **Good:** at-least-once delivery; atomic with the business event; route latency unaffected; no extra broker infra; `SKIP LOCKED` lets multiple drainers run without contention.
- **Bad:** up-to-30s delivery latency; requires the in-process `asyncio` drainer to be running; duplicate-send possible on retry (consumers must tolerate at-least-once); the outbox table grows and must be purged.

**Source.** `docs/decisions/DECISIONS_ACTED.md` DA-15. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
