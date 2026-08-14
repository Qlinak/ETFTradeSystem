#!/usr/bin/env python3
"""Deterministic high-volume data seeding for ETFTradeSystem."""

from __future__ import annotations

import csv
import io
import os
import random
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import execute_values

SEED = 20260814
PRODUCT_COUNT = 50
PD_COUNT = 8
ORDER_COUNT = 1_000_000
COPY_BATCH_SIZE = 100_000
HOLIDAYS_PER_MARKET = 10

CURRENCIES = ["HKD", "USD", "RMB", "CNH"]
STATUSES = ["PENDING", "CONFIRMED", "REJECTED", "CANCELLED", "SETTLED"]
ORDER_TYPES = ["CREATION", "REDEMPTION"]
CUTOFF_TIMES = ["10:30:00", "11:00:00", "11:30:00", "12:00:00"]
BASE_DATE = datetime(2025, 1, 1, 9, 0, 0)
SECONDS_SPAN = 365 * 24 * 60 * 60
HOLIDAY_BASE_DATE = date(2025, 1, 1)


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def get_connection():
    return psycopg2.connect(
        host=env("DB_HOST", "localhost"),
        port=env("DB_PORT", "5433"),
        dbname=env("DB_NAME", "etf_system"),
        user=env("DB_USER", "etf_user"),
        password=env("DB_PASSWORD", "etf_password"),
    )


def business_days_after(start: date, days: int) -> date:
    current = start
    left = days
    while left > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            left -= 1
    return current


def build_reference_data(rng: random.Random) -> Tuple[List[Tuple], List[Tuple], Dict[str, Dict[str, int]], Dict[str, List[str]]]:
    products = []
    product_meta: Dict[str, Dict[str, int]] = {}
    product_ids_by_currency: Dict[str, List[str]] = {c: [] for c in CURRENCIES}

    for i in range(1, PRODUCT_COUNT + 1):
        product_id = f"ETF{i:03d}"
        currency = CURRENCIES[(i - 1) % len(CURRENCIES)]
        creation_unit_size = rng.choice([100_000, 200_000, 500_000, 1_000_000])
        cutoff_time = CUTOFF_TIMES[(i - 1) % len(CUTOFF_TIMES)]
        has_qdii_quota = currency in {"RMB", "CNH"}
        remaining_quota = round(rng.uniform(0, 5_000_000), 4)

        products.append(
            (
                product_id,
                f"ETF Product {i:03d}",
                currency,
                creation_unit_size,
                cutoff_time,
                has_qdii_quota,
                remaining_quota,
            )
        )
        product_meta[product_id] = {
            "creation_unit_size": creation_unit_size,
            "currency": currency,
        }
        product_ids_by_currency[currency].append(product_id)

    pds = []
    for i in range(1, PD_COUNT + 1):
        pd_id = f"PD{i:02d}"
        pds.append((pd_id, f"Participating Dealer {i:02d}"))

    return products, pds, product_meta, product_ids_by_currency


def build_holiday_calendars(rng: random.Random) -> List[Tuple[str, date]]:
    markets = ["HK", "US", "CN"]
    holiday_rows: List[Tuple[str, date]] = []

    for market in markets:
        picked_days = set()
        while len(picked_days) < HOLIDAYS_PER_MARKET:
            candidate = HOLIDAY_BASE_DATE + timedelta(days=rng.randrange(365))
            if candidate.weekday() < 5:
                picked_days.add(candidate)

        for holiday_date in sorted(picked_days):
            holiday_rows.append((market, holiday_date))

    return holiday_rows


def reset_and_seed_reference_tables(
    conn,
    products: List[Tuple],
    pds: List[Tuple],
    holiday_rows: List[Tuple[str, date]],
) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE orders, products, pds, holiday_calendars RESTART IDENTITY CASCADE;")

        execute_values(
            cur,
            """
            INSERT INTO products
                (id, name, currency, creation_unit_size, cutoff_time, has_qdii_quota, remaining_quota)
            VALUES %s
            """,
            products,
            page_size=500,
        )

        execute_values(
            cur,
            "INSERT INTO pds (id, name) VALUES %s",
            pds,
            page_size=200,
        )

        execute_values(
            cur,
            "INSERT INTO holiday_calendars (market, holiday_date) VALUES %s",
            holiday_rows,
            page_size=200,
        )


