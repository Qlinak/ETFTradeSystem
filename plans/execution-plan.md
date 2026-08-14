# ETF System Execution Plan

## Objective

Build the ETF order system as a Python FastAPI application with:
- Swagger-first API delivery
- direct PostgreSQL transactional flow for order decisions
- one file containing all HTTP endpoints
- single-purpose files for services, repositories, schemas, DB config, and utilities

## Delivery Principles

- `POST /orders` stays synchronous: request -> DB transaction -> final response
- PostgreSQL is the source of truth for idempotency, quota, cutoff, status, and ledger integrity
- Endpoint definitions live in one file only
- Each implementation file has one clear responsibility
- No floating-point arithmetic in request handling, domain logic, or persistence

## Phase 1: Project Bootstrap

### Goal
Create a runnable FastAPI project with Swagger UI before business logic is implemented.

### Files To Implement In This Phase
1. `app/main.py`
2. `app/api/endpoints.py`
3. `app/core/errors.py`
4. `app/core/enums.py`

### Tasks
1. Add Python web dependencies: `fastapi`, `uvicorn`, `sqlalchemy`, `psycopg`, `pydantic-settings`
2. Create `app/main.py` with FastAPI app bootstrapping
3. Create a single endpoints file: `app/api/endpoints.py`
4. Mount all required routes with placeholder responses
5. Expose Swagger UI and OpenAPI JSON
6. Define all system state constant and error message

### Deliverable
A running app where reviewers can open Swagger and inspect all endpoint contracts.

## Phase 2: API Contract First

### Goal
Lock request and response shapes before transaction logic is added.

### Files To Implement In This Phase
1. `app/schemas/orders.py`
2. `app/schemas/products.py`
3. `app/schemas/common.py`
4. `app/api/endpoints.py`

### Tasks
1. Define Pydantic models for:
   - submit order request
   - order success response
   - order rejection response
   - cancel request
   - quota response
2. Add request examples from `PDClientDesign.md`
3. Add error response schemas and error code enum
4. Ensure `GET /orders/{clientOrderId}` returns the same shape as `POST /orders`

### Deliverable
Stable Swagger contract for the four core endpoints.

## Phase 3: Database Model and Transaction Primitives

### Goal
Add DB structures required for correctness guarantees.

### Files To Implement In This Phase
1. `schema.sql`
2. `app/db/config.py`
3. `app/db/session.py`
4. `app/core/decimal_rules.py`
5. `app/core/time_provider.py`
6. `DECISIONS.md`
7. `tests/integration/db_function_tests.sql`

### Tasks
1. Extend the schema with:
   - `order_idempotency`
   - `product_daily_quota`
   - `quota_allocations`
   - `cash_movements`
   - `ledger_entries`
2. Add constraints for:
   - valid order status
   - valid order type
   - positive units
   - exact numeric precision
3. Add indexes for:
   - idempotency lookup
   - product quota update path
   - order retrieval by `client_order_id`
   - open quota allocations
4. Add DB-level cutoff enforcement trigger using product market timezone and `server_received_at`.
5. Add DB function test cases that validate success and failure paths for each trigger function.
6. Decide whether to keep `products.remaining_quota` as a derived cache or replace it with `product_daily_quota`

### Deliverable
Schema capable of enforcing the assignment rules under concurrent access.

## Phase 4: Service and Repository Layer

### Goal
Implement business logic in single-purpose modules.

### Files To Implement In This Phase
1. `app/repositories/order_repository.py`
2. `app/repositories/product_repository.py`
3. `app/repositories/quota_repository.py`
4. `app/repositories/idempotency_repository.py`
5. `app/repositories/ledger_repository.py`
6. `app/services/order_submission_service.py`
7. `app/services/order_query_service.py`
8. `app/services/order_cancellation_service.py`
9. `app/services/quota_service.py`
10. `app/services/cutoff_service.py`
11. `app/services/idempotency_service.py`
12. `app/services/ledger_service.py`

### Tasks
1. Create DB session/config modules
2. Create repositories for raw DB operations
3. Create services for orchestration and rules
4. Separate concerns strictly:
   - repositories do SQL only
   - services do use-case orchestration only
   - endpoints do HTTP mapping only

