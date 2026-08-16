# ETF Trade System - Database Setup

This project uses PostgreSQL in Docker and initializes the schema from [schema.sql](schema.sql).
It also runs a Python seeding job on startup to load deterministic benchmark data.

## Ship Application in Docker

Run the full stack (PostgreSQL + seed + FastAPI API + Operations Frontend):

```bash
docker compose up -d --build
```

API endpoints after startup:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health: `http://localhost:8000/health`
- Operations Frontend: `http://localhost:5173`

## Run Operations Frontend

An operations blotter frontend starts automatically with Docker Compose and is available at `http://localhost:5173`.

### Option A: Start with Docker Compose (recommended)

```bash
docker compose up -d --build
```

### Option B: Run frontend locally in dev mode

An operations blotter frontend source app is available under `frontend/operations-console`.

1) Start backend services:

```bash
docker compose up -d --build
```

2) Start the frontend dev server:

```bash
cd frontend/operations-console
npm install
npm run dev
```

3) Open the frontend URL shown by Vite (usually `http://localhost:5173`).

Optional API base URL override:

```bash
VITE_API_BASE_URL=http://localhost:8000/api/v1 npm run dev
```

Check API logs:

```bash
docker compose logs -f api
```

Verify container health:

```bash
docker compose ps
```

The `api` service should show `healthy`.

If you changed dependencies or Dockerfiles, rebuild explicitly:

```bash
docker compose up -d --build api
```

Stop everything:

```bash
docker compose down
```

## Run Database Instance

```bash
docker compose up -d
```

This command starts PostgreSQL and runs the Python seeding container (`seed`) that loads 50 products, 8 PDs, and 1,000,000 orders.

## Stop Database Instance

```bash
docker compose down
```

## Notes

- Database: `etf_system`
- User: `etf_user`
- Password: `etf_password`
- Host port: `5433`
- Connection example: `postgresql://etf_user:etf_password@localhost:5433/etf_system`
- Seeding mode: Python + PostgreSQL `COPY` with fixed seed `20260814`
- The initialization scripts in `/docker-entrypoint-initdb.d` only run on first container startup.
- If you need to re-run initialization from scratch, remove volumes first:

```bash
docker compose down -v
```

## Seed Deterministic Test Data (Python)

This project includes a Python seeding script at [scripts/seed_data.py](scripts/seed_data.py) that generates and bulk-loads:

- 50 `products`
- 8 `pds`
- 30 `holiday_calendars` rows (10 each for HK, US, CN)
- 1,000,000 `orders`

The script uses a fixed random seed (`20260814`) and PostgreSQL `COPY` in chunked CSV streams for fast and reproducible loading.

### 1) Start PostgreSQL and auto-seed

```bash
docker compose up -d --build
```

### 2) Check seed logs

```bash
docker compose logs -f seed
```

### 3) (Optional) Run seeding script manually from your local Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_data.py
```

### 4) Verify row counts

```bash
docker compose exec postgres psql -U etf_user -d etf_system -c "SELECT 'products' AS table_name, COUNT(*) FROM products UNION ALL SELECT 'pds', COUNT(*) FROM pds UNION ALL SELECT 'orders', COUNT(*) FROM orders;"
```

### Reseed for repeated benchmarks

To reseed with the exact same deterministic dataset:

```bash
docker compose run --rm --build seed
```

The script truncates and reloads `orders`, `products`, and `pds`, so each run produces the same dataset.

## Run DB Function Test Session

This session validates database-level function and trigger behavior, including:

- product consistency validation
- cutoff time validation
- double-entry zero-sum validation

### 1) Start PostgreSQL

```bash
docker compose up -d
```

### 2) Run the DB function tests

```bash
./scripts/run_db_function_tests.sh
```

### 3) Expected result

The test run should end with:

```text
DB function tests passed.
All DB function tests completed successfully.
```

### What the test runner does

- recreates `etf_system_test`
- applies the latest `schema.sql`
- executes `tests/integration/db_function_tests.sql`
