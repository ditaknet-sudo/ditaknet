"""Reporting and CSV export helpers."""

from __future__ import annotations

import csv
from io import StringIO
from statistics import mean
from typing import Iterable

OK_STATUSES = {"ok", "up", "recovery"}
FAIL_STATUSES = {"warning", "critical", "down"}


def calculate_summary(rows: Iterable[dict]) -> dict:
    data = list(rows)
    total = len(data)
    ok_count = sum(1 for row in data if str(row.get("status", "")).lower() in OK_STATUSES)
    fail_count = sum(1 for row in data if str(row.get("status", "")).lower() in FAIL_STATUSES)
    unknown_count = max(total - ok_count - fail_count, 0)
    response_times = [
        float(row["response_time_ms"])
        for row in data
        if row.get("response_time_ms") is not None
    ]
    availability = (ok_count / total * 100.0) if total else 100.0
    failure_rate = (fail_count / total * 100.0) if total else 0.0
    return {
        "total_checks": total,
        "ok_checks": ok_count,
        "failed_checks": fail_count,
        "unknown_checks": unknown_count,
        "availability_percent": round(availability, 2),
        "failure_rate_percent": round(failure_rate, 2),
        "average_response_time_ms": round(mean(response_times), 2) if response_times else 0.0,
        "min_response_time_ms": round(min(response_times), 2) if response_times else 0.0,
        "max_response_time_ms": round(max(response_times), 2) if response_times else 0.0,
    }


def service_breakdown(rows: Iterable[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["service_id"]), []).append(row)
    result = []
    for service_id, service_rows in sorted(grouped.items()):
        first = service_rows[0]
        result.append(
            {
                "service_id": service_id,
                "service_name": first.get("service_name", "Unknown"),
                "host_id": first.get("host_id"),
                "host_name": first.get("host_name", "Unknown"),
                **calculate_summary(service_rows),
            }
        )
    return result


def checks_to_csv(rows: Iterable[dict]) -> str:
    output = StringIO(newline="")
    fieldnames = [
        "id",
        "service_id",
        "service_name",
        "host_id",
        "host_name",
        "status",
        "response_time_ms",
        "message",
        "checked_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return output.getvalue()
