"""Global test isolation established before application modules are imported."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


# Keep every default test run away from repository and production runtime data.
# Force isolation even when pytest is launched from a shell that contains
# production variables. Tests must never inherit a real DATA_DIR/database.
_TEST_ROOT_HANDLE = tempfile.TemporaryDirectory(prefix="ditaknet-tests-")
TEST_RUNTIME_ROOT = Path(_TEST_ROOT_HANDLE.name)

os.environ["APP_ENV"] = "development"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DITAKNET_UPDATE_CHECK_ENABLED"] = "false"
os.environ["DATA_DIR"] = str(TEST_RUNTIME_ROOT / "data")
os.environ["LOG_DIR"] = str(TEST_RUNTIME_ROOT / "logs")
os.environ["BACKUP_DIR"] = str(TEST_RUNTIME_ROOT / "backups")
os.environ["PLUGIN_DIR"] = str(TEST_RUNTIME_ROOT / "plugins")
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_PATH"] = str(TEST_RUNTIME_ROOT / "ditaknet-test.db")
os.environ["SESSION_SECRET"] = "ditaknet-test-session-secret-not-for-production"

for directory_name in ("data", "logs", "backups", "plugins"):
    (TEST_RUNTIME_ROOT / directory_name).mkdir(parents=True, exist_ok=True)
