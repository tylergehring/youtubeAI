"""Job manifest, statuses, stage names and the stage dependency graph."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class Audience(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class StageName(StrEnum):
    """Pipeline stages in execution order."""

    ACQUIRE = "acquire"
    KNOWLEDGE = "knowledge"
    RESEARCH = "research"
    LESSON = "lesson"
    SCRIPT = "script"
    SCENES = "scenes"
    VISUALS = "visuals"
    VOICE = "voice"
    CAPTIONS = "captions"
    RENDER = "render"
    VALIDATE = "validate"
    PACKAGE = "package"


STAGE_ORDER: tuple[StageName, ...] = tuple(StageName)

# Direct data dependencies. Used for prerequisite checks, fingerprints and staleness.
S = StageName
STAGE_DEPENDENCIES: dict[StageName, tuple[StageName, ...]] = {
    S.ACQUIRE: (),
    S.KNOWLEDGE: (S.ACQUIRE,),
    S.RESEARCH: (S.KNOWLEDGE,),
    S.LESSON: (S.KNOWLEDGE, S.RESEARCH),
    S.SCRIPT: (S.KNOWLEDGE, S.RESEARCH, S.LESSON),
    S.SCENES: (S.SCRIPT,),
    S.VISUALS: (S.SCENES,),
    S.VOICE: (S.SCENES,),
    S.CAPTIONS: (S.SCENES, S.VOICE),
    S.RENDER: (S.SCENES, S.VISUALS, S.VOICE, S.CAPTIONS),
    S.VALIDATE: (S.ACQUIRE, S.SCRIPT, S.SCENES, S.VISUALS, S.VOICE, S.CAPTIONS, S.RENDER),
    S.PACKAGE: (S.CAPTIONS, S.RENDER, S.VALIDATE),
}


def downstream_of(stage: StageName) -> list[StageName]:
    """All stages that transitively depend on `stage`, in execution order."""
    affected = {stage}
    for candidate in STAGE_ORDER:
        if any(dep in affected for dep in STAGE_DEPENDENCIES[candidate]):
            affected.add(candidate)
    affected.discard(stage)
    return [s for s in STAGE_ORDER if s in affected]


class JobStatus(StrEnum):
    CREATED = "created"
    ACQUIRING_SOURCE = "acquiring_source"
    EXTRACTING_KNOWLEDGE = "extracting_knowledge"
    RESEARCHING = "researching"
    PLANNING_LESSON = "planning_lesson"
    GENERATING_SCRIPT = "generating_script"
    PLANNING_SCENES = "planning_scenes"
    GENERATING_VISUALS = "generating_visuals"
    GENERATING_AUDIO = "generating_audio"
    GENERATING_CAPTIONS = "generating_captions"
    RENDERING = "rendering"
    VALIDATING = "validating"
    PACKAGING = "packaging"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    UPLOADED = "uploaded"
    FAILED = "failed"


# The status a job reports while the given stage is running.
STAGE_STATUS: dict[StageName, JobStatus] = {
    S.ACQUIRE: JobStatus.ACQUIRING_SOURCE,
    S.KNOWLEDGE: JobStatus.EXTRACTING_KNOWLEDGE,
    S.RESEARCH: JobStatus.RESEARCHING,
    S.LESSON: JobStatus.PLANNING_LESSON,
    S.SCRIPT: JobStatus.GENERATING_SCRIPT,
    S.SCENES: JobStatus.PLANNING_SCENES,
    S.VISUALS: JobStatus.GENERATING_VISUALS,
    S.VOICE: JobStatus.GENERATING_AUDIO,
    S.CAPTIONS: JobStatus.GENERATING_CAPTIONS,
    S.RENDER: JobStatus.RENDERING,
    S.VALIDATE: JobStatus.VALIDATING,
    S.PACKAGE: JobStatus.PACKAGING,
}

PIPELINE_STATUSES: frozenset[JobStatus] = frozenset(STAGE_STATUS.values())

# Pipeline statuses may move freely between each other (retries and forced reruns go
# backwards; prerequisite checks guarantee data order). Everything else is explicit.
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: PIPELINE_STATUSES | {JobStatus.FAILED},
    **{s: PIPELINE_STATUSES | {JobStatus.FAILED} for s in PIPELINE_STATUSES},
    JobStatus.PACKAGING: PIPELINE_STATUSES | {JobStatus.AWAITING_REVIEW, JobStatus.FAILED},
    JobStatus.FAILED: PIPELINE_STATUSES,
    JobStatus.AWAITING_REVIEW: PIPELINE_STATUSES | {JobStatus.APPROVED, JobStatus.REJECTED},
    JobStatus.APPROVED: frozenset({JobStatus.UPLOADED}),
    JobStatus.REJECTED: PIPELINE_STATUSES,
    JobStatus.UPLOADED: frozenset(),
}


class StageState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    STALE = "stale"  # was complete, but an upstream stage has since re-run


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: StageState = StageState.PENDING
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_fingerprint: str | None = None
    outputs: list[str] = Field(default_factory=list)  # paths relative to the job directory
    note: str | None = None
    error: str | None = None


class SourceFile(BaseModel):
    """A user-supplied transcript, copied into the job directory at creation time."""

    model_config = ConfigDict(extra="forbid")

    name: str  # stored file name (sanitised)
    stored_path: str  # relative to the job directory
    label: str  # human-readable name, defaults to the file stem
    reference_url: str | None = None  # where the user got it; recorded only, never fetched
    sha256: str
    size_bytes: int = Field(ge=0)


class JobInput(BaseModel):
    """What the user supplied: a topic and zero or more transcripts. No network sources."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    sources: list[SourceFile] = Field(default_factory=list)


class JobManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    job_id: str
    input: JobInput
    style: str
    target_duration_minutes: float = Field(gt=0)
    audience: Audience
    status: JobStatus = JobStatus.CREATED
    failed_stage: StageName | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    stages: dict[StageName, StageRecord] = Field(
        default_factory=lambda: {s: StageRecord() for s in STAGE_ORDER}
    )

    @property
    def topic(self) -> str:
        return self.input.topic

    def stage(self, name: StageName) -> StageRecord:
        return self.stages.setdefault(name, StageRecord())
