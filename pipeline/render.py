"""Render the final MP4 from the scene plan, visuals and audio through the Renderer interface."""

from __future__ import annotations

from collections.abc import Callable

from core.config import Settings
from core.exceptions import RenderError
from core.models.manifest import StageName
from core.models.media import AudioManifest, VisualManifest
from core.models.scenes import ScenePlan
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main
from rendering.renderer import Renderer, RenderRequest, build_renderer
from rendering.timeline import build_timeline


class RenderStage(Stage):
    """Render scenes and narration into video.mp4."""

    name = StageName.RENDER

    def __init__(self, renderer_factory: Callable[[Settings], Renderer] = build_renderer) -> None:
        self._renderer_factory = renderer_factory

    def config_parts(self, ctx: StageContext) -> object:
        return ctx.settings.video.model_dump()

    def run(self, ctx: StageContext) -> StageResult:
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
