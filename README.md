# ETF Trade System

Concise runbook for startup, data generation, tests, and benchmarks.

## Deliverables

- [DECISIONS.md](DECISIONS.md): product, architecture, testing, and tradeoff decisions.
- [SCALING_DESIGN.md](SCALING_DESIGN.md): bottleneck analysis and phased scaling path. (Task 4B)

## Start the app

Run the full stack once:

```bash
docker compose up -d --build
```

After startup:

- Swagger UI: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json
- Health: http://localhost:8000/health
- Operations Frontend: http://localhost:5173

## Generate data

Data is generated automatically when Docker Compose starts.

To reseed the same deterministic dataset:

```bash
docker compose run --rm --build seed
```

The seed job loads 50 products, 8 PDs, 30 holiday rows, and 1,000,000 orders.

## Run tests

DB function tests:

```bash
./scripts/run_db_function_tests.sh
```

Targeted app tests:

```bash
.venv/bin/python -m pytest tests/concurrency/test_quota_concurrency.py tests/integration/test_order_endpoints.py tests/integration/test_cash_ladder_endpoint.py
```

## Run benchmarks

Cash ladder benchmark:

```bash
.venv/bin/python scripts/benchmark_cash_ladder.py --url 'http://localhost:8000/api/v1/cash-ladder?asOf=2025-11-03&horizon=30' --warmup 2 --runs 12 --output benchmark_after_index.json
```

Before/after benchmark outputs are stored in:

- benchmark_before.json
- benchmark_after_query_only.json
- benchmark_after_reseed_precompute.json
- benchmark_after_index.json

## Notes

- Database name: etf_system
- User: etf_user
- Password: etf_password
- Host port: 5433
- Connection string: postgresql://etf_user:etf_password@localhost:5433/etf_system
