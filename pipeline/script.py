"""Generate original narration, organised by section, from the lesson and verified knowledge."""

from __future__ import annotations

import json

from core.exceptions import StageError
from core.models.content import (
    ClaimStatus,
    KnowledgeBase,
    LessonPlan,
    ResearchReport,
    Script,
)
from core.models.manifest import StageName
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main

SYSTEM = (
    "You write spoken narration for educational videos: concrete examples, progressive "
    "explanation, natural spoken language, varied sentence length, no filler, no generic AI "
    "phrasing, and never copy the source's wording. State uncertain claims with hedging or "
    "omit them. Output only JSON matching the schema."
)


def check_claim_references(script: Script, knowledge: KnowledgeBase) -> list[str]:
    """Return problems: narration citing claim IDs that do not exist in the knowledge base."""
    known = {c.id for c in knowledge.claims}
    return [
        f"section '{section.id}' cites unknown claim '{claim_id}'"
        for section in script.sections
        for claim_id in section.claim_ids
        if claim_id not in known
    ]


class ScriptStage(Stage):
    """Generate the sectioned narration script."""

    name = StageName.SCRIPT

    def provider_parts(self, ctx: StageContext) -> tuple[str, str]:
        return ctx.providers.llm.name, ctx.providers.llm.model

    def run(self, ctx: StageContext) -> StageResult:
        knowledge = ctx.load(artifacts.knowledge_json(ctx.paths), KnowledgeBase)
        research = ctx.load(artifacts.research_json(ctx.paths), ResearchReport)
        lesson = ctx.load(artifacts.lesson_json(ctx.paths), LessonPlan)
        verification = research.by_claim()

        claims = [
            {
                "id": c.id,
                "text": c.text,
                "status": verification[c.id].status.value
                if c.id in verification
                else c.status.value,
            }
            for c in knowledge.claims
        ]
        prompt = (
            f"TOPIC: {knowledge.topic}\n"
            f"AUDIENCE: {ctx.manifest.audience.value}\n"
            f"TARGET DURATION MINUTES: {ctx.manifest.target_duration_minutes}\n"
            f"LESSON: {lesson.model_dump_json()}\n"
            f"CLAIMS: {json.dumps(claims)}\n"
            "Return JSON matching the Script schema:\n"
            f"{json.dumps(Script.model_json_schema())}"
        )
        llm = ctx.providers.llm
        response = llm.generate_structured(prompt, Script, system=SYSTEM)
        ctx.record_usage(llm.name, llm.model, response.usage)
        script = response.value

        problems = check_claim_references(script, knowledge)
        if problems:
            raise StageError("Script failed validation: " + "; ".join(problems))

        uncertain = {c["id"] for c in claims if c["status"] == ClaimStatus.UNCERTAIN_CLAIM.value}
        for section in script.sections:
            used = uncertain.intersection(section.claim_ids)
            if used:
                ctx.log.warning(
                    "Section '%s' relies on uncertain claims: %s", section.id, sorted(used)
                )
        return StageResult([ctx.save(artifacts.script_json(ctx.paths), script)])


main = make_main(ScriptStage)

if __name__ == "__main__":
    raise SystemExit(main())
