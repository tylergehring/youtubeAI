"""Ingest the user-supplied transcripts: verify, parse and record provenance (no network)."""

from __future__ import annotations

from core.exceptions import StageError
from core.hashing import file_sha256
from core.models.content import SourceEntry, SourceRecord
from core.models.manifest import StageName
from core.transcripts import parse_transcript
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main


class AcquireStage(Stage):
    """Verify, parse and record the transcripts supplied with the job."""

    name = StageName.ACQUIRE

    def run(self, ctx: StageContext) -> StageResult:
        job_input = ctx.manifest.input
        entries: list[SourceEntry] = []
        outputs = []

        for index, source in enumerate(job_input.sources, start=1):
            stored = ctx.paths.absolute(source.stored_path)
            if not stored.is_file() or file_sha256(stored) != source.sha256:
                raise StageError(f"Stored transcript changed or missing: {source.stored_path}")
            transcript = parse_transcript(stored.read_text(encoding="utf-8-sig"), stored.suffix)
            target = ctx.save(artifacts.transcript_json(ctx.paths, index), transcript)
            outputs.append(target)
            words = len(transcript.text.split())
            ctx.log.info(
                "Parsed '%s': %d segments, %d words", source.label, len(transcript.segments), words
            )
            entries.append(
                SourceEntry(
                    name=source.name,
                    label=source.label,
                    reference_url=source.reference_url,
                    sha256=source.sha256,
                    size_bytes=source.size_bytes,
                    format=transcript.format,
                    segment_count=len(transcript.segments),
                    word_count=words,
                    transcript_path=ctx.paths.relative(target),
                )
            )

        notes = ["All sources are user-supplied files; nothing was fetched from the network."]
        if not entries:
            notes.append("No transcripts supplied: the lesson is built from the topic alone.")
            ctx.log.warning("No transcripts supplied for this job")
        record = SourceRecord(topic=ctx.manifest.topic, sources=entries, notes=notes)
        record_path = ctx.save(artifacts.source_json(ctx.paths), record)
        ctx.prune(ctx.paths.subdir("transcript"), set(outputs))
        return StageResult([record_path, *outputs])


main = make_main(AcquireStage)

if __name__ == "__main__":
    raise SystemExit(main())