def generate_order_row(
    i: int,
    rng: random.Random,
    pd_ids: List[str],
    product_ids: List[str],
    product_meta: Dict[str, Dict[str, int]],
    product_ids_by_currency: Dict[str, List[str]],
) -> List[str]:
    if i < len(STATUSES):
        status = STATUSES[i]
    else:
        status = STATUSES[rng.randrange(len(STATUSES))]

    if i < len(CURRENCIES):
        currency = CURRENCIES[i]
        product_id = product_ids_by_currency[currency][0]
    else:
        product_id = product_ids[rng.randrange(len(product_ids))]
        currency = product_meta[product_id]["currency"]

    pd_id = pd_ids[rng.randrange(len(pd_ids))]
    order_type = ORDER_TYPES[rng.randrange(len(ORDER_TYPES))]

    creation_unit_size = product_meta[product_id]["creation_unit_size"]
    unit_multiple = rng.randint(1, 15)
    units = creation_unit_size * unit_multiple

    nav = rng.uniform(5.0, 250.0)
    cash_amount = units / creation_unit_size * nav

    submitted_dt = BASE_DATE + timedelta(seconds=rng.randrange(SECONDS_SPAN))

    if status == "SETTLED":
        settlement_date = business_days_after(submitted_dt.date(), 2).isoformat()
    else:
        settlement_date = ""

    return [
        f"COID-{i + 1:07d}",
        product_id,
        pd_id,
        order_type,
        str(units),
        f"{cash_amount:.4f}",
        currency,
        status,
        submitted_dt.isoformat(),
        settlement_date,
    ]


def copy_orders(
    conn,
    rng: random.Random,
    product_meta: Dict[str, Dict[str, int]],
    product_ids_by_currency: Dict[str, List[str]],
) -> None:
    product_ids = list(product_meta.keys())
    pd_ids = [f"PD{i:02d}" for i in range(1, PD_COUNT + 1)]

    insert_start = time.perf_counter()
    with conn.cursor() as cur:
        for batch_start in range(0, ORDER_COUNT, COPY_BATCH_SIZE):
            batch_end = min(batch_start + COPY_BATCH_SIZE, ORDER_COUNT)
            buffer = io.StringIO()
            writer = csv.writer(buffer)

            for i in range(batch_start, batch_end):
                writer.writerow(
                    generate_order_row(
                        i,
                        rng,
                        pd_ids,
                        product_ids,
                        product_meta,
                        product_ids_by_currency,
                    )
                )

            buffer.seek(0)
            cur.copy_expert(
                """
                COPY orders
                    (client_order_id, product_id, pd_id, order_type, units, cash_amount, currency, status, submitted_at, settlement_date)
                FROM STDIN WITH (FORMAT CSV)
                """,
                buffer,
            )

            print(f"Copied orders {batch_start + 1:,} to {batch_end:,}")

    elapsed = time.perf_counter() - insert_start
    print(f"Inserted {ORDER_COUNT:,} orders in {elapsed:.2f}s")


def main() -> None:
    total_start = time.perf_counter()
    rng = random.Random(SEED)

    print("Building deterministic reference data...")
    products, pds, product_meta, product_ids_by_currency = build_reference_data(rng)
    holiday_rows = build_holiday_calendars(rng)

    print("Connecting to PostgreSQL...")
    with get_connection() as conn:
        conn.autocommit = False

        print("Resetting tables and loading products/PDs/holidays...")
        reset_and_seed_reference_tables(conn, products, pds, holiday_rows)

        print("Bulk loading 1,000,000 orders via COPY...")
        copy_orders(conn, rng, product_meta, product_ids_by_currency)

        conn.commit()

    total_elapsed = time.perf_counter() - total_start
    print(f"Seeding completed in {total_elapsed:.2f}s (seed={SEED}).")


if __name__ == "__main__":
    main()
