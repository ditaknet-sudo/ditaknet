#!/usr/bin/env python3
"""Generate or verify DitakNet's hash-locked dependency sets.

The runtime lock is consumed by the Dockerfile.  The CI lock resolves the
runtime and CI/test inputs together, so the tested environment is a single,
fully pinned dependency graph.  Resolution is universal with Python 3.11 as
the lower bound because 3.11 is the release container's Python version.
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.11.29"
PYTHON_VERSION = "3.11"
COMPILE_COMMAND = "python scripts/lock_dependencies.py"


@dataclass(frozen=True)
class LockSpec:
    output: str
    inputs: tuple[str, ...]


LOCKS = (
    LockSpec("requirements.txt", ("requirements-app-direct.txt",)),
    LockSpec(
        "requirements-ci.txt",
        ("requirements-app-direct.txt", "requirements-ci-direct.txt"),
    ),
)


def _uv_executable() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise RuntimeError(
            f"uv {UV_VERSION} is required; install it before refreshing locks"
        )
    completed = subprocess.run(
        [executable, "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    reported = completed.stdout.strip()
    version_fields = reported.split()
    if len(version_fields) < 2 or version_fields[:2] != ["uv", UV_VERSION]:
        raise RuntimeError(
            f"uv {UV_VERSION} is required, found {reported!r}"
        )
    return executable


def _compile(
    uv: str,
    spec: LockSpec,
    destination: Path,
    *,
    upgrade: bool,
) -> None:
    command = [
        uv,
        "pip",
        "compile",
        *spec.inputs,
        "--output-file",
        str(destination),
        "--python-version",
        PYTHON_VERSION,
        "--universal",
        "--generate-hashes",
        "--custom-compile-command",
        COMPILE_COMMAND,
        "--quiet",
    ]
    if upgrade:
        command.append("--upgrade")
    subprocess.run(command, cwd=ROOT, check=True)


def _check(uv: str) -> int:
    stale = False
    with tempfile.TemporaryDirectory(prefix="ditaknet-lock-check-") as temp_name:
        temp_root = Path(temp_name)
        for spec in LOCKS:
            expected_path = ROOT / spec.output
            if not expected_path.is_file():
                print(f"missing dependency lock: {spec.output}", file=sys.stderr)
                stale = True
                continue

            candidate_path = temp_root / spec.output
            # uv intentionally preserves existing pins unless --upgrade is used.
            # Seed the temporary output so a routine validation does not turn
            # the appearance of a newer transitive release into false drift.
            shutil.copyfile(expected_path, candidate_path)
            _compile(uv, spec, candidate_path, upgrade=False)

            expected = expected_path.read_text(encoding="utf-8")
            candidate = candidate_path.read_text(encoding="utf-8")
            if expected == candidate:
                print(f"dependency lock is current: {spec.output}")
                continue

            stale = True
            print(f"dependency lock is stale: {spec.output}", file=sys.stderr)
            diff = difflib.unified_diff(
                expected.splitlines(),
                candidate.splitlines(),
                fromfile=spec.output,
                tofile=f"regenerated/{spec.output}",
                lineterm="",
            )
            for line in diff:
                print(line, file=sys.stderr)

    return 1 if stale else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify locks without modifying tracked files",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="upgrade eligible transitive dependencies while regenerating",
    )
    args = parser.parse_args()
    if args.check and args.upgrade:
        parser.error("--check and --upgrade cannot be combined")

    try:
        uv = _uv_executable()
        if args.check:
            return _check(uv)
        for spec in LOCKS:
            _compile(uv, spec, ROOT / spec.output, upgrade=args.upgrade)
            print(f"wrote {spec.output}")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"dependency lock operation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
