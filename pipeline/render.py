"""Render the final MP4 from the scene plan, visuals and audio through the Renderer interface."""

from __future__ import annotations

from collections.abc import Callable

from core.config import Settings
from core.exceptions import RenderError
from core.models.finance import ReelPlan
from core.models.manifest import JobFormat, StageName
from core.models.media import AudioManifest, VisualManifest
from core.models.scenes import ScenePlan
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main
from rendering.reel import ReelRenderer, ReelRenderRequest, build_reel_renderer
from rendering.renderer import Renderer, RenderRequest, build_renderer
from rendering.timeline import build_timeline


class RenderStage(Stage):
    """Render scenes and narration into video.mp4, or a finance reel's text over its background."""

    name = StageName.RENDER

    def __init__(
        self,
        renderer_factory: Callable[[Settings], Renderer] = build_renderer,
        reel_renderer_factory: Callable[[Settings], ReelRenderer] = build_reel_renderer,
    ) -> None:
        self._renderer_factory = renderer_factory
        self._reel_renderer_factory = reel_renderer_factory

    def config_parts(self, ctx: StageContext) -> object:
        if ctx.manifest.input.format is JobFormat.FINANCE_FACTS:
            return ctx.settings.finance.model_dump(mode="json")
        return ctx.settings.video.model_dump()

    def _run_reel(self, ctx: StageContext) -> StageResult:
        plan = ctx.load(artifacts.reel_plan_json(ctx.paths), ReelPlan)
        renderer = self._reel_renderer_factory(ctx.settings)
        out = artifacts.render_video(ctx.paths)
        ctx.log.info(
            "Rendering finance reel: %d row(s), %.1fs, %s, with '%s'",
            len(plan.rows),
            plan.timing.duration_s,
            "voiceover" if plan.voiceover else "text only",
            renderer.name,
        )
        layout = artifacts.reel_layout_json(ctx.paths)
        result = renderer.render(
            ReelRenderRequest(
                paths=ctx.paths,
                plan=plan,
                finance=ctx.settings.finance,
                out_path=out,
                layout_path=layout,
            )
        )
        if not out.is_file() or out.stat().st_size == 0:
            raise RenderError("Renderer reported success but produced no video file")
        return StageResult([out, layout, ctx.save(artifacts.render_json(ctx.paths), result)])

    def run(self, ctx: StageContext) -> StageResult:
        if ctx.manifest.input.format is JobFormat.FINANCE_FACTS:
            return self._run_reel(ctx)
        plan = ctx.load(artifacts.scenes_json(ctx.paths), ScenePlan)
        audio = ctx.load(artifacts.audio_json(ctx.paths), AudioManifest)
        visuals = ctx.load(artifacts.visuals_json(ctx.paths), VisualManifest)
        video = ctx.settings.video
        timeline = build_timeline(plan, audio, padding_s=video.scene_padding_s, fps=video.fps)

        renderer = self._renderer_factory(ctx.settings)
        out = artifacts.render_video(ctx.paths)
        ctx.log.info(
            "Rendering %d scene(s), %.1fs total, with '%s'",
            len(timeline.entries),
            timeline.total_duration_s,
            renderer.name,
        )
        result = renderer.render(
            RenderRequest(
                paths=ctx.paths,
                plan=plan,
                audio=audio,
                visuals=visuals,
                timeline=timeline,
                out_path=out,
                video=video,
            )
        )
        if not out.is_file() or out.stat().st_size == 0:
            raise RenderError("Renderer reported success but produced no video file")
        return StageResult([out, ctx.save(artifacts.render_json(ctx.paths), result)])


main = make_main(RenderStage)

if __name__ == "__main__":
    raise SystemExit(main())
