"""Job directories, manifest persistence, the status state machine and the SQLite index.

manifest.json is the single source of truth. The SQLite index is derived data and can be
rebuilt at any time with JobStore.rebuild_index().
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from core.exceptions import (
    InvalidInputError,
    InvalidTransitionError,
    JobExistsError,
    JobNotFoundError,
)
from core.ingest import PreparedSource
from core.models.manifest import (
    ALLOWED_TRANSITIONS,
    Audience,
    JobInput,
    JobManifest,
    JobStatus,
    SourceFile,
    StageName,
    utcnow,
)
from core.naming import slugify, validate_job_id
from core.storage import atomic_write_bytes, read_json, write_json, write_model

JOB_SUBDIRS = (
    "source",
    "source/originals",
    "transcript",
    "research",
    "knowledge",
    "lesson",
    "script",
    "scenes",
    "audio",
    "visuals",
    "captions",
    "render",
    "validation",
    "final",
    "logs",
)


@dataclass(frozen=True)
class JobPaths:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def input_json(self) -> Path:
        return self.root / "input.json"

    @property
    def cost_file(self) -> Path:
        return self.root / "job_cost.json"

    @property
    def log_file(self) -> Path:
        return self.root / "logs" / "pipeline.log"

    @property
    def lock_file(self) -> Path:
        return self.root / ".manifest.lock"

    def subdir(self, name: str) -> Path:
        return self.root / name

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def absolute(self, relative: str) -> Path:
        """Resolve a manifest-relative path, refusing anything outside the job directory."""
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise InvalidInputError(f"Path escapes the job directory: {relative!r}")
        return resolved


class JobStore:
    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.jobs_dir = workspace_dir / "jobs"
        self.index_path = workspace_dir / "index.db"

    # ------------------------------------------------------------ lookup

    def paths(self, job_id: str) -> JobPaths:
        return JobPaths(self.jobs_dir / validate_job_id(job_id))

    def exists(self, job_id: str) -> bool:
        return self.paths(job_id).manifest.is_file()

    def load(self, job_id: str) -> JobManifest:
        paths = self.paths(job_id)
        if not paths.manifest.is_file():
            raise JobNotFoundError(f"Job {job_id!r} not found in {self.jobs_dir}")
        return JobManifest.model_validate(read_json(paths.manifest))

    def list_jobs(self) -> list[JobManifest]:
        if not self.jobs_dir.is_dir():
            return []
        manifests: list[JobManifest] = []
        for child in sorted(self.jobs_dir.iterdir()):
            if (child / "manifest.json").is_file():
                manifests.append(JobManifest.model_validate(read_json(child / "manifest.json")))
        return manifests

    # ------------------------------------------------------------ creation

    def _next_job_id(self, topic_slug: str, day: datetime) -> str:
        prefix = f"{day:%Y-%m-%d}_{topic_slug}_"
        existing = (
            [p.name for p in self.jobs_dir.iterdir() if p.name.startswith(prefix)]
            if self.jobs_dir.is_dir()
            else []
        )
        sequence = 1
        for name in existing:
            suffix = name[len(prefix) :]
            if suffix.isdigit():
                sequence = max(sequence, int(suffix) + 1)
        return f"{prefix}{sequence:03d}"

    def create(
        self,
        topic: str,
        *,
        sources: Sequence[PreparedSource] = (),
        style: str,
        target_duration_minutes: float,
        audience: Audience,
        job_id: str | None = None,
    ) -> JobManifest:
        """Create a job. Transcripts are copied into the job so it is self-contained."""
        topic = topic.strip()
        if not topic:
            raise InvalidInputError("A topic is required.")
        if job_id is None:
            job_id = self._next_job_id(slugify(topic), utcnow())
        paths = self.paths(job_id)
        if paths.root.exists():
            raise JobExistsError(f"Job {job_id!r} already exists.")

        files: list[SourceFile] = []
        for index, source in enumerate(sources, start=1):
            stored = f"source/originals/{index:02d}_{source.name}"
            files.append(
                SourceFile(
                    name=source.name,
                    stored_path=stored,
                    label=source.label,
                    reference_url=source.reference_url,
                    sha256=source.sha256,
                    size_bytes=len(source.data),
                )
            )
        manifest = JobManifest(
            job_id=job_id,
            input=JobInput(topic=topic, sources=files),
            style=style,
            target_duration_minutes=target_duration_minutes,
            audience=audience,
        )
        for name in JOB_SUBDIRS:
            paths.subdir(name).mkdir(parents=True, exist_ok=True)
        for source, file in zip(sources, files, strict=True):
            atomic_write_bytes(paths.absolute(file.stored_path), source.data)
        write_json(paths.input_json, manifest.input.model_dump(mode="json"))
        with self._lock(paths):
            self._save(manifest, paths)
        return manifest

    # ------------------------------------------------------------ mutation

    @contextlib.contextmanager
    def _lock(self, paths: JobPaths) -> Iterator[None]:
        with FileLock(str(paths.lock_file), timeout=30):
            yield

    def _save(self, manifest: JobManifest, paths: JobPaths) -> None:
        manifest.updated_at = utcnow()
        write_model(paths.manifest, manifest)
        self._index(manifest)

    def update(self, job_id: str, mutate: Callable[[JobManifest], None]) -> JobManifest:
        """Load, mutate and atomically save the manifest under an inter-process lock.

        The mutation is applied to a copy; if it raises, nothing is written.
        """
        paths = self.paths(job_id)
        with self._lock(paths):
            manifest = self.load(job_id)
            mutate(manifest)
            validated = JobManifest.model_validate(manifest.model_dump())
            self._save(validated, paths)
            return validated

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        failed_stage: StageName | None = None,
        error: str | None = None,
    ) -> JobManifest:
        def mutate(manifest: JobManifest) -> None:
            if status not in ALLOWED_TRANSITIONS[manifest.status]:
                raise InvalidTransitionError(
                    f"Job {job_id}: cannot go from {manifest.status.value!r} to {status.value!r}"
                )
            manifest.status = status
            if status is JobStatus.FAILED:
                manifest.failed_stage = failed_stage
                manifest.error = error
            else:
                manifest.failed_stage = None
                manifest.error = None

        return self.update(job_id, mutate)

    # ------------------------------------------------------------ SQLite index

    def _connect(self) -> sqlite3.Connection:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.index_path, timeout=10)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "job_id TEXT PRIMARY KEY, status TEXT NOT NULL, topic TEXT, style TEXT, "
            "failed_stage TEXT, created_at TEXT, updated_at TEXT)"
        )
        return conn

    def _index(self, manifest: JobManifest) -> None:
        # The index is derived data; a failure here must never fail the job.
        with (
            contextlib.suppress(sqlite3.Error),
            contextlib.closing(self._connect()) as conn,
            conn,
        ):
            conn.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, "
                "topic=excluded.topic, style=excluded.style, "
                "failed_stage=excluded.failed_stage, updated_at=excluded.updated_at",
                (
                    manifest.job_id,
                    manifest.status.value,
                    manifest.topic,
                    manifest.style,
                    manifest.failed_stage.value if manifest.failed_stage else None,
                    manifest.created_at.isoformat(),
                    manifest.updated_at.isoformat(),
                ),
            )

    def rebuild_index(self) -> int:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM jobs")
            count = 0
            for manifest in self.list_jobs():
                self._index(manifest)
                count += 1
        return count

    def query_index(self, status: JobStatus | None = None) -> list[dict[str, str | None]]:
        with contextlib.closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM jobs"
            params: tuple[str, ...] = ()
            if status:
                sql += " WHERE status = ?"
                params = (status.value,)
            rows = conn.execute(sql + " ORDER BY created_at", params).fetchall()
            return [dict(r) for r in rows]