### Deliverable
A maintainable internal architecture that matches the one-file-one-purpose convention.

## Phase 5: Endpoint Implementation Order

### Files To Implement In This Phase
1. `app/api/endpoints.py`
2. `app/services/order_submission_service.py`
3. `app/services/order_query_service.py`
4. `app/services/order_cancellation_service.py`
5. `app/services/quota_service.py`
6. `app/services/cutoff_service.py`
7. `app/services/idempotency_service.py`
8. `app/services/ledger_service.py`
9. `app/repositories/order_repository.py`
10. `app/repositories/product_repository.py`
11. `app/repositories/quota_repository.py`
12. `app/repositories/idempotency_repository.py`
13. `app/repositories/ledger_repository.py`
14. `app/schemas/orders.py`
15. `app/schemas/products.py`
16. `app/schemas/common.py`

### 1. POST `/api/v1/orders`
Implement first because it covers the hardest requirements.

Flow:
1. Validate payload with strict decimal/integer parsing
2. Enforce idempotency by `(pd_id, client_order_id)`
3. Read authoritative time from PostgreSQL
4. Reject if cutoff already passed
5. Validate units are an integer multiple of `creation_unit_size`
6. Deduct quota atomically if product is quota-bound
7. Create order row with final status
8. Write ledger rows
9. Return final response

### 2. GET `/api/v1/orders/{clientOrderId}`

Flow:
1. Read canonical stored order outcome
2. Return the same shape as submit response
3. Use this to support retry-safe client behavior

### 3. POST `/api/v1/orders/{clientOrderId}/cancel`

Flow:
1. Lock target order row
2. Reject invalid state transitions
3. Release quota once only if previously allocated
4. Post reversal ledger movement if required
5. Return final order state

### 4. GET `/api/v1/products/{productId}/quota`

Flow:
1. Read authoritative daily quota state
2. Return product, currency, total quota, remaining quota, cutoff, and `asOf`

## Phase 6: Verification

### Files To Implement In This Phase
1. `tests/integration/`
2. `tests/concurrency/`
3. `tests/fixtures/`
4. `app/services/order_submission_service.py`
5. `app/services/order_cancellation_service.py`
6. `app/services/ledger_service.py`
7. `schema.sql`

### Functional Tests
1. Same order submitted five times returns the same result
2. Eight concurrent QDII requests never over-issue quota
3. Cancelling twice does not double-release quota
4. Orders crossing cutoff are rejected based on DB time
5. Adversarial unit values are rejected
6. Double-entry movements remain balanced by currency

### Integration Tests
1. Run against Docker PostgreSQL
2. Reuse deterministic seed data where helpful
3. Add targeted fixtures for quota and cutoff edge cases

## Phase 7: Documentation and Runbook

### Files To Implement In This Phase
1. `README.md`
2. `DECISIONS.md`
3. `plans/execution-plan.md`
4. `plans/folder-structure.md`
5. `plans/system-architecture.md`
6. `plans/api-contract.md`

### Tasks
1. Document local startup for API and DB
2. Document Swagger URL
3. Document how to seed and reseed the DB
4. Document how to run tests
5. Document architecture decisions in `DECISIONS.md`

## Recommended Implementation Order

1. FastAPI skeleton with Swagger
2. Pydantic API schemas
3. DB config/session modules
4. Schema upgrades for idempotency/quota/ledger
5. `POST /orders`
6. `GET /orders/{clientOrderId}`
7. `POST /orders/{clientOrderId}/cancel`
8. `GET /products/{productId}/quota`
9. Integration tests
10. Documentation cleanup

## Acceptance Criteria

1. Swagger is available before business logic is completed
2. All endpoints are declared in a single file
3. Business logic is split into single-purpose files
4. No float is used for units or money
5. Quota cannot be over-issued under concurrency
6. Idempotent replay returns the same semantic outcome
7. Repeated cancellation cannot release quota twice
8. Double-entry balance is executable as a test or runtime assertion
