"""Upload an approved reel or reviewed editorial topic and verify its future schedule."""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from core.config import load_settings
from core.exceptions import InvalidInputError
from core.hashing import file_sha256
from core.jobs import JobStore
from core.models.manifest import JobFormat, JobStatus
from core.models.media import ValidationReport
from core.naming import validate_job_id
from core.storage import read_json, read_model, write_json
from core.youtube import (
    Schedule,
    Secrets,
    Transport,
    UploadReceipt,
    channel_identity,
    upload_scheduled,
)
from core.youtube_auth import CredentialStore, GoogleTransport, login
from pipeline import artifacts
from pipeline.stage import EXIT_OK, handle_errors


def upload_job(
    store: JobStore,
    http: Transport,
    secrets: Secrets,
    job_id: str,
    schedule: Schedule,
    *,
    approve: bool,
    confirm_rights: bool,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> UploadReceipt:
    paths = store.paths(job_id)
    if not paths.manifest.is_file():
        raise InvalidInputError(f"Job {job_id!r} not found.")
    with FileLock(str(paths.root / ".youtube.lock"), timeout=1):
        job = store.load(job_id)
        if job.input.format is not JobFormat.FINANCE_FACTS:
            raise InvalidInputError("This uploader currently supports finance_facts reels only.")
        report = read_model(artifacts.validation_json(paths), ValidationReport)
        if not report.technical_passed:
            raise InvalidInputError("Technical QA failed; upload is blocked.")
        receipt_path = paths.root / "youtube_upload.json"
        if job.status is JobStatus.AWAITING_REVIEW:
            if not approve:
                raise InvalidInputError("Review the final video and supply --approve.")
            schedule.require_future()
            store.set_status(job_id, JobStatus.APPROVED)
        elif job.status not in {JobStatus.APPROVED, JobStatus.UPLOADED}:
            raise InvalidInputError(f"Job in state {job.status} is not approved for upload.")
        session_key = "upload:" + hashlib.sha256(str(paths.root.resolve()).encode()).hexdigest()
        result = upload_scheduled(
            http, secrets, video=artifacts.final_video(paths),
            metadata_path=paths.subdir("final") / "publishing.json",
            receipt_path=receipt_path, session_key=session_key,
            schedule=schedule, rights_confirmed=confirm_rights, progress=progress,
        )
        if store.load(job_id).status is JobStatus.APPROVED:
            store.set_status(job_id, JobStatus.UPLOADED)
        return result


def upload_editorial_topic(
    workspace: Path,
    batch_id: str,
    topic_id: str,
    http: Transport,
    secrets: Secrets,
    schedule: Schedule,
    *,
    approve: bool,
    evidence_reviewed: bool,
    confirm_rights: bool,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> UploadReceipt:
    """Publish one explicitly reviewed editorial draft; never schedules from generation."""
    batch_id = validate_job_id(batch_id)
    if topic_id not in {"topic-1", "topic-2", "topic-3"}:
        raise InvalidInputError("Topic must be topic-1, topic-2, or topic-3.")
    if not approve:
        raise InvalidInputError("Review the exact final video and supply --approve.")
    if not evidence_reviewed:
        raise InvalidInputError(
            "Independently verify the financial claims and supply --evidence-reviewed."
        )
    if not confirm_rights:
        raise InvalidInputError("Publishing rights must be explicitly confirmed.")
    schedule.require_future()
    batch_dir = workspace / "editorial_batches" / batch_id
    topic_dir = batch_dir / topic_id
    manifest_path = topic_dir / "render_manifest.json"
    video = topic_dir / "final" / "video.mp4"
    metadata_path = topic_dir / "final" / "publishing.json"
    validation_path = topic_dir / "validation.json"
    review_path = topic_dir / "final" / "review_summary.json"
    for path in (manifest_path, video, metadata_path, validation_path, review_path):
        if not path.is_file():
            raise InvalidInputError(f"Editorial video is missing required file {path.name}.")
    with FileLock(str(topic_dir / ".youtube.lock"), timeout=1):
        manifest = read_json(manifest_path)
        if (
            manifest.get("status") != "awaiting_review"
            or manifest.get("video_path") != "final/video.mp4"
        ):
            raise InvalidInputError("Editorial video is not in the awaiting-review state.")
        review = read_json(review_path)
        if (
            review.get("status") != "awaiting_review"
            or review.get("evidence_review_required") is not True
            or review.get("human_review_required") is not True
        ):
            raise InvalidInputError("Editorial review record is missing or has changed.")
        if manifest.get("video_sha256") != file_sha256(video):
            raise InvalidInputError("Final video changed after technical QA; rerender and review.")
        report = read_model(validation_path, ValidationReport)
        if not report.technical_passed:
            raise InvalidInputError("Technical QA failed; upload is blocked.")
        approval_path = topic_dir / "publishing_approval.json"
        approval = {
            "approved": True,
            "evidence_reviewed": True,
            "rights_confirmed": True,
            "channel_id": schedule.channel_id,
            "schedule": schedule.model_dump(mode="json"),
            "video_sha256": file_sha256(video),
            "metadata_sha256": file_sha256(metadata_path),
        }
        if approval_path.is_file():
            existing = read_json(approval_path)
            if existing != approval:
                raise InvalidInputError(
                    "Editorial approval differs from the saved channel, schedule, or media. "
                    "Create a new editorial batch for changed publishing inputs."
                )
        else:
            write_json(approval_path, approval)
        session_key = "upload:editorial:" + hashlib.sha256(
            str(topic_dir.resolve()).encode()
        ).hexdigest()
        result = upload_scheduled(
            http,
            secrets,
            video=video,
            metadata_path=metadata_path,
            receipt_path=topic_dir / "youtube_upload.json",
            session_key=session_key,
            schedule=schedule,
            rights_confirmed=True,
            progress=progress,
        )
        with FileLock(str(batch_dir / ".plan.lock"), timeout=30):
            plan_path = batch_dir / "plan.json"
            plan = read_json(plan_path)
            topic = next(
                (item for item in plan["topics"] if item["topic_id"] == topic_id),
                None,
            )
            if topic is None:
                raise InvalidInputError(f"Editorial batch has no topic {topic_id}.")
            topic["status"] = "scheduled"
            topic["youtube_video_id"] = result.video_id
            topic["publish_at"] = schedule.timestamp
            statuses = [item["status"] for item in plan["topics"]]
            if all(status == "scheduled" for status in statuses):
                plan["status"] = "scheduled"
            elif any(status == "scheduled" for status in statuses):
                plan["status"] = "partially_scheduled"
            write_json(plan_path, plan)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.youtube", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser(
        "login", help="Open browser authorization; store tokens in OS credential store"
    )
    auth.add_argument("--client-secrets", required=True, type=Path)
    sub.add_parser("channel", help="Show the authorized channel name and ID")
    upload = sub.add_parser("upload", help="Upload and verify one future scheduled reel")
    upload.add_argument("--job", required=True)
    upload.add_argument("--workspace", type=Path)
    upload.add_argument("--config-dir", type=Path)
    upload.add_argument("--channel-id", required=True)
    upload.add_argument(
        "--publish-at", required=True, help="ISO 8601 with explicit time-zone offset"
    )
    upload.add_argument("--made-for-kids", required=True, choices=["yes", "no"])
    upload.add_argument("--contains-synthetic-media", choices=["yes", "no"], default="no")
    upload.add_argument("--approve", action="store_true", help="Approve this reviewed job")
    upload.add_argument("--confirm-rights", action="store_true", required=True)
    editorial = sub.add_parser(
        "upload-editorial",
        help="Upload a reviewed editorial topic draft and verify its future schedule",
    )
    editorial.add_argument("--batch-id", required=True)
    editorial.add_argument("--topic", required=True, choices=["topic-1", "topic-2", "topic-3"])
    editorial.add_argument("--workspace", type=Path)
    editorial.add_argument("--config-dir", type=Path)
    editorial.add_argument("--channel-id", required=True)
    editorial.add_argument(
        "--publish-at", required=True, help="ISO 8601 with explicit time-zone offset"
    )
    editorial.add_argument("--made-for-kids", required=True, choices=["yes", "no"])
    editorial.add_argument("--contains-synthetic-media", required=True, choices=["yes", "no"])
    editorial.add_argument("--approve", action="store_true")
    editorial.add_argument("--evidence-reviewed", action="store_true")
    editorial.add_argument("--confirm-rights", action="store_true", required=True)
    args = parser.parse_args(argv)

    def action() -> int:
        secrets = CredentialStore()
        if args.command == "login":
            login(args.client_secrets, secrets)
        http = GoogleTransport(secrets)
        if args.command in {"login", "channel"}:
            channel_id, title = channel_identity(http)
            print(f"Authorized channel: {title}", flush=True)
            print(f"Channel ID: {channel_id}", flush=True)
            return EXIT_OK
        settings = load_settings(args.config_dir)
        if args.workspace:
            settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
        store = JobStore(settings.workspace_dir)
        schedule = Schedule(
            publish_at=args.publish_at,
            channel_id=args.channel_id,
            made_for_kids=args.made_for_kids == "yes",
            contains_synthetic_media=args.contains_synthetic_media == "yes",
        )
        if args.command == "upload-editorial":
            result = upload_editorial_topic(
                settings.workspace_dir,
                args.batch_id,
                args.topic,
                http,
                secrets,
                schedule,
                approve=args.approve,
                evidence_reviewed=args.evidence_reviewed,
                confirm_rights=args.confirm_rights,
                progress=lambda done, total: print(
                    f"Uploaded {done / total:.0%} ({done}/{total} bytes)", flush=True,
                ),
            )
            print(f"Verified video: https://www.youtube.com/watch?v={result.video_id}")
            print(f"Verified publication: {result.schedule.timestamp}")
            print(
                "YouTube accepted the schedule; processing and rights checks may still be pending."
            )
            return EXIT_OK
        result = upload_job(
            store, http, secrets, args.job, schedule, approve=args.approve,
            confirm_rights=args.confirm_rights,
            progress=lambda done, total: print(
                f"Uploaded {done / total:.0%} ({done}/{total} bytes)", flush=True,
            ),
        )
        print(f"Verified video: https://www.youtube.com/watch?v={result.video_id}")
        print(f"Verified publication: {result.schedule.timestamp}")
        print("YouTube accepted the schedule; processing and rights checks may still be pending.")
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
