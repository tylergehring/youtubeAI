import json
import threading
from pathlib import Path

import pytest

from core.exceptions import (
    InvalidInputError,
    InvalidTransitionError,
    JobExistsError,
    JobNotFoundError,
)
from core.jobs import JOB_SUBDIRS, JobStore
from core.models.manifest import (
    ALLOWED_TRANSITIONS,
    PIPELINE_STATUSES,
    Audience,
    JobInput,
    JobManifest,
    JobStatus,
    StageName,
    StageState,
)
from tests.conftest import SOURCE_SRT, SOURCE_TEXT, MakeJob, WriteFile


class TestCreate:
    def test_creates_layout_and_manifest(self, store: JobStore, make_job: MakeJob) -> None:
        manifest = make_job()
        paths = store.paths(manifest.job_id)
        assert manifest.job_id.endswith("_how_tcp_works_001")
        for name in JOB_SUBDIRS:
            assert paths.subdir(name).is_dir()
        assert json.loads(paths.input_json.read_text())["topic"] == "How TCP works"
        assert store.load(manifest.job_id) == manifest
        assert manifest.topic == "How TCP works"
        assert manifest.status is JobStatus.CREATED

    def test_sequence_increments(self, make_job: MakeJob) -> None:
        first, second = make_job(), make_job()
        assert first.job_id.endswith("_001")
        assert second.job_id.endswith("_002")

    def test_explicit_id_and_duplicate(self, make_job: MakeJob) -> None:
        make_job(job_id="my-job")
        with pytest.raises(JobExistsError):
            make_job(job_id="my-job")

    def test_rejects_traversal_ids(self, make_job: MakeJob, store: JobStore) -> None:
        with pytest.raises(InvalidInputError):
            make_job(job_id="../escape")
        assert not (store.workspace_dir / "escape").exists()

    def test_transcripts_are_copied_into_the_job(
        self, store: JobStore, make_job: MakeJob, write_file: WriteFile
    ) -> None:
        manifest = make_job(transcripts=[("Creator A.txt", SOURCE_TEXT), ("b.srt", SOURCE_SRT)])
        paths = store.paths(manifest.job_id)
        first, second = manifest.input.sources
        assert first.stored_path == "source/originals/01_creator_a.txt"
        assert second.stored_path == "source/originals/02_b.srt"
        assert paths.absolute(first.stored_path).read_text(encoding="utf-8") == SOURCE_TEXT
        assert first.label == "Creator A"
        assert len(first.sha256) == 64 and first.size_bytes == len(SOURCE_TEXT.encode())
        # The job no longer depends on the original upload.
        write_file("Creator A.txt", "changed").unlink()
        assert paths.absolute(first.stored_path).read_text(encoding="utf-8") == SOURCE_TEXT
        assert store.load(manifest.job_id).input.sources == manifest.input.sources

    def test_topic_is_required(self, store: JobStore) -> None:
        with pytest.raises(InvalidInputError, match="topic"):
            store.create("   ", style="s", target_duration_minutes=1, audience=Audience.BEGINNER)
        assert store.list_jobs() == []

    def test_job_without_transcripts_is_allowed(self, make_job: MakeJob) -> None:
        assert make_job().input.sources == []

    def test_load_missing(self, store: JobStore) -> None:
        with pytest.raises(JobNotFoundError):
            store.load("nope")

    def test_list_jobs(self, store: JobStore, make_job: MakeJob) -> None:
        ids = {make_job().job_id, make_job().job_id}
        assert {m.job_id for m in store.list_jobs()} == ids


class TestManifestUpdates:
    def test_update_persists_and_bumps_timestamp(self, store: JobStore, make_job: MakeJob) -> None:
        job = make_job()

        def mutate(m: JobManifest) -> None:
            m.stage(StageName.ACQUIRE).state = StageState.SUCCEEDED

        updated = store.update(job.job_id, mutate)
        assert store.load(job.job_id).stage(StageName.ACQUIRE).state is StageState.SUCCEEDED
        assert updated.updated_at >= job.updated_at

    def test_failed_mutation_writes_nothing(self, store: JobStore, make_job: MakeJob) -> None:
        job = make_job()

        def mutate(m: JobManifest) -> None:
            m.stage(StageName.ACQUIRE).attempts = 99
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            store.update(job.job_id, mutate)
        assert store.load(job.job_id).stage(StageName.ACQUIRE).attempts == 0

    def test_manifest_is_valid_json_on_disk_and_no_temp_files(
        self, store: JobStore, make_job: MakeJob
    ) -> None:
        job = make_job()
        store.set_status(job.job_id, JobStatus.ACQUIRING_SOURCE)
        root = store.paths(job.job_id).root
        assert json.loads((root / "manifest.json").read_text())["status"] == "acquiring_source"
        assert not list(root.glob("*.tmp")) and not list(root.glob(".manifest.json.*"))

    def test_concurrent_updates_do_not_lose_writes(
        self, store: JobStore, make_job: MakeJob
    ) -> None:
        job = make_job()

        def bump() -> None:
            for _ in range(10):
                store.update(
                    job.job_id,
                    lambda m: setattr(
                        m.stage(StageName.KNOWLEDGE),
                        "attempts",
                        m.stage(StageName.KNOWLEDGE).attempts + 1,
                    ),
                )

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert store.load(job.job_id).stage(StageName.KNOWLEDGE).attempts == 40


