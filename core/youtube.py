"""Scheduling and resumable uploading with a persistent, non-secret receipt per job."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.exceptions import InvalidInputError, StageError
from core.reel_input import sha256_of
from core.reel_metadata import ReelMetadata
from core.storage import read_model, write_model

CHUNK_SIZE = 8 * 1024 * 1024  # YouTube requires multiples of 256 KiB except the final chunk.
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3/videos"


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_at: datetime
    channel_id: str = Field(pattern=r"^UC[A-Za-z0-9_-]{22}$")
    made_for_kids: bool
    contains_synthetic_media: bool = False

    @field_validator("publish_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("publish_at requires an explicit time-zone offset")
        return value.astimezone(UTC)

    def require_future(self, now: datetime | None = None) -> None:
        if self.publish_at <= (now or datetime.now(UTC)):
            raise InvalidInputError("Publish time is in the past; refusing immediate publication.")

    @property
    def timestamp(self) -> str:
        return self.publish_at.isoformat().replace("+00:00", "Z")


class UploadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["prepared", "uploading", "uploaded", "verified"] = "prepared"
    channel_id: str
    video_sha256: str
    metadata_sha256: str
    schedule: Schedule
    rights_confirmed: bool
    video_id: str | None = None
    verified_at: datetime | None = None


class Response(Protocol):
    status_code: int
    headers: Any

    def json(self) -> Any: ...


class Transport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class Secrets(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


def _object(response: Response) -> dict[str, Any]:
    data = response.json()
    if not isinstance(data, dict):
        raise StageError("YouTube returned a non-object response")
    return data


def _check(response: Response, expected: tuple[int, ...] = (200,)) -> None:
    if response.status_code in expected:
        return
    # Do not echo HTTP URLs, headers, credentials or session identifiers.
    reason = "unknown"
    try:
        errors = _object(response).get("error", {}).get("errors", [])
        if errors and isinstance(errors[0], dict):
            candidate = str(errors[0].get("reason", "unknown"))
            if re.fullmatch(r"[A-Za-z0-9_]+", candidate):
                reason = candidate
    except (ValueError, StageError):
        reason = "unreadable_response"
    raise StageError(
        f"YouTube HTTP {response.status_code} ({reason}). "
        "The local receipt was retained; do not create a second upload blindly."
    )


def channel_identity(http: Transport) -> tuple[str, str]:
    response = http.request(
        "GET",
        f"{API}/channels",
        params={"part": "id,snippet", "mine": "true"},
        timeout=60,
    )
    _check(response)
    items = _object(response).get("items", [])
    if len(items) != 1:
        raise InvalidInputError(
            "Authorization must resolve to exactly one YouTube channel. "
            "Sign in again and select the intended channel."
        )
    return str(items[0]["id"]), str(items[0]["snippet"]["title"])


def request_body(metadata: ReelMetadata, schedule: Schedule) -> dict[str, Any]:
    return {
        "snippet": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.hashtags,
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": schedule.timestamp,
            "selfDeclaredMadeForKids": schedule.made_for_kids,
            "containsSyntheticMedia": schedule.contains_synthetic_media,
        },
    }


def _safe_session(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.googleapis.com"
        or not parsed.path.startswith("/upload/youtube/v3/videos")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise StageError("YouTube returned an unexpected resumable-upload endpoint")
    return url


def _offset(response: Response, size: int) -> int:
    value = response.headers.get("Range")
    if not value:
        return 0
    match = re.fullmatch(r"bytes=0-(\d+)", value)
    if not match or not 0 <= int(match[1]) < size:
        raise StageError("YouTube returned an invalid resumable-upload byte range")
    return int(match[1]) + 1


def verify_schedule(
    http: Transport,
    receipt: UploadReceipt,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    response = http.request(
        "GET",
        f"{API}/videos",
        params={"part": "snippet,status,processingDetails", "id": receipt.video_id},
        timeout=60,
    )
    _check(response)
    items = _object(response).get("items", [])
    if len(items) != 1:
        raise StageError("Uploaded video could not be retrieved; schedule is not verified.")
    video = items[0]
    status = video.get("status", {})
    if video.get("snippet", {}).get("channelId") != receipt.channel_id:
        raise StageError("The uploaded video's channel differs from the approved channel.")
    if status.get("uploadStatus") in {"failed", "rejected", "deleted"}:
        raise StageError(f"YouTube video upload status: {status['uploadStatus']}")
    if video.get("processingDetails", {}).get("processingStatus") in {"failed", "terminated"}:
        raise StageError("YouTube video processing failed.")
    if status.get("selfDeclaredMadeForKids") != receipt.schedule.made_for_kids:
        raise StageError("YouTube did not confirm the requested audience designation.")
    current_time = now or datetime.now(UTC)
    if receipt.schedule.publish_at <= current_time:
        if status.get("privacyStatus") != "public":
            raise StageError("Scheduled time has passed but the video is not public.")
    else:
        actual = status.get("publishAt")
        if status.get("privacyStatus") != "private" or not isinstance(actual, str):
            raise StageError("YouTube did not retain the private, future publication schedule.")
        parsed = datetime.fromisoformat(actual.replace("Z", "+00:00"))
        if parsed != receipt.schedule.publish_at:
            raise StageError(
                "YouTube's scheduled publication time differs from the requested time."
            )
    return dict(video)


def upload_scheduled(
    http: Transport,
    secrets: Secrets,
    *,
    video: Path,
    metadata_path: Path,
    receipt_path: Path,
    session_key: str,
    schedule: Schedule,
    rights_confirmed: bool,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> UploadReceipt:
    """Resume the same upload across runs. Unknown/expired sessions never trigger a new upload."""
    if not rights_confirmed:
        raise InvalidInputError("Publishing rights must be explicitly confirmed.")
    if not video.is_file() or not video.stat().st_size:
        raise InvalidInputError("Final video is missing or empty.")
    metadata = read_model(metadata_path, ReelMetadata)
    intended = UploadReceipt(
        channel_id=schedule.channel_id,
        video_sha256=sha256_of(video),
        metadata_sha256=sha256_of(metadata_path),
        schedule=schedule,
        rights_confirmed=True,
    )
    receipt = read_model(receipt_path, UploadReceipt) if receipt_path.is_file() else intended
    if (
        receipt.channel_id != intended.channel_id
        or receipt.schedule != intended.schedule
        or receipt.video_sha256 != intended.video_sha256
        or receipt.metadata_sha256 != intended.metadata_sha256
    ):
        raise InvalidInputError(
            "Upload inputs differ from the saved receipt. Refusing a duplicate or changed upload."
        )
    channel_id, _ = channel_identity(http)
    if channel_id != schedule.channel_id:
        raise InvalidInputError("Authorized channel differs from --channel-id; nothing uploaded.")
    if receipt.video_id:
        verify_schedule(http, receipt)
        receipt.state = "verified"
        receipt.verified_at = datetime.now(UTC)
        write_model(receipt_path, receipt)
        return receipt
    schedule.require_future()
    size = video.stat().st_size
    session = secrets.get(session_key)
    if receipt.state == "uploading" and not session:
        raise StageError(
            "Upload session is missing. Reconcile the channel in YouTube Studio; "
            "refusing a new upload because the previous result is uncertain."
        )
    if not receipt_path.is_file():
        write_model(receipt_path, receipt)
    if not session:
        response = http.request(
            "POST",
            UPLOAD_API,
            params={
                "uploadType": "resumable",
                "part": "snippet,status",
                "notifySubscribers": "false",
            },
            headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
            json=request_body(metadata, schedule),
            timeout=60,
        )
        _check(response, (200, 201))
        session = _safe_session(str(response.headers.get("Location", "")))
        secrets.set(session_key, session)
        receipt.state = "uploading"
        write_model(receipt_path, receipt)
    session = _safe_session(session)
    response = http.request(
        "PUT",
        session,
        headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
        data=b"",
        timeout=120,
    )
    _check(response, (200, 201, 308))
    offset = _offset(response, size) if response.status_code == 308 else size
    with video.open("rb") as source:
        while offset < size:
            # A retry must not turn a missed schedule into immediate publication.
            schedule.require_future()
            source.seek(offset)
            chunk = source.read(min(CHUNK_SIZE, size - offset))
            response = http.request(
                "PUT",
                session,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}",
                },
                data=chunk,
                timeout=180,
            )
            _check(response, (200, 201, 308))
            next_offset = _offset(response, size) if response.status_code == 308 else size
            if next_offset <= offset or next_offset > offset + len(chunk):
                raise StageError(
                    "Upload did not advance consistently. Re-run to query the session."
                )
            offset = next_offset
            progress(offset, size)
    result = _object(response)
    video_id = result.get("id")
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise StageError(
            "Upload finished without a valid video ID; retain the session for recovery."
        )
    receipt.video_id = video_id
    receipt.state = "uploaded"
    # Save ID before read-back so failed verification cannot duplicate the upload.
    write_model(receipt_path, receipt)
    verify_schedule(http, receipt)
    receipt.state = "verified"
    receipt.verified_at = datetime.now(UTC)
    write_model(receipt_path, receipt)
    return receipt
