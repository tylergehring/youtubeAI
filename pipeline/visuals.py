"""Resolve each scene into a concrete visual specification (palette, assets) for the renderer."""

from __future__ import annotations

from core.models.manifest import StageName
from core.models.media import SceneVisual, VisualManifest
from core.models.scenes import ScenePlan
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main


class VisualsStage(Stage):
    """Assign visual styling per scene. Milestone 1 uses only programmatic placeholders."""

    name = StageName.VISUALS

    def config_parts(self, ctx: StageContext) -> object:
        return ctx.settings.style(ctx.manifest.style).model_dump()

    def run(self, ctx: StageContext) -> StageResult:
        plan = ctx.load(artifacts.scenes_json(ctx.paths), ScenePlan)
        style = ctx.settings.style(ctx.manifest.style)
        scenes = [
            SceneVisual(
                scene_id=scene.scene_id,
                renderer="placeholder",
                background_color=style.palette[index % len(style.palette)],
                accent_color=style.accent_color,
                object_count=len(scene.objects),
            )
            for index, scene in enumerate(plan.scenes)
        ]
        return StageResult(
            [ctx.save(artifacts.visuals_json(ctx.paths), VisualManifest(scenes=scenes))]
        )


main = make_main(VisualsStage)

if __name__ == "__main__":
    raise SystemExit(main())