class TestStateMachine:
    def test_pipeline_statuses_move_freely(self, store: JobStore, make_job: MakeJob) -> None:
        job = make_job()
        store.set_status(job.job_id, JobStatus.RENDERING)
        store.set_status(job.job_id, JobStatus.EXTRACTING_KNOWLEDGE)  # retry/backwards is allowed
        assert store.load(job.job_id).status is JobStatus.EXTRACTING_KNOWLEDGE

    def test_failed_records_stage_and_clears_on_retry(
        self, store: JobStore, make_job: MakeJob
    ) -> None:
        job = make_job()
        store.set_status(job.job_id, JobStatus.RENDERING)
        failed = store.set_status(
            job.job_id, JobStatus.FAILED, failed_stage=StageName.RENDER, error="boom"
        )
        assert (failed.failed_stage, failed.error) == (StageName.RENDER, "boom")
        retried = store.set_status(job.job_id, JobStatus.RENDERING)
        assert retried.failed_stage is None and retried.error is None

    @pytest.mark.parametrize(
        ("start", "target"),
        [
            (JobStatus.CREATED, JobStatus.AWAITING_REVIEW),
            (JobStatus.CREATED, JobStatus.APPROVED),
            (JobStatus.RENDERING, JobStatus.AWAITING_REVIEW),  # only packaging may do this
            (JobStatus.AWAITING_REVIEW, JobStatus.UPLOADED),  # must be approved first
            (JobStatus.UPLOADED, JobStatus.RENDERING),
            (JobStatus.APPROVED, JobStatus.REJECTED),
        ],
    )
    def test_invalid_transitions_rejected(
        self, start: JobStatus, target: JobStatus, store: JobStore, make_job: MakeJob
    ) -> None:
        job = make_job()
        # Force the starting status directly (bypassing the machine) to test one transition.
        store.update(job.job_id, lambda m: setattr(m, "status", start))
        with pytest.raises(InvalidTransitionError):
            store.set_status(job.job_id, target)
        assert store.load(job.job_id).status is start

    def test_review_flow(self, store: JobStore, make_job: MakeJob) -> None:
        job = make_job()
        for status in (
            JobStatus.PACKAGING,
            JobStatus.AWAITING_REVIEW,
            JobStatus.APPROVED,
            JobStatus.UPLOADED,
        ):
            store.set_status(job.job_id, status)
        assert store.load(job.job_id).status is JobStatus.UPLOADED

    def test_every_status_has_a_rule(self) -> None:
        assert set(ALLOWED_TRANSITIONS) == set(JobStatus)
        assert set(JobStatus) >= PIPELINE_STATUSES


class TestPathsAndIndex:
    def test_absolute_refuses_escape(self, store: JobStore, make_job: MakeJob) -> None:
        paths = store.paths(make_job().job_id)
        assert paths.absolute("final/video.mp4").is_relative_to(paths.root)
        with pytest.raises(InvalidInputError):
            paths.absolute("../../outside.txt")

    def test_index_tracks_status_and_rebuilds(self, store: JobStore, make_job: MakeJob) -> None:
        job = make_job()
        store.set_status(job.job_id, JobStatus.RENDERING)
        rows = store.query_index(JobStatus.RENDERING)
        assert [r["job_id"] for r in rows] == [job.job_id]

        store.index_path.unlink()
        assert store.rebuild_index() == 1
        assert store.query_index()[0]["status"] == "rendering"

    def test_input_json_matches_manifest(self, store: JobStore, make_job: MakeJob) -> None:
        job = make_job()
        saved = JobInput.model_validate_json(Path(store.paths(job.job_id).input_json).read_text())
        assert saved == job.input
