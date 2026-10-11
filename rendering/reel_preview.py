"""Preview a finished reel without playing it: key frames plus one contact sheet."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.exceptions import RenderError
from core.models.finance import ReelPlan
from rendering.ffmpeg import FFmpeg

SHEET_FRAME_HEIGHT = 640


def preview_times(plan: ReelPlan) -> list[float]:
    """Moments worth looking at: each stage of the reveal once it has faded in, and the end."""
    t = plan.timing
    settled = t.fade_s + 0.05
    rows = t.row_at_s
    candidates = [
        t.title_at_s + settled,
        t.methodology_at_s + settled,
        rows[0] + settled,
        rows[len(rows) // 2] + settled,
        rows[-1] + settled,
        t.cta_at_s + settled,
        t.duration_s - 0.1,
    ]
    last = max(0.0, t.duration_s - 0.1)
    unique: list[float] = []
    for value in candidates:
        value = round(min(value, last), 2)
        if value not in unique:
            unique.append(value)
    return unique


def extract_frames(ffmpeg: FFmpeg, video: Path, times: list[float], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("frame_*.png"):
        stale.unlink()
    frames: list[Path] = []
    for index, moment in enumerate(times, start=1):
        target = out_dir / f"frame_{index:02d}_{moment:05.2f}s.png"
        ffmpeg.run_checked(
            ["-y", "-hide_banner", "-nostats", "-loglevel", "error", "-ss", f"{moment:.3f}",
             "-i", str(video), "-frames:v", "1", str(target)]
        )  # fmt: skip
        if not target.is_file():
            raise RenderError(f"Could not extract a frame at {moment:.2f}s")
        frames.append(target)
    return frames


def contact_sheet(frames: list[Path], out: Path) -> Path:
    """All frames side by side, each scaled to a fixed height, on a dark background."""
    images = []
    for path in frames:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        ratio = SHEET_FRAME_HEIGHT / image.height
        images.append(image.resize((round(image.width * ratio), SHEET_FRAME_HEIGHT)))
    gap = 16
    width = sum(i.width for i in images) + gap * (len(images) + 1)
    sheet = Image.new("RGB", (width, SHEET_FRAME_HEIGHT + 2 * gap), (24, 24, 28))
    x = gap
    for image in images:
        sheet.paste(image, (x, gap))
        x += image.width + gap
    sheet.save(out, "PNG")
    return out
