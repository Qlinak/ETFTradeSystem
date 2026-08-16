# ETF Trade System - Decisions

## Executive Summary

This document records the main product and engineering decisions behind the ETF Trade System. It is written to include each decision states, what was chosen, why it was chosen, and what tradeoff it solves.

## 1) Core Product Decisions

1. We keep order submission synchronous.
    - `POST /api/v1/orders` runs inside one **PostgreSQL** transaction and returns the final result.

2. PostgreSQL is the source of truth.
    - Database constraints and triggers enforce important rules: cutoff, product consistency, and ledger balance.

3. The tech stack is intentionally small and practical.

| Layer | Technology | Why it was used |
| --- | --- | --- |
| Backend API | **FastAPI** | Fast to build, easy to test, and a good fit for clear HTTP APIs. |
| Data store | **PostgreSQL** | Gives strong transactional safety for orders, quota, and ledger updates. |
| Database access | **SQLAlchemy + psycopg** | Keeps database access explicit while still being maintainable. |
| Test runner | **pytest** | Makes the business rules easy to verify with repeatable tests. |
| Frontend | **React + TypeScript + Vite** | Gives a responsive operations console with typed UI logic and quick local development. |
| Realtime updates | **SSE** with polling fallback | Keeps the UI close to real time without adding extra infrastructure. |
| Deployment | **Docker Compose** | Starts the full stack the same way in local development and in review runs. |
| Web serving | **Nginx** | Serves the frontend as a simple production container with SPA routing support. |

Why this stack works for this project:
- It keeps the core flow simple and auditable.
- It fits a transaction-heavy product better than a more complex event-driven design.
- It lets us ship a working end-to-end system quickly without sacrificing correctness.

## 2) Data and Rule Decisions

1. Idempotency key is `(pd_id, client_order_id)`.
    - The same key and same payload return the same stored response.

2. Cutoff time uses database time.
    - We use PostgreSQL `statement_timestamp()` and the product market timezone.

3. Quota is daily and transactional.
    - The authoritative table is `product_daily_quota`.
    - Reservations are tracked in `quota_allocations` and released at most once.

4. Double-entry must balance by currency.
    - A deferred DB constraint trigger checks every movement sums to zero on commit.

5. Pre-validation for foreign keys in submit flow.
    - We validate `pd_id` and `product_id` before idempotency write to avoid internal server errors.

## 3) Trade Offs

### Current bottleneck 1:
To scale up the application, we can add more FastAPI/API instances, but the database remains the main coordination point because idempotency, quota reservation, cutoff validation, and ledger posting all depend on one transactional source of truth. Under the current time constraint, a single PostgreSQL instance is the simplest way to guarantee correctness without introducing cross-node consistency bugs. It keeps the implementation auditable and makes the acceptance tests deterministic.

Future optimization plan:
1. Add read replicas for read-heavy endpoints such as blotter and cash ladder.
2. Keep write traffic on the primary database only.
3. Add targeted indexes and query-shape tuning for the hottest list views.
4. Split low-risk read models into cached projections only after write-path correctness is fully stable.
5. If throughput later exceeds one primary, move to partitioning or sharding by product or trade date, but only after reworking idempotency and quota ownership semantics.

### Current bottleneck 2:
We chose synchronous ingestion because the business requires immediate final confirmation or rejection, not delayed eventual completion. The request path is short and transactional, so the client gets one final answer per submission and retries are handled cleanly by idempotency. That is easier to reason about than an async queue, where status would be split across producer, queue, worker, and consumer states.

Tradeoffs versus async processing:
1. Synchronous flow gives stronger user feedback and simpler failure semantics.
2. Async flow could absorb bursts better, but it would add queue lag and make cutoff handling more fragile.
3. Async flow would also require an extra status-projection layer, which increases operational complexity and the chance of temporary inconsistency.
4. With more time, we could add async processing for non-critical background tasks such as notifications, reporting, or downstream audit export, but not for the core submit/cancel path.

## 4) Concurrency Control Decision

Chosen approach: a database-first approach using multiple controls together.

1. Pessimistic row locking
We use SELECT ... FOR UPDATE on rows that must be serialized.

2. Database unique constraints
Unique keys protect idempotency and prevent duplicate semantic records.

3. Atomic quota update condition
Quota reservation updates only succeed when used_quota + amount <= total_quota.

4. Single ACID transaction per request
Order creation, quota allocation, and ledger posting are committed together.

