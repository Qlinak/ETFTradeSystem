# Folder Structure

## Target Layout

```text
ETFTradeSystem/
├── app/
│   ├── main.py
│   ├── api/
│   │   └── endpoints.py
│   ├── schemas/
│   │   ├── orders.py
│   │   ├── products.py
│   │   └── common.py
│   ├── db/
│   │   ├── config.py
│   │   ├── session.py
│   │   └── migrations/
│   ├── repositories/
│   │   ├── order_repository.py
│   │   ├── product_repository.py
│   │   ├── quota_repository.py
│   │   ├── idempotency_repository.py
│   │   └── ledger_repository.py
│   ├── services/
│   │   ├── order_submission_service.py
│   │   ├── order_query_service.py
│   │   ├── order_cancellation_service.py
│   │   ├── quota_service.py
│   │   ├── cutoff_service.py
│   │   ├── idempotency_service.py
│   │   └── ledger_service.py
│   └── core/
│       ├── decimal_rules.py
│       ├── errors.py
│       ├── enums.py
│       └── time_provider.py
├── tests/
│   ├── integration/
│   ├── concurrency/
│   └── fixtures/
├── plans/
│   ├── execution-plan.md
│   ├── folder-structure.md
│   ├── system-architecture.md
│   └── api-contract.md
├── scripts/
├── docker/
├── schema.sql
├── docker-compose.yml
├── README.md
└── DECISIONS.md
```

## Responsibility by Folder

## `app/api/`
Contains HTTP endpoint declarations only.

Rules:
- all routes live in `endpoints.py`
- no raw SQL
- no transaction logic
- only request parsing, service invocation, and response mapping

## `app/schemas/`
Contains Pydantic request and response models.

Rules:
- request validation lives here
- OpenAPI examples live here or are referenced here
- no DB access
- no business rules beyond format validation

## `app/db/`
Contains DB connection and migration-related setup.

Rules:
- engine/session creation only
- environment-driven configuration
- no use-case logic

## `app/repositories/`
Contains raw DB access methods.

Rules:
- one repository file per persistence concern
- SQL statements or SQLAlchemy Core queries only
- no HTTP concerns
- no cross-step orchestration

## `app/services/`
Contains business use cases and transactional orchestration.

Rules:
- one service file per use case or rule domain
- services call repositories
- services enforce sequencing and invariants
- services decide status transitions and validation outcomes

## `app/core/`
Contains shared utilities and global rules.

Rules:
- decimal precision rules
- domain enums
- reusable exceptions
- time helper abstractions

## `tests/`
Contains verification by category.

Suggested split:
- `integration/` for endpoint + DB behavior
- `concurrency/` for quota race tests
- `fixtures/` for common DB setup helpers

## File-Level Single-Purpose Convention

### Endpoint Layer
- `app/api/endpoints.py`
  Purpose: all route definitions and Swagger metadata

### Schema Layer
- `app/schemas/orders.py`
  Purpose: order request/response schemas
- `app/schemas/products.py`
  Purpose: quota response schemas
- `app/schemas/common.py`
  Purpose: shared error and metadata schemas

### Repository Layer
- `app/repositories/order_repository.py`
  Purpose: CRUD for orders
- `app/repositories/product_repository.py`
  Purpose: product reads
- `app/repositories/quota_repository.py`
  Purpose: quota lock/update/release SQL
- `app/repositories/idempotency_repository.py`
  Purpose: idempotency row read/write/lock SQL
- `app/repositories/ledger_repository.py`
  Purpose: ledger insert and balance checks

### Service Layer
- `app/services/order_submission_service.py`
  Purpose: submit-order use case end-to-end
- `app/services/order_query_service.py`
  Purpose: query-by-client-order-id use case
- `app/services/order_cancellation_service.py`
  Purpose: cancel-order use case
- `app/services/quota_service.py`
  Purpose: quota read formatting and remaining quota logic
- `app/services/cutoff_service.py`
  Purpose: cutoff evaluation against DB time
- `app/services/idempotency_service.py`
  Purpose: request fingerprinting and replay behavior
- `app/services/ledger_service.py`
  Purpose: double-entry posting orchestration

## Why This Structure

1. It keeps Swagger and endpoint review simple.
2. It keeps transaction logic out of controllers.
3. It makes concurrency-sensitive code easy to find.
4. It makes unit and integration tests more focused.
5. It follows your rule that a file should do only one purpose.
