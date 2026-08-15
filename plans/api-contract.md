# API Contract and Implementation Order

## Swagger-First Approach

The API should be exposed through FastAPI so Swagger UI is available from the first runnable version.

Initial goal:
- all routes visible in Swagger
- request/response schemas visible
- example payloads visible
- placeholder implementations allowed until DB transaction logic is complete

## Endpoint File Convention

All routes live in one file:
- `app/api/endpoints.py`

That file should contain:
- route declarations
- tags and summaries
- request/response bindings
- OpenAPI examples
- HTTP-to-service mapping

That file should not contain:
- SQL
- transaction orchestration
- precision logic
- quota update logic
- ledger posting logic

## Endpoints

## 1. POST `/api/v1/orders`

### Purpose
Submit and confirm an ETF order in a synchronous transactional flow.

### Request
```json
{
  "clientOrderId": "ORD-GS-20260814-00123",
  "productId": "PROD-HK-001",
  "pdId": "PD-GOLDMAN-HK",
  "orderType": "CREATION",
  "units": "2000000",
  "estimatedPrice": "50.2500",
  "currency": "HKD"
}
```

### Success Response
```json
{
  "systemOrderId": "550e8400-e29b-41d4-a716-446655440000",
  "clientOrderId": "ORD-GS-20260814-00123",
  "productId": "PROD-HK-001",
  "pdId": "PD-GOLDMAN-HK",
  "orderType": "CREATION",
  "units": "2000000",
  "cashAmount": "100500000.0000",
  "currency": "HKD",
  "status": "CONFIRMED",
  "submittedAt": "2026-08-14T10:58:30.124Z",
  "settlementDate": "2026-08-18",
  "rejectionReason": null
}
```

### Rejection Response
```json
{
  "errorCode": "ERR_QUOTA_EXCEEDED",
  "message": "Requested cash amount exceeds remaining QDII quota for this product.",
  "clientOrderId": "ORD-GS-20260814-00123",
  "timestamp": "2026-08-14T10:59:59.002Z"
}
```

### Implementation Notes
1. Implement first
2. Must be idempotent
3. Must use DB time for cutoff
4. Must never use float for units or amounts
5. Must support exact same response on retry

## 2. GET `/api/v1/orders/{clientOrderId}`

### Purpose
Retrieve the canonical stored order outcome.

### Notes
1. Response shape should match submit order success response
2. Useful for retries and operational lookup
3. Implement second

## 3. POST `/api/v1/orders/{clientOrderId}/cancel`

### Purpose
Cancel an order and release quota exactly once when applicable.

### Request
```json
{
  "pdId": "PD-GOLDMAN-HK",
  "reason": "Algorithmic execution adjustment"
}
```

### Notes
1. Implement third
2. Must guard against double cancellation side effects
3. Must reject invalid state transitions
4. Must release quota exactly once

## 4. GET `/api/v1/products/{productId}/quota`

### Purpose
Return current product quota state.

### Success Response
```json
{
  "productId": "PROD-QDII-RMB-01",
  "currency": "RMB",
  "totalDailyQuota": "500000000.0000",
  "remainingQuota": "45000000.0000",
  "cutoffTime": "11:00:00",
  "asOf": "2026-08-14T10:55:00.000Z"
}
```

### Notes
1. Implement fourth
2. Read-only endpoint
3. `asOf` should come from DB-backed time context

## 5. GET `/api/v1/cash-ladder?asOf=YYYY-MM-DD&horizon=30`

### Purpose
Return expected cash inflow, outflow, and net values for unsettled confirmed orders across:
- the next N settlement dates
- all currencies
- all products

### Query Parameters
1. `asOf` (required)
   - Type: `date` (`YYYY-MM-DD`)
   - Meaning: valuation date used as the ladder start boundary.
   - Rule: include orders with `status = CONFIRMED`, `settlement_date IS NOT NULL`, and `settlement_date >= asOf`.

2. `horizon` (optional)
   - Type: integer
   - Default: `30`
   - Allowed range: `1..90`
   - Meaning: number of settlement dates (calendar days) to include, starting at `asOf`.

### Example Request
```http
GET /api/v1/cash-ladder?asOf=2026-08-14&horizon=30
```

### Response Shape
```json
{
  "asOf": "2026-08-14",
  "horizon": 30,
  "windowEnd": "2026-09-12",
  "generatedAt": "2026-08-14T11:02:05.442Z",
  "responseTimeMs": 12,
  "rows": [
    {
      "settlementDate": "2026-08-18",
      "productId": "ETF001",
      "currency": "HKD",
      "inflow": "12500000.0000",
      "outflow": "1500000.0000",
      "net": "11000000.0000"
    },
    {
      "settlementDate": "2026-08-18",
      "productId": "ETF006",
      "currency": "USD",
      "inflow": "500000.0000",
      "outflow": "800000.0000",
      "net": "-300000.0000"
    }
  ],
  "totalsByDateCurrency": [
    {
      "settlementDate": "2026-08-18",
      "currency": "HKD",
      "inflow": "13000000.0000",
      "outflow": "1500000.0000",
      "net": "11500000.0000"
    }
  ]
}
```

### Settlement Date Rules
1. Settlement date must be derived from trade date using market holiday calendars.
2. Business-day shifts must skip weekends and skip `holiday_calendars` rows for the product market.
3. Ladder output must reflect those derived `settlement_date` values, not naive `trade_date + 2`.

### Calculation Rules
1. Source orders: `status = CONFIRMED` and not yet settled.
2. Exclude `CANCELLED`, `REJECTED`, and already `SETTLED` orders.
3. Treat creation/redeem direction consistently:
   - CREATION contributes to outflow/inflow based on agreed accounting side.
   - REDEMPTION contributes to the opposite side.
4. `net = inflow - outflow`.
5. Use fixed precision numeric strings (4 d.p.) in API response.

### Error Cases
1. `400 ERR_INVALID_AS_OF` when `asOf` is missing or invalid format.
2. `400 ERR_INVALID_HORIZON` when `horizon` is outside allowed range.

## Shared Error Codes

Suggested initial error catalog:
- `ERR_QUOTA_EXCEEDED`
- `ERR_CUTOFF_PASSED`
- `ERR_INVALID_UNITS`
- `ERR_INVALID_CURRENCY`
- `ERR_ORDER_NOT_FOUND`
- `ERR_INVALID_ORDER_STATE`
- `ERR_IDEMPOTENCY_CONFLICT`
- `ERR_INVALID_AS_OF`
- `ERR_INVALID_HORIZON`

## Recommended Endpoint Implementation Sequence

1. Declare all five endpoints in Swagger with placeholder service calls
2. Implement Pydantic schemas and examples
3. Wire submit-order service
4. Wire query-order service
5. Wire cancel-order service
6. Wire quota-read service
7. Wire cash-ladder read service
8. Add endpoint integration tests

## Acceptance Checklist

1. Swagger shows all endpoints and examples
2. Request and response schemas are visible in OpenAPI
3. Endpoint file contains only route concerns
4. Services hide transactional complexity from HTTP layer
5. Responses match the agreed contract in `PDClientDesign.md`
6. Cash ladder output is grouped by settlement date, currency, and product with correct holiday-aware dates
