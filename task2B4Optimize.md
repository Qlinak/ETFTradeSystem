# Task 2B4 Optimization Report

## Goal

Get `GET /api/v1/cash-ladder` to **p99 < 200ms** at the generated 1,000,000-order scale.

Result: **achieved**.

Final measured API p99 (`responseTimeMs`): **31.89ms**.
Final measured end-to-end wall p99: **75.91ms**.

---

## Benchmarking Method

### Endpoint and workload

- Endpoint: `GET /api/v1/cash-ladder?asOf=2025-11-03&horizon=30`
- Dataset: 1,000,000 seeded orders
- Sampling: 2 warmup calls + 12 measured calls per stage

### Measurement dimensions

1. `wall_ms`
- Measured client-side around HTTP call (`perf_counter`)

2. `api_responseTimeMs`
- Measured inside API response payload (server-side execution time)

### Benchmark script

- Script: `scripts/benchmark_cash_ladder.py`
- Example:

```bash
/Users/nevillelam/Desktop/ETFTradeSystem/.venv/bin/python scripts/benchmark_cash_ladder.py \
  --url 'http://localhost:8000/api/v1/cash-ladder?asOf=2025-11-03&horizon=30' \
  --warmup 2 \
  --runs 12 \
  --output benchmark_after_index.json
```

---

## Before/After Results (Raw)

### Baseline (before optimization)

Source: `benchmark_before.json`

```json
{
  "wall_ms": {"p50": 4152.12, "p95": 8180.37, "p99": 8283.9, "avg": 5701.21},
  "api_responseTimeMs": {"p50": 3983.5, "p95": 4173.05, "p99": 4194.61, "avg": 3999.83}
}
```

### Stage 1: Query rewrite only

Source: `benchmark_after_query_only.json`

```json
{
  "wall_ms": {"p50": 4161.78, "p95": 4359.53, "p99": 4364.63, "avg": 4185.34},
  "api_responseTimeMs": {"p50": 4135.0, "p95": 4344.5, "p99": 4348.9, "avg": 4165.92}
}
```

### Stage 2: Reseed with precomputed settlement dates

Source: `benchmark_after_reseed_precompute.json`

```json
{
  "wall_ms": {"p50": 624.61, "p95": 688.36, "p99": 709.06, "avg": 631.68},
  "api_responseTimeMs": {"p50": 609.0, "p95": 671.5, "p99": 693.5, "avg": 617.25}
}
```

### Stage 3: Add confirmed-settlement partial covering index

Source: `benchmark_after_index.json`

```json
{
  "wall_ms": {"p50": 41.42, "p95": 61.95, "p99": 75.91, "avg": 44.9},
  "api_responseTimeMs": {"p50": 30.0, "p95": 31.45, "p99": 31.89, "avg": 30.0}
}
```

---

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

### 4) Cash amount correctness in seed data

File: `scripts/seed_data.py`

- Seed now writes non-zero `estimated_price`.
- `cash_amount` uses Decimal math from `units * estimated_price`.
- This fixed data quality and removed formula drift.

---

## Gain by Individual Optimization

Using server-side p99 (`api_responseTimeMs`):

1. Baseline -> Query rewrite only
- 4194.61ms -> 4348.9ms
- No real improvement (noise/regression range)
- Conclusion: query split alone does not help when almost all confirmed rows have null settlement_date.

2. Query rewrite -> Precompute settlement date
- 4348.9ms -> 693.5ms
- Gain: **3655.4ms (~84.1% reduction)**
- Causal reason: fallback derivation now runs on very few rows.

3. Precompute settlement date -> Add index
- 693.5ms -> 31.89ms
- Gain: **661.61ms (~95.4% reduction)**
- Causal reason: range scan/grouping is index-supported on confirmed settlement path.

4. Baseline -> Final
- 4194.61ms -> 31.89ms
- Gain: **4162.72ms (~99.24% reduction)**

---

## Why This Is Causation (Not Coincidence)

1. Single-variable staging
- Changes were benchmarked in sequence:
  - baseline
  - query rewrite only
  - + precomputed settlement dates
  - + index
- This isolates impact of each step.

2. Stable test endpoint and params
- Same endpoint and query each run:
  - `/api/v1/cash-ladder?asOf=2025-11-03&horizon=30`

3. Same benchmark harness
- Same warmup/runs and percentile calculation script.

4. Expected directional behavior matched observed results
- No benefit from query split when null settlements dominate.
- Big gain after precomputing settlement_date.
- Very large gain after adding matching partial covering index.

---

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

## Validation

Regression test suite after optimization:

```bash
/Users/nevillelam/Desktop/ETFTradeSystem/.venv/bin/pytest \
  tests/integration/test_cash_ladder_endpoint.py \
  tests/integration/test_order_endpoints.py \
  tests/concurrency/test_quota_concurrency.py -q
```

