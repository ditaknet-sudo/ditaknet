"""
Agent token helpers.

Tokens are shown once to the operator at registration; only the hash is persisted
so DB backups do not contain usable credentials.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_agent_token() -> str:
    """Create a new opaque agent authentication token."""
    return secrets.token_urlsafe(32)


def hash_agent_token(token: str) -> str:
    """Hash an agent token for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
