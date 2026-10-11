"""Deterministic local demo: render the example finance reel with no network and no API calls.

python -m pipeline.demo [--workspace DIR]

It generates an animated abstract placeholder background (soft drifting light on a dusk
gradient; clearly not real footage), copies the
example spec next to it, creates a finance_facts job, runs the pipeline and extracts preview
frames. To use your own footage instead, see "Finance reels" in docs/pipeline.md.
"""

from __future__ import annotations

import argparse
import math
import random
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from core.config import PROJECT_ROOT, load_settings
from core.jobs import JobStore
from core.logging import configure_logging
from core.models.manifest import Audience
from core.reel_input import prepare_reel
from pipeline.preview import write_preview
from pipeline.run import run_pipeline
from pipeline.stage import EXIT_OK, handle_errors
from providers.registry import build_providers
from rendering.ffmpeg import FFmpeg

EXAMPLE_SPEC = PROJECT_ROOT / "examples" / "finance" / "retirement_withdrawal.yaml"
BACKGROUND_NAME = "demo_background.mp4"


def generate_demo_background(path: Path, width: int = 1080, height: int = 1920) -> Path:
    """An abstract dusk gradient with a soft glow. Pure geometry: no randomness, no imagery."""
    gradient = Image.linear_gradient("L").resize((width, height))
    base = ImageOps.colorize(
        gradient, black=(20, 34, 82), mid=(112, 78, 140), white=(232, 140, 96), midpoint=140
    ).convert("RGBA")
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    radius = width // 3
    centre = (width // 2, round(height * 0.62))
    ImageDraw.Draw(glow).ellipse(
        (centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius),
        fill=(255, 214, 150, 170),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius // 2))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(base, glow).convert("RGB").save(path, "PNG")
    return path


def _orbs(count: int, seed: int) -> list[dict[str, float]]:
    """Fixed, seeded orb parameters: position, size, colour, and a closed drift path."""
    rng = random.Random(seed)
    palette = [(255, 196, 140), (255, 150, 170), (150, 160, 255), (255, 225, 170), (190, 140, 255)]
    orbs = []
    for _ in range(count):
        colour = rng.choice(palette)
        orbs.append(
            {
                "x": rng.uniform(0.05, 0.95), "y": rng.uniform(0.1, 0.95),
                "r": rng.uniform(0.05, 0.16), "alpha": rng.uniform(0.18, 0.42),
                "dx": rng.uniform(0.02, 0.07), "dy": rng.uniform(0.03, 0.09),
                "cx": rng.choice((1, 2)), "cy": rng.choice((1, 2)),  # whole cycles: loops cleanly
                "phase": rng.uniform(0, 2 * math.pi),
                "red": colour[0], "green": colour[1], "blue": colour[2],
            }
        )  # fmt: skip
    return orbs


def _bokeh_frame(base: Image.Image, sprites: list[Image.Image], orbs: list[dict[str, float]],
                 progress: float) -> Image.Image:  # fmt: skip
    frame = base.copy()
    width, height = frame.size
    angle = 2 * math.pi * progress
    for sprite, orb in zip(sprites, orbs, strict=True):
        x = width * (orb["x"] + orb["dx"] * math.sin(orb["cx"] * angle + orb["phase"]))
        y = height * (orb["y"] + orb["dy"] * math.cos(orb["cy"] * angle + orb["phase"]))
        frame.paste(sprite, (round(x - sprite.width / 2), round(y - sprite.height / 2)), sprite)
    return frame


def generate_demo_video(
    ffmpeg: FFmpeg,
    path: Path,
    width: int = 1080,
    height: int = 1920,
    seconds: int = 6,
    fps: int = 30,
) -> Path:
    """An animated abstract backdrop: a dusk gradient with softly drifting light orbs.

    Generated here from seeded geometry, so it is reproducible and not pretending to be real
    footage. It loops seamlessly (every orb completes whole cycles per clip).
    """
    work_w, work_h = max(2, width // 2), max(2, height // 2)  # soft content: draw small, scale up
    gradient = Image.linear_gradient("L").resize((work_w, work_h))
    base = ImageOps.colorize(
        gradient, black=(18, 30, 78), mid=(108, 74, 138), white=(236, 138, 92), midpoint=140
    ).convert("RGBA")
    orbs = _orbs(16, seed=7)
    sprites = []
    for orb in orbs:
        radius = max(2, round(work_w * orb["r"]))
        mask = Image.new("L", (radius * 4, radius * 4), 0)
        ImageDraw.Draw(mask).ellipse((radius, radius, radius * 3, radius * 3), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(radius * 0.45)).point(
            lambda v, a=orb["alpha"]: round(v * a)
        )
        sprite = Image.new(
            "RGBA", mask.size, (round(orb["red"]), round(orb["green"]), round(orb["blue"]), 0)
        )
        sprite.putalpha(mask)
        sprites.append(sprite)

    path.parent.mkdir(parents=True, exist_ok=True)
    total = seconds * fps
    with tempfile.TemporaryDirectory() as tmp:
        for index in range(total):
            _bokeh_frame(base, sprites, orbs, index / total).convert("RGB").save(
                Path(tmp) / f"f{index:04d}.png"
            )
        ffmpeg.run_checked(
            ["-y", "-hide_banner", "-nostats", "-loglevel", "error", "-framerate", str(fps),
             "-i", str(Path(tmp) / "f%04d.png"),
             "-vf", f"scale={width}:{height}:flags=bicubic,format=yuv420p",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart",
             str(path)]
        )  # fmt: skip
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.demo", description=__doc__)
    parser.add_argument("--workspace", type=Path, help="Workspace directory (default: settings)")
    parser.add_argument("--config-dir", type=Path)
    args = parser.parse_args(argv)

    def action() -> int:
        settings = load_settings(args.config_dir)
        if args.workspace:
            settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
        configure_logging(settings.log_level)
        store = JobStore(settings.workspace_dir)

        demo_dir = settings.workspace_dir / "demo"
        demo_dir.mkdir(parents=True, exist_ok=True)
        spec = demo_dir / EXAMPLE_SPEC.name
        shutil.copyfile(EXAMPLE_SPEC, spec)
        generate_demo_video(
            FFmpeg.locate(settings.ffmpeg_path),
            demo_dir / BACKGROUND_NAME,
            settings.finance.width,
            settings.finance.height,
            fps=settings.finance.fps,
        )

        manifest = store.create(
            "How Much Do You Need to Retire? (demo)",
            reel=prepare_reel(spec, settings),
            style=settings.defaults.style,
            target_duration_minutes=settings.defaults.target_duration_minutes,
            audience=Audience(settings.defaults.audience),
        )
        run_pipeline(store, manifest.job_id, settings, build_providers(settings))
        frames, sheet = write_preview(settings, store, manifest.job_id)
        final = store.paths(manifest.job_id).subdir("final")
        print(f"job:           {manifest.job_id}")
        print(f"video:         {final / 'video.mp4'}")
        print(f"contact sheet: {sheet}")
        print(f"frames:        {frames[0].parent}")
        print(f"provenance:    {final / 'provenance.json'}")
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
