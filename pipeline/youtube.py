"""Authorize a YouTube channel, upload one approved reel, and verify its publication schedule."""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from core.config import load_settings
from core.exceptions import InvalidInputError
from core.jobs import JobStore
from core.models.manifest import JobFormat, JobStatus
from core.models.media import ValidationReport
from core.storage import read_model
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
