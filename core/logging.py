"""Structured logging: every record carries job ID and stage; secrets are redacted."""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT_LOGGER = "vidgen"
LOG_FORMAT = "[%(asctime)s] [JOB=%(job_id)s] [STAGE=%(stage)s] [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)(\s*[=:]\s*)[^\s,;'\"]+"),
]
REDACTED = "[REDACTED]"


def _secret_values(environ: dict[str, str] | None = None) -> list[str]:
    environ = dict(os.environ) if environ is None else environ
    return [
        value
        for name, value in environ.items()
        if len(value) >= 8 and any(hint in name.upper() for hint in _SECRET_ENV_HINTS)
    ]


def redact(text: str, environ: dict[str, str] | None = None) -> str:
    """Remove known secret values and common key/token patterns from text."""
    for value in _secret_values(environ):
        text = text.replace(value, REDACTED)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        elif pattern.groups == 1:
            text = pattern.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.job_id = getattr(record, "job_id", "-")
        record.stage = getattr(record, "stage", "-")
        return redact(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    """Attach a single stderr handler to the application logger (idempotent)."""
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(level.upper())
    logger.propagate = False
    if not any(getattr(h, "_vidgen_console", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))
        handler._vidgen_console = True  # type: ignore[attr-defined]
        logger.addHandler(handler)


def get_logger(job_id: str = "-", stage: str = "-") -> logging.LoggerAdapter[logging.Logger]:
    return logging.LoggerAdapter(
        logging.getLogger(f"{ROOT_LOGGER}.{stage}"), {"job_id": job_id, "stage": stage}
    )


@contextmanager
def job_log_file(path: Path) -> Iterator[None]:
    """Also write all application logs to the job's log file for the duration of the block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))
    logger = logging.getLogger(ROOT_LOGGER)
    logger.addHandler(handler)
    previous_level = logger.level
    if previous_level == logging.NOTSET:  # not configured (e.g. library use): still log INFO
        logger.setLevel(logging.INFO)
    try:
        yield
    finally:
        logger.setLevel(previous_level)
        logger.removeHandler(handler)
        handler.close()
