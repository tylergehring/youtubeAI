"""Batch factory: turn one campaign file into hundreds of finished, post-ready reels.

python -m pipeline.batch plan   --campaign config/campaigns/finance_1000.yaml [--count N]
python -m pipeline.batch run    --batch finance_1000 [--limit N] [--workers N] [--retry-failed]
python -m pipeline.batch status --batch finance_1000
python -m pipeline.batch export --batch finance_1000

`plan` expands the campaign into validated specs (no rendering). `run` renders every reel that is
not done yet, so it can be stopped and restarted freely. `export` writes publish.csv with the
video path, title, caption, hashtags and a posting schedule for each finished reel.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock

from core.campaign import PlannedReel, Publishing, expand, load_campaign
from core.config import Settings, load_settings
from core.exceptions import InvalidInputError, StageError
from core.jobs import JobStore
from core.logging import ROOT_LOGGER, configure_logging, redact
from core.models.finance import ReelSpec
from core.models.manifest import Audience, JobStatus
from core.models.media import ValidationReport
from core.publish import build_post
from core.reel_input import prepare_reel
from core.storage import atomic_write_text, read_model, write_json
from pipeline import artifacts
from pipeline.backgrounds import choose_background, ensure_generated
from pipeline.run import run_pipeline
from pipeline.stage import EXIT_OK, EXIT_STAGE_FAILED, handle_errors
from providers.registry import build_providers

PLAN_FILE = "plan.json"
PUBLISH_FILE = "publish.csv"
FINISHED = {JobStatus.AWAITING_REVIEW, JobStatus.APPROVED, JobStatus.UPLOADED}
CSV_COLUMNS = [
    "index", "job_id", "family", "status", "video", "duration_s", "title", "caption",
    "hashtags", "publish_at",
    "background", "music", "youtube_url", "upload_state",
]  # fmt: skip


def batch_dir(settings: Settings, name: str) -> Path:
    return settings.workspace_dir / "batches" / name


def _load_plan(settings: Settings, name: str) -> dict[str, Any]:
    path = batch_dir(settings, name) / PLAN_FILE
    if not path.is_file():
        raise InvalidInputError(f"No plan for batch {name!r}; run `pipeline.batch plan` first.")
    plan: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return plan


# ------------------------------------------------------------------ plan


def write_plan(settings: Settings, campaign_text: str, count: int | None) -> tuple[str, list[str]]:
    """Expand a campaign into spec files plus plan.json. Returns (batch name, report lines)."""
    campaign = load_campaign(campaign_text)
    planned, report = expand(campaign, count=count)
    wanted = count or campaign.count
    if len(planned) != wanted:
        raise InvalidInputError(
            "Campaign cannot produce the exact requested number of unique videos.\n"
            + "\n".join(report.lines(wanted))
        )
    directory = batch_dir(settings, campaign.name)
    if (directory / PLAN_FILE).is_file():
        previous = (directory / "campaign.yaml").read_text(encoding="utf-8")
        plan = _load_plan(settings, campaign.name)
        if previous != campaign_text or plan["requested"] != wanted:
            raise InvalidInputError(
                "This batch already exists. Use a new name; plans are immutable."
            )
        return campaign.name, [f"Reusing frozen plan with {len(plan['items'])} videos."]
    tracks: list[Path] = []
    if campaign.music:
        library = Path(campaign.music.library)
        if not library.is_dir():
            raise InvalidInputError(f"Music library not found: {library}")
        tracks = sorted(
            p for p in library.iterdir()
            if p.is_file() and p.suffix.lower() in settings.finance.audio_extensions
        )
        if not tracks:
            raise InvalidInputError("No supported music files in the supplied library.")
    specs_dir = directory / "specs"
    used_pairs: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    specs: list[tuple[str, str]] = []
    for reel in planned:
        reel.spec["background"] = choose_background(
            reel.mood, f"{campaign.seed}:{reel.key}", campaign.backgrounds, settings
        )
        if campaign.music:
            background = reel.spec["background"]["path"]
            available = [p for p in tracks if (background, str(p.resolve())) not in used_pairs]
            if not available:
                raise InvalidInputError(
                    "Not enough music/background combinations for unique asset pairs."
                )
            rng = random.Random(f"{campaign.seed}:{reel.key}:music")
            track = rng.choice(available)
            used_pairs.add((background, str(track.resolve())))
            reel.spec["music"] = {
                **campaign.music.model_dump(exclude={"library"}),
                "path": str(track.resolve()), "attribution": track.stem,
            }
        ReelSpec.model_validate(reel.spec)
        spec_file = f"{reel.index:04d}_{reel.key}.yaml"
        specs.append((spec_file, yaml.safe_dump(reel.spec, sort_keys=False, allow_unicode=True)))
        items.append(_plan_item(reel, spec_file, campaign.publishing))
    for spec_file, text in specs:
        atomic_write_text(specs_dir / spec_file, text)
    atomic_write_text(directory / "campaign.yaml", campaign_text)
    write_json(
        directory / PLAN_FILE,
        {"name": campaign.name, "requested": count or campaign.count, "items": items},
    )
    export_publish_csv(settings, campaign.name)
    return campaign.name, report.lines(count or campaign.count)


def _plan_item(reel: PlannedReel, spec_file: str, publishing: Publishing) -> dict[str, Any]:
    post = build_post(reel.spec, reel.hashtags, publishing, reel.index)
    return {
        "index": reel.index,
        "job_id": reel.job_id,
        "family": reel.family,
        "mood": reel.mood,
        "spec": f"specs/{spec_file}",
        "generated_background": reel.spec["background"]["origin"] == "generated_placeholder",
        "title": post.title,
        "caption": post.caption,
        "hashtags": post.hashtags,
        "publish_at": post.publish_at,
        "background": reel.spec["background"]["path"],
        "music": reel.spec.get("music", {}).get("path", ""),
    }


# ------------------------------------------------------------------ run


def _quiet_console() -> None:
    """Keep per-stage INFO lines in each job's own log file, not on the console."""
    for handler in logging.getLogger(ROOT_LOGGER).handlers:
        if getattr(handler, "_vidgen_console", False):
            handler.setLevel(logging.WARNING)


