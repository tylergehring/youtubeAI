"""Convert the script into a validated scene plan, separating content from rendering."""

from __future__ import annotations

import json

from core.exceptions import StageError
from core.models.content import Script
from core.models.manifest import StageName
from core.models.scenes import ScenePlan
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main

SYSTEM = (
    "You are a visual director. Break the script into scenes using only the supported object "
    "and animation types. Positions are normalised 0..1. Output only JSON matching the schema."
)


def check_scene_coverage(plan: ScenePlan, script: Script) -> list[str]:
    """Return problems: unknown section references and script sections with no scene."""
    section_ids = {s.id for s in script.sections}
    problems = [
        f"scene {scene.scene_id} references unknown script section '{scene.section_id}'"
        for scene in plan.scenes
        if scene.section_id is not None and scene.section_id not in section_ids
    ]
    covered = {s.section_id for s in plan.scenes}
    problems += [f"script section '{sid}' has no scene" for sid in sorted(section_ids - covered)]
    return problems


class ScenesStage(Stage):
    """Plan scenes (visual type, objects, animations) from the script."""

    name = StageName.SCENES

    def provider_parts(self, ctx: StageContext) -> tuple[str, str]:
        return ctx.providers.llm.name, ctx.providers.llm.model

    def run(self, ctx: StageContext) -> StageResult:
        script = ctx.load(artifacts.script_json(ctx.paths), Script)
        prompt = (
            f"TOPIC: {ctx.manifest.topic}\n"
            f"AUDIENCE: {ctx.manifest.audience.value}\n"
            f"SCRIPT: {script.model_dump_json()}\n"
            "Return JSON matching the ScenePlan schema:\n"
            f"{json.dumps(ScenePlan.model_json_schema())}"
        )
        value = ctx.generate_structured(prompt, ScenePlan, system=SYSTEM)
        problems = check_scene_coverage(value, script)
        if problems:
            raise StageError("Scene plan failed validation: " + "; ".join(problems))
        return StageResult([ctx.save(artifacts.scenes_json(ctx.paths), value)])


main = make_main(ScenesStage)

if __name__ == "__main__":
    raise SystemExit(main())
