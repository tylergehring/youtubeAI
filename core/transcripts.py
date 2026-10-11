"""Parse user-supplied transcript files (.txt, .srt, .vtt) into the Transcript model.

Parsing also removes non-speech noise that auto-generated transcripts contain
(`[music]`, `[Applause]`, `>>` speaker changes, ♪ symbols) and breaks long single-block text
into readable chunks. The original file is never modified.
"""

from __future__ import annotations

import re

from core.exceptions import InvalidInputError
from core.models.content import Transcript, TranscriptSegment

SUPPORTED_FORMATS = ("txt", "srt", "vtt")
MAX_SEGMENT_CHARS = 800  # untimed text longer than this is split into chunks

_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE_TIME_RE = re.compile(rf"({_TIME})\s*-->\s*({_TIME})")
_TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
_STAMP_LINE_RE = re.compile(r"^\[?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]?$")
_SKIP_VTT_BLOCKS = ("WEBVTT", "NOTE", "STYLE", "REGION")
# Bracketed sound cues such as [music] or [ __ ]; numeric citations like [1] are kept.
_CUE_MARKER_RE = re.compile(r"\[(?!\d+\])[^\]\n]{0,40}\]|[♪♫]+")
_SPEAKER_RE = re.compile(r"\s*>>+\s*")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str | None = None) -> float:
    whole = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    fraction = int(millis.ljust(3, "0")) / 1000 if millis else 0.0
    return whole + fraction


class _Cleaner:
    """Strips markup and non-speech markers, counting the markers removed."""

    def __init__(self) -> None:
        self.markers_removed = 0

    def clean(self, text: str) -> str:
        text = _TAG_RE.sub("", text)
        text, count = _CUE_MARKER_RE.subn(" ", text)
        self.markers_removed += count
        return " ".join(_SPEAKER_RE.sub(" ", text).split())


def _chunk(text: str, max_chars: int) -> list[str]:
    """Greedily pack whole sentences into chunks; hard-split sentences with no punctuation."""
    pieces: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        words = sentence.split()
        while words and len(" ".join(words)) > max_chars:
            take, size = 0, 0
            while take < len(words) and size + len(words[take]) + 1 <= max_chars:
                size += len(words[take]) + 1
                take += 1
            take = max(take, 1)
            pieces.append(" ".join(words[:take]))
            words = words[take:]
        if words:
            pieces.append(" ".join(words))

    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 1 + len(piece) <= max_chars:
            chunks[-1] = f"{chunks[-1]} {piece}"
        else:
            chunks.append(piece)
    return chunks


def _parse_cues(text: str, cleaner: _Cleaner, *, vtt: bool) -> list[TranscriptSegment]:
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
        body = cleaner.clean(" ".join(lines[timing + 1 :]))
        if body:
            segments.append(TranscriptSegment(start_s=start, end_s=max(start, end), text=body))
    return segments


def _parse_plain(text: str, cleaner: _Cleaner) -> list[TranscriptSegment]:
    """Paragraphs split by blank lines or `>>`; standalone `0:05` lines act as timestamps."""
    segments: list[TranscriptSegment] = []
    paragraph: list[str] = []
    pending_start: float | None = None

    def flush() -> None:
        nonlocal pending_start
        body = cleaner.clean(" ".join(paragraph))
        for index, chunk in enumerate(_chunk(body, MAX_SEGMENT_CHARS) if body else []):
            segments.append(
                TranscriptSegment(start_s=pending_start if index == 0 else None, text=chunk)
            )
        paragraph.clear()
        pending_start = None

    for raw in _SPEAKER_RE.sub("\n\n", text).split("\n"):
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
    cleaner = _Cleaner()
    if fmt == "txt":
        segments = _parse_plain(text, cleaner)
    else:
        segments = _parse_cues(text, cleaner, vtt=fmt == "vtt")
    if not segments:
        raise InvalidInputError(f"The {fmt} transcript contains no usable text.")
    return Transcript(format=fmt, segments=segments, markers_removed=cleaner.markers_removed)