def render_one(settings: Settings, job_id: str, spec_path: str, topic: str) -> tuple[str, str]:
    """Create (if needed) and run one reel's job. Returns (job_id, error or ''). Never raises
    for a failed reel, so one bad video cannot stop a batch of a thousand."""
    configure_logging(settings.log_level)
    _quiet_console()
    store = JobStore(settings.workspace_dir)
    try:
        if not store.exists(job_id):
            store.create(
                topic,
                reel=prepare_reel(Path(spec_path), settings),
                style=settings.defaults.style,
                target_duration_minutes=settings.defaults.target_duration_minutes,
                audience=Audience(settings.defaults.audience),
                job_id=job_id,
            )
        run_pipeline(store, job_id, settings, build_providers(settings))
    except Exception as exc:  # noqa: BLE001 - reported per reel, the batch carries on
        return job_id, redact(f"{type(exc).__name__}: {exc}")
    return job_id, ""


def job_state(store: JobStore, job_id: str) -> JobStatus | None:
    return store.load(job_id).status if store.exists(job_id) else None


def pending_items(
    store: JobStore, items: list[dict[str, Any]], *, retry_failed: bool
) -> list[dict[str, Any]]:
    out = []
    for item in items:
        state = job_state(store, item["job_id"])
        if state in FINISHED or (state is JobStatus.FAILED and not retry_failed):
            continue
        out.append(item)
    return out


def run_batch(
    settings: Settings,
    name: str,
    *,
    limit: int | None = None,
    workers: int = 1,
    retry_failed: bool = False,
) -> dict[str, str]:
    """Render every unfinished reel; returns {job_id: error} for the ones that failed."""
    plan = _load_plan(settings, name)
    store = JobStore(settings.workspace_dir)
    directory = batch_dir(settings, name)
    todo = pending_items(store, plan["items"], retry_failed=retry_failed)
    if limit is not None:
        todo = todo[:limit]
    total = len(plan["items"])
    print(f"{total - len(pending_items(store, plan['items'], retry_failed=True))}/{total} "
          f"already finished; rendering {len(todo)} now with {workers} worker(s)")  # fmt: skip
    if not todo:
        return {}

    generated = {i["mood"] for i in todo if i["generated_background"]}
    for path in ensure_generated(directory, generated, settings):
        print(f"generated background {path.name}")

    failures: dict[str, str] = {}
    started = time.monotonic()
    jobs = [(item["job_id"], str(directory / item["spec"]), item["title"]) for item in todo]

    def report(done: int, job_id: str, error: str) -> None:
        elapsed = time.monotonic() - started
        eta = elapsed / done * (len(jobs) - done)
        mark = "FAILED " + error.splitlines()[0][:120] if error else "ok"
        print(f"[{done}/{len(jobs)}] {job_id}: {mark}  (eta {eta / 60:.1f} min)", flush=True)
        if error:
            failures[job_id] = error

    if workers <= 1:
        for done, (job_id, spec, topic) in enumerate(jobs, start=1):
            report(done, *render_one(settings, job_id, spec, topic))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(render_one, settings, *job) for job in jobs]
            for done, future in enumerate(as_completed(futures), start=1):
                report(done, *future.result())
    export_publish_csv(settings, name)
    return failures


