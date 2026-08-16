# ETF Trade System Scaling Design

## Question

If products grow from 50 to 2,000, PDs from 8 to 60, and the platform adds intraday real-time creation/redemption, where does the first bottleneck appear?

## Answer

The first bottleneck appears in the **PostgreSQL write path**, not in the frontend or the FastAPI layer.

Why:

- The system is designed so that order submission is synchronous and transactional.
- Each creation/redemption writes to the same core tables and depends on the same source of truth.
- As intraday volume rises, the hot path becomes lock contention, commit latency, and connection pressure on the primary database.
- The most sensitive points are `orders`, `product_daily_quota`, `quota_allocations`, `cash_movements`, `ledger_entries`, and the live cash-ladder query path.

In practical terms, the first limit is not product count itself. The larger product set is manageable. The real stress comes from more concurrent intraday transactions and more frequent operator refreshes against the same primary database.

## Why this is the first bottleneck

- **FastAPI** can be scaled horizontally earlier than the database.
- **React** and the frontend container are not the limiting factor.
- **PostgreSQL** remains the coordination point because idempotency, quota reservation, cutoff validation, and ledger posting all need one transactional answer.
- Real-time creation/redemption increases both write pressure and read freshness expectations, so the database becomes the first shared bottleneck.

## Phased Evolution Path

### Phase 1: Stabilize the current transactional core

What to do:

- Keep the synchronous submit path.
- Add targeted indexes on the hottest transactional and list queries.
- Reduce unnecessary columns in read responses.
- Tune connection pool sizes and timeouts.
- Monitor lock waits, slow queries, and dead tuples.

Cost:

- Low to medium engineering effort.
- Mostly schema, query, and operational tuning.

Risk:

- Low.
- This preserves the current model and does not change business semantics.

### Phase 2: Separate operational reads from the write path

What to do:

- Move read-heavy views such as blotter and cash ladder to dedicated read models.
- Use replicas or projection tables for operator screens.
- Keep writes on the primary database only.
- Add cache only for safe, non-authoritative views.

Cost:

- Medium.
- Requires additional data plumbing, freshness rules, and more test coverage.

Risk:

- Medium.
- Read freshness can drift if projection lag is not managed well.
- If operators rely on stale data, the business impact is visible immediately.

### Phase 3: Partition the write load

What to do:

- Partition the largest transactional tables by trade date or another natural business key.
- Revisit indexes and maintenance jobs for each partition.
- Make quota ownership and idempotency rules partition-safe.

Cost:

- High.
- Requires careful schema work, migration planning, and regression testing.

Risk:

- High.
- Partitioning can improve performance, but it also increases complexity in queries, operations, and recovery.
- A bad partition key can make future scaling harder instead of easier.

### Phase 4: Introduce asynchronous processing for non-critical work

What to do:

- Move non-core tasks such as notifications, reporting, and audit export to async workers.
- Keep the core order decision synchronous unless the business changes its service-level expectations.

Cost:

- Medium to high.
- Adds queueing, worker management, retry handling, and observability.

Risk:

- Medium.
- Async systems are harder to reason about near cutoff time.
- They also introduce temporary inconsistency, which is acceptable for some tasks but not for the core submission decision.

## Recommended order

1. Tune the current PostgreSQL path first.
2. Split operational reads from writes second.
3. Partition the largest write tables third.
4. Add async processing only for non-critical side tasks last.

## Summary

With 2,000 products and 60 PDs, the product catalog itself is not the main problem. The first bottleneck is the **single PostgreSQL transaction path** that powers order submission, quota, and ledger updates. The safest evolution is to keep the core transaction model, then progressively offload reads, then partition writes, and only then add async processing where consistency is not critical.
