"""Thin, safe wrapper around the FFmpeg executable.

All invocations use argument lists (never shell strings), and only paths and values that
the application itself generated are passed in.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import RenderError

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?Video:.*")
_RESOLUTION_RE = re.compile(r",\s*(\d{2,5})x(\d{2,5})(?=[\s,\[]|$)")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?Audio:")
_MEAN_VOL_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB")


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    fps: float | None
    mean_volume_db: float | None  # None when there is no audio; -inf is reported as -999.0


def find_ffmpeg(configured: str | None = None) -> Path | None:
    candidate = configured or shutil.which("ffmpeg")
    if not candidate:
        return None
    path = Path(candidate)
    return path if path.is_file() else None


class FFmpeg:
    def __init__(self, executable: Path) -> None:
        self.executable = executable

    @classmethod
    def locate(cls, configured: str | None = None) -> FFmpeg:
        exe = find_ffmpeg(configured)
        if exe is None:
            raise RenderError(
                "FFmpeg not found. Install it and add it to PATH, or set FFMPEG_PATH / "
                "ffmpeg_path in config/settings.yaml."
            )
        return cls(exe)

    def run(self, args: list[str], *, timeout: float = 900) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 - fixed executable, argument list, no shell
                [str(self.executable), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError(f"FFmpeg timed out after {timeout:.0f}s") from exc
        except OSError as exc:
            raise RenderError(f"Could not start FFmpeg: {exc}") from exc

    def run_checked(self, args: list[str], *, timeout: float = 900) -> None:
        result = self.run(args, timeout=timeout)
        if result.returncode != 0:
            tail = "\n".join(result.stderr.strip().splitlines()[-15:])
            raise RenderError(f"FFmpeg failed (exit {result.returncode}):\n{tail}")

    def analyze(self, media: Path) -> MediaInfo:
        """Probe a media file using FFmpeg alone (no ffprobe needed)."""
        probe = self.run(["-hide_banner", "-nostats", "-i", str(media)], timeout=60)
        info = probe.stderr  # FFmpeg prints stream info to stderr and exits non-zero (no output)
        duration = _DURATION_RE.search(info)
        if not duration:
            raise RenderError(f"Cannot open media file {media.name}: no duration found")
        hours, minutes, seconds = duration.groups()
        total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

        video_line = _VIDEO_RE.search(info)
        width = height = None
        fps = None
        if video_line:
            res = _RESOLUTION_RE.search(video_line.group(0))
            if res:
                width, height = int(res.group(1)), int(res.group(2))
            rate = _FPS_RE.search(video_line.group(0))
            if rate:
                fps = float(rate.group(1))

        has_audio = bool(_AUDIO_RE.search(info))
        mean_volume: float | None = None
        if has_audio:
            vol = self.run(
                [
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(media),
                    "-vn",
                    "-af",
                    "volumedetect",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=300,
            )
            match = _MEAN_VOL_RE.search(vol.stderr)
            if match:
                mean_volume = -999.0 if match.group(1) == "-inf" else float(match.group(1))
        return MediaInfo(total, bool(video_line), has_audio, width, height, fps, mean_volume)