# ------------------------------------------------------------------ status / export


def batch_status(settings: Settings, name: str) -> dict[str, list[str]]:
    """Job ids grouped by state ('not_started' when no job exists yet)."""
    plan = _load_plan(settings, name)
    store = JobStore(settings.workspace_dir)
    groups: dict[str, list[str]] = {}
    for item in plan["items"]:
        state = job_state(store, item["job_id"])
        groups.setdefault(state.value if state else "not_started", []).append(item["job_id"])
    return groups


def export_publish_csv(settings: Settings, name: str) -> Path:
    """publish.csv: one row per planned reel, with the video path once it is rendered."""
    plan = _load_plan(settings, name)
    store = JobStore(settings.workspace_dir)
    out = batch_dir(settings, name) / PUBLISH_FILE
    with io.StringIO(newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in plan["items"]:
            state = job_state(store, item["job_id"])
            video = artifacts.final_video(store.paths(item["job_id"])) if state else None
            ready = state in FINISHED and video is not None and video.is_file()
            receipt_path = store.paths(item["job_id"]).root / "youtube_upload.json"
            video_id, upload_state = "", ""
            if receipt_path.is_file():
                from core.youtube import UploadReceipt
                receipt = read_model(receipt_path, UploadReceipt)
                video_id, upload_state = receipt.video_id or "", receipt.state
            writer.writerow(
                {
                    "index": item["index"],
                    "job_id": item["job_id"],
                    "family": item["family"],
                    "status": state.value if state else "not_started",
                    "video": str(video) if ready and video else "",
                    "duration_s": _duration(store, item["job_id"]) if ready else "",
                    "title": item["title"],
                    "caption": item["caption"],
                    "hashtags": " ".join(f"#{t}" for t in item["hashtags"]),
                    "publish_at": item["publish_at"],
                    "background": item.get("background", ""),
                    "music": item.get("music", ""),
                    "youtube_url": (
                        f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
                    ),
                    "upload_state": upload_state,
                }
            )
        atomic_write_text(out, "\ufeff" + handle.getvalue())
    return out


def _duration(store: JobStore, job_id: str) -> str:
    path = artifacts.final_summary(store.paths(job_id))
    if not path.is_file():
        return ""
    return str(json.loads(path.read_text(encoding="utf-8")).get("duration_s", ""))


# ------------------------------------------------------------------ command line


def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.config_dir)
    if args.workspace:
        settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
    configure_logging(settings.log_level)
    _quiet_console()
    return settings


def cmd_plan(args: argparse.Namespace) -> int:
    path = Path(args.campaign)
    if not path.is_file():
        raise InvalidInputError(f"Campaign file not found: {path}")
    settings = _settings(args)
    name, lines = write_plan(settings, path.read_text(encoding="utf-8"), args.count)
    print("\n".join(lines))
    print(f"planned batch '{name}' -> {batch_dir(settings, name)}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    failures = run_batch(
        settings,
        args.batch,
        limit=args.limit,
        workers=args.workers,
        retry_failed=args.retry_failed,
    )
    print(f"publish sheet: {batch_dir(settings, args.batch) / PUBLISH_FILE}")
    if failures:
        print(f"{len(failures)} reel(s) failed; fix and re-run with --retry-failed")
        return EXIT_STAGE_FAILED
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    groups = batch_status(settings, args.batch)
    store = JobStore(settings.workspace_dir)
    for state, ids in sorted(groups.items()):
        print(f"{state:<18}{len(ids)}")
    for job_id in groups.get("failed", [])[:10]:
        manifest = store.load(job_id)
        print(f"  failed {job_id}: {manifest.failed_stage} {manifest.error}")
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    print(export_publish_csv(_settings(args), args.batch))
    return EXIT_OK


