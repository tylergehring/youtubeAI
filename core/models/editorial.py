"""Structured editorial topic candidates for source-led finance videos."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from core.models.content import Strict
from core.models.scenes import (
    ArrowObject,
    Draw,
    GraphObject,
    LineObject,
    Move,
    SceneObject,
    SupportedAnimation,
    TextObject,
    TypeText,
    VisualType,
)


class TopicCandidate(Strict):
    id: str = Field(pattern=r"^topic-[1-3]$")
    title: str = Field(min_length=10, max_length=100)
    question: str = Field(min_length=20, max_length=240)
    original_angle: str = Field(min_length=40, max_length=1000)
    transcript_segment_indices: list[int] = Field(min_length=1, max_length=20)
    source_leads: list[str] = Field(min_length=1, max_length=8)
    research_questions: list[str] = Field(min_length=1, max_length=8)
    visual_concept: str = Field(min_length=30, max_length=500)

    @field_validator("source_leads", "research_questions")
    @classmethod
    def _nonblank_entries(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("list entries must not be blank")
        return values


class TopicSuggestions(Strict):
    topics: list[TopicCandidate] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _distinct_topics(self) -> Self:
        if len({topic.id for topic in self.topics}) != 3:
            raise ValueError("topic IDs must be unique")
        titles = {topic.title.casefold().strip() for topic in self.topics}
        questions = {topic.question.casefold().strip() for topic in self.topics}
        if len(titles) != 3 or len(questions) != 3:
            raise ValueError("topics must have distinct titles and questions")
        return self


class TopicBatch(TopicSuggestions):
    source_video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ShortScriptScene(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    purpose: str = Field(min_length=3, max_length=40)
    narration: str = Field(min_length=12, max_length=350)

    @field_validator("narration")
    @classmethod
    def _scene_word_limit(cls, value: str) -> str:
        if len(value.split()) > 28:
            raise ValueError("each narration scene must contain at most 28 words")
        return value


class ShortScript(Strict):
    title: str = Field(min_length=10, max_length=100)
    hook: str = Field(min_length=15, max_length=240)
    scenes: list[ShortScriptScene] = Field(min_length=4, max_length=8)
    claims_to_verify: list[str] = Field(min_length=1, max_length=12)
    disclosure: str = Field(min_length=20, max_length=180)

    @model_validator(mode="after")
    def _script_limits(self) -> ShortScript:
        if len({scene.id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("script scene IDs must be unique")
        if sum(len(scene.narration.split()) for scene in self.scenes) > 105:
            raise ValueError("script narration must contain at most 105 words")
        return self


class VisualPlanScene(Strict):
    scene_id: int = Field(ge=1)
    script_scene_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    visual_goal: str = Field(min_length=20, max_length=500)
    visual_type: VisualType
    objects: list[SceneObject] = Field(min_length=1, max_length=30)
    animations: list[SupportedAnimation] = Field(default_factory=list, max_length=40)
    text: list[str] = Field(default_factory=list, max_length=6)
    transition: Literal["cut"] = "cut"

    @field_validator("text")
    @classmethod
    def _support_copy_is_brief(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value.split()) > 7 for value in values):
            raise ValueError("visual support text must be nonblank and at most 7 words")
        return values

    @model_validator(mode="after")
    def _animation_targets_resolve(self) -> VisualPlanScene:
        ids = [obj.id for obj in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("visual object IDs must be unique within a scene")
        if any(
            not 0.0 <= coordinate <= 1.0
            for obj in self.objects
            if isinstance(obj, GraphObject)
            for point in obj.points
            for coordinate in point
        ):
            raise ValueError("editorial graph points must be normalized within 0..1")
        known = set(ids)
        objects = {obj.id: obj for obj in self.objects}
        for animation in self.animations:
            if animation.target not in known:
                raise ValueError("visual animation references an unknown object")
            target = objects[animation.target]
            if isinstance(animation, Move) and not hasattr(target, "position"):
                raise ValueError("move animations require a positioned visual object")
            if isinstance(animation, TypeText) and not isinstance(target, TextObject):
                raise ValueError("type animations require a text visual object")
            if isinstance(animation, Draw) and not isinstance(target, (LineObject, ArrowObject)):
                raise ValueError("draw animations require a line or arrow visual object")
        return self


class VisualPlan(Strict):
    creative_direction: str = Field(min_length=30, max_length=1200)
    scenes: list[VisualPlanScene] = Field(min_length=4, max_length=8)

    @model_validator(mode="after")
    def _unique_scenes(self) -> VisualPlan:
        if len({scene.scene_id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("visual scene IDs must be unique")
        if len({scene.script_scene_id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("each script scene must map to exactly one visual scene")
        return self
