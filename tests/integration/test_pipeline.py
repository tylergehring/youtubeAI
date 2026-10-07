import json
from collections.abc import Callable

import pytest

from core.config import Settings
from core.exceptions import (
    InvalidTransitionError,
    QualityCheckError,
    RenderError,
    StageError,
    UpstreamNotReadyError,
)
from core.jobs import JobStore
from core.models.content import SourceRecord
from core.models.manifest import JobStatus, StageName, StageState
from core.models.media import CostRecord, JobCost, ValidationReport
from pipeline import artifacts
from pipeline.acquire import AcquireStage
from pipeline.render import RenderStage
from pipeline.run import run_pipeline
from pipeline.script import ScriptStage
from pipeline.stage import Outcome, run_stage
from providers.llm.mock import MockLLMProvider
from providers.registry import ProviderSet
from providers.tts.mock import MockTTSProvider
from rendering.renderer import Renderer
from tests.conftest import SOURCE_SRT, SOURCE_TEXT, MakeJob

S = StageName
ALL_NO_RENDER = dict(end=S.CAPTIONS)  # stages that need no ffmpeg


def states(store: JobStore, job_id: str) -> dict[StageName, StageState]:
    return {n: r.state for n, r in store.load(job_id).stages.items()}


class TestFakePipelineWithoutFfmpeg:
    def test_runs_through_captions(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        outcomes = run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        assert all(o is Outcome.EXECUTED for o in outcomes.values())

        paths = store.paths(job.job_id)
        for artifact in (
            artifacts.source_json, artifacts.knowledge_json, artifacts.research_json,
            artifacts.lesson_json, artifacts.script_json, artifacts.scenes_json,
            artifacts.visuals_json, artifacts.audio_json, artifacts.captions_srt,
            artifacts.captions_json,
        ):  # fmt: skip
            assert artifact(paths).is_file(), artifact.__name__
        assert len(list(paths.subdir("audio").glob("scene_*.wav"))) == 5
        assert store.load(job.job_id).status is JobStatus.GENERATING_CAPTIONS

    def test_cost_records_every_external_call(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        cost = JobCost.model_validate_json(store.paths(job.job_id).cost_file.read_text())
        by_stage: dict[str, list[CostRecord]] = {}
        for record in cost.records:
            by_stage.setdefault(record.stage, []).append(record)
        assert {"knowledge", "research", "lesson", "script", "scenes", "voice"} <= set(by_stage)
        assert len(by_stage["voice"]) == 5 and by_stage["voice"][0].characters > 0
        assert by_stage["knowledge"][0].tokens_in > 0
        assert cost.total_estimated_cost_usd == 0.0
        assert all(r.job_id == job.job_id for r in cost.records)

    def test_logs_identify_job_and_stage(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, end=S.SCRIPT)
        text = store.paths(job.job_id).log_file.read_text()
        assert f"[JOB={job.job_id}] [STAGE=script]" in text
        assert "[INFO]" in text


class TestIdempotency:
    def test_rerun_reuses_everything_and_makes_no_calls(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        llm, tts = providers.llm, providers.tts
        assert isinstance(llm, MockLLMProvider) and isinstance(tts, MockTTSProvider)
        llm_calls, tts_calls = list(llm.calls), list(tts.calls)

        again = run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        assert set(again.values()) == {Outcome.REUSED}
        assert llm.calls == llm_calls and tts.calls == tts_calls
        assert {
            r.attempts
            for r in store.load(job.job_id).stages.values()
            if r.state is not StageState.PENDING
        } == {1}

    def test_force_regenerates_one_stage_and_marks_downstream_stale(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        assert isinstance(providers.llm, MockLLMProvider)
        before = len(providers.llm.calls)

        from pipeline.knowledge import KnowledgeStage

        outcome = run_stage(KnowledgeStage(), store, job.job_id, settings, providers, force=True)
        assert outcome is Outcome.EXECUTED and len(providers.llm.calls) == before + 1
        state = states(store, job.job_id)
        assert state[S.ACQUIRE] is StageState.SUCCEEDED
        assert state[S.KNOWLEDGE] is StageState.SUCCEEDED
        for stale in (S.RESEARCH, S.LESSON, S.SCRIPT, S.SCENES, S.VOICE, S.CAPTIONS):
            assert state[stale] is StageState.STALE

        again = run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        assert again[S.KNOWLEDGE] is Outcome.REUSED
        assert again[S.RESEARCH] is Outcome.EXECUTED  # stale stages re-run

    def test_changed_provider_config_invalidates_only_affected_stages(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        providers.tts = MockTTSProvider(voice="another-voice")
        outcomes = run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        assert outcomes[S.SCENES] is Outcome.REUSED
        assert outcomes[S.VISUALS] is Outcome.REUSED
        assert outcomes[S.VOICE] is Outcome.EXECUTED
        assert outcomes[S.CAPTIONS] is Outcome.EXECUTED

    def test_missing_output_triggers_rerun(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)
        artifacts.knowledge_json(store.paths(job.job_id)).unlink()
        outcomes = run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)
        assert outcomes[S.KNOWLEDGE] is Outcome.EXECUTED
        assert outcomes[S.ACQUIRE] is Outcome.REUSED


class TestFailureAndRetry:
    def test_failed_stage_can_be_retried_without_redoing_earlier_work(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        good_tts = providers.tts
        providers.tts = MockTTSProvider(fail_on_text_containing="SYN-ACK")
        with pytest.raises(StageError, match="voice"):
            run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)

        failed = store.load(job.job_id)
        assert failed.status is JobStatus.FAILED and failed.failed_stage is S.VOICE
        assert "ProviderError" in (failed.error or "")
        assert failed.stage(S.VOICE).state is StageState.FAILED
        assert failed.stage(S.SCENES).state is StageState.SUCCEEDED
        assert artifacts.scenes_json(store.paths(job.job_id)).is_file()  # intermediates preserved

        # Stage 8 fails -> only stage 8 is rerun.
        assert isinstance(providers.llm, MockLLMProvider)
        llm_calls = list(providers.llm.calls)
        providers.tts = good_tts
        outcomes = run_pipeline(store, job.job_id, settings, providers, **ALL_NO_RENDER)
        assert providers.llm.calls == llm_calls
        assert outcomes[S.SCENES] is Outcome.REUSED and outcomes[S.VOICE] is Outcome.EXECUTED
        done = store.load(job.job_id)
        assert done.status is JobStatus.GENERATING_CAPTIONS and done.failed_stage is None
        assert done.stage(S.VOICE).attempts == 2 and done.stage(S.SCENES).attempts == 1
        assert done.stage(S.VOICE).error is None

    def test_malformed_llm_output_fails_the_stage_and_writes_nothing(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        providers.llm = MockLLMProvider(raw_overrides={"LessonPlan": '{"title": "x"}'})
        with pytest.raises(StageError, match="StructuredOutputError"):
            run_pipeline(store, job.job_id, settings, providers)
        paths = store.paths(job.job_id)
        assert not artifacts.lesson_json(paths).exists()
        assert artifacts.knowledge_json(paths).is_file()
        manifest = store.load(job.job_id)
        assert manifest.failed_stage is S.LESSON
        assert manifest.stage(S.SCRIPT).state is StageState.PENDING

    def test_stage_cannot_run_before_its_upstream(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        with pytest.raises(UpstreamNotReadyError, match="knowledge"):
            run_stage(ScriptStage(), store, job.job_id, settings, providers)
        manifest = store.load(job.job_id)
        assert manifest.status is JobStatus.CREATED
        assert manifest.stage(S.SCRIPT).attempts == 0

    def test_script_with_unknown_claim_is_rejected(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        from providers import mock_data

        bad = mock_data.script()
        bad["sections"][0]["claim_ids"] = ["nope"]
        providers.llm = MockLLMProvider(raw_overrides={"Script": json.dumps(bad)})
        job = make_job()
        with pytest.raises(StageError, match="unknown claim 'nope'"):
            run_pipeline(store, job.job_id, settings, providers)
        assert not artifacts.script_json(store.paths(job.job_id)).exists()

    def test_finished_job_cannot_be_rerun_into_a_pipeline_state(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        store.update(job.job_id, lambda m: setattr(m, "status", JobStatus.UPLOADED))
        with pytest.raises(InvalidTransitionError):
            run_stage(AcquireStage(), store, job.job_id, settings, providers, force=True)
        assert store.load(job.job_id).stage(S.ACQUIRE).attempts == 0

    def test_unexpected_exception_is_wrapped_and_secrets_redacted(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:  # fmt: skip
        job = make_job()

        def explode(*_: object, **__: object) -> None:
            raise RuntimeError("request failed with api_key=sk-SECRETSECRETSECRET1234")

        monkeypatch.setattr(providers.llm, "generate_structured", explode)
        with pytest.raises(StageError) as info:
            run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)
        assert "SECRETSECRET" not in str(info.value)
        assert "SECRETSECRET" not in (store.load(job.job_id).error or "")
        assert "SECRETSECRET" not in store.paths(job.job_id).log_file.read_text()


class TestTranscriptIngestion:
    UPLOADS = [("Creator A.txt", SOURCE_TEXT), ("creator_b.srt", SOURCE_SRT)]

    def test_transcripts_are_parsed_recorded_and_fed_to_the_llm(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job(transcripts=self.UPLOADS)
        run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)

        paths = store.paths(job.job_id)
        record = SourceRecord.model_validate_json(artifacts.source_json(paths).read_text())
        assert [(e.label, e.format) for e in record.sources] == [
            ("Creator A", "txt"),
            ("creator_b", "srt"),
        ]
        assert all(e.word_count > 0 and e.sha256 for e in record.sources)
        assert any("nothing was fetched" in n for n in record.notes)
        assert artifacts.transcript_json(paths, 1).is_file()
        assert artifacts.transcript_json(paths, 2).is_file()

        assert isinstance(providers.llm, MockLLMProvider)
        prompt = providers.llm.prompts[0]
        assert "TOPIC: How TCP works" in prompt
        assert "SOURCE 1: Creator A" in prompt and "SOURCE 2: creator_b" in prompt
        assert "TCP basically makes sure" in prompt and "so nothing is lost" in prompt

    def test_works_with_topic_only(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)
        paths = store.paths(job.job_id)
        record = SourceRecord.model_validate_json(artifacts.source_json(paths).read_text())
        assert record.sources == [] and any("topic alone" in n for n in record.notes)
        assert isinstance(providers.llm, MockLLMProvider)
        assert "none supplied" in providers.llm.prompts[0]

    def test_rerun_after_ingestion_reuses_everything(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job(transcripts=self.UPLOADS)
        run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)
        again = run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)
        assert set(again.values()) == {Outcome.REUSED}

    def test_tampered_stored_transcript_fails_acquire(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job(transcripts=self.UPLOADS)
        stored = store.paths(job.job_id).absolute(job.input.sources[0].stored_path)
        stored.write_text("something else entirely", encoding="utf-8")
        with pytest.raises(StageError, match="changed or missing"):
            run_pipeline(store, job.job_id, settings, providers, end=S.ACQUIRE)
        assert store.load(job.job_id).failed_stage is S.ACQUIRE

    def test_no_network_provider_is_involved(self, providers: ProviderSet) -> None:
        assert not hasattr(providers, "youtube") and not hasattr(providers, "transcription")


class TestFullPipelineWithFfmpeg:
    @pytest.mark.ffmpeg
    def test_end_to_end(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job(transcripts=TestTranscriptIngestion.UPLOADS)
        run_pipeline(store, job.job_id, settings, providers)
        manifest = store.load(job.job_id)
        assert manifest.status is JobStatus.AWAITING_REVIEW
        assert all(
            r.state in (StageState.SUCCEEDED, StageState.SKIPPED) for r in manifest.stages.values()
        )

        paths = store.paths(job.job_id)
        assert artifacts.final_video(paths).stat().st_size > 1000
        assert artifacts.final_captions(paths).read_text().startswith("1\n")
        summary = json.loads(artifacts.final_summary(paths).read_text())
        assert summary["technical_checks_passed"] is True
        assert summary["estimated_cost_usd"] == 0.0
        report = ValidationReport.model_validate_json(artifacts.validation_json(paths).read_text())
        assert report.technical_passed and not report.failures
        overlap = next(c for c in report.checks if c.name == "source_overlap")
        assert overlap.passed and not overlap.skipped  # compared against the uploaded transcripts
        assert paths.cost_file.is_file()

    @pytest.mark.ffmpeg
    def test_render_retry_after_failure_keeps_earlier_stages(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()

        def broken(_: Settings) -> Renderer:
            raise RenderError("simulated renderer crash")

        from pipeline.run import default_stages

        stages = default_stages()
        stages[S.RENDER] = RenderStage(renderer_factory=broken)
        with pytest.raises(StageError, match="simulated renderer crash"):
            run_pipeline(store, job.job_id, settings, providers, stages=stages)
        failed = store.load(job.job_id)
        assert failed.failed_stage is S.RENDER and failed.status is JobStatus.FAILED

        assert isinstance(providers.llm, MockLLMProvider)
        calls = list(providers.llm.calls)
        outcomes = run_pipeline(store, job.job_id, settings, providers)
        assert providers.llm.calls == calls
        assert outcomes[S.VOICE] is Outcome.REUSED and outcomes[S.RENDER] is Outcome.EXECUTED
        done = store.load(job.job_id)
        assert done.status is JobStatus.AWAITING_REVIEW
        assert done.stage(S.RENDER).attempts == 2

    @pytest.mark.ffmpeg
    def test_corrupt_video_fails_quality_control_but_keeps_report(
        self, store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
    ) -> None:
        job = make_job()
        run_pipeline(store, job.job_id, settings, providers, end=S.RENDER)
        paths = store.paths(job.job_id)
        artifacts.render_video(paths).write_bytes(b"this is not a video" * 200)

        with pytest.raises(QualityCheckError, match="video_opens"):
            run_pipeline(store, job.job_id, settings, providers, start=S.VALIDATE, end=S.VALIDATE)
        report = ValidationReport.model_validate_json(artifacts.validation_json(paths).read_text())
        assert not report.technical_passed
        assert "video_opens" in {c.name for c in report.failures}
        assert store.load(job.job_id).failed_stage is S.VALIDATE


def test_render_without_ffmpeg_fails_clearly(
    store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
) -> None:
    no_ffmpeg = settings.model_copy(
        update={"ffmpeg_path": str(settings.workspace_dir / "missing.exe")}
    )
    job = make_job()
    run_pipeline(store, job.job_id, no_ffmpeg, providers, end=S.CAPTIONS)
    with pytest.raises(StageError, match="FFmpeg not found"):
        run_pipeline(store, job.job_id, no_ffmpeg, providers, start=S.RENDER, end=S.RENDER)
    assert store.load(job.job_id).failed_stage is S.RENDER


_ = Callable  # keep import grouping stable for tooling
