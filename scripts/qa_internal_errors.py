#!/usr/bin/env python3
"""QA sweep for pages/APIs that must not return raw internal server errors."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5833"

PAGES = [
    "/",
    "/dashboard",
    "/setup",
    "/devices",
    "/discovery",
    "/hosts",
    "/services",
    "/alerts",
    "/results",
    "/maintenance",
    "/system/activity",
    "/system/logs",
    "/settings",
    "/settings/license",
    "/settings/system",
    "/about",
    "/support",
]

APIS = [
    "/health",
    "/health/deep",
    "/api/system/about",
    "/api/system/version",
    "/api/system/update",
    "/api/system/health-dashboard",
    "/api/system/metrics",
    "/api/system/activity/summary",
    "/api/system/activity/jobs",
    "/api/system/activity/events",
    "/api/system/logs",
    "/api/discovery/networks",
    "/api/discovery/settings",
    "/api/license/status",
]


def fetch(path: str) -> tuple[int, str, dict]:
    req = urllib.request.Request(f"{BASE}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, dict(exc.headers)


def main() -> int:
    failures: list[dict] = []
    print(f"QA base: {BASE}")
    for path in PAGES + APIS:
        status, body, headers = fetch(path)
        bad = status == 500 and '"detail":"Internal server error"' in body.replace(" ", "")
        raw_500 = status == 500 and "internal_server_error" not in body
        if bad or (status == 500 and raw_500 and "request_id" not in body):
            failures.append({"path": path, "status": status, "body": body[:300]})
        rid = headers.get("X-Request-ID") or headers.get("x-request-id")
        mark = "FAIL" if path in {f["path"] for f in failures} else "OK"
        print(f"[{mark}] {status:3} {path} request_id={rid or '-'}")

    if failures:
        print("\nFailures:")
        print(json.dumps(failures, indent=2))
        return 1
    print("\nAll checked routes avoided raw internal server error responses.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
