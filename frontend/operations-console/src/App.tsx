import { useEffect, useMemo, useRef, useState } from "react";

import { cancelOrder, confirmOrder, createUpdatesStream, fetchOrders, fetchQuota, fetchUpdates } from "./api";
import { ApiFailure, OrderRow, SortBy, SortDir } from "./types";

const ROW_HEIGHT = 42;
const PAGE_SIZE = 300;
const OVERSCAN = 8;

function todayDateString(): string {
  return new Date().toISOString().slice(0, 10);
}

function parseServerOffset(serverTimeIso: string): number {
  return new Date(serverTimeIso).getTime() - Date.now();
}

function toHumanFailure(error: ApiFailure): string {
  if (error.errorCode === "ERR_TIMEOUT") {
    return "Request timed out. Retry the action.";
  }
  if (!navigator.onLine || error.errorCode === "ERR_OFFLINE") {
    return "You are offline. Reconnecting automatically.";
  }
  if (error.errorCode === "ERR_NETWORK") {
    return "Cannot reach API from browser (check CORS/API URL).";
  }
  if (error.status === 409) {
    return "Order already actioned by another operator.";
  }
  if (error.status === 422) {
    return "Quota exceeded. Order cannot be confirmed.";
  }
  return error.message || "Request failed.";
}

