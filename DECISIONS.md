# ETF Trade System - Decisions

## 1) Core Product Decisions

1. We keep order submission synchronous.
POST /api/v1/orders runs inside one PostgreSQL transaction and returns the final result.

2. PostgreSQL is the source of truth.
Database constraints and triggers enforce important rules (cutoff, product consistency, ledger balance).

3. One endpoint file, single-purpose internal files.
HTTP mapping stays in one file. Services handle business flow. Repositories handle SQL only.

4. No float for money or units.
Units are integer strings. Prices and cash use Decimal and NUMERIC columns.

## 2) Data and Rule Decisions

1. Idempotency key is (pd_id, client_order_id).
Same key and same payload returns the same stored response.

2. Cutoff time uses database time.
We use PostgreSQL statement_timestamp() and product market timezone.

3. Quota is daily and transactional.
Authoritative table is product_daily_quota.
Reservations are tracked in quota_allocations and released at most once.

4. Double-entry must balance by currency.
A deferred DB constraint trigger checks every movement sums to zero on commit.

5. Pre-validation for foreign keys in submit flow.
We validate pd_id and product_id before idempotency write to avoid internal server errors.

## 3) Runtime and Shipping Decisions

1. Full stack ships in Docker Compose.
Services: postgres, seed, api.

2. API healthcheck is enabled.
Compose probes /health and marks the API container healthy only after startup is complete.

3. psycopg runtime compatibility in containers.
We use psycopg[binary] and include libpq runtime support in the API image.

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

## 6) Test Cases Included (Current State)

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

### Concurrency test
File: tests/concurrency/test_quota_concurrency.py

1. Eight concurrent QDII submissions never over-issue quota.
2. Expected split is confirmed vs rejected based on remaining daily quota.

## 7) Current Known Behavior

1. If client sends IDs that do not exist in seed data, API returns a clean 404 business error (not 500).
2. Valid test payloads must use real seeded IDs and valid unit multiples for the selected product.
3. We do not use compensating transactions for mid-flight submit failures; correctness comes from single-transaction atomicity plus idempotent retry.