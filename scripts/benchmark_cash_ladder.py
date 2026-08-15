#!/usr/bin/env python3
"""Benchmark cash ladder endpoint latency with warmup and percentile summary."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def run_benchmark(url: str, warmup: int, runs: int, timeout: int) -> dict:
    wall_ms: list[float] = []
    api_ms: list[float] = []

    for i in range(warmup + runs):
        start = time.perf_counter()
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        elapsed_ms = (time.perf_counter() - start) * 1000

        if i >= warmup:
            wall_ms.append(elapsed_ms)
            api_ms.append(float(payload.get("responseTimeMs", 0)))

    return {
        "url": url,
        "warmup": warmup,
        "runs": runs,
        "wall_ms": {
            "p50": round(percentile(wall_ms, 0.50), 2),
            "p95": round(percentile(wall_ms, 0.95), 2),
            "p99": round(percentile(wall_ms, 0.99), 2),
            "avg": round(sum(wall_ms) / len(wall_ms), 2),
        },
        "api_responseTimeMs": {
            "p50": round(percentile(api_ms, 0.50), 2),
            "p95": round(percentile(api_ms, 0.95), 2),
            "p99": round(percentile(api_ms, 0.99), 2),
            "avg": round(sum(api_ms) / len(api_ms), 2),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark cash ladder endpoint latency")
    parser.add_argument("--url", required=True)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output", default="benchmark.json")
    args = parser.parse_args()

    result = run_benchmark(args.url, args.warmup, args.runs, args.timeout)
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
