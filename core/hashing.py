"""Stable hashing used for idempotency fingerprints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def fingerprint(*parts: Any) -> str:
    """SHA-256 over a canonical JSON encoding of the given parts."""
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
