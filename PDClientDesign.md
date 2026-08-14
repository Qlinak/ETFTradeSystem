Requirement:
Implement the full path from "PD submits an order" through validation and confirmation to "the order
appears in the cash ladder":
1. Quota must never be over-issued. A QDII product has RMB 50,000,000 of remaining quota for the
day. Eight PDs submit simultaneously, requesting RMB 200,000,000 in aggregate. The system must
accept some and reject others, and under no circumstances exceed the remaining quota — while
also not being so defensive about concurrency that the quota gets "locked away" and becomes
unusable by anyone.
2. Cancellation and rejection must release quota correctly. Once cancelled, the quota returns to
the available pool, and it must not be released twice (cancelling twice does not release double).
3. The confirmation endpoint must be idempotent. PD systems retry on timeout. Submitting the
same clientOrderId five times must produce exactly the same outcome as submitting it once —
not "an error on the second attempt", but returning the same order as the first call.
4. Cut-off time is a hard boundary. Explain what you use as the time reference (client time?
application server time? database time?), and state whether an order counts when operations clicks
"confirm" at 10:59:58 but the request only lands in the database at 11:00:01.
5. Floating-point types must never be used for amounts or units. Define the precision and
rounding rules for units, prices, and cash amounts respectively, and explain how the "integer
multiple of creation unit" check behaves under adversarial input (for example, a PD submitting
1000000.0000000001 ).
6. Double-entry invariant — for every cash movement, the sum of all entries within the same
currency must be zero at all times. Express this invariant as an executable check (a test or a
runtime assertion both work).

[ PD Client / Algorithmic Engine ]
               │
               ▼
   1. Idempotency Gate (Redis / Fast DB Unique Index)
               │
               ▼
   2. Hard Cut-Off Time Validation (Server/DB Reference Clock)
               │
               ▼
   3. Domain Validation (Creation Unit Modulo & Fixed Precision Math)
               │
               ▼
   4. Transactional Core (DB Row-Level Locking & Quota Deduct)
               │
               ▼
   5. Double-Entry Posting Ledger (Zero-Sum Assertion)
               │
               ▼
   6. Event Publishing (Push state update to Ops Blotter)


Endpoints
POST: /api/v1/orders
example payload:
Request
{
  "clientOrderId": "ORD-GS-20260814-00123",
  "productId": "PROD-HK-001",
  "pdId": "PD-GOLDMAN-HK",
  "orderType": "CREATION",
  "units": "2000000",
  "estimatedPrice": "50.2500",
  "currency": "HKD"
}
Response:
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
{
  "errorCode": "ERR_QUOTA_EXCEEDED",
  "message": "Requested cash amount exceeds remaining QDII quota for this product.",
  "clientOrderId": "ORD-GS-20260814-00123",
  "timestamp": "2026-08-14T10:59:59.002Z"
}

GET: /api/v1/orders/:clientOrderId
Response: exactly the same as /api/v1/orders


POST: /api/v1/orders/:clientOrderId/cancel
{
  "pdId": "PD-GOLDMAN-HK",
  "reason": "Algorithmic execution adjustment"
}

GET: /api/v1/products/:productId/quota
Response:
{
  "productId": "PROD-QDII-RMB-01",
  "currency": "RMB",
  "totalDailyQuota": "500000000.0000",
  "remainingQuota": "45000000.0000",
  "cutoffTime": "11:00:00",
  "asOf": "2026-08-14T10:55:00.000Z"
}