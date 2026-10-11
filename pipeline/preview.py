"""Write preview frames and a contact sheet for a finished finance reel.

python -m pipeline.preview --job JOB_ID
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.config import Settings, load_settings
from core.exceptions import InvalidInputError
from core.jobs import JobStore
from core.models.finance import ReelPlan
from core.models.manifest import JobFormat
from core.storage import read_model
from pipeline import artifacts
from pipeline.stage import EXIT_OK, handle_errors
from rendering.ffmpeg import FFmpeg
from rendering.reel_preview import contact_sheet, extract_frames, preview_times


def write_preview(settings: Settings, store: JobStore, job_id: str) -> tuple[list[Path], Path]:
    """Extract key frames from the job's rendered video; returns (frames, contact sheet)."""
    manifest = store.load(job_id)
    if manifest.input.format is not JobFormat.FINANCE_FACTS:
        raise InvalidInputError("Preview frames are only available for finance_facts jobs.")
    paths = store.paths(job_id)
    video = artifacts.render_video(paths)
    if not video.is_file():
        raise InvalidInputError(
            f"No rendered video yet; run: python -m pipeline.render --job {job_id}"
        )
    plan = read_model(artifacts.reel_plan_json(paths), ReelPlan)
    out_dir = artifacts.reel_preview_dir(paths)
    frames = extract_frames(
        FFmpeg.locate(settings.ffmpeg_path), video, preview_times(plan), out_dir
    )
    return frames, contact_sheet(frames, out_dir / "contact_sheet.png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.preview", description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--config-dir", type=Path)
    args = parser.parse_args(argv)

    def action() -> int:
        settings = load_settings(args.config_dir)
        if args.workspace:
            settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
        frames, sheet = write_preview(settings, JobStore(settings.workspace_dir), args.job)
        for frame in frames:
            print(frame)
        print(f"contact sheet: {sheet}")
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
