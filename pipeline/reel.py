"""Create one reel, validate it, and extract a cover and contact sheet for review."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from core.config import load_settings
from core.jobs import JobStore
from core.logging import configure_logging
from core.models.manifest import Audience
from core.reel_input import prepare_reel
from pipeline.preview import write_preview
from pipeline.run import run_pipeline
from pipeline.stage import EXIT_OK, handle_errors
from providers.registry import build_providers
from rendering.ffmpeg import FFmpeg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.reel", description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--job-id", help="Optional unique ID; existing jobs are never overwritten")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--config-dir", type=Path)
    args = parser.parse_args(argv)

    def action() -> int:
        settings = load_settings(args.config_dir)
        if args.workspace:
            settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
        configure_logging(settings.log_level)
        FFmpeg.locate(settings.ffmpeg_path)
        reel = prepare_reel(args.spec, settings)
        store = JobStore(settings.workspace_dir)
        job = store.create(
            reel.spec.title,
            reel=reel,
            style=settings.defaults.style,
            target_duration_minutes=settings.defaults.target_duration_minutes,
            audience=Audience(settings.defaults.audience),
            job_id=args.job_id,
        )
        print(f"job: {job.job_id}", flush=True)
        run_pipeline(store, job.job_id, settings, build_providers(settings))
        frames, sheet = write_preview(settings, store, job.job_id)
        final = store.paths(job.job_id).subdir("final")
        shutil.copyfile(frames[-1], final / "cover.png")
        print(f"video: {final / 'video.mp4'}")
        print(f"cover: {final / 'cover.png'}")
        print(f"publishing copy: {final / 'publishing.json'}")
        print(f"contact sheet: {sheet}")
        print("Awaiting review. Nothing has been uploaded or scheduled.")
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
