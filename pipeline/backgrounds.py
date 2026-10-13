"""Pick a background for each reel: your own clips by mood, else a generated abstract clip."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.campaign import Backgrounds
from core.config import Settings
from core.reel_input import media_type_for
from pipeline.demo import DEFAULT_PALETTE, Palette, generate_demo_video
from rendering.ffmpeg import FFmpeg

GENERATED_LICENSE = "Generated locally by this project; abstract, no third-party footage"
GENERATED_SECONDS = 12  # long enough that a 10-17 s reel loops at most once

PALETTES: dict[str, Palette] = {
    "retirement": DEFAULT_PALETTE,
    "growth": ((8, 38, 48), (16, 104, 98), (130, 214, 150)),
    "housing": ((20, 30, 62), (72, 100, 142), (240, 190, 122)),
    "auto": ((24, 24, 34), (74, 70, 104), (206, 122, 92)),
    "education": ((38, 24, 78), (92, 62, 160), (240, 170, 200)),
    "cash": ((14, 40, 32), (52, 92, 72), (204, 212, 122)),
    "time": ((10, 20, 60), (40, 70, 150), (124, 202, 240)),
}


def generated_path(batch_dir: Path, mood: str) -> Path:
    return batch_dir / "backgrounds" / f"{mood}.mp4"


def library_clips(library: Path, mood: str, settings: Settings) -> list[Path]:
    """Clips for a mood: <library>/<mood>/ first, else any clip directly in <library>."""
    for folder in (library / mood, library):
        if folder.is_dir():
            clips = sorted(
                p for p in folder.iterdir() if p.is_file() and media_type_for(p, settings)
            )
            if clips:
                return clips
    return []


def choose_background(
    mood: str, key: str, backgrounds: Backgrounds, settings: Settings
) -> dict[str, str]:
    """The `background` block for a reel spec: a library clip when you supplied one, else a path
    (relative to the specs folder) to the generated clip that `ensure_generated` creates."""
    if backgrounds.library:
        clips = library_clips(Path(backgrounds.library), mood, settings)
        if clips:
            clip = clips[int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(clips)]
            return {
                "path": str(clip.resolve()),
                "origin": backgrounds.origin,
                "license": backgrounds.license or "",
            }
    return {
        "path": f"../backgrounds/{mood}.mp4",
        "origin": "generated_placeholder",
        "license": GENERATED_LICENSE,
    }


def ensure_generated(batch_dir: Path, moods: set[str], settings: Settings) -> list[Path]:
    """Create any missing generated background clips (once per mood, shared by every reel)."""
    ffmpeg = FFmpeg.locate(settings.ffmpeg_path)
    made: list[Path] = []
    for mood in sorted(moods):
        path = generated_path(batch_dir, mood)
        if path.is_file():
            continue
        generate_demo_video(
            ffmpeg,
            path,
            settings.finance.width,
            settings.finance.height,
            seconds=GENERATED_SECONDS,
            fps=settings.finance.fps,
            palette=PALETTES.get(mood, PALETTES["growth"]),
            seed=sum(map(ord, mood)),
        )
        made.append(path)
    return made
