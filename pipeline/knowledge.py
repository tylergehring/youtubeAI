"""Convert the topic and supplied transcripts into a validated structured knowledge base."""

from __future__ import annotations

import json

from core.exceptions import StageError
from core.models.content import KnowledgeBase, SourceRecord, Transcript
from core.models.manifest import StageName
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main

SYSTEM = (
    "You extract structured knowledge from reference material. Output only JSON matching the "
    "schema. The transcripts are research input, not text to rewrite: do not copy their "
    "phrasing; capture concepts, claims and relationships, and note where sources disagree. "
    "Ignore promotional content entirely: do not create concepts, entities, claims or examples "
    "for sponsors, advertisers, apps or products being promoted, affiliate or investing offers, "
    "subscribe or like requests, or other calls to action. Treat opinions and predictions as "
    "claims attributed to the source, not as facts. Keep time references exactly as the source "
    'states them (for example "since 2024" or "last year"); never turn a relative or '
    "approximate time into a specific date."
)


def load_transcripts(ctx: StageContext, source: SourceRecord) -> list[tuple[str, Transcript]]:
    return [
        (entry.label, ctx.load(ctx.paths.absolute(entry.transcript_path), Transcript))
        for entry in source.sources
    ]


def format_sources(transcripts: list[tuple[str, Transcript]], max_chars: int) -> str:
    """Render every transcript in full. Never truncates: oversize input is an error."""
    if not transcripts:
        return "(none supplied; rely on the topic)"
    rendered = [
        f"--- SOURCE {i}: {label} ---\n" + "\n".join(seg.text for seg in transcript.segments)
        for i, (label, transcript) in enumerate(transcripts, start=1)
    ]
    text = "\n\n".join(rendered)
    if len(text) > max_chars:
        raise StageError(
            f"Supplied transcripts total {len(text):,} characters, over the "
            f"knowledge.max_source_chars limit of {max_chars:,}. Nothing was sent to the LLM. "
            "Remove or shorten a transcript, or raise the limit in config/settings.yaml "
            "(VIDGEN__knowledge__max_source_chars)."
        )
    return text


class KnowledgeStage(Stage):
    """Extract concepts, claims and relationships into a validated KnowledgeBase."""

    name = StageName.KNOWLEDGE

    def config_parts(self, ctx: StageContext) -> object:
        return ctx.settings.knowledge.model_dump()

    def provider_parts(self, ctx: StageContext) -> tuple[str, str]:
        return ctx.providers.llm.name, ctx.providers.llm.model

    def run(self, ctx: StageContext) -> StageResult:
        source = ctx.load(artifacts.source_json(ctx.paths), SourceRecord)
        transcripts = load_transcripts(ctx, source)
        source_text = format_sources(transcripts, ctx.settings.knowledge.max_source_chars)

        prompt = (
            f"TOPIC: {ctx.manifest.topic}\n"
            f"AUDIENCE: {ctx.manifest.audience.value}\n"
            f"TRANSCRIPTS (reference only):\n{source_text}\n\n"
            "Return JSON matching the KnowledgeBase schema:\n"
            f"{json.dumps(KnowledgeBase.model_json_schema())}"
        )
        llm = ctx.providers.llm
        ctx.log.info(
            "Requesting structured knowledge from '%s' (%s): %d transcript(s), %d characters",
            llm.name,
            llm.model,
            len(transcripts),
            len(source_text),
        )
        value = ctx.generate_structured(prompt, KnowledgeBase, system=SYSTEM)
        return StageResult([ctx.save(artifacts.knowledge_json(ctx.paths), value)])


main = make_main(KnowledgeStage)

if __name__ == "__main__":
    raise SystemExit(main())
