"""Render a finance reel with FFmpeg: background + scrim + staged text layers (+ optional audio).

This sits next to, not inside, the explainer's `Renderer` interface: that one is shaped around
narrated scenes (scene plan, per-scene audio, timeline), which a text-first reel does not have.
The same building blocks are reused (FFmpeg wrapper, RenderResult, atomic partial-file encode).
Remotion was evaluated and not adopted: see docs/pipeline.md ("Finance reels").
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from core.config import FinanceSettings, Settings
from core.exceptions import RenderError
from core.jobs import JobPaths
from core.models.finance import ReelPlan
from core.models.media import RenderResult, Timeline
from core.reel_input import sha256_of
from core.storage import write_model
from rendering.ffmpeg import FFmpeg
from rendering.reel_layout import (
    Block,
    FontSet,
    ReelLayoutResult,
    build_layout,
    prepare_background_image,
)


@dataclass
class ReelRenderRequest:
    paths: JobPaths
    plan: ReelPlan
    finance: FinanceSettings
    out_path: Path
    layout_path: Path


class ReelRenderer(ABC):
    name: str

    @abstractmethod
    def render(self, request: ReelRenderRequest) -> RenderResult: ...


def _fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def build_filter_graph(
    plan: ReelPlan, finance: FinanceSettings, blocks: list[Block], *, background_is_video: bool,
    has_audio: bool,
) -> str:  # fmt: skip
    """The filter_complex script. Inputs: 0 background, 1 scrim, 2.. text blocks, then audio."""
    width, height, fps = finance.width, finance.height, finance.fps
    fade = plan.timing.fade_s
    slide = round(finance.timing.slide_px * width / 1080)
    chains: list[str] = []
    if background_is_video:
        chains.append(
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:x=(iw-ow)*{_fmt(plan.background.crop_x)}:"
            f"y=(ih-oh)*{_fmt(plan.background.crop_y)},setsar=1,fps={fps},format=yuv420p[base]"
        )
    else:
        chains.append(f"[0:v]setsar=1,fps={fps},format=yuv420p[base]")
    chains.append("[base][1:v]overlay=0:0:format=auto[l1]")
    previous = "l1"
    for offset, block in enumerate(blocks):
        index = offset + 2
        start = _fmt(block.start_s)
        chains.append(
            f"[{index}:v]format=rgba,fade=t=in:st={start}:d={_fmt(fade)}:alpha=1[b{index}]"
        )
        y = (
            f"{block.y}+{slide}*pow(1-min(1,max(0,(t-{start})/{_fmt(fade)})),2)"
            if block.slide and slide
            else str(block.y)
        )
        chains.append(
            f"[{previous}][b{index}]overlay=x={block.x}:y='{y}':eval=frame:format=auto:"
            f"enable='gte(t,{start})'[o{index}]"
        )
        previous = f"o{index}"
    chains.append(f"[{previous}]format=yuv420p[v]")
    if has_audio:
        audio_index = len(blocks) + 2
        chains.extend(_audio_filters(plan, audio_index))
    return ";\n".join(chains) + "\n"


def _audio_filters(plan: ReelPlan, audio_index: int) -> list[str]:
    duration = plan.timing.duration_s
    end = _fmt(duration)
    chains: list[str] = []
    if plan.voiceover:
        chains.append(
            f"[{audio_index}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"apad=whole_dur={end},atrim=end={end},asetpts=PTS-STARTPTS[voice]"
        )
        audio_index += 1
    if plan.music:
        music = plan.music.settings
        fade_in = min(music.fade_in_s, duration / 2)
        fade_out = min(music.fade_out_s, duration / 2)
        chains.append(
            f"[{audio_index}:a]atrim=start={_fmt(music.start_s)}:"
            f"duration={end},asetpts=PTS-STARTPTS,"
            "loudnorm=I=-18:TP=-2:LRA=7,"
            "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={_fmt(music.gain_db)}dB,"
            f"afade=t=in:d={_fmt(fade_in)},"
            f"afade=t=out:st={_fmt(duration - fade_out)}:d={_fmt(fade_out)}[music]"
        )
        if plan.voiceover:
            chains.extend(
                [
                    "[voice]asplit=2[narration][sidechain]",
                    "[music][sidechain]sidechaincompress=threshold=0.02:ratio=8:"
                    "attack=20:release=250[ducked]",
                    "[narration][ducked]amix=inputs=2:duration=first:normalize=0,"
                    "alimiter=limit=0.89:level=0:latency=1[a]",
                ]
            )
        else:
            chains.append("[music]alimiter=limit=0.89:level=0:latency=1[a]")
    else:
        # Keep the original voiceover-only path unchanged.
        chains.append("[voice]anull[a]")
    return chains


class FfmpegReelRenderer(ReelRenderer):
    name = "ffmpeg_reel"

    def __init__(self, ffmpeg: FFmpeg, fonts: FontSet | None = None) -> None:
        self._ffmpeg = ffmpeg
        self._fonts = fonts

    def render(self, request: ReelRenderRequest) -> RenderResult:
        plan, finance = request.plan, request.finance
        background = request.paths.absolute(plan.background.stored_path)
        if not background.is_file():
            raise RenderError(
                f"Background file missing from the job: {plan.background.stored_path}"
            )
        voiceover: Path | None = None
        if plan.voiceover:
            voiceover = request.paths.absolute(plan.voiceover.stored_path)
            if not voiceover.is_file():
                raise RenderError(
                    f"Voiceover file missing from the job: {plan.voiceover.stored_path}"
                )
        music: Path | None = None
        if plan.music:
            music = request.paths.absolute(plan.music.stored_path)
            if not music.is_file() or sha256_of(music) != plan.music.sha256:
                raise RenderError("Music file is missing or changed since job creation")
        source = request.paths.manifest
        if source.is_file():
            from core.models.manifest import JobManifest
            from core.storage import read_model

            reel = read_model(source, JobManifest).input.reel
            if reel:
                for asset in reel.fonts.values():
                    path = request.paths.absolute(asset.stored_path)
                    if not path.is_file() or sha256_of(path) != asset.sha256:
                        raise RenderError(f"Font {asset.label} is missing or changed")

        regular = (
            request.paths.absolute(plan.style.font_regular)
            if plan.style.font_regular
            else finance.font_regular
        )
        bold = (
            request.paths.absolute(plan.style.font_bold)
            if plan.style.font_bold
            else finance.font_bold
        )
        fonts = self._fonts or FontSet(regular, bold)
        layout = build_layout(plan, finance, fonts)
        request.layout_path.parent.mkdir(parents=True, exist_ok=True)
        write_model(request.layout_path, layout.report)

        work = request.out_path.parent / "reel_work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            self._encode(request, layout, background, voiceover, music, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

        return RenderResult(
            renderer=self.name,
            video_path=request.paths.relative(request.out_path),
            duration_s=plan.timing.duration_s,
            width=finance.width,
            height=finance.height,
            fps=finance.fps,
            scene_ids=[],
            timeline=Timeline(entries=[]),
        )

    def _encode(
        self,
        request: ReelRenderRequest,
        layout: ReelLayoutResult,
        background: Path,
        voiceover: Path | None,
        music: Path | None,
        work: Path,
    ) -> None:
        plan, finance = request.plan, request.finance
        duration = _fmt(plan.timing.duration_s)
        fps = str(finance.fps)
        is_video = plan.background.media_type == "video"

        scrim_path = work / "scrim.png"
        layout.scrim.save(scrim_path, "PNG")
        inputs: list[str] = []
        if is_video:
            inputs += ["-stream_loop", "-1", "-t", duration, "-i", str(background)]
        else:
            cover = work / "background.png"
            prepare_background_image(
                background,
                finance.width,
                finance.height,
                cover,
                centering=(plan.background.crop_x, plan.background.crop_y),
            )
            inputs += ["-loop", "1", "-framerate", fps, "-t", duration, "-i", str(cover)]
        inputs += ["-loop", "1", "-framerate", fps, "-t", duration, "-i", str(scrim_path)]
        for block in layout.blocks:
            path = work / f"{block.name}.png"
            block.image.save(path, "PNG")
            inputs += ["-loop", "1", "-framerate", fps, "-t", duration, "-i", str(path)]
        if voiceover is not None:
            inputs += ["-i", str(voiceover)]
        if music is not None:
            inputs += ["-stream_loop", "-1", "-i", str(music)]
        has_audio = voiceover is not None or music is not None

        script = work / "filter_complex.txt"
        script.write_text(
            build_filter_graph(
                plan, finance, layout.blocks, background_is_video=is_video,
                has_audio=has_audio,
            ),
            encoding="utf-8",
        )  # fmt: skip
        partial = request.out_path.with_name(request.out_path.stem + ".partial.mp4")
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-y", "-hide_banner", "-nostats", "-loglevel", "error", *inputs,
            "-filter_complex_script", str(script),
            "-map", "[v]", *(["-map", "[a]"] if has_audio else ["-an"]),
            "-t", duration, "-r", fps,
            "-c:v", "libx264", "-preset",
            "medium" if plan.style.preset == "editorial" else "veryfast",
            "-crf", "18" if plan.style.preset == "editorial" else "20", "-pix_fmt", "yuv420p",
            *(["-c:a", "aac", "-b:a", "192k", "-ar", "44100"] if has_audio else []),
            "-movflags", "+faststart",
            "-f", "mp4", str(partial),
        ]  # fmt: skip
        try:
            self._ffmpeg.run_checked(args)
            partial.replace(request.out_path)
        finally:
            partial.unlink(missing_ok=True)


def build_reel_renderer(settings: Settings) -> ReelRenderer:
    return FfmpegReelRenderer(FFmpeg.locate(settings.ffmpeg_path))
