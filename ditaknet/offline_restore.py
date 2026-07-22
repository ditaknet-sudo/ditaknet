"""CLI entry point for a stopped-container DitakNet database restore."""

from __future__ import annotations

import argparse
import json
import sys

from ditaknet.core.process_lock import RuntimeLockError
from ditaknet.core.restore import restore_backup_offline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore a validated DitakNet backup only while the web service is stopped"
        )
    )
    parser.add_argument("--backup", required=True, help="backup filename in BACKUP_DIR")
    parser.add_argument(
        "--expected-sha256",
        required=True,
        help="approved SHA-256 printed by validation/preflight",
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help='exact destructive confirmation: "RESTORE <backup filename>"',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = restore_backup_offline(
            args.backup,
            confirmation=args.confirm,
            expected_sha256=args.expected_sha256,
        )
    except (OSError, RuntimeError, ValueError, RuntimeLockError) as exc:
        print(f"offline restore failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
