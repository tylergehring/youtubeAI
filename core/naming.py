"""Input sanitisation helpers: slugs, job IDs and URL validation."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from core.exceptions import InvalidInputError

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")


def slugify(text: str, max_length: int = 40) -> str:
    """Lowercase, ASCII-only, filesystem-safe slug. Never returns an empty string."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_length].strip("_")
    return slug or "untitled"


def validate_job_id(job_id: str) -> str:
    """Reject anything that could escape the jobs directory or is awkward on disk."""
    if not _JOB_ID_RE.fullmatch(job_id):
        raise InvalidInputError(
            f"Invalid job id {job_id!r}: use 1-100 letters, digits, '_' or '-', "
            "starting with a letter or digit."
        )
    return job_id


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Safe file name: drops any directory part, keeps a short ASCII-only stem and suffix."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    clean_suffix = re.sub(r"[^a-z0-9]", "", suffix.lower())[:8]
    clean_stem = slugify(stem, max_length)
    return f"{clean_stem}.{clean_suffix}" if clean_suffix else clean_stem


def validate_reference_url(url: str) -> str:
    """Accept only http(s) URLs. The URL is stored as a citation and is never fetched."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidInputError(f"Reference URL must be a valid http(s) URL: {url!r}")
    return url.strip()