5. Crash safety and recovery model
If the service crashes before commit, PostgreSQL rolls back the whole transaction, so no partial state is saved.
If the service crashes after commit but before response, client retry is handled by idempotency replay.

Why this was chosen:

1. It is simple to reason about.
2. It prevents quota over-issue under concurrency.
3. It keeps request latency low for synchronous API calls.
4. It avoids extra infrastructure.

## 5) Rejected Concurrency Approaches and Why

1. Optimistic locking only
Rejected because it adds retry loops and unstable tail latency during bursts.

2. Serialized queue (Kafka/request-reply)
Rejected because it adds queue lag, polling complexity, and cutoff-time drift risk for synchronous HTTP.

3. Application-level distributed locks
Rejected because PostgreSQL already gives strong transactional locks for this scale, with less operational complexity.

4. "Serializable queue" style architecture
Rejected because it solves a problem we already solve inside one DB transaction, but with much higher ops cost.

## 6) Test Cases Included and Testing Strategy

### Test coverage map

| Requirement | Test coverage |
| --- | --- |
| Quota concurrency | [tests/concurrency/test_quota_concurrency.py](tests/concurrency/test_quota_concurrency.py) |
| Idempotency | [tests/integration/test_order_endpoints.py](tests/integration/test_order_endpoints.py) |
| Double-entry invariant | [tests/integration/db_function_tests.sql](tests/integration/db_function_tests.sql) and [tests/integration/test_order_endpoints.py](tests/integration/test_order_endpoints.py) |
| T+2 calendar computation | [tests/integration/test_cash_ladder_endpoint.py](tests/integration/test_cash_ladder_endpoint.py) |

### Database function tests
File: tests/integration/db_function_tests.sql

1. Product consistency trigger checks units/currency rules.
2. Cutoff trigger rejects late active orders.
3. Deferred ledger trigger enforces zero-sum balance.

### Integration API tests
File: tests/integration/test_order_endpoints.py

1. Same order submitted five times returns the same result (idempotency replay).
2. Cancelling twice does not double-release quota.
3. Orders crossing cutoff are rejected.
4. Adversarial unit values are rejected.
5. Ledger movements remain balanced.

### Cash ladder API tests
File: tests/integration/test_cash_ladder_endpoint.py

1. Cash ladder returns `responseTimeMs` in payload.
2. Settlement-date derivation respects holiday calendar and weekend skipping.
3. Inflow, outflow, and net values are correct for derived settlement date rows.
4. Invalid horizon is rejected.

### Concurrency test
File: tests/concurrency/test_quota_concurrency.py

1. Eight concurrent QDII submissions never over-issue quota.
2. Expected split is confirmed vs rejected based on remaining daily quota.

### Performance benchmark checks
Files:
- scripts/benchmark_cash_ladder.py
- benchmark_before.json
- benchmark_after_query_only.json
- benchmark_after_reseed_precompute.json
- benchmark_after_index.json

1. Baseline latency is captured before optimization.
2. Each optimization stage is measured with the same endpoint and method.
3. Final p99 is verified against target (<200ms).

### Testing strategy notes

1. Unit tests belong on deterministic business logic that does not need HTTP or a live database.
- Examples: pure validation helpers, formatting, math, and branch logic.

2. Integration tests belong on behavior that crosses a database or API boundary.
- Examples: idempotency replay, quota concurrency, double-entry enforcement, cutoff checks, and T+2 settlement-date computation.

3. We do not try to unit test framework wiring or database-enforced rules in isolation.
- Those checks are better covered by the database function tests and API integration tests because they verify the real production path.

4. The required behaviors are covered as follows.
- Quota concurrency: [tests/concurrency/test_quota_concurrency.py](tests/concurrency/test_quota_concurrency.py)
- Idempotency: [tests/integration/test_order_endpoints.py](tests/integration/test_order_endpoints.py)
- Double-entry invariant: [tests/integration/db_function_tests.sql](tests/integration/db_function_tests.sql) and [tests/integration/test_order_endpoints.py](tests/integration/test_order_endpoints.py)
- T+2 calendar computation: [tests/integration/test_cash_ladder_endpoint.py](tests/integration/test_cash_ladder_endpoint.py)

## 7) Cash Ladder Benchmark Decision

### Method used

1. Benchmark target endpoint:
`GET /api/v1/cash-ladder?asOf=2025-11-03&horizon=30`

2. Dataset size:
1,000,000 seeded orders.

3. Measurement:
- Client wall-clock latency (`wall_ms`)
- API-reported server latency (`responseTimeMs`)

