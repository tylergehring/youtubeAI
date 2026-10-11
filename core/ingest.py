"""Validate and read user-supplied transcript files before they enter a job."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.config import IngestSettings
from core.exceptions import InvalidInputError
from core.naming import sanitize_filename, validate_reference_url
from core.transcripts import parse_transcript


@dataclass(frozen=True)
class SourceSpec:
    """What the user asked to ingest."""

    path: Path
    label: str | None = None
    reference_url: str | None = None


@dataclass(frozen=True)
class PreparedSource:
    """A validated transcript, read into memory and ready to be stored in a job."""

    name: str
    label: str
    reference_url: str | None
    data: bytes
    sha256: str
    format: str


def check_location(path: Path, ingest: IngestSettings) -> None:
    if not ingest.allowed_dirs:
        return
    resolved = path.resolve()
    if not any(resolved.is_relative_to(d.resolve()) for d in ingest.allowed_dirs):
        raise InvalidInputError(
            f"{path.name}: not inside an allowed transcript directory "
            f"({', '.join(str(d) for d in ingest.allowed_dirs)})"
        )


def prepare_sources(specs: Sequence[SourceSpec], ingest: IngestSettings) -> list[PreparedSource]:
    """Validate every spec up front so a bad file never leaves a half-created job behind."""
    if len(specs) > ingest.max_sources:
        raise InvalidInputError(
            f"Too many transcripts ({len(specs)}); maximum is {ingest.max_sources}."
        )
    allowed = {e.lower() for e in ingest.extensions}
    prepared: list[PreparedSource] = []
    seen: set[str] = set()

    for spec in specs:
        path = Path(spec.path)
        if not path.is_file():
            raise InvalidInputError(f"Transcript file not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in allowed:
            raise InvalidInputError(
                f"{path.name}: unsupported file type {suffix!r}; allowed: {sorted(allowed)}"
            )
        check_location(path, ingest)
        size = path.stat().st_size
        if size == 0 or size > ingest.max_file_mb * 1024 * 1024:
            raise InvalidInputError(
                f"{path.name}: file must be non-empty and at most {ingest.max_file_mb} MB"
            )
        data = path.read_bytes()
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidInputError(f"{path.name}: transcript must be UTF-8 text") from exc

        fmt = suffix.lstrip(".")
        parse_transcript(text, fmt)  # raises if there is nothing usable
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            raise InvalidInputError(f"{path.name}: duplicate of another supplied transcript")
        seen.add(digest)

        label = (spec.label or path.stem).strip()[:120] or path.stem
        url = validate_reference_url(spec.reference_url) if spec.reference_url else None
        prepared.append(PreparedSource(sanitize_filename(path.name), label, url, data, digest, fmt))
    return prepared
