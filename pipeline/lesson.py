"""Plan an independently organised lesson from verified knowledge."""

from __future__ import annotations

import json

from core.models.content import KnowledgeBase, LessonPlan, ResearchReport
from core.models.manifest import StageName
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main

SYSTEM = (
    "You are an instructional designer. Organise the lesson around what the learner needs, "
    "not around the structure of any source video. Output only JSON matching the schema."
)


class LessonStage(Stage):
    """Transform knowledge into a structured lesson plan."""

    name = StageName.LESSON

    def provider_parts(self, ctx: StageContext) -> tuple[str, str]:
        return ctx.providers.llm.name, ctx.providers.llm.model

    def run(self, ctx: StageContext) -> StageResult:
        knowledge = ctx.load(artifacts.knowledge_json(ctx.paths), KnowledgeBase)
        research = ctx.load(artifacts.research_json(ctx.paths), ResearchReport)
        statuses = {r.claim_id: r.status.value for r in research.results}

        prompt = (
            f"TOPIC: {knowledge.topic}\n"
            f"AUDIENCE: {ctx.manifest.audience.value}\n"
            f"TARGET DURATION MINUTES: {ctx.manifest.target_duration_minutes}\n"
            f"KNOWLEDGE: {knowledge.model_dump_json()}\n"
            f"CLAIM STATUSES: {json.dumps(statuses)}\n"
            "Return JSON matching the LessonPlan schema:\n"
            f"{json.dumps(LessonPlan.model_json_schema())}"
        )
        value = ctx.generate_structured(prompt, LessonPlan, system=SYSTEM)
        return StageResult([ctx.save(artifacts.lesson_json(ctx.paths), value)])


main = make_main(LessonStage)

if __name__ == "__main__":
    raise SystemExit(main())