4. Sampling setup:
2 warmup calls + 12 measured calls for each stage.

5. Script used:
`scripts/benchmark_cash_ladder.py`

## Optimizations Implemented

### 1) Query path split for confirmed orders

File: `app/repositories/cash_ladder_repository.py`

- Split logic into:
  - `confirmed_with_settlement`: fast indexed path for rows with precomputed `settlement_date`
  - `confirmed_needs_derive`: fallback path only for `settlement_date IS NULL`
- Effect: expensive holiday derivation no longer runs on every confirmed row.

### 2) Precompute settlement date at write/seed time

Files:
- `app/services/order_submission_service.py`
- `app/repositories/product_repository.py`
- `app/repositories/order_repository.py`
- `scripts/seed_data.py`

- Added DB-based settlement date derivation in submit flow.
- Persisted `settlement_date` during order insert.
- Updated seed generation to precompute holiday-aware T+2 settlement dates for all seeded orders.

### 3) Add partial covering index for cash ladder read path

File: `schema.sql`

```sql
CREATE INDEX orders_confirmed_settlement_idx
    ON orders (settlement_date, product_id, currency)
    INCLUDE (order_type, cash_amount)
    WHERE status = 'CONFIRMED';
```

Applied in live DB and analyzed table:

```sql
CREATE INDEX IF NOT EXISTS orders_confirmed_settlement_idx
ON orders (settlement_date, product_id, currency)
INCLUDE (order_type, cash_amount)
WHERE status = 'CONFIRMED';
ANALYZE orders;
```

## Optimizations Deliberately Not Chosen

1. Materialized view + periodic refresh
- Rejected for now: adds refresh orchestration and staleness management.
- Current indexed query already meets p99 target with fresh transactional reads.

2. External cache (Redis)
- Rejected for now: adds invalidation complexity and another dependency.
- Not needed to hit target.

3. Asynchronous queue pre-aggregation
- Rejected: operationally heavier and conflicts with direct synchronous read requirements.

4. Full denormalized projection table maintained by triggers/jobs
- Rejected for now: more write-path complexity and correctness risk.
- Current approach is simpler and already fast enough.

---

### Results before/after optimization

1. Before optimization (`benchmark_before.json`):
- API p99: `4194.61ms`
- Wall p99: `8283.90ms`

2. After final optimization (`benchmark_after_index.json`):
- API p99: `31.89ms`
- Wall p99: `75.91ms`

3. Outcome:
Target met (`p99 < 200ms`).

## 8) USD 300M Redemption Refresh Scenario

Scenario:
Operations confirms a USD 300 million redemption and refreshes cash ladder immediately.

Decision:
1. If the confirm transaction is committed, refresh should show the new number.
2. If refresh happens before commit finishes, it can briefly show the old number.
3. A refresh after commit will show the new number.

Reason:
Cash ladder reads from live PostgreSQL state per request (no cache layer in front).

## 9) Eventual Consistency Decision for This Business Case

Question:
Is "eventually consistent, correct in a few seconds" acceptable?

Decision:
No, not for this cash-ladder use case.

Reason:
1. This view is used by operations for immediate liquidity decisions.
2. A few seconds of stale exposure can cause wrong cash actions near large confirmations/cancellations.
3. We require fresh committed reads, not delayed projection reads, for this endpoint.

## 10) Scope Left Out, Next Steps, and Business Brief Notes

### What was not done, and why that was the right call within this time budget

1. We did not build a full event-driven microservices architecture.
- That would add queueing, retries, and consistency gaps that are not needed for this scope.

2. We did not add Redis or another cache in front of the ladder endpoint.
- The live PostgreSQL query already met the target after optimization, so a cache would add complexity without clear benefit.

3. We did not add a full reporting or analytics layer.
- The brief was about the operational trade flow, not long-running BI or historical dashboards.

4. We did not split the application into separate deployable backend services.
- Keeping one backend made the rules easier to test, debug, and audit in a short delivery window.

### What we would do next with three more days, in priority order

1. Tighten validation and observability.
- Add more focused integration tests around edge cases, cutoff handling, and failed replay paths.
- Add clearer request logs and metrics for the slowest endpoints.

2. Improve operations usability.
- Polish the console filters, empty states, and error messages.
- Add a few more status views for operators who need faster triage.

3. Harden deployment and maintenance.
- Add a better health-check and startup verification flow.
- Add repeatable seed/reset scripts for demo and review environments.