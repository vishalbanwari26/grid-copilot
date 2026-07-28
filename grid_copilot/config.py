"""Minimal .env loading, so live providers can find their keys.

Cortex's clients read keys from the environment only (never hard-coded, never
committed). This helper loads a local ``.env`` into ``os.environ`` if present, so
``--provider anthropic`` works without the user having to export the key by hand.
It is intentionally tiny and dependency-free: ``KEY=value`` lines, ``#`` comments,
and it never overwrites a variable already set in the real environment.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
