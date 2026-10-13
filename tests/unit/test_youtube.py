"""Scheduling, channel safety, resumable recovery and verified receipts without Google calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.exceptions import InvalidInputError, StageError
from core.reel_metadata import ReelMetadata
from core.storage import read_model, write_model
from core.youtube import (
    API,
    UPLOAD_API,
    Schedule,
    UploadReceipt,
    _safe_session,
    request_body,
    upload_scheduled,
)

CHANNEL = "UC" + "a" * 22
OTHER_CHANNEL = "UC" + "b" * 22
VIDEO_ID = "aBcDeFgHiJk"
SESSION = UPLOAD_API + "?upload_id=private-session"


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


class FakeResponse:
    def __init__(
        self,
        code: int = 200,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = code
        self.headers = headers or {}
        self.data = data or {}

    def json(self) -> dict[str, Any]:
        return self.data


class FakeGoogle:
    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule
        self.channel = schedule.channel_id
        self.video_channel = schedule.channel_id
        self.uploaded = False
        self.inserts = 0
        self.chunks: list[bytes] = []
        self.expired = False
        self.lose_final_response = False
        self.missing_schedule = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if url == f"{API}/channels":
            return FakeResponse(
                data={
                    "items": [
                        {"id": self.channel, "snippet": {"title": "Test channel"}},
                    ]
                }
            )
        if url == f"{API}/videos":
            status: dict[str, Any] = {
                "privacyStatus": "private",
                "publishAt": self.schedule.timestamp,
                "selfDeclaredMadeForKids": self.schedule.made_for_kids,
                "uploadStatus": "uploaded",
            }
            if self.missing_schedule:
                del status["publishAt"]
            return FakeResponse(
                data={
                    "items": [
                        {
                            "id": VIDEO_ID,
                            "snippet": {"channelId": self.video_channel},
                            "status": status,
                            "processingDetails": {"processingStatus": "processing"},
                        }
                    ]
                }
            )
        if method == "POST":
            self.inserts += 1
            assert kwargs["json"]["status"]["privacyStatus"] == "private"
            return FakeResponse(headers={"Location": SESSION})
        assert url == SESSION
        if self.expired:
            return FakeResponse(404)
        if not kwargs["data"]:
            return FakeResponse(data={"id": VIDEO_ID}) if self.uploaded else FakeResponse(308)
        self.chunks.append(kwargs["data"])
        self.uploaded = True
        if self.lose_final_response:
            raise StageError("Network interruption after server accepted final chunk")
        return FakeResponse(data={"id": VIDEO_ID})


@pytest.fixture
def schedule() -> Schedule:
    return Schedule(
        publish_at=datetime.now(UTC) + timedelta(days=1),
        channel_id=CHANNEL,
        made_for_kids=False,
    )


@pytest.fixture
def inputs(tmp_path: Path, schedule: Schedule) -> dict[str, Any]:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video-data")
    metadata_path = tmp_path / "publishing.json"
    write_model(
        metadata_path,
        ReelMetadata(title="A finance question?", description="Hypothetical.", hashtags=["Shorts"]),
    )
    return {
        "video": video,
        "metadata_path": metadata_path,
        "receipt_path": tmp_path / "youtube_upload.json",
        "session_key": "job1",
        "schedule": schedule,
        "rights_confirmed": True,
    }


def test_schedule_requires_timezone_and_future() -> None:
    with pytest.raises(ValidationError, match="time-zone"):
        Schedule(publish_at="2026-10-13T17:00:00", channel_id=CHANNEL, made_for_kids=False)
    schedule = Schedule(
        publish_at="2026-10-13T17:00:00-07:00",
        channel_id=CHANNEL,
        made_for_kids=False,
    )
    assert schedule.timestamp == "2026-10-14T00:00:00Z"
    with pytest.raises(InvalidInputError, match="past"):
        schedule.require_future(datetime(2026, 10, 14, 1, tzinfo=UTC))


def test_body_is_private_and_explicit(schedule: Schedule) -> None:
    metadata = ReelMetadata(title="A question?", description="Hypothetical.", hashtags=["Shorts"])
    body = request_body(metadata, schedule)
    assert body["status"] == {
        "privacyStatus": "private",
        "publishAt": schedule.timestamp,
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": False,
    }


def test_upload_verified_and_repeat_never_duplicates(inputs: dict[str, Any]) -> None:
    http, secrets = FakeGoogle(inputs["schedule"]), MemorySecrets()
    first = upload_scheduled(http, secrets, **inputs)
    assert first.state == "verified" and first.video_id == VIDEO_ID
    assert first.verified_at
    assert http.chunks == [b"video-data"]
    assert SESSION not in inputs["receipt_path"].read_text()
    second = upload_scheduled(http, secrets, **inputs)
    assert second.video_id == first.video_id
    assert http.inserts == 1


def test_lost_final_response_recovers_existing_video(inputs: dict[str, Any]) -> None:
    http, secrets = FakeGoogle(inputs["schedule"]), MemorySecrets()
    http.lose_final_response = True
    with pytest.raises(StageError, match="Network"):
        upload_scheduled(http, secrets, **inputs)
    receipt = read_model(inputs["receipt_path"], UploadReceipt)
    assert receipt.state == "uploading" and receipt.video_id is None
    http.lose_final_response = False
    recovered = upload_scheduled(http, secrets, **inputs)
    assert recovered.state == "verified"
    assert http.inserts == 1 and len(http.chunks) == 1


def test_expired_session_never_starts_second_upload(inputs: dict[str, Any]) -> None:
    http, secrets = FakeGoogle(inputs["schedule"]), MemorySecrets()
    http.lose_final_response = True
    with pytest.raises(StageError):
        upload_scheduled(http, secrets, **inputs)
    http.expired = True
    with pytest.raises(StageError, match="HTTP 404"):
        upload_scheduled(http, secrets, **inputs)
    assert http.inserts == 1


def test_unconfirmed_rights_and_wrong_channel_block_upload(inputs: dict[str, Any]) -> None:
    http, secrets = FakeGoogle(inputs["schedule"]), MemorySecrets()
    with pytest.raises(InvalidInputError, match="rights"):
        upload_scheduled(http, secrets, **{**inputs, "rights_confirmed": False})
    http.channel = OTHER_CHANNEL
    with pytest.raises(InvalidInputError, match="channel"):
        upload_scheduled(http, secrets, **inputs)
    assert http.inserts == 0


def test_schedule_not_retained_saves_id_and_reports_failure(inputs: dict[str, Any]) -> None:
    http, secrets = FakeGoogle(inputs["schedule"]), MemorySecrets()
    http.missing_schedule = True
    with pytest.raises(StageError, match="did not retain"):
        upload_scheduled(http, secrets, **inputs)
    assert read_model(inputs["receipt_path"], UploadReceipt).video_id == VIDEO_ID
    http.missing_schedule = False
    assert upload_scheduled(http, secrets, **inputs).state == "verified"
    assert http.inserts == 1


def test_changed_file_does_not_upload_again(inputs: dict[str, Any]) -> None:
    http, secrets = FakeGoogle(inputs["schedule"]), MemorySecrets()
    upload_scheduled(http, secrets, **inputs)
    inputs["video"].write_bytes(b"different-video")
    with pytest.raises(InvalidInputError, match="inputs differ"):
        upload_scheduled(http, secrets, **inputs)
    assert http.inserts == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://www.googleapis.com/upload/youtube/v3/videos?x=1",
        "https://example.com/upload/youtube/v3/videos",
        "https://www.googleapis.com@evil.example/upload/youtube/v3/videos",
        "https://www.googleapis.com:444/upload/youtube/v3/videos",
    ],
)
def test_unexpected_session_host_is_rejected(url: str) -> None:
    with pytest.raises(StageError, match="endpoint"):
        _safe_session(url)
