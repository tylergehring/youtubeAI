"""Explicit public-caption intake. No cookies, proxies, audio downloads or bypasses."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from pydantic import Field

from core.exceptions import ConfigError, InvalidInputError, ProviderError
from core.hashing import file_sha256
from core.models.content import Strict, Transcript, TranscriptSegment
from core.models.manifest import utcnow
from core.storage import atomic_write_text, write_model

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YouTubeSource(Strict):
    video_id: str
    reference_url: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    language: str
    is_generated: bool
    transcript_sha256: str
    provider: str = "youtube-transcript-api"
    notes: str = (
        "Research input only, not verified facts or a reuse licence. Automatic captions may "
        "mishear numbers. No source footage or audio is included in the output."
    )


def video_id_from_url(value: str) -> str:
    value = value.strip()
    if _ID.fullmatch(value):
        return value
    parsed = urlparse(value)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
        or parsed.username
        or parsed.password
        or parsed.netloc != host
    ):
        raise InvalidInputError("Supply a YouTube video ID or a standard HTTPS YouTube URL.")
    parts = parsed.path.strip("/").split("/")
    candidate = ""
    if host == "youtu.be" and len(parts) == 1:
        candidate = parts[0]
    elif parsed.path == "/watch":
        ids = parse_qs(parsed.query).get("v", [])
        if len(ids) == 1:
            candidate = ids[0]
    elif len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
        candidate = parts[1]
    if not _ID.fullmatch(candidate):
        raise InvalidInputError("The YouTube URL must identify exactly one valid video.")
    return candidate


class _TimedSession(requests.Session):
    def request(
        self,
        method: str | bytes,
        url: str | bytes,
        *args: Any,
        **kwargs: Any,
    ) -> requests.Response:
        if len(args) < 7:
            kwargs.setdefault("timeout", (10, 30))
        return super().request(method, url, *args, **kwargs)


def fetch_transcript(value: str, destination: Path, languages: list[str]) -> YouTubeSource:
    """Save a typed timestamped transcript and provenance; refuse accidental replacement."""
    video_id = video_id_from_url(value)
    if not languages or any(not language.strip() for language in languages):
        raise InvalidInputError("At least one non-empty caption language is required.")
    if destination.exists() and any(destination.iterdir()):
        raise InvalidInputError(f"Research destination is not empty: {destination}")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, YouTubeTranscriptApiException
    except ImportError as exc:
        raise ConfigError('Install transcript support: pip install -e ".[transcripts]"') from exc
    try:
        with _TimedSession() as session:
            fetched = YouTubeTranscriptApi(http_client=session).fetch(video_id, languages=languages)
    except (YouTubeTranscriptApiException, requests.RequestException) as exc:
        raise ProviderError(
            f"Caption retrieval failed ({type(exc).__name__}) for {video_id}. "
            "No transcript was invented. Supply a permitted local transcript if unavailable; "
            "this command does not bypass access restrictions."
        ) from exc
    transcript = Transcript(
        format="youtube",
        segments=[
            TranscriptSegment(start_s=s.start, end_s=s.start + s.duration, text=s.text)
            for s in fetched
            if s.text.strip()
        ],
    )
    path = destination / "transcript.json"
    write_model(path, transcript)
    source = YouTubeSource(
        video_id=video_id,
        reference_url=f"https://www.youtube.com/watch?v={video_id}",
        language=fetched.language_code,
        is_generated=fetched.is_generated,
        transcript_sha256=file_sha256(path),
    )
    write_model(destination / "source.json", source)
    atomic_write_text(destination / "transcript.txt", transcript.text + "\n")
    return source
