import { ApiFailure, OrderRow, OrdersResponse, OrderUpdatesResponse, QuotaResponse, SortBy, SortDir } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
const REQUEST_TIMEOUT_MS = 10000;

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(input, {
      ...init,
      signal: controller.signal,
      headers: init?.headers,
    });

    const body = await response.json().catch(() => ({}));

    if (!response.ok) {
      const detail = (body?.detail ?? body) as { errorCode?: string; message?: string };
      const error: ApiFailure = {
        status: response.status,
        errorCode: detail?.errorCode,
        message: detail?.message ?? response.statusText,
      };
      throw error;
    }

    return body as T;
  } catch (error) {
    if ((error as DOMException).name === "AbortError") {
      throw {
        status: 0,
        errorCode: "ERR_TIMEOUT",
        message: "Request timed out",
      } as ApiFailure;
    }

    if ((error as ApiFailure).status !== undefined) {
      throw error;
    }

    throw {
      status: 0,
      errorCode: navigator.onLine ? "ERR_NETWORK" : "ERR_OFFLINE",
      message: navigator.onLine ? "Cannot reach API" : "Network unavailable",
    } as ApiFailure;
  } finally {
    window.clearTimeout(timeout);
  }
}

function makeQuery(params: Record<string, string | number | null | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      query.set(key, String(value));
    }
  });
  return query.toString();
}

export async function fetchOrders(args: {
  tradeDate: string;
  productId?: string;
  pdId?: string;
  status?: string;
  currency?: string;
  sortBy: SortBy;
  sortDir: SortDir;
  cursor: number;
  limit: number;
}): Promise<OrdersResponse> {
  const query = makeQuery({
    tradeDate: args.tradeDate,
    productId: args.productId,
    pdId: args.pdId,
    status: args.status,
    currency: args.currency,
    sortBy: args.sortBy,
    sortDir: args.sortDir,
    cursor: args.cursor,
    limit: args.limit,
  });
  return requestJson<OrdersResponse>(`${API_BASE}/orders?${query}`);
}

export async function fetchUpdates(args: {
  since: number;
  tradeDate?: string;
  productId?: string;
  pdId?: string;
  status?: string;
  currency?: string;
  limit?: number;
}): Promise<OrderUpdatesResponse> {
  const query = makeQuery({
    since: args.since,
    tradeDate: args.tradeDate,
    productId: args.productId,
    pdId: args.pdId,
    status: args.status,
    currency: args.currency,
    limit: args.limit ?? 200,
  });
  return requestJson<OrderUpdatesResponse>(`${API_BASE}/orders-updates?${query}`);
}

export function createUpdatesStream(args: {
  since: number;
  tradeDate?: string;
  productId?: string;
  pdId?: string;
  status?: string;
  currency?: string;
}): EventSource {
  const query = makeQuery({
    since: args.since,
    tradeDate: args.tradeDate,
    productId: args.productId,
    pdId: args.pdId,
    status: args.status,
    currency: args.currency,
  });
  return new EventSource(`${API_BASE}/orders-stream?${query}`);
}

export async function confirmOrder(clientOrderId: string, pdId: string): Promise<OrderRow> {
  return requestJson<OrderRow>(`${API_BASE}/orders/${encodeURIComponent(clientOrderId)}/confirm`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ pdId }),
  });
}

export async function cancelOrder(clientOrderId: string, pdId: string): Promise<OrderRow> {
  return requestJson<OrderRow>(`${API_BASE}/orders/${encodeURIComponent(clientOrderId)}/cancel`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ pdId, reason: "Cancelled from operations blotter" }),
  });
}

export async function fetchQuota(productId: string): Promise<QuotaResponse> {
  return requestJson<QuotaResponse>(`${API_BASE}/products/${encodeURIComponent(productId)}/quota`);
}
