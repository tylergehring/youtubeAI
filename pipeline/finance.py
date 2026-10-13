"""Finance reels: validate the spec, calculate the figures, verify the assets, write the plan."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.config import Settings
from core.exceptions import RenderError, StageError
from core.finance import build_timing, resolve_methodology, resolve_rows
from core.models.finance import (
    ReelPlan,
    ReelSpec,
    ReelStyle,
    ResolvedBackground,
    ResolvedMusic,
    ResolvedVoiceover,
)
from core.models.manifest import SourceFile, StageName
from core.reel_input import media_type_for, parse_reel_spec, sha256_of, verify_image
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main
from rendering.ffmpeg import FFmpeg


class FinanceStage(Stage):
    """Resolve a finance reel spec into a verified, renderable plan."""

    name = StageName.FINANCE
    version = 2

    def __init__(
        self,
        ffmpeg_factory: Callable[[Settings], FFmpeg] = lambda s: FFmpeg.locate(s.ffmpeg_path),
    ) -> None:
        self._ffmpeg_factory = ffmpeg_factory

    def config_parts(self, ctx: StageContext) -> object:
        finance = ctx.settings.finance
        return {
            "timing": finance.timing.model_dump(),
            "fps": finance.fps,
            "min_short_side": finance.min_background_short_side_px,
            "extensions": [finance.image_extensions, finance.video_extensions],
        }

    def run(self, ctx: StageContext) -> StageResult:
        reel = ctx.manifest.input.reel
        if reel is None:
            raise StageError("This job has no reel spec; create it with --format finance_facts.")
        spec_path = self._stored(ctx, reel.spec)
        spec = parse_reel_spec(spec_path.read_text(encoding="utf-8-sig"), spec_path.suffix)

        rows, calculation = resolve_rows(spec)
        warnings: list[str] = []
        background = self._inspect_background(ctx, reel.background, spec, warnings)
        voiceover = self._inspect_voiceover(ctx, reel.voiceover) if reel.voiceover else None
        music = None
        if reel.music and spec.music:
            audio = self._inspect_voiceover(ctx, reel.music)
            if spec.music.start_s >= audio.duration_s:
                raise StageError("music.start_s must be before the end of the supplied track")
            music = ResolvedMusic(**audio.model_dump(), settings=spec.music)
        style_data = spec.style.model_dump()
        for name, source in reel.fonts.items():
            self._stored(ctx, source)
            style_data[name] = source.stored_path
        style = ReelStyle.model_validate(style_data)

        finance = ctx.settings.finance
        timing = finance.timing
        if spec.pacing:
            timing = timing.model_copy(update=spec.pacing.model_dump())
        plan = ReelPlan(
            title=spec.title,
            methodology=resolve_methodology(spec),
            rows=rows,
            cta=spec.cta,
            disclosure=spec.disclosure,
            accent_color=spec.accent_color,
            background=background,
            voiceover=voiceover,
            music=music,
            style=style,
            provenance=spec.provenance,
            calculation=calculation,
            timing=build_timing(
                len(rows), timing, finance.fps, voiceover.duration_s if voiceover else None
            ),
            warnings=warnings,
        )
        for warning in warnings:
            ctx.log.warning(warning)
        out = ctx.save(artifacts.reel_plan_json(ctx.paths), plan)
        kind = "calculated" if calculation else "manual"
        note = f"{len(rows)} {kind} row(s), {plan.timing.duration_s:.1f}s, " + (
            "with voiceover" if voiceover else "text only"
        )
        return StageResult([out], note=note)

    @staticmethod
    def _stored(ctx: StageContext, source: SourceFile) -> Path:
        """The job's copy of a user file, checked against the hash taken at job creation."""
        path = ctx.paths.absolute(source.stored_path)
        if not path.is_file():
            raise StageError(f"{source.label} file is missing from the job: {source.stored_path}")
        if sha256_of(path) != source.sha256:
            raise StageError(f"{source.label} file changed since the job was created")
        return path

    def _inspect_background(
        self, ctx: StageContext, source: SourceFile, spec: ReelSpec, warnings: list[str]
    ) -> ResolvedBackground:
        path = self._stored(ctx, source)
        kind = media_type_for(path, ctx.settings)
        duration: float | None = None
        if kind == "image":
            width, height = verify_image(path)
        elif kind == "video":
            info = self._ffmpeg_factory(ctx.settings).analyze(path)
            if not info.has_video or not info.width or not info.height:
                raise RenderError(f"Background {path.name} has no readable video stream")
            if info.duration_s <= 0:
                raise RenderError(f"Background {path.name} has no duration")
            width, height, duration = info.width, info.height, info.duration_s
        else:
            raise StageError(f"Background {path.name} is neither a supported image nor video")

        floor = ctx.settings.finance.min_background_short_side_px
        if min(width, height) < floor:
            warnings.append(
                f"Background is only {width}x{height}; it will be upscaled and may look soft "
                f"(recommended short side >= {floor}px)."
            )
        return ResolvedBackground(
            stored_path=source.stored_path,
            sha256=source.sha256,
            media_type=kind,
            width=width,
            height=height,
            duration_s=duration,
            origin=spec.background.origin,
            license=spec.background.license,
            attribution=spec.background.attribution,
            crop_x=spec.background.crop_x,
            crop_y=spec.background.crop_y,
        )

    def _inspect_voiceover(self, ctx: StageContext, source: SourceFile) -> ResolvedVoiceover:
        path = self._stored(ctx, source)
        info = self._ffmpeg_factory(ctx.settings).analyze(path)
        if not info.has_audio or info.duration_s <= 0:
            raise RenderError(f"{source.label} {path.name} has no readable audio")
        if source.label == "Music" and (
            info.mean_volume_db is None or info.mean_volume_db <= ctx.settings.qa.min_mean_volume_db
        ):
            raise RenderError(
                f"Music {path.name} is silent or below the configured audio threshold"
            )
        return ResolvedVoiceover(
            stored_path=source.stored_path, sha256=source.sha256, duration_s=info.duration_s
        )


main = make_main(FinanceStage)

if __name__ == "__main__":
    raise SystemExit(main())
