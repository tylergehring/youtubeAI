"""Convert the topic and supplied transcripts into a validated structured knowledge base."""

from __future__ import annotations

import json

from core.models.content import KnowledgeBase, SourceRecord, Transcript
from core.models.manifest import StageName
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main

MAX_SOURCE_CHARS = 12_000  # total across all transcripts, split evenly

SYSTEM = (
    "You extract structured knowledge from reference material. Output only JSON matching the "
    "schema. The transcripts are research input, not text to rewrite: do not copy their "
    "phrasing; capture concepts, claims and relationships, and note where sources disagree."
)


def load_transcripts(ctx: StageContext, source: SourceRecord) -> list[tuple[str, Transcript]]:
    return [
        (entry.label, ctx.load(ctx.paths.absolute(entry.transcript_path), Transcript))
        for entry in source.sources
    ]


def format_sources(transcripts: list[tuple[str, Transcript]]) -> str:
    if not transcripts:
        return "(none supplied; rely on the topic)"
    budget = MAX_SOURCE_CHARS // len(transcripts)
    return "\n\n".join(
        f"--- SOURCE {i}: {label} ---\n{transcript.text[:budget]}"
        for i, (label, transcript) in enumerate(transcripts, start=1)
    )


class KnowledgeStage(Stage):
    """Extract concepts, claims and relationships into a validated KnowledgeBase."""

    name = StageName.KNOWLEDGE

    def provider_parts(self, ctx: StageContext) -> tuple[str, str]:
        return ctx.providers.llm.name, ctx.providers.llm.model

    def run(self, ctx: StageContext) -> StageResult:
        source = ctx.load(artifacts.source_json(ctx.paths), SourceRecord)
        transcripts = load_transcripts(ctx, source)

        prompt = (
            f"TOPIC: {ctx.manifest.topic}\n"
            f"AUDIENCE: {ctx.manifest.audience.value}\n"
            f"TRANSCRIPTS (reference only):\n{format_sources(transcripts)}\n\n"
            "Return JSON matching the KnowledgeBase schema:\n"
            f"{json.dumps(KnowledgeBase.model_json_schema())}"
        )
        llm = ctx.providers.llm
        ctx.log.info(
            "Requesting structured knowledge from '%s' (%s) using %d transcript(s)",
            llm.name,
            llm.model,
            len(transcripts),
        )
        response = llm.generate_structured(prompt, KnowledgeBase, system=SYSTEM)
        ctx.record_usage(llm.name, llm.model, response.usage)
        return StageResult([ctx.save(artifacts.knowledge_json(ctx.paths), response.value)])


main = make_main(KnowledgeStage)

if __name__ == "__main__":
    raise SystemExit(main())
