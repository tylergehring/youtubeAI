"""Audio, caption, visual, render, validation and cost models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core.models.content import Strict
from core.models.manifest import utcnow


class AudioRecord(Strict):
    scene_id: int
    path: str  # relative to the job directory
    duration_s: float = Field(gt=0)
    provider: str
    voice: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AudioManifest(Strict):
    records: list[AudioRecord] = Field(min_length=1)

    def by_scene(self) -> dict[int, AudioRecord]:
        return {r.scene_id: r for r in self.records}


class CaptionCue(Strict):
    index: int = Field(ge=1)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    text: str


class CaptionTrack(Strict):
    format: str = "srt"
    path: str
    cues: list[CaptionCue]


class SceneVisual(Strict):
    scene_id: int
    renderer: str
    background_color: str
    accent_color: str
    object_count: int = 0
    assets: list[str] = Field(default_factory=list)


class VisualManifest(Strict):
    scenes: list[SceneVisual] = Field(min_length=1)

    def by_scene(self) -> dict[int, SceneVisual]:
        return {s.scene_id: s for s in self.scenes}


class TimelineEntry(Strict):
    scene_id: int
    start_s: float
    end_s: float
    audio_duration_s: float


class Timeline(Strict):
    entries: list[TimelineEntry]

    @property
    def total_duration_s(self) -> float:
        return self.entries[-1].end_s if self.entries else 0.0


class RenderResult(Strict):
    renderer: str
    video_path: str  # relative to the job directory
    duration_s: float
    width: int
    height: int
    fps: int
    scene_ids: list[int]
    timeline: Timeline


class CheckResult(Strict):
    name: str
    passed: bool
    detail: str = ""
    skipped: bool = False


class SemanticReport(Strict):
    score: int = Field(ge=0, le=100)
    factuality: int = Field(ge=0, le=100)
    narrative: int = Field(ge=0, le=100)
    visual_alignment: int = Field(ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)
    approved_for_human_review: bool


class ValidationReport(Strict):
    technical_passed: bool
    checks: list[CheckResult]
    semantic: SemanticReport | None = None

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and not c.skipped]


class CostRecord(Strict):
    timestamp: datetime = Field(default_factory=utcnow)
    job_id: str
    stage: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    characters: int = 0
    requests: int = 1
    estimated_cost_usd: float = 0.0
    estimated: bool = True
    note: str = ""


class JobCost(Strict):
    job_id: str
    total_estimated_cost_usd: float = 0.0
    records: list[CostRecord] = Field(default_factory=list)
