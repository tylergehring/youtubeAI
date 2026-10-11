"""Formats: which stages each runs, and that the explainer's behaviour is untouched."""

from __future__ import annotations

import pytest

from core.models.manifest import (
    FINANCE_DEPENDENCIES,
    FINANCE_STAGE_ORDER,
    STAGE_DEPENDENCIES,
    STAGE_ORDER,
    Audience,
    JobFormat,
    JobInput,
    JobManifest,
    StageName,
    StageState,
    downstream_of,
    stage_dependencies,
    stages_for,
)

S = StageName


class TestStagesPerFormat:
    def test_explainer_runs_exactly_the_original_twelve_stages(self) -> None:
        assert [s.value for s in stages_for(JobFormat.EXPLAINER)] == [
            "acquire", "knowledge", "research", "lesson", "script", "scenes", "visuals",
            "voice", "captions", "render", "validate", "package",
        ]  # fmt: skip
        assert stages_for(JobFormat.EXPLAINER) == STAGE_ORDER

    def test_finance_needs_no_transcripts_scenes_or_narration(self) -> None:
        assert stages_for(JobFormat.FINANCE_FACTS) == (
            S.FINANCE, S.RENDER, S.VALIDATE, S.PACKAGE,
        )  # fmt: skip

    def test_explainer_dependencies_are_the_original_graph(self) -> None:
        for stage in STAGE_ORDER:
            assert stage_dependencies(JobFormat.EXPLAINER, stage) == STAGE_DEPENDENCIES[stage]
        assert S.FINANCE not in STAGE_DEPENDENCIES

    def test_finance_dependencies(self) -> None:
        fmt = JobFormat.FINANCE_FACTS
        assert stage_dependencies(fmt, S.FINANCE) == ()
        assert stage_dependencies(fmt, S.RENDER) == (S.FINANCE,)
        assert stage_dependencies(fmt, S.VALIDATE) == (S.FINANCE, S.RENDER)
        assert stage_dependencies(fmt, S.PACKAGE) == (S.RENDER, S.VALIDATE)
        for stage, deps in FINANCE_DEPENDENCIES.items():
            for dep in deps:
                assert FINANCE_STAGE_ORDER.index(dep) < FINANCE_STAGE_ORDER.index(stage)

    def test_downstream_respects_the_format(self) -> None:
        assert downstream_of(S.FINANCE, JobFormat.FINANCE_FACTS) == [
            S.RENDER, S.VALIDATE, S.PACKAGE,
        ]  # fmt: skip
        assert downstream_of(S.SCENES) == downstream_of(S.SCENES, JobFormat.EXPLAINER)
        assert S.FINANCE not in downstream_of(S.ACQUIRE)


class TestManifestStages:
    def make(self, fmt: JobFormat) -> JobManifest:
        return JobManifest(
            job_id="j", input=JobInput(topic="x", format=fmt), style="s",
            target_duration_minutes=1, audience=Audience.BEGINNER,
        )  # fmt: skip

    def test_explainer_manifest_has_the_twelve_pending_stages(self) -> None:
        manifest = self.make(JobFormat.EXPLAINER)
        assert list(manifest.stages) == list(STAGE_ORDER)
        assert all(r.state is StageState.PENDING for r in manifest.stages.values())

    def test_finance_manifest_only_tracks_its_own_stages(self) -> None:
        assert list(self.make(JobFormat.FINANCE_FACTS).stages) == list(FINANCE_STAGE_ORDER)

    def test_old_manifests_without_a_format_load_as_explainer(self) -> None:
        data = self.make(JobFormat.EXPLAINER).model_dump(mode="json")
        del data["input"]["format"], data["input"]["reel"]
        loaded = JobManifest.model_validate(data)
        assert loaded.input.format is JobFormat.EXPLAINER and loaded.input.reel is None
        assert list(loaded.stages) == list(STAGE_ORDER)


class TestFingerprintInput:
    def test_explainer_fingerprint_input_is_the_pre_format_shape(self) -> None:
        data = JobInput(topic="How TCP works").fingerprint_data()
        assert data == {"topic": "How TCP works", "sources": []}

    def test_finance_input_includes_its_format(self) -> None:
        data = JobInput(topic="x", format=JobFormat.FINANCE_FACTS).fingerprint_data()
        assert data["format"] == "finance_facts"

    def test_job_input_rejects_unknown_formats(self) -> None:
        with pytest.raises(ValueError):
            JobInput(topic="x", format="podcast")  # type: ignore[arg-type]
