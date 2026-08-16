# Task 3 - Operations Front-End (Tight Scope)

## 1) Scope and Goal

Build a practical operations page that is fast and reliable, without UI polish work:
- Blotter for up to 50,000 rows for one trade day
- Filter and sort by product, PD, status, and currency
- Near-real-time status refresh within seconds
- Optimistic action UX with safe rollback
- Clear error behavior for timeout, offline, 409, and 422
- Cutoff countdown per product, robust to incorrect client clock

Non-goals:
- Full design system and visual polish
- Real-time collaboration features beyond status updates
- Cross-product analytics and reporting pages

## 2) Proposed Tech Stack

Frontend:
- React 19 + TypeScript
- Vite
- TanStack Table
- TanStack Virtual (windowed rendering)
- TanStack Query (request caching/retry/state)
- Zod (runtime response validation, optional but recommended)

Backend integration pattern:
- Snapshot endpoint for initial table load
- SSE stream for near-real-time order status changes
- Polling fallback for SSE failure/reconnect degradation

Why this stack:
- React + TypeScript keeps logic maintainable for state-heavy ops screens
- Table + virtualization is the most direct way to keep 50,000-row UI responsive
- Query layer standardizes retry/timeout/offline handling

## 3) Front-End Design

### 3.1 Data Model (client side)

OrderRow:
- systemOrderId
- clientOrderId
- productId
- pdId
- status
- currency
- units
- estimatedPrice
- cashAmount
- submittedAt
- settlementDate
- rejectionReason
- updatedAt (from backend)

UI-only fields:
- pendingAction: none | confirming | cancelling
- optimisticVersion: number
- lastError: null | { code, message }

### 3.2 Blotter Rendering for 50,000 rows

Approach:
- Virtualized rows with fixed row height
- Sticky header
- Server-side filter and sort
- Cursor pagination in background while scrolling (load-ahead)

Performance constraints:
- Keep DOM node count near viewport size
- Debounce filter input (250ms)
- Memoize row/cell rendering
- Avoid full-table re-renders on single-row updates

### 3.3 Near-real-time updates

Primary channel: SSE
- One EventSource stream per browser tab
- Events include eventId and changed order payload
- Reconnect with Last-Event-ID
- Heartbeat every 15s

Fallback:
- If SSE reconnect fails repeatedly, switch to short polling (2-5s jitter)
- Resume SSE attempts in background

State merge rule:
- Apply events only if eventId is newer than local cursor
- Update only affected rows, not full snapshot

### 3.4 Optimistic UI and rollback safety

Confirm action flow:
1. User clicks Confirm
2. Row immediately shows temporary state: Confirming...
3. Confirm button is disabled for that row
4. Request sent
5. If success: replace row with backend response and final status
6. If failure: revert row to previous committed state and show inline + toast error

Critical consistency rule:
- UI never shows final CONFIRMED unless backend success is received.
- Temporary optimistic state is visually distinct and non-final.

## 4) Required Error-State Handling

### 4.1 Request timeout
User sees:
- Row action reverts from pending state
- Inline row badge: Request timed out
- Retry action available
- Global non-blocking toast: Request timed out, please retry

### 4.2 Offline
User sees:
- Persistent top banner: You are offline
- Real-time indicator switches to Reconnecting
- Action buttons disabled (or guarded with warning)
- Automatic retry when network returns

### 4.3 HTTP 409 (already actioned by someone else)
User sees:
- Optimistic state rolled back
- Row immediately refetched
- Inline message: Already actioned by another operator
- New authoritative status rendered

### 4.4 HTTP 422 (quota exceeded)
User sees:
- Optimistic state rolled back
- Row remains in prior status
- Business error shown using backend message (for example, quota exceeded)
- No silent failure

## 5) Cutoff Countdown and Inaccurate Client Clock

Countdown logic:
- Backend returns serverTime and product cutoffTime
- Client computes offset:
  - offsetMs = serverNow - clientNow
- Countdown uses adjusted now:
  - adjustedNow = clientNow + offsetMs
  - remaining = cutoffAtProductTZ - adjustedNowInProductTZ

Clock-drift handling:
- Recalculate offset on each snapshot and update response
- If drift exceeds threshold (for example 2s), show small indicator: Clock adjusted to server time

Correctness guarantee:
- Countdown is informational only
- Final accept/reject remains backend-enforced using database time

## 6) API Requirements (minimum additions)

Required minimal endpoints to support this frontend:

1. GET /api/v1/orders
- Query: tradeDate, productId, pdId, status, currency, sortBy, sortDir, cursor, limit
- Returns: rows, nextCursor, serverTime

2. GET /api/v1/orders/stream
- SSE stream of order status changes
- Supports Last-Event-ID or since cursor
- Sends heartbeat comments

3. GET /api/v1/orders/updates
- Polling fallback with since cursor
- Returns deterministic ordered deltas

Notes:
- Keep current submit/cancel endpoints
- Reuse existing error schema for business failures

## 7) Execution Plan (Phased)

Phase 1 - API contract and backend delta
- Define list/filter/sort/cursor response contract
- Define SSE event schema and resume semantics
- Define polling fallback delta contract

Phase 2 - Blotter foundation
- Build virtualized table with server-driven filters/sorts
- Implement cursor pagination and load-ahead
- Add row action controls and pending states

Phase 3 - Real-time sync
- Integrate SSE stream and cursor replay
- Add fallback polling mode and reconnect strategy
- Add stale-connection detection and health indicator

Phase 4 - Optimistic behavior and error UX
- Implement optimistic confirm/cancel flows
- Implement rollback and row-level reconciliation
- Implement timeout/offline/409/422 user-visible behavior

Phase 5 - Cutoff countdown and validation
- Add per-product countdown with server-time offset
- Validate behavior under intentionally skewed client clock

Phase 6 - Hardening and acceptance checks
- Load test with 50,000 rows day snapshot
- Validate smooth scroll and interactive responsiveness
- Validate real-time updates across two concurrent operators

## 8) Why SSE vs Polling vs WebSocket

Chosen now: SSE + polling fallback

Why SSE over polling:
- Faster perceived updates without constant repeated full requests
- Lower backend churn than frequent short polling
- Simpler freshness model for operators

Why SSE over WebSocket:
- This use case is one-way server-to-client status updates
- SSE is simpler to deploy and operate for this need
- Automatic browser reconnection behavior is useful

When WebSocket would be better:
- Heavy bidirectional collaboration
- High-frequency interactive multi-user workflows needing low-latency client-to-server pushes

## 9) Would this choice change for HK + London across multiple AZs?

Short answer:
- Usually no, SSE is still appropriate if events are durable and resumable.

Conditions to keep SSE:
- Event IDs are persisted and globally ordered
- Clients can resume from Last-Event-ID after reconnect
- Load balancer/proxy idle timeout supports SSE heartbeat strategy

When to revisit:
- If concurrent users and event fan-out become very large
- If bidirectional collaboration requirements grow
- If global latency and routing policies require dedicated pub/sub or WebSocket infrastructure

## 10) Acceptance Criteria

1. Blotter remains responsive when viewing and scrolling through a 50,000-row trading day.
2. Filtering and sorting by product, PD, status, and currency are correct and not sluggish.
3. Status changes from another operator appear within seconds without manual refresh.
4. Optimistic action state always reconciles to backend truth and rolls back correctly on failure.
5. Timeout, offline, 409, and 422 each present explicit and distinct user feedback.
6. Cutoff countdown stays aligned with server time even when client clock is wrong.