def upload_batch(
    settings: Settings, name: str, *, channel_id: str, made_for_kids: bool,
    approve: bool, confirm_rights: bool,
) -> int:
    """Upload a frozen calendar; receipts resume safely and any API failure stops the batch."""
    from core.youtube import Schedule, channel_identity
    from core.youtube_auth import CredentialStore, GoogleTransport
    from pipeline.youtube import upload_job

    if not approve or not confirm_rights:
        raise InvalidInputError("Batch uploading requires --approve and --confirm-rights.")
    with FileLock(str(batch_dir(settings, name) / ".youtube.lock"), timeout=1):
        plan = _load_plan(settings, name)
        store = JobStore(settings.workspace_dir)
        schedules: list[Schedule] = []
        for item in plan["items"]:
            schedules.append(Schedule(
                publish_at=item["publish_at"], channel_id=channel_id,
                made_for_kids=made_for_kids,
            ))
            paths = store.paths(item["job_id"])
            if job_state(store, item["job_id"]) not in FINISHED:
                raise InvalidInputError(f"{item['job_id']} is not ready; finish rendering first.")
            report = read_model(artifacts.validation_json(paths), ValidationReport)
            if not report.technical_passed or not artifacts.final_video(paths).is_file():
                raise InvalidInputError(f"{item['job_id']} failed pre-upload QA.")
            if not (paths.root / "youtube_upload.json").is_file():
                schedules[-1].require_future()
        secrets = CredentialStore()
        http = GoogleTransport(secrets)
        actual_id, title = channel_identity(http)
        if actual_id != channel_id:
            raise InvalidInputError(f"Authorized channel is {title} ({actual_id}), not the target.")
        completed = 0
        try:
            for item, schedule in zip(plan["items"], schedules, strict=True):
                result = upload_job(
                    store, http, secrets, item["job_id"], schedule,
                    approve=approve, confirm_rights=confirm_rights,
                )
                completed += 1
                export_publish_csv(settings, name)
                print(
                    f"[{completed}/{len(schedules)}] verified {result.video_id}: "
                    f"{item['publish_at']} | {item['title'].replace(chr(10), ' ')}",
                    flush=True,
                )
        except StageError as exc:
            print(f"Stopped after {completed} verified videos: {exc}", flush=True)
            raise
        finally:
            export_publish_csv(settings, name)
        return completed


def cmd_upload(args: argparse.Namespace) -> int:
    upload_batch(
        _settings(args), args.batch, channel_id=args.channel_id,
        made_for_kids=args.made_for_kids == "yes", approve=args.approve,
        confirm_rights=args.confirm_rights,
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipeline.batch", description=__doc__)
    parser.add_argument("--workspace", type=Path, help="Override the workspace directory")
    parser.add_argument("--config-dir", type=Path, help="Override the config directory")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Expand a campaign into validated specs")
    plan.add_argument("--campaign", required=True, help="Campaign YAML file")
    plan.add_argument("--count", type=int, help="Override the campaign's video count")
    plan.set_defaults(func=cmd_plan)

    run = sub.add_parser("run", help="Render every reel that is not finished yet")
    run.add_argument("--batch", required=True)
    run.add_argument("--limit", type=int, help="Render at most this many (try 5 first)")
    run.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)))
    run.add_argument("--retry-failed", action="store_true", help="Also retry failed reels")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="Count reels by state")
    status.add_argument("--batch", required=True)
    status.set_defaults(func=cmd_status)

    export = sub.add_parser("export", help="Write publish.csv for the batch")
    export.add_argument("--batch", required=True)
    export.set_defaults(func=cmd_export)
    upload = sub.add_parser("upload", help="Approve and upload a frozen calendar to YouTube")
    upload.add_argument("--batch", required=True)
    upload.add_argument("--channel-id", required=True)
    upload.add_argument("--made-for-kids", required=True, choices=["yes", "no"])
    upload.add_argument("--approve", action="store_true")
    upload.add_argument("--confirm-rights", action="store_true")
    upload.set_defaults(func=cmd_upload)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return handle_errors(lambda: int(args.func(args)))


if __name__ == "__main__":
    raise SystemExit(main())