function computeCountdown(cutoffTime: string | undefined, adjustedNowMs: number): string {
  if (!cutoffTime) {
    return "--";
  }
  const nowAdjusted = new Date(adjustedNowMs);
  const [hh, mm, ss] = cutoffTime.split(":").map(Number);
  // Product cutoff values are currently interpreted as UTC wall-clock times.
  const cutoffUtc = new Date(
    Date.UTC(
      nowAdjusted.getUTCFullYear(),
      nowAdjusted.getUTCMonth(),
      nowAdjusted.getUTCDate(),
      hh,
      mm,
      ss,
      0,
    )
  );
  let diff = cutoffUtc.getTime() - nowAdjusted.getTime();
  if (diff < 0) {
    diff = 0;
  }

  const totalSeconds = Math.floor(diff / 1000);
  const h = String(Math.floor(totalSeconds / 3600)).padStart(2, "0");
  const m = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, "0");
  const s = String(totalSeconds % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

export function App() {
  const [tradeDate, setTradeDate] = useState(todayDateString());
  const [productId, setProductId] = useState("");
  const [pdId, setPdId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [currency, setCurrency] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("submittedAt");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const [rows, setRows] = useState<OrderRow[]>([]);
  const [cursor, setCursor] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const [syncMode, setSyncMode] = useState<"sse" | "polling">("sse");
  const [streamFailures, setStreamFailures] = useState(0);
  const [eventCursor, setEventCursor] = useState(0);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  const [tickNowMs, setTickNowMs] = useState(Date.now());

  const [pending, setPending] = useState<Record<string, "confirming" | "cancelling">>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [online, setOnline] = useState(navigator.onLine);

  const [cutoffs, setCutoffs] = useState<Record<string, string>>({});

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);

  const baseFilters = useMemo(
    () => ({ tradeDate, productId: productId || undefined, pdId: pdId || undefined, status: statusFilter || undefined, currency: currency || undefined }),
    [tradeDate, productId, pdId, statusFilter, currency]
  );

  useEffect(() => {
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setTickNowMs(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(interval);
    };
  }, []);

  const mergeRows = (incoming: OrderRow[]) => {
    setRows((previous) => {
      const map = new Map(previous.map((row) => [row.systemOrderId, row]));
      incoming.forEach((row) => {
        map.set(row.systemOrderId, row);
      });
      return Array.from(map.values());
    });
  };

  const resetAndLoad = async () => {
    setLoading(true);
    setErrorBanner(null);
    setRowErrors({});
    setPending({});
    setEventCursor(0);
    try {
      const response = await fetchOrders({ ...baseFilters, sortBy, sortDir, cursor: 0, limit: PAGE_SIZE });
      setRows(response.rows);
      setCursor(response.nextCursor ?? 0);
      setHasMore(response.hasMore);
      setServerOffsetMs(parseServerOffset(response.serverTime));

      const maxEventId = response.rows.reduce((acc, row) => Math.max(acc, row.lastEventId ?? 0), 0);
      setEventCursor(maxEventId);
    } catch (error) {
      setErrorBanner(toHumanFailure(error as ApiFailure));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void resetAndLoad();
  }, [tradeDate, productId, pdId, statusFilter, currency, sortBy, sortDir]);

  const loadMore = async () => {
    if (loading || !hasMore) {
      return;
    }
    setLoading(true);
    try {
      const response = await fetchOrders({ ...baseFilters, sortBy, sortDir, cursor, limit: PAGE_SIZE });
      mergeRows(response.rows);
      setCursor(response.nextCursor ?? cursor);
      setHasMore(response.hasMore);
      setServerOffsetMs(parseServerOffset(response.serverTime));
    } catch (error) {
      setErrorBanner(toHumanFailure(error as ApiFailure));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (syncMode !== "sse" || !online) {
      return;
    }

    const stream = createUpdatesStream({ since: eventCursor, ...baseFilters });

    const listener = (evt: Event) => {
      const message = evt as MessageEvent<string>;
      const payload = JSON.parse(message.data) as { eventId: number; order: OrderRow; occurredAt: string };
      setEventCursor((prev) => Math.max(prev, payload.eventId));
      setServerOffsetMs(parseServerOffset(payload.occurredAt));
      mergeRows([payload.order]);
    };

    stream.addEventListener("order.status", listener);
    stream.onerror = () => {
      stream.close();
      setStreamFailures((prev) => prev + 1);
    };

    return () => {
      stream.removeEventListener("order.status", listener);
      stream.close();
    };
  }, [syncMode, online, eventCursor, baseFilters]);

  useEffect(() => {
    if (streamFailures >= 3) {
      setSyncMode("polling");
      setErrorBanner("Real-time stream degraded. Switched to polling fallback.");
    }
  }, [streamFailures]);

  useEffect(() => {
    if (syncMode !== "polling" || !online) {
      return;
    }

    const interval = window.setInterval(async () => {
      try {
        const update = await fetchUpdates({ since: eventCursor, ...baseFilters, limit: 200 });
        if (update.events.length > 0) {
          setEventCursor(update.nextSince);
          setServerOffsetMs(parseServerOffset(update.serverTime));
          mergeRows(update.events.map((event) => event.order));
        }
      } catch (error) {
        setErrorBanner(toHumanFailure(error as ApiFailure));
      }
    }, 3000);

    return () => {
      window.clearInterval(interval);
    };
  }, [syncMode, online, eventCursor, baseFilters]);

  useEffect(() => {
    const products = Array.from(new Set(rows.map((row) => row.productId)));
    const missing = products.filter((id) => !cutoffs[id]);
    if (missing.length === 0) {
      return;
    }

    void Promise.all(
      missing.slice(0, 20).map(async (id) => {
        try {
          const quota = await fetchQuota(id);
          setCutoffs((prev) => ({ ...prev, [id]: quota.cutoffTime }));
          setServerOffsetMs(parseServerOffset(quota.asOf));
        } catch {
          setCutoffs((prev) => ({ ...prev, [id]: "" }));
        }
      })
    );
  }, [rows, cutoffs]);

  const runAction = async (row: OrderRow, action: "confirm" | "cancel") => {
    const existing = row;
    setRowErrors((prev) => ({ ...prev, [row.systemOrderId]: "" }));
    setPending((prev) => ({ ...prev, [row.systemOrderId]: action === "confirm" ? "confirming" : "cancelling" }));

    try {
      const updated =
        action === "confirm"
          ? await confirmOrder(row.clientOrderId, row.pdId)
          : await cancelOrder(row.clientOrderId, row.pdId);

      mergeRows([updated]);
    } catch (error) {
      mergeRows([existing]);
      const message = toHumanFailure(error as ApiFailure);
      setRowErrors((prev) => ({ ...prev, [row.systemOrderId]: message }));
      setErrorBanner(message);
    } finally {
      setPending((prev) => {
        const next = { ...prev };
        delete next[row.systemOrderId];
        return next;
      });
    }
  };

  const viewportHeight = 560;
  const totalHeight = rows.length * ROW_HEIGHT;
  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN * 2;
  const endIndex = Math.min(rows.length, startIndex + visibleCount);
  const visibleRows = rows.slice(startIndex, endIndex);
  const adjustedNowMs = tickNowMs + serverOffsetMs;

  return (
    <div className="page">
      <header className="topbar">
        <h1>ETF Operations Blotter</h1>
        <div className={`status ${online ? "ok" : "bad"}`}>{online ? `Live via ${syncMode.toUpperCase()}` : "Offline"}</div>
      </header>

      {errorBanner && <div className="banner">{errorBanner}</div>}
      {!online && <div className="banner warning">You are offline. Actions are paused until reconnection.</div>}

      <section className="filters">
        <label>
          Trade Date
          <input type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} />
        </label>
        <label>
          Product
          <input value={productId} onChange={(e) => setProductId(e.target.value)} placeholder="PROD-HK-001" />
        </label>
        <label>
          PD
          <input value={pdId} onChange={(e) => setPdId(e.target.value)} placeholder="PD-GOLDMAN-HK" />
        </label>
        <label>
          Status
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">ALL</option>
            <option value="PENDING">PENDING</option>
            <option value="CONFIRMED">CONFIRMED</option>
            <option value="REJECTED">REJECTED</option>
            <option value="CANCELLED">CANCELLED</option>
            <option value="SETTLED">SETTLED</option>
          </select>
        </label>
        <label>
          Currency
          <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
            <option value="">ALL</option>
            <option value="HKD">HKD</option>
            <option value="USD">USD</option>
            <option value="RMB">RMB</option>
            <option value="CNH">CNH</option>
          </select>
        </label>
      </section>

      <section className="table-wrap">
        <div className="table-header">
          {(["submittedAt", "productId", "pdId", "status", "currency"] as SortBy[]).map((field) => (
            <button
              key={field}
              className="sort-btn"
              onClick={() => {
                if (sortBy === field) {
                  setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
                } else {
                  setSortBy(field);
                  setSortDir("asc");
                }
              }}
            >
              {field} {sortBy === field ? (sortDir === "asc" ? "^" : "v") : ""}
            </button>
          ))}
          <span className="col">clientOrderId</span>
          <span className="col">cutoffLeft</span>
          <span className="col">actions</span>
        </div>

        <div
          ref={viewportRef}
          className="viewport"
          style={{ height: viewportHeight }}
          onScroll={(e) => {
            const target = e.currentTarget;
            setScrollTop(target.scrollTop);
            if (target.scrollTop + target.clientHeight >= target.scrollHeight - 200) {
              void loadMore();
            }
          }}
        >
          <div className="spacer" style={{ height: totalHeight }}>
            {visibleRows.map((row, i) => {
              const index = startIndex + i;
              const top = index * ROW_HEIGHT;
              const pendingState = pending[row.systemOrderId];
              const countdown = computeCountdown(cutoffs[row.productId], adjustedNowMs);
              return (
                <div className="row" style={{ top }} key={row.systemOrderId}>
                  <span>{new Date(row.submittedAt).toLocaleTimeString()}</span>
                  <span>{row.productId}</span>
                  <span>{row.pdId}</span>
                  <span>{pendingState ? `${pendingState}...` : row.status}</span>
                  <span>{row.currency}</span>
                  <span>{row.clientOrderId}</span>
                  <span>{countdown}</span>
                  <span className="actions">
                    <button
                      disabled={!online || !!pendingState || row.status === "CONFIRMED"}
                      onClick={() => void runAction(row, "confirm")}
                    >
                      Confirm
                    </button>
                    <button
                      disabled={!online || !!pendingState || row.status === "CANCELLED"}
                      onClick={() => void runAction(row, "cancel")}
                    >
                      Cancel
                    </button>
                  </span>
                  {rowErrors[row.systemOrderId] && <span className="row-error">{rowErrors[row.systemOrderId]}</span>}
                </div>
              );
            })}
          </div>
        </div>
      </section>

      <footer className="footnote">
        <span>Rows loaded: {rows.length}</span>
        <span>{loading ? "Loading..." : hasMore ? "Scroll to load more" : "End of result"}</span>
      </footer>
    </div>
  );
}
