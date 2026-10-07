"""Renderer abstraction and the Milestone 1 placeholder implementation.

Scene specification -> Renderer -> video. The placeholder renderer draws solid-colour
scenes with the real narration audio; it exists to prove timing and encoding. The real
programmatic renderer arrives in Milestone 4 behind the same interface.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from core.config import Settings, VideoSettings
from core.exceptions import RenderError
from core.jobs import JobPaths
from core.models.media import AudioManifest, RenderResult, Timeline, VisualManifest
from core.models.scenes import ScenePlan
from rendering.ffmpeg import FFmpeg

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")


@dataclass
class RenderRequest:
    paths: JobPaths
    plan: ScenePlan
    audio: AudioManifest
    visuals: VisualManifest
    timeline: Timeline
    out_path: Path
    video: VideoSettings


class Renderer(ABC):
    name: str

    @abstractmethod
    def render(self, request: RenderRequest) -> RenderResult: ...


def ffmpeg_color(color: str) -> str:
    match = _HEX_COLOR_RE.match(color)
    if not match:
        raise RenderError(f"Invalid colour {color!r}; expected #RRGGBB")
    return f"0x{match.group(1)}"


class PlaceholderRenderer(Renderer):
    name = "placeholder"

    def __init__(self, ffmpeg: FFmpeg) -> None:
        self._ffmpeg = ffmpeg

    def render(self, request: RenderRequest) -> RenderResult:
        video = request.video
        visuals = request.visuals.by_scene()
        audio = request.audio.by_scene()
        inputs: list[str] = []
        chains: list[str] = []
        joined: list[str] = []

        for index, entry in enumerate(request.timeline.entries):
            scene_visual = visuals.get(entry.scene_id)
            record = audio.get(entry.scene_id)
            if scene_visual is None or record is None:
                raise RenderError(f"Scene {entry.scene_id} is missing visuals or audio")
            length = entry.end_s - entry.start_s
            audio_path = request.paths.absolute(record.path)
            if not audio_path.is_file():
                raise RenderError(f"Audio file missing for scene {entry.scene_id}: {record.path}")
            colour = ffmpeg_color(scene_visual.background_color)
            inputs += [
                "-f", "lavfi", "-t", f"{length:.6f}",
                "-i", f"color=c={colour}:s={video.width}x{video.height}:r={video.fps}",
                "-i", str(audio_path),
            ]  # fmt: skip
            v, a = 2 * index, 2 * index + 1
            chains.append(f"[{v}:v]setsar=1,format=yuv420p[v{index}]")
            chains.append(
                f"[{a}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad=whole_dur={length:.6f},atrim=end={length:.6f}[a{index}]"
            )
            joined.append(f"[v{index}][a{index}]")

        count = len(request.timeline.entries)
        chains.append(f"{''.join(joined)}concat=n={count}:v=1:a=1[v][a]")

        render_dir = request.out_path.parent
        render_dir.mkdir(parents=True, exist_ok=True)
        script = render_dir / "filter_complex.txt"
        script.write_text(";\n".join(chains) + "\n", encoding="utf-8")

        partial = request.out_path.with_name(request.out_path.stem + ".partial.mp4")
        args = [
            "-y", "-hide_banner", "-nostats", "-loglevel", "error", *inputs,
            "-filter_complex_script", str(script),
            "-map", "[v]", "-map", "[a]",
            "-r", str(video.fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-f", "mp4", str(partial),
        ]  # fmt: skip
        try:
            self._ffmpeg.run_checked(args)
            partial.replace(request.out_path)
        finally:
            partial.unlink(missing_ok=True)

        return RenderResult(
            renderer=self.name,
            video_path=request.paths.relative(request.out_path),
            duration_s=request.timeline.total_duration_s,
            width=video.width,
            height=video.height,
            fps=video.fps,
            scene_ids=[e.scene_id for e in request.timeline.entries],
            timeline=request.timeline,
        )


def build_renderer(settings: Settings) -> Renderer:
    return PlaceholderRenderer(FFmpeg.locate(settings.ffmpeg_path))
