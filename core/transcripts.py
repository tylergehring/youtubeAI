"""Parse user-supplied transcript files (.txt, .srt, .vtt) into the Transcript model."""

from __future__ import annotations

import re

from core.exceptions import InvalidInputError
from core.models.content import Transcript, TranscriptSegment

SUPPORTED_FORMATS = ("txt", "srt", "vtt")

_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE_TIME_RE = re.compile(rf"({_TIME})\s*-->\s*({_TIME})")
_TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
_STAMP_LINE_RE = re.compile(r"^\[?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]?$")
_SKIP_VTT_BLOCKS = ("WEBVTT", "NOTE", "STYLE", "REGION")


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str | None = None) -> float:
    whole = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    fraction = int(millis.ljust(3, "0")) / 1000 if millis else 0.0
    return whole + fraction


def _clean(text: str) -> str:
    return " ".join(_TAG_RE.sub("", text).split())


def _parse_cues(text: str, *, vtt: bool) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines or (vtt and lines[0].startswith(_SKIP_VTT_BLOCKS)):
            continue
        timing = next((i for i, line in enumerate(lines) if _CUE_TIME_RE.search(line)), None)
        if timing is None:
            continue
        match = _CUE_TIME_RE.search(lines[timing])
        if match is None:
            continue
        start = _seconds(*match.group(2, 3, 4, 5))
        end = _seconds(*match.group(7, 8, 9, 10))
        body = _clean(" ".join(lines[timing + 1 :]))
        if body:
            segments.append(TranscriptSegment(start_s=start, end_s=max(start, end), text=body))
    return segments


def _parse_plain(text: str) -> list[TranscriptSegment]:
    """Paragraphs separated by blank lines; standalone `0:05` lines act as timestamps."""
    segments: list[TranscriptSegment] = []
    paragraph: list[str] = []
    pending_start: float | None = None

    def flush() -> None:
        nonlocal pending_start
        body = _clean(" ".join(paragraph))
        if body:
            segments.append(TranscriptSegment(start_s=pending_start, text=body))
        paragraph.clear()
        pending_start = None

    for raw in text.split("\n"):
        line = raw.strip()
        stamp = _STAMP_LINE_RE.match(line)
        if not line:
            flush()
        elif stamp:
            flush()
            pending_start = _seconds(*stamp.groups())
        else:
            paragraph.append(line)
    flush()
    return segments


def parse_transcript(content: str, fmt: str) -> Transcript:
    """Parse transcript text. Raises InvalidInputError if no usable text is found."""
    fmt = fmt.lower().lstrip(".")
    if fmt not in SUPPORTED_FORMATS:
        raise InvalidInputError(f"Unsupported transcript format {fmt!r}; use {SUPPORTED_FORMATS}")
    text = content.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    segments = _parse_plain(text) if fmt == "txt" else _parse_cues(text, vtt=fmt == "vtt")
    if not segments:
        raise InvalidInputError(f"The {fmt} transcript contains no usable text.")
    return Transcript(format=fmt, segments=segments)