Result: `8 passed`.
{
  "asOf": "2025-11-01",
  "horizon": 30,
  "windowEnd": "2025-11-30",
  "generatedAt": "2026-08-15T07:53:17.850391Z",
  "responseTimeMs": 2771,
  "rows": [
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "175735041.0600",
      "outflow": "2621953302.5600",
      "net": "-2446218261.5000"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "305454247.7280",
      "outflow": "913494572.4570",
      "net": "-608040324.7290"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "3101451046.3350",
      "outflow": "2699074458.3550",
      "net": "402376587.9800"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "498965312.2830",
      "outflow": "364377847.1070",
      "net": "134587465.1760"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "2684716084.2650",
      "outflow": "3637890296.6050",
      "net": "-953174212.3400"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "1045916897.8140",
      "outflow": "1506949470.5630",
      "net": "-461032572.7490"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "13605635671.4300",
      "outflow": "1961152996.3100",
      "net": "11644482675.1200"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "2864449605.1300",
      "outflow": "7088214908.2750",
      "net": "-4223765303.1450"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "5044604201.0600",
      "outflow": "4284907913.3600",
      "net": "759696287.7000"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "2054147021.5800",
      "outflow": "1934100789.5600",
      "net": "120046232.0200"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "553076266.7880",
      "outflow": "898453010.0000",
      "net": "-345376743.2120"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "4088683523.2850",
      "outflow": "3195681111.8150",
      "net": "893002411.4700"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "3303613392.8150",
      "outflow": "2324390814.5650",
      "net": "979222578.2500"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "727720402.4310",
      "outflow": "646517281.6880",
      "net": "81203120.7430"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "791102687.9600",
      "outflow": "60408757.1280",
      "net": "730693930.8320"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "1719354480.9200",
      "outflow": "826550844.1780",
      "net": "892803636.7420"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "148723380.9250",
      "outflow": "465241439.2080",
      "net": "-316518058.2830"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "369533554.3200",
      "outflow": "1020471302.9000",
      "net": "-650937748.5800"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "607623113.2200",
      "outflow": "482798267.7560",
      "net": "124824845.4640"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "922339788.4800",
      "outflow": "266993665.7720",
      "net": "655346122.7080"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "2540782795.9950",
      "outflow": "4001717340.9550",
      "net": "-1460934544.9600"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "934874657.7300",
      "outflow": "2091486275.6180",
      "net": "-1156611617.8880"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "1181282500.8300",
      "outflow": "909627838.7790",
      "net": "271654662.0510"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "6711754961.4200",
      "outflow": "4608192118.3700",
      "net": "2103562843.0500"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "3797420596.1350",
      "outflow": "1430804763.7450",
      "net": "2366615832.3900"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "344207000.6070",
      "outflow": "465378570.9300",
      "net": "-121171570.3230"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "66237070.5000",
      "outflow": "851663510.9120",
      "net": "-785426440.4120"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "304676729.3750",
      "outflow": "838191296.6970",
      "net": "-533514567.3220"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "739247933.8580",
      "outflow": "451839696.6100",
      "net": "287408237.2480"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "3635028568.8500",
      "outflow": "9772533510.2100",
      "net": "-6137504941.3600"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "453774865.0730",
      "outflow": "217993993.4920",
      "net": "235780871.5810"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "11067265626.3200",
      "outflow": "6341478875.4100",
      "net": "4725786750.9100"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "4686954997.0450",
      "outflow": "5460689006.4050",
      "net": "-773734009.3600"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "701291613.9840",
      "outflow": "792228341.0640",
      "net": "-90936727.0800"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "218019332.0870",
      "outflow": "392641208.7780",
      "net": "-174621876.6910"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "3170686744.0650",
      "outflow": "2336047093.7800",
      "net": "834639650.2850"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "566624006.5940",
      "outflow": "1027076178.5280",
      "net": "-460452171.9340"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "410178653.9480",
      "outflow": "134535156.8040",
      "net": "275643497.1440"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "1243118775.4700",
      "outflow": "1568899414.1400",
      "net": "-325780638.6700"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "7058440122.7800",
      "outflow": "2647000076.6400",
      "net": "4411440046.1400"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "8392313676.6000",
      "outflow": "7883448075.1600",
      "net": "508865601.4400"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "401332246.7340",
      "outflow": "477414485.9250",
      "net": "-76082239.1910"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "3023266265.5600",
      "outflow": "774248084.9500",
      "net": "2249018180.6100"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "7092120050.4800",
      "outflow": "7893474046.3600",
      "net": "-801353995.8800"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "493233036.1840",
      "outflow": "772955769.4130",
      "net": "-279722733.2290"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "571551621.6080",
      "outflow": "2525145134.1120",
      "net": "-1953593512.5040"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "621746639.5350",
      "outflow": "732426819.5020",
      "net": "-110680179.9670"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "805628366.1040",
      "outflow": "511161637.4560",
      "net": "294466728.6480"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "301786381.6400",
      "outflow": "545353127.6750",
      "net": "-243566746.0350"
    },
    {
      "settlementDate": "2025-11-03",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "65419935.6300",
      "outflow": "815081143.2110",
      "net": "-749661207.5810"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "23388341014.3700",
      "outflow": "11366032142.2900",
      "net": "12022308872.0800"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "1819220432.9790",
      "outflow": "1106484774.0580",
      "net": "712735658.9210"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "12649184468.6650",
      "outflow": "9962993158.7150",
      "net": "2686191309.9500"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "762053356.5470",
      "outflow": "2129417114.6570",
      "net": "-1367363758.1100"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "6358626796.4250",
      "outflow": "9923507840.9500",
      "net": "-3564881044.5250"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "1143582373.2050",
      "outflow": "1654153858.4610",
      "net": "-510571485.2560"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "15956427235.8000",
      "outflow": "13869138570.2800",
      "net": "2087288665.5200"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "14416393122.9750",
      "outflow": "4672093853.1650",
      "net": "9744299269.8100"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "26634658703.4800",
      "outflow": "14483752964.4800",
      "net": "12150905739.0000"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "33989777446.8400",
      "outflow": "14742617871.9300",
      "net": "19247159574.9100"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "3320288370.6040",
      "outflow": "3021284523.2260",
      "net": "299003847.3780"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "7823268171.8500",
      "outflow": "12302445541.8450",
      "net": "-4479177369.9950"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "6574072561.5800",
      "outflow": "11469795801.6250",
      "net": "-4895723240.0450"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "1962806479.8630",
      "outflow": "1648766041.7600",
      "net": "314040438.1030"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "4523915936.0800",
      "outflow": "4070421198.2280",
      "net": "453494737.8520"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "2956542101.6020",
      "outflow": "3896059413.9900",
      "net": "-939517312.3880"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "1879229905.0200",
      "outflow": "1716914903.6940",
      "net": "162315001.3260"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "869497897.3640",
      "outflow": "2243998708.1980",
      "net": "-1374500810.8340"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "2011259570.5040",
      "outflow": "1368685761.0390",
      "net": "642573809.4650"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "4705681232.1720",
      "outflow": "3093884779.7420",
      "net": "1611796452.4300"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "6130127143.8400",
      "outflow": "1941889018.2400",
      "net": "4188238125.6000"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "2977496428.9340",
      "outflow": "4539896565.0240",
      "net": "-1562400136.0900"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "1308182203.3730",
      "outflow": "2203524966.0490",
      "net": "-895342762.6760"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "17062133442.3700",
      "outflow": "22981698343.9700",
      "net": "-5919564901.6000"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "13242782049.5800",
      "outflow": "6597781670.6400",
      "net": "6645000378.9400"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "1915498228.2170",
      "outflow": "2808127341.4740",
      "net": "-892629113.2570"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "2964973150.9800",
      "outflow": "2312632024.5160",
      "net": "652341126.4640"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "1867190487.9640",
      "outflow": "1750803394.6710",
      "net": "116387093.2930"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "1892901386.5070",
      "outflow": "803374786.5930",
      "net": "1089526599.9140"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "25720075218.1600",
      "outflow": "17193569874.0400",
      "net": "8526505344.1200"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "964158787.1930",
      "outflow": "1462258288.6620",
      "net": "-498099501.4690"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "7834807366.9000",
      "outflow": "29842005422.1500",
      "net": "-22007198055.2500"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "8137621151.0850",
      "outflow": "9817313616.7950",
      "net": "-1679692465.7100"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "2220877239.3540",
      "outflow": "3551732065.5840",
      "net": "-1330854826.2300"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "1489392182.6700",
      "outflow": "1358256453.8790",
      "net": "131135728.7910"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "8567360292.1900",
      "outflow": "7890587134.5650",
      "net": "676773157.6250"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "1155131871.9530",
      "outflow": "1882757898.5960",
      "net": "-727626026.6430"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "1812763603.5390",
      "outflow": "1926869293.3910",
      "net": "-114105689.8520"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "5288875131.3350",
      "outflow": "9988500801.2800",
      "net": "-4699625669.9450"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "26279958294.3000",
      "outflow": "20127419182.9900",
      "net": "6152539111.3100"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "14892092278.1600",
      "outflow": "15202968396.8100",
      "net": "-310876118.6500"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "2494240533.7030",
      "outflow": "1773718644.1420",
      "net": "720521889.5610"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "13609127268.2900",
      "outflow": "20228893212.4900",
      "net": "-6619765944.2000"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "13941253807.7200",
      "outflow": "22037993824.9700",
      "net": "-8096740017.2500"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "1199965953.7650",
      "outflow": "2937231522.5390",
      "net": "-1737265568.7740"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "3237708275.1360",
      "outflow": "2704999629.1800",
      "net": "532708645.9560"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "1326582443.6470",
      "outflow": "3079108376.4710",
      "net": "-1752525932.8240"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "2211764377.0280",
      "outflow": "2363717822.4660",
      "net": "-151953445.4380"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "1318931269.5910",
      "outflow": "1394320535.9870",
      "net": "-75389266.3960"
    },
    {
      "settlementDate": "2025-11-04",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "1871258639.9670",
      "outflow": "1775642848.0460",
      "net": "95615791.9210"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "7554975490.3700",
      "outflow": "4697289197.8500",
      "net": "2857686292.5200"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "1035743668.3340",
      "outflow": "700061129.1670",
      "net": "335682539.1670"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "3871025757.3600",
      "outflow": "2090572866.3050",
      "net": "1780452891.0550"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "558866715.1330",
      "outflow": "501995769.2630",
      "net": "56870945.8700"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "1376567960.5250",
      "outflow": "5016125240.5900",
      "net": "-3639557280.0650"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "463187699.2670",
      "outflow": "1184953909.7080",
      "net": "-721766210.4410"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "5899020074.4800",
      "outflow": "1518408640.6800",
      "net": "4380611433.8000"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "5913820919.3800",
      "outflow": "5210237669.6650",
      "net": "703583249.7150"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "11564804429.6900",
      "outflow": "5238914917.7300",
      "net": "6325889511.9600"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "1767691906.9100",
      "outflow": "14151363010.8300",
      "net": "-12383671103.9200"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1193700470.8580",
      "outflow": "302620595.9880",
      "net": "891079874.8700"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "1492651044.8350",
      "outflow": "4218296586.5150",
      "net": "-2725645541.6800"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "380614618.5500",
      "outflow": "6264606306.2400",
      "net": "-5883991687.6900"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "1178302308.1890",
      "outflow": "936555308.3440",
      "net": "241746999.8450"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "154291082.3620",
      "outflow": "1142857605.2060",
      "net": "-988566522.8440"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "1026656536.1300",
      "outflow": "1081078195.3680",
      "net": "-54421659.2380"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "718326584.2690",
      "outflow": "24315969.4990",
      "net": "694010614.7700"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "551084298.6080",
      "outflow": "478194341.3270",
      "net": "72889957.2810"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "436578530.1350",
      "outflow": "300026519.4340",
      "net": "136552010.7010"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "1628678071.0160",
      "outflow": "250166313.2700",
      "net": "1378511757.7460"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "2437803398.7000",
      "outflow": "1888148168.5750",
      "net": "549655230.1250"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "887171387.6200",
      "outflow": "652733996.0320",
      "net": "234437391.5880"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "137736540.6510",
      "outflow": "202444208.7360",
      "net": "-64707668.0850"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "9369551064.5700",
      "outflow": "2544972833.4700",
      "net": "6824578231.1000"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "509452279.0700",
      "outflow": "3433369769.4100",
      "net": "-2923917490.3400"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "224063188.5160",
      "outflow": "713637816.9310",
      "net": "-489574628.4150"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "803948686.6340",
      "outflow": "1407359561.0420",
      "net": "-603410874.4080"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "309498418.7060",
      "outflow": "260084117.1520",
      "net": "49414301.5540"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "393383700.8040",
      "outflow": "673878373.5940",
      "net": "-280494672.7900"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "9180855363.0400",
      "outflow": "7239215109.9700",
      "net": "1941640253.0700"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "220820837.1350",
      "outflow": "730779162.4800",
      "net": "-509958325.3450"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "1252560000.3500",
      "outflow": "1234216604.7000",
      "net": "18343395.6500"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "1882161294.7150",
      "outflow": "3205645850.8100",
      "net": "-1323484556.0950"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "1893545414.6820",
      "outflow": "229441747.1880",
      "net": "1664103667.4940"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "130593122.1710",
      "outflow": "1014921707.7130",
      "net": "-884328585.5420"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "3085861541.5250",
      "outflow": "2558477376.6650",
      "net": "527384164.8600"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "187031109.3110",
      "outflow": "1335094611.5230",
      "net": "-1148063502.2120"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "673484732.8540",
      "outflow": "593348620.3620",
      "net": "80136112.4920"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "3685224631.8900",
      "outflow": "2397722369.1050",
      "net": "1287502262.7850"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "3623700377.4000",
      "outflow": "12032521595.3200",
      "net": "-8408821217.9200"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "8115211877.7200",
      "outflow": "4181099948.6500",
      "net": "3934111929.0700"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "667962634.8050",
      "outflow": "372525532.6210",
      "net": "295437102.1840"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "4663668048.0700",
      "outflow": "6266778446.2200",
      "net": "-1603110398.1500"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "3919316574.3600",
      "outflow": "5132227943.8800",
      "net": "-1212911369.5200"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "911750167.4170",
      "outflow": "758136848.2700",
      "net": "153613319.1470"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "73391029.6740",
      "outflow": "0.0000",
      "net": "73391029.6740"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "441409836.1720",
      "outflow": "263642715.8140",
      "net": "177767120.3580"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "836790078.7680",
      "outflow": "438044543.5040",
      "net": "398745535.2640"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "384822040.1140",
      "outflow": "610789824.8220",
      "net": "-225967784.7080"
    },
    {
      "settlementDate": "2025-11-05",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "488409610.7880",
      "outflow": "538692274.9650",
      "net": "-50282664.1770"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "3967082594.4400",
      "outflow": "10221253090.5500",
      "net": "-6254170496.1100"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "269411498.7660",
      "outflow": "887619078.9950",
      "net": "-618207580.2290"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "1388487263.0000",
      "outflow": "3200981839.7050",
      "net": "-1812494576.7050"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "371912703.9900",
      "outflow": "781384403.8220",
      "net": "-409471699.8320"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "3281964054.4800",
      "outflow": "4865544357.0400",
      "net": "-1583580302.5600"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "414096062.9960",
      "outflow": "442891982.1440",
      "net": "-28795919.1480"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "3627414859.2300",
      "outflow": "3639234746.5000",
      "net": "-11819887.2700"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "2911009719.1900",
      "outflow": "2173065076.2600",
      "net": "737944642.9300"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "9389037786.6800",
      "outflow": "7147824207.3700",
      "net": "2241213579.3100"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "10669001955.8000",
      "outflow": "5886632609.7000",
      "net": "4782369346.1000"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "718879662.0160",
      "outflow": "1211279274.9340",
      "net": "-492399612.9180"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "1338648592.8300",
      "outflow": "2384885785.0050",
      "net": "-1046237192.1750"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "3757743958.9550",
      "outflow": "182577120.4600",
      "net": "3575166838.4950"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "514291684.3180",
      "outflow": "279592437.2930",
      "net": "234699247.0250"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "366652663.6680",
      "outflow": "455833688.8860",
      "net": "-89181025.2180"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "2586678717.8900",
      "outflow": "1932861866.7360",
      "net": "653816851.1540"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "781631615.4620",
      "outflow": "132265032.0660",
      "net": "649366583.3960"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "728299052.1050",
      "outflow": "516091089.6820",
      "net": "212207962.4230"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "999049244.3950",
      "outflow": "423735241.0500",
      "net": "575314003.3450"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "376575199.3780",
      "outflow": "866956653.8740",
      "net": "-490381454.4960"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "3224664639.4200",
      "outflow": "2665489879.5000",
      "net": "559174759.9200"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "1959207617.6040",
      "outflow": "1139674907.5300",
      "net": "819532710.0740"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "716108107.1190",
      "outflow": "120362331.2330",
      "net": "595745775.8860"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "6296086515.5600",
      "outflow": "15483171307.2400",
      "net": "-9187084791.6800"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "3078749213.0450",
      "outflow": "5638030813.1750",
      "net": "-2559281600.1300"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "187477122.6920",
      "outflow": "838178419.2130",
      "net": "-650701296.5210"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "165723934.2940",
      "outflow": "1071341598.7400",
      "net": "-905617664.4460"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "595571628.7590",
      "outflow": "693283454.2180",
      "net": "-97711825.4590"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "596060228.7870",
      "outflow": "558025932.5560",
      "net": "38034296.2310"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "2167681730.2100",
      "outflow": "10597377333.6800",
      "net": "-8429695603.4700"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "798524569.2530",
      "outflow": "516191480.7040",
      "net": "282333088.5490"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "4148832680.7000",
      "outflow": "4361417222.8400",
      "net": "-212584542.1400"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "1273647379.4050",
      "outflow": "2627729368.6950",
      "net": "-1354081989.2900"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "1012614863.5960",
      "outflow": "2383404035.8960",
      "net": "-1370789172.3000"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "333623151.7090",
      "outflow": "148030883.3020",
      "net": "185592268.4070"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "1513078993.6500",
      "outflow": "3619521271.6200",
      "net": "-2106442277.9700"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "701023992.7750",
      "outflow": "544975259.8080",
      "net": "156048732.9670"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "762610881.4580",
      "outflow": "385126255.7150",
      "net": "377484625.7430"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "2254624438.7250",
      "outflow": "3063760191.8000",
      "net": "-809135753.0750"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "7839851653.3300",
      "outflow": "8649340025.5600",
      "net": "-809488372.2300"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "8201325641.2900",
      "outflow": "2664678121.9800",
      "net": "5536647519.3100"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "536849664.9590",
      "outflow": "572624761.0510",
      "net": "-35775096.0920"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "3987336492.3000",
      "outflow": "4512750107.3800",
      "net": "-525413615.0800"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "2716309508.1600",
      "outflow": "6662876227.8300",
      "net": "-3946566719.6700"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "122421104.1660",
      "outflow": "949863850.4850",
      "net": "-827442746.3190"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "576222382.9900",
      "outflow": "1138051339.7580",
      "net": "-561828956.7680"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "89774809.8650",
      "outflow": "392178679.7790",
      "net": "-302403869.9140"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "614982275.2040",
      "outflow": "672131590.8460",
      "net": "-57149315.6420"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "338954110.2330",
      "outflow": "548225314.1370",
      "net": "-209271203.9040"
    },
    {
      "settlementDate": "2025-11-06",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "778758013.8960",
      "outflow": "299504180.8560",
      "net": "479253833.0400"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "4344493973.6200",
      "outflow": "3082671776.9200",
      "net": "1261822196.7000"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "426549023.4720",
      "outflow": "719909634.1760",
      "net": "-293360610.7040"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "1869347035.7200",
      "outflow": "1980606328.7850",
      "net": "-111259293.0650"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "337953206.6750",
      "outflow": "480539085.9390",
      "net": "-142585879.2640"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "2576703958.7850",
      "outflow": "4400332922.1000",
      "net": "-1823628963.3150"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "1431663416.8560",
      "outflow": "753194876.9080",
      "net": "678468539.9480"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "9896699523.2000",
      "outflow": "4838599762.2100",
      "net": "5058099760.9900"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "3500243345.0200",
      "outflow": "3835114093.1600",
      "net": "-334870748.1400"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "164332207.2200",
      "outflow": "2426366904.0500",
      "net": "-2262034696.8300"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "664442777.7200",
      "outflow": "4038033611.4800",
      "net": "-3373590833.7600"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "983904676.8900",
      "outflow": "490760375.3600",
      "net": "493144301.5300"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "4732242529.2700",
      "outflow": "5169207992.6100",
      "net": "-436965463.3400"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "4927378637.2400",
      "outflow": "3299645351.7600",
      "net": "1627733285.4800"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "1104013234.4440",
      "outflow": "496500330.9630",
      "net": "607512903.4810"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "1341362216.0840",
      "outflow": "1291661959.9120",
      "net": "49700256.1720"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "993193077.7580",
      "outflow": "1655182831.3500",
      "net": "-661989753.5920"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "710598922.3150",
      "outflow": "352154729.5390",
      "net": "358444192.7760"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "704336083.5540",
      "outflow": "366281597.4290",
      "net": "338054486.1250"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "637847633.1240",
      "outflow": "800337740.9430",
      "net": "-162490107.8190"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "2007911723.8540",
      "outflow": "1349194968.4500",
      "net": "658716755.4040"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "4505137607.6150",
      "outflow": "1910897633.5600",
      "net": "2594239974.0550"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "317719081.4280",
      "outflow": "509486060.0160",
      "net": "-191766978.5880"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "44341516.2710",
      "outflow": "251428038.8430",
      "net": "-207086522.5720"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "4659200341.0200",
      "outflow": "4027038016.4500",
      "net": "632162324.5700"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "6247020135.3600",
      "outflow": "1496838504.4500",
      "net": "4750181630.9100"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "588020040.9850",
      "outflow": "689682118.0830",
      "net": "-101662077.0980"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "1291058066.3460",
      "outflow": "1271450801.2800",
      "net": "19607265.0660"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "306977581.4750",
      "outflow": "286910351.0340",
      "net": "20067230.4410"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "520491084.2680",
      "outflow": "63875441.3340",
      "net": "456615642.9340"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "1867127321.3500",
      "outflow": "6347391584.5100",
      "net": "-4480264263.1600"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "454847622.0900",
      "outflow": "429126790.8890",
      "net": "25720831.2010"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "4965241280.4700",
      "outflow": "6455837564.9900",
      "net": "-1490596284.5200"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "1341873544.3800",
      "outflow": "2483588155.3150",
      "net": "-1141714610.9350"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "1620254965.2480",
      "outflow": "915316940.8980",
      "net": "704938024.3500"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "357046384.6470",
      "outflow": "561123046.1580",
      "net": "-204076661.5110"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "1498955740.5350",
      "outflow": "1484237226.4000",
      "net": "14718514.1350"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "501672390.2060",
      "outflow": "553550598.4960",
      "net": "-51878208.2900"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "1563496093.2180",
      "outflow": "110936721.1820",
      "net": "1452559372.0360"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "1758866782.5450",
      "outflow": "1237108778.1200",
      "net": "521758004.4250"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "6124990363.7000",
      "outflow": "7158827914.5700",
      "net": "-1033837550.8700"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "9525225371.0000",
      "outflow": "243598507.8400",
      "net": "9281626863.1600"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "329455525.5060",
      "outflow": "1047552826.4960",
      "net": "-718097300.9900"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "6797183505.0600",
      "outflow": "4229008540.2800",
      "net": "2568174964.7800"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "9637576663.6500",
      "outflow": "2702710843.9700",
      "net": "6934865819.6800"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "944708180.1890",
      "outflow": "842714979.7480",
      "net": "101993200.4410"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "1374126929.6980",
      "outflow": "789538630.0860",
      "net": "584588299.6120"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "474064151.5220",
      "outflow": "277042122.6730",
      "net": "197022028.8490"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "1197448220.2220",
      "outflow": "953979191.8320",
      "net": "243469028.3900"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "400281672.0430",
      "outflow": "283122349.5090",
      "net": "117159322.5340"
    },
    {
      "settlementDate": "2025-11-07",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "271532020.4650",
      "outflow": "219911236.3010",
      "net": "51620784.1640"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "2411876918.1900",
      "outflow": "5468111861.0800",
      "net": "-3056234942.8900"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "588049892.9080",
      "outflow": "658886879.0620",
      "net": "-70836986.1540"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "4738198331.3850",
      "outflow": "1063230263.9250",
      "net": "3674968067.4600"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "551374105.9490",
      "outflow": "767413539.3320",
      "net": "-216039433.3830"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "1635853703.6150",
      "outflow": "5386972858.9250",
      "net": "-3751119155.3100"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "19526079.8990",
      "outflow": "1066810308.8760",
      "net": "-1047284228.9770"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "11470049079.1200",
      "outflow": "5013977779.4300",
      "net": "6456071299.6900"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "899579872.5250",
      "outflow": "2119435870.1800",
      "net": "-1219855997.6550"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "5675899434.4800",
      "outflow": "8916150638.1800",
      "net": "-3240251203.7000"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "10003705208.4000",
      "outflow": "2845151405.4400",
      "net": "7158553802.9600"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "2438526531.1080",
      "outflow": "670562816.7900",
      "net": "1767963714.3180"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "3969635533.4200",
      "outflow": "3094927660.6000",
      "net": "874707872.8200"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "3296038774.8550",
      "outflow": "8108917474.2100",
      "net": "-4812878699.3550"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "512557034.6460",
      "outflow": "752020381.8750",
      "net": "-239463347.2290"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "643010901.2460",
      "outflow": "1641177744.2000",
      "net": "-998166842.9540"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "422207636.9240",
      "outflow": "594162494.7060",
      "net": "-171954857.7820"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "568718102.8880",
      "outflow": "967631619.5530",
      "net": "-398913516.6650"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "1079489161.9090",
      "outflow": "595099281.3520",
      "net": "484389880.5570"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "719343078.6560",
      "outflow": "380646997.1210",
      "net": "338696081.5350"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "789614536.6240",
      "outflow": "768344430.6020",
      "net": "21270106.0220"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "1810465491.0250",
      "outflow": "3820508094.8900",
      "net": "-2010042603.8650"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "425912782.2960",
      "outflow": "0.0000",
      "net": "425912782.2960"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "103938590.3490",
      "outflow": "574821830.0640",
      "net": "-470883239.7150"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "2196935278.9100",
      "outflow": "6432733146.1600",
      "net": "-4235797867.2500"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "1852885198.2200",
      "outflow": "1850875017.1850",
      "net": "2010181.0350"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "717180086.7510",
      "outflow": "1349357894.7550",
      "net": "-632177808.0040"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "645248599.3680",
      "outflow": "1194008030.8800",
      "net": "-548759431.5120"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "618969297.1680",
      "outflow": "0.0000",
      "net": "618969297.1680"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "460304318.8640",
      "outflow": "253940731.9780",
      "net": "206363586.8860"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "12400337546.8400",
      "outflow": "6792481529.7400",
      "net": "5607856017.1000"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "922662054.6600",
      "outflow": "201616743.4040",
      "net": "721045311.2560"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "4413419584.9800",
      "outflow": "1938125456.4100",
      "net": "2475294128.5700"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "3166352232.3600",
      "outflow": "1989022736.7400",
      "net": "1177329495.6200"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "520137076.6580",
      "outflow": "1509497403.0120",
      "net": "-989360326.3540"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "493994099.3940",
      "outflow": "840464811.8850",
      "net": "-346470712.4910"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "930894247.1300",
      "outflow": "933742317.9000",
      "net": "-2848070.7700"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "321719440.0320",
      "outflow": "10862127.2160",
      "net": "310857312.8160"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "541479166.6690",
      "outflow": "564658483.7780",
      "net": "-23179317.1090"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "1737663916.6100",
      "outflow": "905125850.0450",
      "net": "832538066.5650"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "6234788435.5100",
      "outflow": "6610950253.9900",
      "net": "-376161818.4800"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "8531503987.8200",
      "outflow": "5611597835.5800",
      "net": "2919906152.2400"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "139836469.0420",
      "outflow": "735995841.2930",
      "net": "-596159372.2510"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "3936534211.7400",
      "outflow": "7478321676.9100",
      "net": "-3541787465.1700"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "3356192739.3100",
      "outflow": "8797154355.4600",
      "net": "-5440961616.1500"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "320130631.0070",
      "outflow": "886504633.8880",
      "net": "-566374002.8810"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "812870311.1340",
      "outflow": "740558040.0920",
      "net": "72312271.0420"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "201184248.9080",
      "outflow": "1026613509.0050",
      "net": "-825429260.0970"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "1746770938.1140",
      "outflow": "98134371.9600",
      "net": "1648636566.1540"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "1666044435.8320",
      "outflow": "530097444.0050",
      "net": "1135946991.8270"
    },
    {
      "settlementDate": "2025-11-10",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "132193279.8220",
      "outflow": "643953678.4290",
      "net": "-511760398.6070"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "18270590406.9500",
      "outflow": "16180244755.2900",
      "net": "2090345651.6600"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "1694878839.4330",
      "outflow": "2011442248.1330",
      "net": "-316563408.7000"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "11244372526.5850",
      "outflow": "8351446443.6650",
      "net": "2892926082.9200"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "1062853613.2400",
      "outflow": "2133625024.8170",
      "net": "-1070771411.5770"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "8619514261.8050",
      "outflow": "9064034172.3850",
      "net": "-444519910.5800"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "1749226816.7410",
      "outflow": "1749007668.6040",
      "net": "219148.1370"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "16106344434.8300",
      "outflow": "23452757870.7300",
      "net": "-7346413435.9000"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "10782757902.9700",
      "outflow": "6530571516.3000",
      "net": "4252186386.6700"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "11755809321.8300",
      "outflow": "15617460946.7100",
      "net": "-3861651624.8800"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "16736536740.7800",
      "outflow": "12730899752.8700",
      "net": "4005636987.9100"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "3869003484.2340",
      "outflow": "1961017849.8220",
      "net": "1907985634.4120"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "11863890916.5300",
      "outflow": "7807228349.2800",
      "net": "4056662567.2500"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "6959796001.6700",
      "outflow": "8943763786.0550",
      "net": "-1983967784.3850"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "1370662521.7020",
      "outflow": "2187026089.7770",
      "net": "-816363568.0750"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "2356627352.3200",
      "outflow": "2311455020.3280",
      "net": "45172331.9920"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "6369196767.6620",
      "outflow": "3362140838.9420",
      "net": "3007055928.7200"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "1545349585.1610",
      "outflow": "1290829909.8290",
      "net": "254519675.3320"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "1666034102.3010",
      "outflow": "1642000625.7810",
      "net": "24033476.5200"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "2408199802.5280",
      "outflow": "1280633549.2950",
      "net": "1127566253.2330"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "3464173535.4720",
      "outflow": "6159953727.2460",
      "net": "-2695780191.7740"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "6739168092.4500",
      "outflow": "6989898229.1700",
      "net": "-250730136.7200"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "2776687301.9520",
      "outflow": "5266864492.0840",
      "net": "-2490177190.1320"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "1039314523.3820",
      "outflow": "1321444904.3660",
      "net": "-282130380.9840"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "15221450318.1600",
      "outflow": "26348370349.8800",
      "net": "-11126920031.7200"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "8758536660.9750",
      "outflow": "6736083379.2050",
      "net": "2022453281.7700"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "1632111210.1340",
      "outflow": "1321752200.6930",
      "net": "310359009.4410"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "5197775911.1780",
      "outflow": "2479447445.8740",
      "net": "2718328465.3040"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "1616550037.3530",
      "outflow": "1439379476.7780",
      "net": "177170560.5750"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "1927862146.8720",
      "outflow": "1472476887.6580",
      "net": "455385259.2140"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "14175560299.9300",
      "outflow": "21520044741.8600",
      "net": "-7344484441.9300"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "2191992640.3590",
      "outflow": "2310540306.4210",
      "net": "-118547666.0620"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "15622783986.7800",
      "outflow": "19577256397.7600",
      "net": "-3954472410.9800"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "13251471343.3200",
      "outflow": "11656385343.2750",
      "net": "1595086000.0450"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "3104524065.6060",
      "outflow": "3900135694.2620",
      "net": "-795611628.6560"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "2896896857.0450",
      "outflow": "1377756512.8690",
      "net": "1519140344.1760"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "7067947542.1600",
      "outflow": "7602666321.6000",
      "net": "-534718779.4400"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "2020864041.4360",
      "outflow": "846491444.4710",
      "net": "1174372596.9650"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "2031333744.2390",
      "outflow": "2010824695.1170",
      "net": "20509049.1220"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "6104741044.3050",
      "outflow": "9197656991.3300",
      "net": "-3092915947.0250"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "13670942662.4600",
      "outflow": "18187230206.8600",
      "net": "-4516287544.4000"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "24112098896.2500",
      "outflow": "15996985631.0800",
      "net": "8115113265.1700"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "1723540913.1210",
      "outflow": "784765087.3320",
      "net": "938775825.7890"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "9751360642.0100",
      "outflow": "23127305390.8700",
      "net": "-13375944748.8600"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "9380443100.3900",
      "outflow": "16012181552.9500",
      "net": "-6631738452.5600"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "2075582856.8730",
      "outflow": "1676891694.2490",
      "net": "398691162.6240"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "3103158420.9080",
      "outflow": "3892346538.9780",
      "net": "-789188118.0700"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "2238538753.4800",
      "outflow": "1221545839.8820",
      "net": "1016992913.5980"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "3221691021.4160",
      "outflow": "2498484359.3980",
      "net": "723206662.0180"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "2156832650.8990",
      "outflow": "2391091320.9920",
      "net": "-234258670.0930"
    },
    {
      "settlementDate": "2025-11-11",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "2020576626.9580",
      "outflow": "1179543631.6260",
      "net": "841032995.3320"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "10344347262.1500",
      "outflow": "929673117.0800",
      "net": "9414674145.0700"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "507213971.2840",
      "outflow": "173965610.0700",
      "net": "333248361.2140"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "2142136722.5450",
      "outflow": "1049686621.8300",
      "net": "1092450100.7150"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "668287451.8740",
      "outflow": "0.0000",
      "net": "668287451.8740"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "1383506840.8350",
      "outflow": "431070820.9500",
      "net": "952436019.8850"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "833417263.0180",
      "outflow": "1299293196.7640",
      "net": "-465875933.7460"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "2042055573.2400",
      "outflow": "2244059844.5100",
      "net": "-202004271.2700"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "4106958931.0550",
      "outflow": "4809849731.4050",
      "net": "-702890800.3500"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "7142702086.0400",
      "outflow": "3675883256.1000",
      "net": "3466818829.9400"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "5905539832.8200",
      "outflow": "643049682.1500",
      "net": "5262490150.6700"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "353253146.7900",
      "outflow": "1129009988.6580",
      "net": "-775756841.8680"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "2505429996.2300",
      "outflow": "2304235301.2150",
      "net": "201194695.0150"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "3720348228.7700",
      "outflow": "2468535417.2300",
      "net": "1251812811.5400"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "408387649.6190",
      "outflow": "476800710.7650",
      "net": "-68413061.1460"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "175311885.0980",
      "outflow": "476240847.7200",
      "net": "-300928962.6220"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "485484993.6560",
      "outflow": "556689469.9180",
      "net": "-71204476.2620"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "340311696.0290",
      "outflow": "340680541.1800",
      "net": "-368845.1510"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "541805944.5980",
      "outflow": "491703775.2630",
      "net": "50102169.3350"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "375091478.7190",
      "outflow": "669304811.4290",
      "net": "-294213332.7100"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "119280668.9080",
      "outflow": "1633966555.0560",
      "net": "-1514685886.1480"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "3587685476.1150",
      "outflow": "1073099933.0300",
      "net": "2514585543.0850"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "1127126490.0860",
      "outflow": "864568408.0460",
      "net": "262558082.0400"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "1025125149.8760",
      "outflow": "988525385.0640",
      "net": "36599764.8120"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "2777007341.0300",
      "outflow": "4119756710.7700",
      "net": "-1342749369.7400"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "2713110191.4300",
      "outflow": "3433696870.6500",
      "net": "-720586679.2200"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "350686509.2040",
      "outflow": "510292669.5140",
      "net": "-159606160.3100"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "1231936994.8020",
      "outflow": "3269773231.8440",
      "net": "-2037836237.0420"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "897837405.4030",
      "outflow": "768411442.9010",
      "net": "129425962.5020"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "364354987.3820",
      "outflow": "874131374.2590",
      "net": "-509776386.8770"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "3543225071.5200",
      "outflow": "3643756524.6600",
      "net": "-100531453.1400"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "810255143.9460",
      "outflow": "1190958180.6780",
      "net": "-380703036.7320"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "3685168128.7500",
      "outflow": "4843937187.0200",
      "net": "-1158769058.2700"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "1337305422.5600",
      "outflow": "3756500058.2300",
      "net": "-2419194635.6700"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "294412053.3020",
      "outflow": "1589114818.5780",
      "net": "-1294702765.2760"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "776585869.4720",
      "outflow": "1345939254.1880",
      "net": "-569353384.7160"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "1633568239.6550",
      "outflow": "4376063187.7900",
      "net": "-2742494948.1350"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "419736039.0070",
      "outflow": "368111857.9620",
      "net": "51624181.0450"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "738192517.0590",
      "outflow": "907284396.2900",
      "net": "-169091879.2310"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "6521728406.4050",
      "outflow": "3335813185.1750",
      "net": "3185915221.2300"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "386905309.3800",
      "outflow": "3940359484.9700",
      "net": "-3553454175.5900"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "8840542758.8600",
      "outflow": "3797211911.0100",
      "net": "5043330847.8500"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "124412973.1880",
      "outflow": "377373035.4170",
      "net": "-252960062.2290"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "6099045602.9400",
      "outflow": "7951149438.7600",
      "net": "-1852103835.8200"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "4958035876.4500",
      "outflow": "9861618270.7200",
      "net": "-4903582394.2700"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "639960800.7020",
      "outflow": "547661035.8520",
      "net": "92299764.8500"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "684234443.8940",
      "outflow": "1717804263.2740",
      "net": "-1033569819.3800"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "479191665.7740",
      "outflow": "582509424.7760",
      "net": "-103317759.0020"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "1337456589.2960",
      "outflow": "164271332.4720",
      "net": "1173185256.8240"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "846230537.6050",
      "outflow": "1144602495.3630",
      "net": "-298371957.7580"
    },
    {
      "settlementDate": "2025-11-12",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "284057806.3340",
      "outflow": "43070420.0560",
      "net": "240987386.2780"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "2843985493.5300",
      "outflow": "4847578971.1600",
      "net": "-2003593477.6300"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "796992083.9000",
      "outflow": "972185368.9450",
      "net": "-175193285.0450"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "3087065148.2250",
      "outflow": "1296301026.5250",
      "net": "1790764121.7000"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "1067449473.4900",
      "outflow": "401362098.8840",
      "net": "666087374.6060"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "6294881939.9350",
      "outflow": "2943026436.1200",
      "net": "3351855503.8150"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "960605086.2670",
      "outflow": "631731387.5510",
      "net": "328873698.7160"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "3157702958.8500",
      "outflow": "3997511660.8800",
      "net": "-839808702.0300"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "5126305010.8250",
      "outflow": "3372788017.2200",
      "net": "1753516993.6050"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "2514191763.4500",
      "outflow": "13019265323.1900",
      "net": "-10505073559.7400"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "7501366820.3500",
      "outflow": "328557607.9800",
      "net": "7172809212.3700"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1074655573.5620",
      "outflow": "492754748.6360",
      "net": "581900824.9260"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "7609724231.9750",
      "outflow": "3655746680.5100",
      "net": "3953977551.4650"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "1536551201.8350",
      "outflow": "6107149063.2700",
      "net": "-4570597861.4350"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "463606240.5460",
      "outflow": "463376863.7410",
      "net": "229376.8050"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "291929084.0640",
      "outflow": "226891996.8560",
      "net": "65037087.2080"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "1111670414.5680",
      "outflow": "1019221170.5360",
      "net": "92449244.0320"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "476494105.4830",
      "outflow": "476786440.5700",
      "net": "-292335.0870"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "308497372.0950",
      "outflow": "687441753.3140",
      "net": "-378944381.2190"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "437894575.2820",
      "outflow": "520798404.3330",
      "net": "-82903829.0510"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "827353701.3660",
      "outflow": "1029636334.0680",
      "net": "-202282632.7020"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "2300636897.9500",
      "outflow": "2682108254.5300",
      "net": "-381471356.5800"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "693083660.1420",
      "outflow": "894342316.0360",
      "net": "-201258655.8940"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "716057273.1110",
      "outflow": "890277315.3450",
      "net": "-174220042.2340"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "5527149955.1100",
      "outflow": "6533216539.3500",
      "net": "-1006066584.2400"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "2817423445.7150",
      "outflow": "1612773360.6100",
      "net": "1204650085.1050"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "196062862.0230",
      "outflow": "69363738.1660",
      "net": "126699123.8570"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "1309641288.5840",
      "outflow": "312871762.4160",
      "net": "996769526.1680"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "1050031371.2430",
      "outflow": "658092678.3210",
      "net": "391938692.9220"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "234560952.6680",
      "outflow": "723661756.3800",
      "net": "-489100803.7120"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "10390840488.0800",
      "outflow": "9114115700.1600",
      "net": "1276724787.9200"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "460419981.4520",
      "outflow": "334394806.6070",
      "net": "126025174.8450"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "934891605.9000",
      "outflow": "6424519613.6600",
      "net": "-5489628007.7600"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "2225721542.6400",
      "outflow": "3049247628.7850",
      "net": "-823526086.1450"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "1149044368.7060",
      "outflow": "1340694268.6360",
      "net": "-191649899.9300"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "477707225.4290",
      "outflow": "469611503.7400",
      "net": "8095721.6890"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "2614661123.9550",
      "outflow": "2059590692.4450",
      "net": "555070431.5100"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "470519477.2680",
      "outflow": "625029709.1920",
      "net": "-154510231.9240"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "224000549.0780",
      "outflow": "528143719.1830",
      "net": "-304143170.1050"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "705869874.9000",
      "outflow": "3129088066.0700",
      "net": "-2423218191.1700"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "3041282754.6500",
      "outflow": "9993707754.3200",
      "net": "-6952424999.6700"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "894052379.5600",
      "outflow": "2047061761.0500",
      "net": "-1153009381.4900"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "443666279.9090",
      "outflow": "250111005.9750",
      "net": "193555273.9340"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "9194114929.2400",
      "outflow": "2538757068.5900",
      "net": "6655357860.6500"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "4862110009.7200",
      "outflow": "2850735185.6400",
      "net": "2011374824.0800"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "775093933.0420",
      "outflow": "837592286.6310",
      "net": "-62498353.5890"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "719457515.2660",
      "outflow": "794822500.9500",
      "net": "-75364985.6840"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "489844944.3020",
      "outflow": "240150950.6100",
      "net": "249693993.6920"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "1910104455.1280",
      "outflow": "2132431586.9860",
      "net": "-222327131.8580"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "201654939.5260",
      "outflow": "469915199.1630",
      "net": "-268260259.6370"
    },
    {
      "settlementDate": "2025-11-13",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "289039285.5240",
      "outflow": "505914075.0180",
      "net": "-216874789.4940"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "3702367631.4000",
      "outflow": "2081373602.2300",
      "net": "1620994029.1700"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "537375954.0310",
      "outflow": "748263970.5830",
      "net": "-210888016.5520"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "2565551536.2950",
      "outflow": "7029931618.2650",
      "net": "-4464380081.9700"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "281890681.9450",
      "outflow": "275104846.8590",
      "net": "6785835.0860"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "2640151540.5100",
      "outflow": "2596541492.5000",
      "net": "43610048.0100"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "626097284.3220",
      "outflow": "904121717.4180",
      "net": "-278024433.0960"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "3976471410.0400",
      "outflow": "8188176470.6300",
      "net": "-4211705060.5900"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "2043020371.6550",
      "outflow": "3602257745.4150",
      "net": "-1559237373.7600"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "7479896480.9300",
      "outflow": "2022642286.2900",
      "net": "5457254194.6400"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "4244899203.4100",
      "outflow": "8134813408.9800",
      "net": "-3889914205.5700"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1681264366.0660",
      "outflow": "989594773.3660",
      "net": "691669592.7000"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "1017933816.7950",
      "outflow": "1399910960.6800",
      "net": "-381977143.8850"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "4727298363.3000",
      "outflow": "6313394732.4450",
      "net": "-1586096369.1450"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "475696638.1570",
      "outflow": "537517408.3110",
      "net": "-61820770.1540"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "782915001.1020",
      "outflow": "1496331393.0860",
      "net": "-713416391.9840"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "326859727.4440",
      "outflow": "2654682268.9520",
      "net": "-2327822541.5080"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "245681860.1020",
      "outflow": "575306425.1960",
      "net": "-329624565.0940"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "900649623.0060",
      "outflow": "557982036.3570",
      "net": "342667586.6490"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "337486849.9970",
      "outflow": "449458741.2080",
      "net": "-111971891.2110"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "454369745.3720",
      "outflow": "514515865.7440",
      "net": "-60146120.3720"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "2662779391.2100",
      "outflow": "2383946952.1000",
      "net": "278832439.1100"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "487153071.5120",
      "outflow": "1344472689.6820",
      "net": "-857319618.1700"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "704399122.9740",
      "outflow": "168533688.1750",
      "net": "535865434.7990"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "10750836016.2900",
      "outflow": "5612826511.5100",
      "net": "5138009504.7800"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "1349552108.5800",
      "outflow": "5452075071.2200",
      "net": "-4102522962.6400"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "1125440676.3050",
      "outflow": "761625684.1180",
      "net": "363814992.1870"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "616141052.8060",
      "outflow": "843509365.3700",
      "net": "-227368312.5640"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "474829779.3410",
      "outflow": "165708668.9410",
      "net": "309121110.4000"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "492804212.1350",
      "outflow": "314006770.5860",
      "net": "178797441.5490"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "3316888801.2700",
      "outflow": "6232340584.3600",
      "net": "-2915451783.0900"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "460730124.0400",
      "outflow": "667957879.5550",
      "net": "-207227755.5150"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "7572591592.3800",
      "outflow": "2326622626.5800",
      "net": "5245968965.8000"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "1556230791.5500",
      "outflow": "379229771.2550",
      "net": "1177001020.2950"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "875715705.0860",
      "outflow": "1391102244.8240",
      "net": "-515386539.7380"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "672560565.2370",
      "outflow": "1002553675.8960",
      "net": "-329993110.6590"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "5648145509.2900",
      "outflow": "1951101991.0150",
      "net": "3697043518.2750"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "797697916.6140",
      "outflow": "531649753.4550",
      "net": "266048163.1590"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "787232336.0490",
      "outflow": "1034972964.2200",
      "net": "-247740628.1710"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "3628525967.2450",
      "outflow": "3867052342.0100",
      "net": "-238526374.7650"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "2977501242.0800",
      "outflow": "4183907769.0400",
      "net": "-1206406526.9600"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "3677064501.8200",
      "outflow": "0.0000",
      "net": "3677064501.8200"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "279605197.7150",
      "outflow": "959589695.2940",
      "net": "-679984497.5790"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "6080085294.5200",
      "outflow": "5977058447.3600",
      "net": "103026847.1600"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "1556690266.4700",
      "outflow": "5707089766.0900",
      "net": "-4150399499.6200"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "456212487.3840",
      "outflow": "521103420.0810",
      "net": "-64890932.6970"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "628513975.5300",
      "outflow": "353556789.9720",
      "net": "274957185.5580"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "765018392.7110",
      "outflow": "410250781.7400",
      "net": "354767610.9710"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "1538819839.6300",
      "outflow": "255463617.4100",
      "net": "1283356222.2200"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "876689936.2790",
      "outflow": "373983573.1990",
      "net": "502706363.0800"
    },
    {
      "settlementDate": "2025-11-17",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "636012633.0240",
      "outflow": "846500404.8960",
      "net": "-210487771.8720"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "24738398826.2300",
      "outflow": "17690696354.8600",
      "net": "7047702471.3700"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "2964721152.2350",
      "outflow": "2004848077.4990",
      "net": "959873074.7360"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "15195721162.5300",
      "outflow": "16408485403.4800",
      "net": "-1212764240.9500"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "2750796422.8650",
      "outflow": "1379981867.3100",
      "net": "1370814555.5550"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "10914189193.8850",
      "outflow": "10828430180.0650",
      "net": "85759013.8200"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "1248933001.5330",
      "outflow": "2841085042.2830",
      "net": "-1592152040.7500"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "14055676878.8900",
      "outflow": "26137962747.6900",
      "net": "-12082285868.8000"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "4446198411.2550",
      "outflow": "12218978152.2300",
      "net": "-7772779740.9750"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "22544978760.5100",
      "outflow": "31674502311.1400",
      "net": "-9129523550.6300"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "22470419133.1300",
      "outflow": "38885068173.9500",
      "net": "-16414649040.8200"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "4780469912.1820",
      "outflow": "3733575062.6500",
      "net": "1046894849.5320"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "14131750208.0600",
      "outflow": "11535743274.1000",
      "net": "2596006933.9600"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "14622367002.0900",
      "outflow": "8998251956.1800",
      "net": "5624115045.9100"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "1920723089.8840",
      "outflow": "2395504536.6750",
      "net": "-474781446.7910"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "1615277187.3520",
      "outflow": "5858932343.2240",
      "net": "-4243655155.8720"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "5069652519.3640",
      "outflow": "2271720501.8740",
      "net": "2797932017.4900"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "2357080291.2120",
      "outflow": "3124193150.2350",
      "net": "-767112859.0230"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "2336804700.2420",
      "outflow": "3035753083.8400",
      "net": "-698948383.5980"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "1354216622.9100",
      "outflow": "3415967296.1160",
      "net": "-2061750673.2060"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "5071114140.1560",
      "outflow": "7489437019.5480",
      "net": "-2418322879.3920"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "6401136149.7150",
      "outflow": "8662300712.4950",
      "net": "-2261164562.7800"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "4830067442.3700",
      "outflow": "5185044672.4840",
      "net": "-354977230.1140"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "2287291132.2310",
      "outflow": "1563015230.5150",
      "net": "724275901.7160"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "15435680955.7500",
      "outflow": "20713129990.1000",
      "net": "-5277449034.3500"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "11962107709.7250",
      "outflow": "16005364678.5100",
      "net": "-4043256968.7850"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "1680164772.9810",
      "outflow": "2571832941.0350",
      "net": "-891668168.0540"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "6062399290.6100",
      "outflow": "4717821365.7640",
      "net": "1344577924.8460"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "1912312463.7870",
      "outflow": "1707052521.7440",
      "net": "205259942.0430"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "2835839167.0530",
      "outflow": "3121034846.5010",
      "net": "-285195679.4480"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "29257795581.6500",
      "outflow": "25121405555.8300",
      "net": "4136390025.8200"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "1706861910.3710",
      "outflow": "1972290182.1700",
      "net": "-265428271.7990"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "25834433474.8800",
      "outflow": "34504707515.1100",
      "net": "-8670274040.2300"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "11951865297.8350",
      "outflow": "13449842825.1600",
      "net": "-1497977527.3250"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "4239784566.3720",
      "outflow": "4699904012.8560",
      "net": "-460119446.4840"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "1673583193.6820",
      "outflow": "1781137713.2930",
      "net": "-107554519.6110"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "13639817485.7700",
      "outflow": "7295289322.1950",
      "net": "6344528163.5750"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "1446282955.6670",
      "outflow": "3019512677.9120",
      "net": "-1573229722.2450"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "1747017685.6810",
      "outflow": "2232974333.9030",
      "net": "-485956648.2220"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "4497504505.9500",
      "outflow": "11252460002.6750",
      "net": "-6754955496.7250"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "33450830707.5800",
      "outflow": "25604806364.2800",
      "net": "7846024343.3000"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "15314754133.1000",
      "outflow": "20075036508.9800",
      "net": "-4760282375.8800"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "2583506192.4990",
      "outflow": "2559747263.7250",
      "net": "23758928.7740"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "22650739951.4500",
      "outflow": "28381977704.2900",
      "net": "-5731237752.8400"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "25002066085.3900",
      "outflow": "24254764492.6900",
      "net": "747301592.7000"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "2224841181.1200",
      "outflow": "1100845572.3160",
      "net": "1123995608.8040"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "5513015437.9920",
      "outflow": "3550312675.3500",
      "net": "1962702762.6420"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "2291782720.0510",
      "outflow": "2159786108.4130",
      "net": "131996611.6380"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "7189452535.7480",
      "outflow": "3919772555.4800",
      "net": "3269679980.2680"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "2318695906.3230",
      "outflow": "1960823255.6330",
      "net": "357872650.6900"
    },
    {
      "settlementDate": "2025-11-18",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "2460168253.2740",
      "outflow": "2716770866.5260",
      "net": "-256602613.2520"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "6983702699.1500",
      "outflow": "3474555076.9200",
      "net": "3509147622.2300"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "189487773.2990",
      "outflow": "341379357.4490",
      "net": "-151891584.1500"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "2159107841.2950",
      "outflow": "2158350879.0800",
      "net": "756962.2150"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "743442717.1570",
      "outflow": "579756349.1840",
      "net": "163686367.9730"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "3426219023.6650",
      "outflow": "1560498927.1300",
      "net": "1865720096.5350"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "98798070.6140",
      "outflow": "615099157.4890",
      "net": "-516301086.8750"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "6514462104.3700",
      "outflow": "8182797202.7300",
      "net": "-1668335098.3600"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "4961490444.5900",
      "outflow": "2387628128.7650",
      "net": "2573862315.8250"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "2446509986.4800",
      "outflow": "2645805199.7000",
      "net": "-199295213.2200"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "1917181079.9500",
      "outflow": "4023942149.1900",
      "net": "-2106761069.2400"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1204131034.6180",
      "outflow": "2251678913.5040",
      "net": "-1047547878.8860"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "5147659495.9900",
      "outflow": "3172559741.3050",
      "net": "1975099754.6850"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "1311518218.6500",
      "outflow": "1382449486.9900",
      "net": "-70931268.3400"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "141920188.7800",
      "outflow": "236447413.5980",
      "net": "-94527224.8180"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "789346750.7420",
      "outflow": "2618215053.1180",
      "net": "-1828868302.3760"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "339472066.5520",
      "outflow": "929609163.0900",
      "net": "-590137096.5380"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "1040389343.0280",
      "outflow": "563979989.0300",
      "net": "476409353.9980"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "73103637.3840",
      "outflow": "436763097.5240",
      "net": "-363659460.1400"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "1222571488.4220",
      "outflow": "409668052.7280",
      "net": "812903435.6940"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "915484098.5860",
      "outflow": "748907458.9940",
      "net": "166576639.5920"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "4008218509.5150",
      "outflow": "3726717635.8550",
      "net": "281500873.6600"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "1090387211.4000",
      "outflow": "1780162549.7160",
      "net": "-689775338.3160"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "483752339.6610",
      "outflow": "339888984.2580",
      "net": "143863355.4030"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "4209075190.7100",
      "outflow": "6894902653.4800",
      "net": "-2685827462.7700"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "4099352501.5000",
      "outflow": "1480935784.9050",
      "net": "2618416716.5950"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "1000439918.7410",
      "outflow": "747894103.9510",
      "net": "252545814.7900"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "1089634393.8080",
      "outflow": "1742057685.0040",
      "net": "-652423291.1960"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "181589998.9250",
      "outflow": "302566523.6030",
      "net": "-120976524.6780"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "602972826.2430",
      "outflow": "591767602.3660",
      "net": "11205223.8770"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "6830260250.6300",
      "outflow": "5891704294.6800",
      "net": "938555955.9500"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "239459326.5720",
      "outflow": "292618490.4490",
      "net": "-53159163.8770"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "7351585652.7100",
      "outflow": "2657562310.2800",
      "net": "4694023342.4300"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "629177280.6100",
      "outflow": "2654899180.3150",
      "net": "-2025721899.7050"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "1519222928.2320",
      "outflow": "393225183.5780",
      "net": "1125997744.6540"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "553199835.0280",
      "outflow": "680575942.9350",
      "net": "-127376107.9070"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "5775184644.7700",
      "outflow": "4033687108.8050",
      "net": "1741497535.9650"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "492833110.7280",
      "outflow": "731814443.6800",
      "net": "-238981332.9520"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "1496644233.8640",
      "outflow": "981108372.4790",
      "net": "515535861.3850"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "1826493269.5200",
      "outflow": "1602280749.3450",
      "net": "224212520.1750"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "3445001047.9100",
      "outflow": "9691894962.0000",
      "net": "-6246893914.0900"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "7099269095.1500",
      "outflow": "3917074149.2100",
      "net": "3182194945.9400"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "222926700.7560",
      "outflow": "1079938236.0690",
      "net": "-857011535.3130"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "2866429309.2300",
      "outflow": "17054339384.6900",
      "net": "-14187910075.4600"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "9263555547.5700",
      "outflow": "4755948895.5600",
      "net": "4507606652.0100"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "944460388.1680",
      "outflow": "562791871.4640",
      "net": "381668516.7040"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "372569536.4380",
      "outflow": "757280120.6980",
      "net": "-384710584.2600"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "1231825095.8590",
      "outflow": "442766699.9850",
      "net": "789058395.8740"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "1443647255.1300",
      "net": "-1443647255.1300"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "320361922.8800",
      "outflow": "818645476.0450",
      "net": "-498283553.1650"
    },
    {
      "settlementDate": "2025-11-19",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "127163448.2130",
      "outflow": "125767512.1670",
      "net": "1395936.0460"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "9093371668.2400",
      "outflow": "6410269877.4900",
      "net": "2683101790.7500"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "908893847.5880",
      "outflow": "138957378.1570",
      "net": "769936469.4310"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "1954972336.7700",
      "outflow": "1432605785.5450",
      "net": "522366551.2250"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "239616062.0860",
      "outflow": "398949504.6400",
      "net": "-159333442.5540"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "3255217330.8950",
      "outflow": "1170735190.4250",
      "net": "2084482140.4700"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "176578000.6160",
      "outflow": "598753580.4150",
      "net": "-422175579.7990"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "3810062850.3300",
      "outflow": "9652347552.2700",
      "net": "-5842284701.9400"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "4916562447.0400",
      "outflow": "200953029.4950",
      "net": "4715609417.5450"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "14852321170.2300",
      "outflow": "6211828533.3400",
      "net": "8640492636.8900"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "9012646647.6200",
      "outflow": "6368201617.7700",
      "net": "2644445029.8500"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1955500832.4120",
      "outflow": "497924077.7140",
      "net": "1457576754.6980"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "1042485190.0600",
      "outflow": "1064645889.1800",
      "net": "-22160699.1200"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "1418436881.3400",
      "outflow": "3546938721.9000",
      "net": "-2128501840.5600"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "490614642.0980",
      "outflow": "709274991.0470",
      "net": "-218660348.9490"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "711987830.3780",
      "outflow": "1876677942.4660",
      "net": "-1164690112.0880"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "1256181517.5740",
      "outflow": "822203106.8240",
      "net": "433978410.7500"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "312153171.7850",
      "outflow": "611103879.6640",
      "net": "-298950707.8790"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "336917934.6580",
      "outflow": "256431450.0350",
      "net": "80486484.6230"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "673950445.4740",
      "outflow": "341809790.9820",
      "net": "332140654.4920"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "1053633130.9200",
      "outflow": "329133831.5180",
      "net": "724499299.4020"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "2470865795.5050",
      "outflow": "1105256547.5850",
      "net": "1365609247.9200"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "844935627.9380",
      "outflow": "1348145693.6640",
      "net": "-503210065.7260"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "398765276.6450",
      "outflow": "1045331061.0430",
      "net": "-646565784.3980"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "8073646857.4800",
      "outflow": "10552181404.5400",
      "net": "-2478534547.0600"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "4637190948.8100",
      "outflow": "1075774437.2850",
      "net": "3561416511.5250"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "810812181.4150",
      "outflow": "970278922.2590",
      "net": "-159466740.8440"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "829895361.1840",
      "outflow": "984132026.7080",
      "net": "-154236665.5240"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "276777470.2130",
      "outflow": "176634272.2940",
      "net": "100143197.9190"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "374178524.8890",
      "outflow": "787000034.1610",
      "net": "-412821509.2720"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "3180413421.7900",
      "outflow": "3334096692.6300",
      "net": "-153683270.8400"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "44123899.2690",
      "outflow": "626299105.5830",
      "net": "-582175206.3140"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "1416924670.6400",
      "outflow": "7080160768.8600",
      "net": "-5663236098.2200"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "1873982565.4300",
      "outflow": "3107128007.4400",
      "net": "-1233145442.0100"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "1029051030.4400",
      "outflow": "894769149.5240",
      "net": "134281880.9160"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "807716417.8610",
      "outflow": "427467537.3710",
      "net": "380248880.4900"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "4332582968.2450",
      "outflow": "1171545208.5150",
      "net": "3161037759.7300"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "1219081007.9680",
      "outflow": "113078036.4330",
      "net": "1106002971.5350"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "790675124.3890",
      "outflow": "267833747.9200",
      "net": "522841376.4690"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "1247797154.1000",
      "outflow": "6101246665.4950",
      "net": "-4853449511.3950"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "3416212488.9700",
      "outflow": "3632862707.3100",
      "net": "-216650218.3400"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "10070912321.3700",
      "outflow": "5858716383.6100",
      "net": "4212195937.7600"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "781325889.7470",
      "outflow": "678869460.1180",
      "net": "102456429.6290"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "7452141926.3300",
      "outflow": "1465579261.9500",
      "net": "5986562664.3800"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "4830583173.6000",
      "outflow": "7263808817.8500",
      "net": "-2433225644.2500"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "521430898.7770",
      "outflow": "444731306.7670",
      "net": "76699592.0100"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "1377726917.4000",
      "outflow": "857391374.3580",
      "net": "520335543.0420"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "216249121.3100",
      "outflow": "549366629.2130",
      "net": "-333117507.9030"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "804427158.3640",
      "outflow": "1252775031.2020",
      "net": "-448347872.8380"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "989898628.5140",
      "outflow": "1168669805.1200",
      "net": "-178771176.6060"
    },
    {
      "settlementDate": "2025-11-20",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "412505630.0930",
      "outflow": "775584183.7400",
      "net": "-363078553.6470"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "4057870237.1500",
      "outflow": "5740296525.2600",
      "net": "-1682426288.1100"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "715475484.1630",
      "outflow": "601429799.4660",
      "net": "114045684.6970"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "893745764.0350",
      "outflow": "7538928203.4100",
      "net": "-6645182439.3750"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "319921430.3560",
      "outflow": "674897584.5820",
      "net": "-354976154.2260"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "3224654395.7650",
      "outflow": "2666235185.4300",
      "net": "558419210.3350"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "249571594.7070",
      "outflow": "140550869.5600",
      "net": "109020725.1470"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "6016534409.2600",
      "outflow": "7451320942.6100",
      "net": "-1434786533.3500"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "2415665419.6150",
      "outflow": "2408586966.6100",
      "net": "7078453.0050"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "3055703192.2500",
      "outflow": "2627860748.5300",
      "net": "427842443.7200"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "6837984652.7000",
      "outflow": "3180780366.4800",
      "net": "3657204286.2200"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1085114548.9680",
      "outflow": "926049718.0860",
      "net": "159064830.8820"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "2718247975.0450",
      "outflow": "1410885367.7850",
      "net": "1307362607.2600"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "2720439426.2600",
      "outflow": "993056368.6200",
      "net": "1727383057.6400"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "502181066.7620",
      "outflow": "569070531.1240",
      "net": "-66889464.3620"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "1098552437.4300",
      "outflow": "2202996061.3040",
      "net": "-1104443623.8740"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "1866035611.7420",
      "outflow": "860589539.4660",
      "net": "1005446072.2760"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "842688894.9730",
      "outflow": "870686635.1060",
      "net": "-27997740.1330"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "523139013.8760",
      "outflow": "719679933.0900",
      "net": "-196540919.2140"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "330761609.0850",
      "outflow": "482009266.4890",
      "net": "-151247657.4040"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "1248489949.1000",
      "outflow": "977041428.3200",
      "net": "271448520.7800"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "3646084038.8700",
      "outflow": "1986060684.8400",
      "net": "1660023354.0300"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "3667784534.3420",
      "outflow": "667743042.1060",
      "net": "3000041492.2360"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "231607592.0000",
      "outflow": "22734490.1890",
      "net": "208873101.8110"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "8338042415.3000",
      "outflow": "12852998591.5200",
      "net": "-4514956176.2200"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "1569321343.3550",
      "outflow": "2900169126.8600",
      "net": "-1330847783.5050"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "582891734.2500",
      "outflow": "449519953.1610",
      "net": "133371781.0890"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "159412915.0560",
      "outflow": "882540518.0440",
      "net": "-723127602.9880"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "454127509.0790",
      "outflow": "321337962.3970",
      "net": "132789546.6820"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "261875626.4270",
      "outflow": "546224399.9000",
      "net": "-284348773.4730"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "3833704549.9300",
      "outflow": "6747546852.6700",
      "net": "-2913842302.7400"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "134814403.7280",
      "outflow": "499629848.2720",
      "net": "-364815444.5440"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "4432984240.4500",
      "outflow": "6149098210.4600",
      "net": "-1716113970.0100"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "1377450134.1900",
      "outflow": "4000811657.6650",
      "net": "-2623361523.4750"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "796276532.4740",
      "outflow": "789002549.9520",
      "net": "7273982.5220"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "500819875.6520",
      "outflow": "350211579.1640",
      "net": "150608296.4880"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "338783473.1150",
      "outflow": "1792182972.4350",
      "net": "-1453399499.3200"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "322163382.7330",
      "outflow": "79431718.1930",
      "net": "242731664.5400"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "1189511671.2140",
      "outflow": "382638713.1120",
      "net": "806872958.1020"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "573115669.9500",
      "outflow": "1091889980.9950",
      "net": "-518774311.0450"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "5946827794.1600",
      "outflow": "747097556.9000",
      "net": "5199730237.2600"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "6884275105.3900",
      "outflow": "8278374502.8300",
      "net": "-1394099397.4400"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "834502400.5110",
      "outflow": "398320518.2810",
      "net": "436181882.2300"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "9195657217.5400",
      "outflow": "12675569618.7000",
      "net": "-3479912401.1600"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "8879677505.9600",
      "outflow": "6114960960.2800",
      "net": "2764716545.6800"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "339361197.5390",
      "outflow": "422013022.8020",
      "net": "-82651825.2630"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "1956058772.6280",
      "outflow": "1335568686.8300",
      "net": "620490085.7980"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "358229826.8550",
      "outflow": "422024234.1920",
      "net": "-63794407.3370"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "850991604.0800",
      "outflow": "654453814.3440",
      "net": "196537789.7360"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "636529291.8690",
      "outflow": "514224999.0840",
      "net": "122304292.7850"
    },
    {
      "settlementDate": "2025-11-24",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "646542701.3320",
      "outflow": "1517186507.5670",
      "net": "-870643806.2350"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "18608140799.9000",
      "outflow": "28197076121.4200",
      "net": "-9588935321.5200"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "2969203579.9540",
      "outflow": "2612885176.9260",
      "net": "356318403.0280"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "13891441738.2450",
      "outflow": "14454229226.5700",
      "net": "-562787488.3250"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "2908996216.5650",
      "outflow": "2734661126.6290",
      "net": "174335089.9360"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "8799351771.4300",
      "outflow": "8031680021.4100",
      "net": "767671750.0200"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "2590884399.6970",
      "outflow": "2352447191.5940",
      "net": "238437208.1030"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "25107110401.9000",
      "outflow": "26066758845.2000",
      "net": "-959648443.3000"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "9225231186.1150",
      "outflow": "9109133827.8300",
      "net": "116097358.2850"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "16173645069.0900",
      "outflow": "21432083002.4600",
      "net": "-5258437933.3700"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "20153415601.0000",
      "outflow": "36133865228.0100",
      "net": "-15980449627.0100"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "4058135682.1940",
      "outflow": "4425484716.5620",
      "net": "-367349034.3680"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "5421495010.8200",
      "outflow": "11136689775.6900",
      "net": "-5715194764.8700"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "10707500036.6850",
      "outflow": "9204637622.4650",
      "net": "1502862414.2200"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "1979362545.1330",
      "outflow": "1559191251.4590",
      "net": "420171293.6740"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "5935668878.4620",
      "outflow": "3086408350.2920",
      "net": "2849260528.1700"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "5020639399.5620",
      "outflow": "3355656944.5240",
      "net": "1664982455.0380"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "3559775015.9790",
      "outflow": "1502833424.4290",
      "net": "2056941591.5500"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "1752918902.4370",
      "outflow": "2416210934.4720",
      "net": "-663292032.0350"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "1966263374.0380",
      "outflow": "2473613411.4060",
      "net": "-507350037.3680"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "2640380183.0380",
      "outflow": "4666398996.4340",
      "net": "-2026018813.3960"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "13608072810.1500",
      "outflow": "10742109570.6100",
      "net": "2865963239.5400"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "3137866841.0860",
      "outflow": "4060325818.0000",
      "net": "-922458976.9140"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "2109563131.4270",
      "outflow": "3394974034.7850",
      "net": "-1285410903.3580"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "21303586350.3100",
      "outflow": "27637836415.0600",
      "net": "-6334250064.7500"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "11475095994.1900",
      "outflow": "10314727588.8750",
      "net": "1160368405.3150"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "2672566202.9070",
      "outflow": "2210732667.0800",
      "net": "461833535.8270"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "3570529687.7920",
      "outflow": "3458793627.0480",
      "net": "111736060.7440"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "3418581452.7370",
      "outflow": "1996220695.4430",
      "net": "1422360757.2940"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "2463136836.0060",
      "outflow": "1554176651.4050",
      "net": "908960184.6010"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "24326685476.0600",
      "outflow": "18892550082.9800",
      "net": "5434135393.0800"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "1288890830.1410",
      "outflow": "2013172206.3020",
      "net": "-724281376.1610"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "15929290989.2400",
      "outflow": "31536228215.8400",
      "net": "-15606937226.6000"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "12381250919.8450",
      "outflow": "6741887804.7300",
      "net": "5639363115.1150"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "7340333223.0300",
      "outflow": "5231746455.4600",
      "net": "2108586767.5700"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "2413753100.9060",
      "outflow": "2281758858.7790",
      "net": "131994242.1270"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "12723090348.7100",
      "outflow": "10408011337.0550",
      "net": "2315079011.6550"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "2187345230.0630",
      "outflow": "1947984526.1290",
      "net": "239360703.9340"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "2224193771.3270",
      "outflow": "2466347561.1050",
      "net": "-242153789.7780"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "7681139704.6300",
      "outflow": "11069915856.2400",
      "net": "-3388776151.6100"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "21824991342.2300",
      "outflow": "12291965123.2400",
      "net": "9533026218.9900"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "15370751406.1000",
      "outflow": "9103392597.5800",
      "net": "6267358808.5200"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "1619999917.1880",
      "outflow": "2077341972.1700",
      "net": "-457342054.9820"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "30772395475.3000",
      "outflow": "22537173847.0100",
      "net": "8235221628.2900"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "25921064232.9000",
      "outflow": "14686577181.6900",
      "net": "11234487051.2100"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "2356903266.4310",
      "outflow": "2627982593.8680",
      "net": "-271079327.4370"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "6729900039.7300",
      "outflow": "5155748316.9620",
      "net": "1574151722.7680"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "3675632932.0960",
      "outflow": "3291745178.1000",
      "net": "383887753.9960"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "3196875015.5700",
      "outflow": "2720292278.9440",
      "net": "476582736.6260"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "2258642552.5390",
      "outflow": "2319054644.1120",
      "net": "-60412091.5730"
    },
    {
      "settlementDate": "2025-11-25",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "3381959164.9490",
      "outflow": "2683747253.0140",
      "net": "698211911.9350"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "3955307100.4200",
      "outflow": "3801726884.6300",
      "net": "153580215.7900"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "145414566.3110",
      "outflow": "1241410152.5680",
      "net": "-1095995586.2570"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "3771849630.9800",
      "outflow": "1626849607.3350",
      "net": "2145000023.6450"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "286913088.0350",
      "outflow": "692197377.8700",
      "net": "-405284289.8350"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "1826091803.6800",
      "outflow": "2118997300.6700",
      "net": "-292905496.9900"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "404465937.4350",
      "outflow": "318796504.3230",
      "net": "85669433.1120"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "10493472217.8900",
      "outflow": "1823789406.6300",
      "net": "8669682811.2600"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "3546831298.3850",
      "outflow": "1618296262.7100",
      "net": "1928535035.6750"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "4180127263.1300",
      "outflow": "3088613713.0100",
      "net": "1091513550.1200"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "6846116728.2200",
      "outflow": "3690506114.4500",
      "net": "3155610613.7700"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1594577407.6180",
      "outflow": "2009080400.8780",
      "net": "-414502993.2600"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "4521116949.9900",
      "net": "-4521116949.9900"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "2951004995.3450",
      "outflow": "2722725561.7700",
      "net": "228279433.5750"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "428096130.1780",
      "outflow": "829721638.1120",
      "net": "-401625507.9340"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "690334744.4400",
      "outflow": "96847644.2520",
      "net": "593487100.1880"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "1774575773.7740",
      "outflow": "2476716829.9000",
      "net": "-702141056.1260"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "487719840.5920",
      "outflow": "660585522.7120",
      "net": "-172865682.1200"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "773993759.2700",
      "outflow": "298431979.0770",
      "net": "475561780.1930"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "273028190.0180",
      "outflow": "553411919.9140",
      "net": "-280383729.8960"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "709365250.1300",
      "outflow": "1550645856.1540",
      "net": "-841280606.0240"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "2425947249.9950",
      "outflow": "2914171588.2150",
      "net": "-488224338.2200"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "704088091.5620",
      "outflow": "1068188854.4040",
      "net": "-364100762.8420"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "519482301.8200",
      "outflow": "261267772.4990",
      "net": "258214529.3210"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "4442829788.9900",
      "outflow": "4125329654.9900",
      "net": "317500134.0000"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "3927341503.6200",
      "outflow": "440085849.3050",
      "net": "3487255654.3150"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "251087786.3220",
      "outflow": "1013709752.5070",
      "net": "-762621966.1850"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "945194461.3860",
      "outflow": "898863793.5300",
      "net": "46330667.8560"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "610817920.5770",
      "outflow": "367088013.9610",
      "net": "243729906.6160"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "561974799.0860",
      "outflow": "775016437.2510",
      "net": "-213041638.1650"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "4345092544.8100",
      "outflow": "4359702244.2700",
      "net": "-14609699.4600"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "1417845884.9040",
      "outflow": "865073201.9050",
      "net": "552772682.9990"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "7636816498.6200",
      "outflow": "2727581196.9900",
      "net": "4909235301.6300"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "3290629262.1500",
      "outflow": "3723794499.3350",
      "net": "-433165237.1850"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "2236802172.2120",
      "outflow": "1160309014.9320",
      "net": "1076493157.2800"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "773286465.0340",
      "outflow": "284993652.8130",
      "net": "488292812.2210"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "3460918783.2450",
      "outflow": "1766788107.7800",
      "net": "1694130675.4650"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "868495081.1430",
      "outflow": "528126643.1120",
      "net": "340368438.0310"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "311714935.4090",
      "outflow": "1116038158.2010",
      "net": "-804323222.7920"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "650674452.5850",
      "outflow": "3603288892.7100",
      "net": "-2952614440.1250"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "9837857785.9100",
      "outflow": "1013732926.4700",
      "net": "8824124859.4400"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "4197875478.1700",
      "outflow": "8893033023.6700",
      "net": "-4695157545.5000"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "754357333.0410",
      "outflow": "733838930.8680",
      "net": "20518402.1730"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "2115701503.0800",
      "outflow": "6305761663.2200",
      "net": "-4190060160.1400"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "2643684326.7200",
      "outflow": "5334384287.9800",
      "net": "-2690699961.2600"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "224714770.6790",
      "outflow": "523847335.8650",
      "net": "-299132565.1860"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "1961429285.9780",
      "outflow": "960451216.7640",
      "net": "1000978069.2140"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "196156023.8070",
      "outflow": "728411402.0770",
      "net": "-532255378.2700"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "1192188545.5620",
      "outflow": "1524299832.1520",
      "net": "-332111286.5900"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "587997755.8150",
      "outflow": "556779138.2440",
      "net": "31218617.5710"
    },
    {
      "settlementDate": "2025-11-26",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "923431837.2960",
      "outflow": "273975468.9260",
      "net": "649456368.3700"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "5181712516.2300",
      "outflow": "7798957467.9700",
      "net": "-2617244951.7400"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "777832047.9140",
      "outflow": "911737702.5390",
      "net": "-133905654.6250"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "887067567.3150",
      "outflow": "2502064870.0100",
      "net": "-1614997302.6950"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "926680925.2850",
      "outflow": "1548429603.6500",
      "net": "-621748678.3650"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "3413029538.1500",
      "outflow": "3990710142.8650",
      "net": "-577680604.7150"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "459229520.4530",
      "outflow": "55288708.6920",
      "net": "403940811.7610"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "5937811059.1800",
      "outflow": "11312977429.6700",
      "net": "-5375166370.4900"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "1381678766.4450",
      "outflow": "1332082787.5800",
      "net": "49595978.8650"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "5874220128.1800",
      "outflow": "10417901988.1900",
      "net": "-4543681860.0100"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "4533426756.3500",
      "outflow": "5365200679.5200",
      "net": "-831773923.1700"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1252088739.8300",
      "outflow": "154648651.8060",
      "net": "1097440088.0240"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "1027875762.2600",
      "outflow": "2210505373.2750",
      "net": "-1182629611.0150"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "4251355915.5800",
      "outflow": "3697826538.2000",
      "net": "553529377.3800"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "648586493.9110",
      "outflow": "227002209.4310",
      "net": "421584284.4800"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "305153759.1180",
      "outflow": "902125064.6300",
      "net": "-596971305.5120"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "2182919591.7880",
      "outflow": "1592457307.2540",
      "net": "590462284.5340"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "554685177.6890",
      "outflow": "708996907.2260",
      "net": "-154311729.5370"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "668144019.6360",
      "outflow": "438189837.2570",
      "net": "229954182.3790"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "189015068.9670",
      "outflow": "115113456.3920",
      "net": "73901612.5750"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "1673854269.6580",
      "outflow": "640205665.5220",
      "net": "1033648604.1360"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "1936157648.3100",
      "outflow": "2272059517.3800",
      "net": "-335901869.0700"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "243774382.1380",
      "outflow": "1110822398.4800",
      "net": "-867048016.3420"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "735565055.9900",
      "outflow": "662125691.0330",
      "net": "73439364.9570"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "3145627816.4300",
      "outflow": "3474042891.3900",
      "net": "-328415074.9600"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "2001288942.5350",
      "outflow": "2537324749.0800",
      "net": "-536035806.5450"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "968887841.8420",
      "outflow": "608995199.2350",
      "net": "359892642.6070"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "1189030582.1600",
      "outflow": "1281889042.6220",
      "net": "-92858460.4620"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "492506870.4100",
      "outflow": "669912781.2870",
      "net": "-177405910.8770"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "161286986.7230",
      "outflow": "445129428.2800",
      "net": "-283842441.5570"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "2815095388.7900",
      "outflow": "11599466299.7300",
      "net": "-8784370910.9400"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "173560881.9810",
      "outflow": "752444167.5380",
      "net": "-578883285.5570"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "5457517175.7800",
      "outflow": "5197828694.0400",
      "net": "259688481.7400"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "2539355549.2600",
      "outflow": "3728386519.5400",
      "net": "-1189030970.2800"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "2268102328.2420",
      "outflow": "291963194.3040",
      "net": "1976139133.9380"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "990024979.8510",
      "outflow": "291943628.2800",
      "net": "698081351.5710"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "1473292795.9550",
      "outflow": "6799867112.8050",
      "net": "-5326574316.8500"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "976121667.6780",
      "outflow": "187424932.9880",
      "net": "788696734.6900"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "365264891.7850",
      "outflow": "843459484.0380",
      "net": "-478194592.2530"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "3526900729.0050",
      "outflow": "4167566464.1050",
      "net": "-640665735.1000"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "17273732717.6100",
      "outflow": "11780822119.7900",
      "net": "5492910597.8200"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "4428156010.0600",
      "outflow": "8354092421.8300",
      "net": "-3925936411.7700"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "730102553.1530",
      "outflow": "501175561.1070",
      "net": "228926992.0460"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "1383587114.4100",
      "outflow": "6027525421.0000",
      "net": "-4643938306.5900"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "11691937605.0200",
      "outflow": "5166268910.2800",
      "net": "6525668694.7400"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "812960366.8110",
      "outflow": "462655980.6150",
      "net": "350304386.1960"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "368000398.2880",
      "outflow": "159158512.9380",
      "net": "208841885.3500"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "432269863.5940",
      "outflow": "591519896.6790",
      "net": "-159250033.0850"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "798620931.1960",
      "outflow": "1416924768.8800",
      "net": "-618303837.6840"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "854790276.7870",
      "outflow": "764247760.3540",
      "net": "90542516.4330"
    },
    {
      "settlementDate": "2025-11-27",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "540445790.7080",
      "outflow": "641762373.0580",
      "net": "-101316582.3500"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "1141310777.2200",
      "outflow": "1656219604.0000",
      "net": "-514908826.7800"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "1229314838.9560",
      "outflow": "443133210.4750",
      "net": "786181628.4810"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "4428315776.9650",
      "outflow": "2808266218.1000",
      "net": "1620049558.8650"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "367081104.8190",
      "outflow": "463567549.1960",
      "net": "-96486444.3770"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "2435123060.3900",
      "outflow": "3902057632.2050",
      "net": "-1466934571.8150"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "197970015.6510",
      "outflow": "559112059.3730",
      "net": "-361142043.7220"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "15306167665.2500",
      "outflow": "6727711430.2500",
      "net": "8578456235.0000"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "3121352433.7950",
      "outflow": "6561190954.7750",
      "net": "-3439838520.9800"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "4813978379.0800",
      "outflow": "2303998115.4600",
      "net": "2509980263.6200"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "4042735279.5900",
      "outflow": "8787538721.2600",
      "net": "-4744803441.6700"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "1050463574.2320",
      "outflow": "2253917870.2480",
      "net": "-1203454296.0160"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "2386105631.8250",
      "outflow": "1149704876.8800",
      "net": "1236400754.9450"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "2347239087.5200",
      "outflow": "3121327550.3900",
      "net": "-774088462.8700"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "616809150.0600",
      "outflow": "366870304.7500",
      "net": "249938845.3100"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "1041741114.1660",
      "outflow": "1400564989.3200",
      "net": "-358823875.1540"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "1302568621.9940",
      "outflow": "527354870.3520",
      "net": "775213751.6420"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "105178344.4040",
      "outflow": "906135003.0010",
      "net": "-800956658.5970"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "879831374.0970",
      "outflow": "152014307.1710",
      "net": "727817066.9260"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "139772289.1010",
      "outflow": "346915020.6500",
      "net": "-207142731.5490"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "770443174.1220",
      "outflow": "1223490927.4460",
      "net": "-453047753.3240"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "2114664950.0850",
      "outflow": "2123485164.3400",
      "net": "-8820214.2550"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "1054038576.8640",
      "outflow": "660497614.1680",
      "net": "393540962.6960"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "551663895.5150",
      "outflow": "609455465.9190",
      "net": "-57791570.4040"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "5872235740.9000",
      "outflow": "2329082174.3500",
      "net": "3543153566.5500"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "3419894311.3800",
      "outflow": "3975207037.2650",
      "net": "-555312725.8850"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "639772891.1090",
      "outflow": "461148741.4500",
      "net": "178624149.6590"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "1221998380.0720",
      "outflow": "354105908.3880",
      "net": "867892471.6840"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "331919558.7510",
      "outflow": "627226772.9200",
      "net": "-295307214.1690"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "702497252.2240",
      "outflow": "308937494.6010",
      "net": "393559757.6230"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "2314307699.5900",
      "outflow": "7141993733.5800",
      "net": "-4827686033.9900"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "43276957.8250",
      "outflow": "149047895.3960",
      "net": "-105770937.5710"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "2509402833.1900",
      "outflow": "5978612062.1200",
      "net": "-3469209228.9300"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "3991125536.2650",
      "outflow": "1058157858.1200",
      "net": "2932967678.1450"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "1414770249.7120",
      "outflow": "1588359013.4160",
      "net": "-173588763.7040"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "181417272.2390",
      "outflow": "352543782.9300",
      "net": "-171126510.6910"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "2013572781.7900",
      "outflow": "6371298847.9900",
      "net": "-4357726066.2000"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "535056697.8040",
      "outflow": "624201742.1950",
      "net": "-89145044.3910"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "776630471.2180",
      "outflow": "1143687056.8620",
      "net": "-367056585.6440"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "4068937259.1150",
      "outflow": "3211013499.1850",
      "net": "857923759.9300"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "4191846434.4900",
      "outflow": "5097740616.9700",
      "net": "-905894182.4800"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "4297029629.3900",
      "outflow": "6182158714.5500",
      "net": "-1885129085.1600"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "181745316.6530",
      "outflow": "1245857962.2030",
      "net": "-1064112645.5500"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "7375916977.4600",
      "outflow": "4584367040.1900",
      "net": "2791549937.2700"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "5021607817.5600",
      "outflow": "6318713765.6000",
      "net": "-1297105948.0400"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "580767596.2590",
      "outflow": "673231828.7300",
      "net": "-92464232.4710"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "2141593537.8620",
      "outflow": "906619855.5280",
      "net": "1234973682.3340"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "579742873.9440",
      "outflow": "101193267.7800",
      "net": "478549606.1640"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "1063675418.8920",
      "outflow": "357826255.7580",
      "net": "705849163.1340"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "352445589.5540",
      "outflow": "385002379.8070",
      "net": "-32556790.2530"
    },
    {
      "settlementDate": "2025-11-28",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "433264613.2920",
      "outflow": "1146637343.5720",
      "net": "-713372730.2800"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF004",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF008",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF012",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF016",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF020",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF024",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF028",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF032",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF036",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF040",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF044",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF048",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF005",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF009",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF013",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF017",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF021",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF025",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF029",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF033",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF037",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF041",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF045",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF049",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF003",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF007",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF011",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF015",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF019",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF023",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF027",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF031",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF035",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF039",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF043",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF047",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF002",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF010",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF014",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF018",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF022",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF026",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF030",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF034",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF038",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF042",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF046",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "productId": "ETF050",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    }
  ],
  "totalsByDateCurrency": [
    {
      "settlementDate": "2025-11-01",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-01",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-02",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-03",
      "currency": "CNH",
      "inflow": "36022834918.7580",
      "outflow": "31106250676.9670",
      "net": "4916584241.7910"
    },
    {
      "settlementDate": "2025-11-03",
      "currency": "HKD",
      "inflow": "23756126313.1810",
      "outflow": "19135200710.6620",
      "net": "4620925602.5190"
    },
    {
      "settlementDate": "2025-11-03",
      "currency": "RMB",
      "inflow": "25954014488.3580",
      "outflow": "28947761282.8160",
      "net": "-2993746794.4580"
    },
    {
      "settlementDate": "2025-11-03",
      "currency": "USD",
      "inflow": "30480135772.2730",
      "outflow": "27281142971.3480",
      "net": "3198992800.9250"
    },
    {
      "settlementDate": "2025-11-04",
      "currency": "CNH",
      "inflow": "148261821493.7400",
      "outflow": "99233922214.0570",
      "net": "49027899279.6830"
    },
    {
      "settlementDate": "2025-11-04",
      "currency": "HKD",
      "inflow": "66203726952.2820",
      "outflow": "67773317172.1990",
      "net": "-1569590219.9170"
    },
    {
      "settlementDate": "2025-11-04",
      "currency": "RMB",
      "inflow": "64729987363.1730",
      "outflow": "80673418301.5250",
      "net": "-15943430938.3520"
    },
    {
      "settlementDate": "2025-11-04",
      "currency": "USD",
      "inflow": "89484521876.1810",
      "outflow": "105541384090.7620",
      "net": "-16056862214.5810"
    },
    {
      "settlementDate": "2025-11-05",
      "currency": "CNH",
      "inflow": "42692056137.1420",
      "outflow": "44830839534.2910",
      "net": "-2138783397.1490"
    },
    {
      "settlementDate": "2025-11-05",
      "currency": "HKD",
      "inflow": "19416246699.8700",
      "outflow": "19199469534.9110",
      "net": "216777164.9590"
    },
    {
      "settlementDate": "2025-11-05",
      "currency": "RMB",
      "inflow": "19564322677.5890",
      "outflow": "20602752039.7680",
      "net": "-1038429362.1790"
    },
    {
      "settlementDate": "2025-11-05",
      "currency": "USD",
      "inflow": "28485141640.0320",
      "outflow": "33585530663.5330",
      "net": "-5100389023.5010"
    },
    {
      "settlementDate": "2025-11-06",
      "currency": "CNH",
      "inflow": "38346946753.4180",
      "outflow": "42842596452.0250",
      "net": "-4495649698.6070"
    },
    {
      "settlementDate": "2025-11-06",
      "currency": "HKD",
      "inflow": "25385738228.9190",
      "outflow": "29836642368.7250",
      "net": "-4450904139.8060"
    },
    {
      "settlementDate": "2025-11-06",
      "currency": "RMB",
      "inflow": "13493860275.8300",
      "outflow": "27959476261.2720",
      "net": "-14465615985.4420"
    },
    {
      "settlementDate": "2025-11-06",
      "currency": "USD",
      "inflow": "28820020976.5760",
      "outflow": "30511110647.1770",
      "net": "-1691089670.6010"
    },
    {
      "settlementDate": "2025-11-07",
      "currency": "CNH",
      "inflow": "30928575674.4480",
      "outflow": "32215337363.6980",
      "net": "-1286761689.2500"
    },
    {
      "settlementDate": "2025-11-07",
      "currency": "HKD",
      "inflow": "28200060210.0670",
      "outflow": "17806647763.6650",
      "net": "10393412446.4020"
    },
    {
      "settlementDate": "2025-11-07",
      "currency": "RMB",
      "inflow": "15313566022.0000",
      "outflow": "21542090619.3870",
      "net": "-6228524597.3870"
    },
    {
      "settlementDate": "2025-11-07",
      "currency": "USD",
      "inflow": "40398955478.8180",
      "outflow": "20096052642.6070",
      "net": "20302902836.2110"
    },
    {
      "settlementDate": "2025-11-08",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-08",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-09",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-10",
      "currency": "CNH",
      "inflow": "44402274690.9990",
      "outflow": "37071631881.8200",
      "net": "7330642809.1790"
    },
    {
      "settlementDate": "2025-11-10",
      "currency": "HKD",
      "inflow": "14421116568.5480",
      "outflow": "26486938511.9180",
      "net": "-12065821943.3700"
    },
    {
      "settlementDate": "2025-11-10",
      "currency": "RMB",
      "inflow": "25611218584.2050",
      "outflow": "17013119783.9200",
      "net": "8598098800.2850"
    },
    {
      "settlementDate": "2025-11-10",
      "currency": "USD",
      "inflow": "29357192771.5180",
      "outflow": "34629665974.4350",
      "net": "-5272473202.9170"
    },
    {
      "settlementDate": "2025-11-11",
      "currency": "CNH",
      "inflow": "113755779265.9280",
      "outflow": "107589736598.6060",
      "net": "6166042667.3220"
    },
    {
      "settlementDate": "2025-11-11",
      "currency": "HKD",
      "inflow": "60675196565.7350",
      "outflow": "73840464901.9580",
      "net": "-13165268336.2230"
    },
    {
      "settlementDate": "2025-11-11",
      "currency": "RMB",
      "inflow": "70706340082.1730",
      "outflow": "75504332773.5210",
      "net": "-4797992691.3480"
    },
    {
      "settlementDate": "2025-11-11",
      "currency": "USD",
      "inflow": "81590841333.3090",
      "outflow": "98176852940.6640",
      "net": "-16586011607.3550"
    },
    {
      "settlementDate": "2025-11-12",
      "currency": "CNH",
      "inflow": "37934849077.8810",
      "outflow": "18689777170.7320",
      "net": "19245071907.1490"
    },
    {
      "settlementDate": "2025-11-12",
      "currency": "HKD",
      "inflow": "17396077193.9340",
      "outflow": "17593569436.1210",
      "net": "-197492242.1870"
    },
    {
      "settlementDate": "2025-11-12",
      "currency": "RMB",
      "inflow": "15345071865.0030",
      "outflow": "26536989787.6240",
      "net": "-11191917922.6210"
    },
    {
      "settlementDate": "2025-11-12",
      "currency": "USD",
      "inflow": "31939995287.8870",
      "outflow": "34370728694.1350",
      "net": "-2430733406.2480"
    },
    {
      "settlementDate": "2025-11-13",
      "currency": "CNH",
      "inflow": "42034925584.3590",
      "outflow": "35958809327.6010",
      "net": "6076116256.7580"
    },
    {
      "settlementDate": "2025-11-13",
      "currency": "HKD",
      "inflow": "17508347927.2670",
      "outflow": "23144019812.5590",
      "net": "-5635671885.2920"
    },
    {
      "settlementDate": "2025-11-13",
      "currency": "RMB",
      "inflow": "21514102287.9480",
      "outflow": "25181193858.5080",
      "net": "-3667091570.5600"
    },
    {
      "settlementDate": "2025-11-13",
      "currency": "USD",
      "inflow": "23750291849.8450",
      "outflow": "26318431160.1860",
      "net": "-2568139310.3410"
    },
    {
      "settlementDate": "2025-11-14",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-14",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-15",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-16",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-17",
      "currency": "CNH",
      "inflow": "30796920277.3990",
      "outflow": "37972732893.2160",
      "net": "-7175812615.8170"
    },
    {
      "settlementDate": "2025-11-17",
      "currency": "HKD",
      "inflow": "24205677519.0460",
      "outflow": "28061043783.9860",
      "net": "-3855366264.9400"
    },
    {
      "settlementDate": "2025-11-17",
      "currency": "RMB",
      "inflow": "23609776726.0540",
      "outflow": "16567409015.9550",
      "net": "7042367710.0990"
    },
    {
      "settlementDate": "2025-11-17",
      "currency": "USD",
      "inflow": "23887972070.4570",
      "outflow": "24490529571.3120",
      "net": "-602557500.8550"
    },
    {
      "settlementDate": "2025-11-18",
      "currency": "CNH",
      "inflow": "140242253063.3050",
      "outflow": "175339356647.2570",
      "net": "-35097103583.9520"
    },
    {
      "settlementDate": "2025-11-18",
      "currency": "HKD",
      "inflow": "75263518943.0010",
      "outflow": "88718615171.7960",
      "net": "-13455096228.7950"
    },
    {
      "settlementDate": "2025-11-18",
      "currency": "RMB",
      "inflow": "102241140160.6580",
      "outflow": "103961831479.5700",
      "net": "-1720691318.9120"
    },
    {
      "settlementDate": "2025-11-18",
      "currency": "USD",
      "inflow": "127244375296.1580",
      "outflow": "129770077704.2610",
      "net": "-2525702408.1030"
    },
    {
      "settlementDate": "2025-11-19",
      "currency": "CNH",
      "inflow": "35792192271.1780",
      "outflow": "31394051082.4460",
      "net": "4398141188.7320"
    },
    {
      "settlementDate": "2025-11-19",
      "currency": "HKD",
      "inflow": "19724591544.9300",
      "outflow": "21548647323.2860",
      "net": "-1824055778.3560"
    },
    {
      "settlementDate": "2025-11-19",
      "currency": "RMB",
      "inflow": "26265560166.9970",
      "outflow": "20720372869.6460",
      "net": "5545187297.3510"
    },
    {
      "settlementDate": "2025-11-19",
      "currency": "USD",
      "inflow": "29216699595.5580",
      "outflow": "43233483684.8420",
      "net": "-14016784089.2840"
    },
    {
      "settlementDate": "2025-11-20",
      "currency": "CNH",
      "inflow": "51218228383.8870",
      "outflow": "34146172016.4410",
      "net": "17072056367.4460"
    },
    {
      "settlementDate": "2025-11-20",
      "currency": "HKD",
      "inflow": "22679280060.6050",
      "outflow": "23620262858.5530",
      "net": "-940982797.9480"
    },
    {
      "settlementDate": "2025-11-20",
      "currency": "RMB",
      "inflow": "16195539519.3440",
      "outflow": "19672589761.7780",
      "net": "-3477050242.4340"
    },
    {
      "settlementDate": "2025-11-20",
      "currency": "USD",
      "inflow": "32911886432.9640",
      "outflow": "30317435374.6530",
      "net": "2594451058.3110"
    },
    {
      "settlementDate": "2025-11-21",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-21",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-22",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-23",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-24",
      "currency": "CNH",
      "inflow": "31590489104.0140",
      "outflow": "35367822277.8090",
      "net": "-3777333173.7950"
    },
    {
      "settlementDate": "2025-11-24",
      "currency": "HKD",
      "inflow": "26585127933.0950",
      "outflow": "26104835699.0340",
      "net": "480292234.0610"
    },
    {
      "settlementDate": "2025-11-24",
      "currency": "RMB",
      "inflow": "13195304377.0840",
      "outflow": "22607538222.3130",
      "net": "-9412233845.2290"
    },
    {
      "settlementDate": "2025-11-24",
      "currency": "USD",
      "inflow": "38291280759.0280",
      "outflow": "34554323115.9170",
      "net": "3736957643.1110"
    },
    {
      "settlementDate": "2025-11-25",
      "currency": "CNH",
      "inflow": "129907051456.9100",
      "outflow": "166686994260.3010",
      "net": "-36779942803.3910"
    },
    {
      "settlementDate": "2025-11-25",
      "currency": "HKD",
      "inflow": "85196693462.4970",
      "outflow": "84414924362.8110",
      "net": "781769099.6860"
    },
    {
      "settlementDate": "2025-11-25",
      "currency": "RMB",
      "inflow": "90715454297.4370",
      "outflow": "88273263128.2510",
      "net": "2442191169.1860"
    },
    {
      "settlementDate": "2025-11-25",
      "currency": "USD",
      "inflow": "127014448820.9900",
      "outflow": "93031284404.0350",
      "net": "33983164416.9550"
    },
    {
      "settlementDate": "2025-11-26",
      "currency": "CNH",
      "inflow": "37051167042.1040",
      "outflow": "26551380675.0640",
      "net": "10499786367.0400"
    },
    {
      "settlementDate": "2025-11-26",
      "currency": "HKD",
      "inflow": "20107807619.7340",
      "outflow": "17998130671.3040",
      "net": "2109676948.4300"
    },
    {
      "settlementDate": "2025-11-26",
      "currency": "RMB",
      "inflow": "26398961659.4890",
      "outflow": "18471046558.3860",
      "net": "7927915101.1030"
    },
    {
      "settlementDate": "2025-11-26",
      "currency": "USD",
      "inflow": "25597784034.0520",
      "outflow": "31567842277.1470",
      "net": "-5970058243.0950"
    },
    {
      "settlementDate": "2025-11-27",
      "currency": "CNH",
      "inflow": "31652653327.5920",
      "outflow": "47600505405.7670",
      "net": "-15947852078.1750"
    },
    {
      "settlementDate": "2025-11-27",
      "currency": "HKD",
      "inflow": "18536128141.7500",
      "outflow": "18378292233.2750",
      "net": "157835908.4750"
    },
    {
      "settlementDate": "2025-11-27",
      "currency": "RMB",
      "inflow": "19504783048.6720",
      "outflow": "31855251000.6490",
      "net": "-12350467951.9770"
    },
    {
      "settlementDate": "2025-11-27",
      "currency": "USD",
      "inflow": "43206769248.4270",
      "outflow": "40877179674.6740",
      "net": "2329589573.7530"
    },
    {
      "settlementDate": "2025-11-28",
      "currency": "CNH",
      "inflow": "40519918537.7730",
      "outflow": "37616418242.2220",
      "net": "2903500295.5510"
    },
    {
      "settlementDate": "2025-11-28",
      "currency": "HKD",
      "inflow": "20216080630.2080",
      "outflow": "17742400429.1220",
      "net": "2473680201.0860"
    },
    {
      "settlementDate": "2025-11-28",
      "currency": "RMB",
      "inflow": "15899118110.5710",
      "outflow": "25015633853.1060",
      "net": "-9116515742.5350"
    },
    {
      "settlementDate": "2025-11-28",
      "currency": "USD",
      "inflow": "31065203535.6890",
      "outflow": "31354049586.7350",
      "net": "-288846051.0460"
    },
    {
      "settlementDate": "2025-11-29",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-29",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "currency": "CNH",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "currency": "HKD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "currency": "RMB",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    },
    {
      "settlementDate": "2025-11-30",
      "currency": "USD",
      "inflow": "0.0000",
      "outflow": "0.0000",
      "net": "0.0000"
    }
  ]
}