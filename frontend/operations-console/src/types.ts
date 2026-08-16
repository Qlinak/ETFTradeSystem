export type SortBy = "submittedAt" | "productId" | "pdId" | "status" | "currency";
export type SortDir = "asc" | "desc";

export type OrderRow = {
  systemOrderId: string;
  clientOrderId: string;
  productId: string;
  pdId: string;
  orderType: string;
  units: string;
  estimatedPrice: string;
  cashAmount: string;
  currency: string;
  status: string;
  submittedAt: string;
  settlementDate: string | null;
  rejectionReason: string | null;
  updatedAt: string;
  lastEventId: number | null;
};

export type OrdersResponse = {
  tradeDate: string;
  sortBy: SortBy;
  sortDir: SortDir;
  cursor: number;
  nextCursor: number | null;
  hasMore: boolean;
  serverTime: string;
  rows: OrderRow[];
};

export type OrderUpdateEvent = {
  eventId: number;
  eventType: string;
  occurredAt: string;
  order: OrderRow;
};

export type OrderUpdatesResponse = {
  since: number;
  nextSince: number;
  hasMore: boolean;
  serverTime: string;
  events: OrderUpdateEvent[];
};

export type QuotaResponse = {
  productId: string;
  currency: string;
  totalDailyQuota: string;
  remainingQuota: string;
  cutoffTime: string;
  asOf: string;
};

export type ApiFailure = {
  status: number;
  errorCode?: string;
  message: string;
};
