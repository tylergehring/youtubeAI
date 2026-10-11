"""Source, transcript, knowledge, research, lesson and script models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.models.manifest import utcnow


class Strict(BaseModel):
    """Base for all content models: reject unknown fields so malformed LLM output fails loudly."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- source


class TranscriptSegment(Strict):
    """One chunk of a transcript. Timing is optional: plain-text transcripts have none."""

    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    text: str

    @model_validator(mode="after")
    def _ordered(self) -> TranscriptSegment:
        if self.start_s is not None and self.end_s is not None and self.end_s < self.start_s:
            raise ValueError("segment end_s must be >= start_s")
        return self


class Transcript(Strict):
    """A parsed user-supplied transcript."""

    format: str  # "txt", "srt" or "vtt"
    segments: list[TranscriptSegment] = Field(min_length=1)
    markers_removed: int = 0  # non-speech markers such as [music] stripped during parsing

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)


class SourceEntry(Strict):
    name: str
    label: str
    reference_url: str | None = None
    sha256: str
    size_bytes: int
    format: str
    segment_count: int
    word_count: int
    markers_removed: int = 0
    transcript_path: str  # parsed transcript, relative to the job directory


class SourceRecord(Strict):
    """Provenance of everything the job was built from. All sources are user-supplied files."""

    topic: str
    ingested_at: datetime = Field(default_factory=utcnow)
    sources: list[SourceEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- knowledge


class ClaimStatus(StrEnum):
    SOURCE_CLAIM = "SOURCE_CLAIM"
    VERIFIED_FACT = "VERIFIED_FACT"
    INFERENCE = "INFERENCE"
    UNCERTAIN_CLAIM = "UNCERTAIN_CLAIM"


class Concept(Strict):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    importance: float = Field(ge=0, le=1)


class Entity(Strict):
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    description: str = ""


class Relationship(Strict):
    subject: str
    predicate: str
    object: str


class Claim(Strict):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    status: ClaimStatus = ClaimStatus.SOURCE_CLAIM
    confidence: float = Field(ge=0, le=1)


class Definition(Strict):
    term: str
    definition: str


class Example(Strict):
    title: str
    description: str


class ChronologyEntry(Strict):
    when: str
    event: str


class SourceReference(Strict):
    label: str
    url: str | None = None


class KnowledgeBase(Strict):
    topic: str = Field(min_length=1)
    concepts: list[Concept] = Field(min_length=1)
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    definitions: list[Definition] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)
    chronology: list[ChronologyEntry] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(default_factory=list)

    @field_validator("claims")
    @classmethod
    def _unique_claim_ids(cls, claims: list[Claim]) -> list[Claim]:
        ids = [c.id for c in claims]
        if len(ids) != len(set(ids)):
            raise ValueError("claim ids must be unique")
        return claims


# ---------------------------------------------------------------- research


class Evidence(Strict):
    claim_id: str
    source_url: str
    source_title: str
    source_type: str  # e.g. specification, documentation, reference, academic
    excerpt: str = ""
    supports: bool = True


class ClaimVerification(Strict):
    claim_id: str
    status: ClaimStatus
    confidence: float = Field(ge=0, le=1)
    disputed: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


class ResearchReport(Strict):
    provider: str
    results: list[ClaimVerification] = Field(default_factory=list)

    def by_claim(self) -> dict[str, ClaimVerification]:
        return {r.claim_id: r for r in self.results}


# ---------------------------------------------------------------- lesson


class LessonSection(Strict):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    concepts: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class LessonPlan(Strict):
    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    audience: str
    prerequisites: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(min_length=1)
    sections: list[LessonSection] = Field(min_length=1)
    conclusion: str
    estimated_duration_s: float = Field(gt=0)

    @field_validator("sections")
    @classmethod
    def _unique_section_ids(cls, sections: list[LessonSection]) -> list[LessonSection]:
        ids = [s.id for s in sections]
        if len(ids) != len(set(ids)):
            raise ValueError("lesson section ids must be unique")
        return sections


# ---------------------------------------------------------------- script


class SectionPurpose(StrEnum):
    HOOK = "hook"
    EXPLANATION = "explanation"
    EXAMPLE = "example"
    RECAP = "recap"
    CONCLUSION = "conclusion"


class ScriptSection(Strict):
    id: str = Field(min_length=1)
    purpose: SectionPurpose
    narration: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)

    @field_validator("narration")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("narration must not be blank")
        return value


class Script(Strict):
    sections: list[ScriptSection] = Field(min_length=1)

    @field_validator("sections")
    @classmethod
    def _unique_ids(cls, sections: list[ScriptSection]) -> list[ScriptSection]:
        ids = [s.id for s in sections]
        if len(ids) != len(set(ids)):
            raise ValueError("script section ids must be unique")
        return sections
