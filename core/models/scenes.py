"""Scene specification: the contract between content generation and rendering.

Positions are normalised (0-1) so scenes are resolution independent. Animation timing is a
fraction of the scene's final duration, which is only known once narration audio exists.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from core.models.content import Strict


def _unit_point(value: tuple[float, float]) -> tuple[float, float]:
    if not all(0.0 <= v <= 1.0 for v in value):
        raise ValueError(f"position {value} must be within the normalised range 0..1")
    return value


Point = Annotated[tuple[float, float], AfterValidator(_unit_point)]


class VisualType(StrEnum):
    TITLE = "title"
    TEXT_SLIDE = "text_slide"
    DIAGRAM = "diagram"
    NETWORK_DIAGRAM = "network_diagram"
    CODE = "code"
    EQUATION = "equation"
    GRAPH = "graph"
    TIMELINE = "timeline"


class Transition(StrEnum):
    CUT = "cut"
    FADE = "fade"
    SLIDE = "slide"


# ---------------------------------------------------------------- objects


class _Object(Strict):
    id: str = Field(min_length=1)
    label: str | None = None


class _Positioned(_Object):
    position: Point


class TextObject(_Positioned):
    type: Literal["text"] = "text"
    text: str
    font_size: float = Field(default=0.05, gt=0, le=1)


class CircleObject(_Positioned):
    type: Literal["circle"] = "circle"
    radius: float = Field(default=0.05, gt=0, le=1)


class RectangleObject(_Positioned):
    type: Literal["rectangle"] = "rectangle"
    width: float = Field(default=0.1, gt=0, le=1)
    height: float = Field(default=0.1, gt=0, le=1)


class LineObject(_Object):
    type: Literal["line"] = "line"
    start: Point
    end: Point


class ArrowObject(_Object):
    type: Literal["arrow"] = "arrow"
    start: Point
    end: Point


class IconObject(_Positioned):
    type: Literal["icon"] = "icon"
    icon_name: str


class CodeBlockObject(_Positioned):
    type: Literal["code_block"] = "code_block"
    code: str
    language: str = "text"


class EquationObject(_Positioned):
    type: Literal["equation"] = "equation"
    latex: str


class GraphObject(_Positioned):
    type: Literal["graph"] = "graph"
    points: list[tuple[float, float]] = Field(min_length=1)
    x_label: str = ""
    y_label: str = ""


class TimelineEvent(Strict):
    label: str
    t: float = Field(ge=0, le=1)


class TimelineObject(_Positioned):
    type: Literal["timeline"] = "timeline"
    events: list[TimelineEvent] = Field(min_length=1)


class PersonObject(_Positioned):
    type: Literal["person"] = "person"


class ComputerObject(_Positioned):
    type: Literal["computer"] = "computer"


class ServerObject(_Positioned):
    type: Literal["server"] = "server"


class DatabaseObject(_Positioned):
    type: Literal["database"] = "database"


class RouterObject(_Positioned):
    type: Literal["router"] = "router"


class PacketObject(_Positioned):
    type: Literal["packet"] = "packet"


SceneObject = Annotated[
    TextObject
    | CircleObject
    | RectangleObject
    | LineObject
    | ArrowObject
    | IconObject
    | CodeBlockObject
    | EquationObject
    | GraphObject
    | TimelineObject
    | PersonObject
    | ComputerObject
    | ServerObject
    | DatabaseObject
    | RouterObject
    | PacketObject,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------- animations


class _Animation(Strict):
    start: float = Field(default=0.0, ge=0, le=1)  # fraction of scene duration
    end: float = Field(default=1.0, ge=0, le=1)
    cue: str | None = None  # narration word to align to once word timings exist

    @model_validator(mode="after")
    def _ordered(self) -> _Animation:
        if self.end <= self.start:
            raise ValueError("animation end must be greater than start")
        return self


class _Targeted(_Animation):
    target: str


class FadeIn(_Targeted):
    type: Literal["fade_in"] = "fade_in"


class FadeOut(_Targeted):
    type: Literal["fade_out"] = "fade_out"


class Move(_Targeted):
    type: Literal["move"] = "move"
    to: Point


class Scale(_Targeted):
    type: Literal["scale"] = "scale"
    factor: float = Field(gt=0)


class Rotate(_Targeted):
    type: Literal["rotate"] = "rotate"
    degrees: float


class Draw(_Targeted):
    type: Literal["draw"] = "draw"


class Highlight(_Targeted):
    type: Literal["highlight"] = "highlight"
    color: str | None = None


class Pulse(_Targeted):
    type: Literal["pulse"] = "pulse"


class TypeText(_Targeted):
    type: Literal["type"] = "type"
    text: str | None = None


class Transform(_Targeted):
    type: Literal["transform"] = "transform"
    into: str


class PacketAnimation(_Animation):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["packet"] = "packet"
    from_id: str = Field(alias="from")
    to: str
    label: str = ""


Animation = Annotated[
    FadeIn
    | FadeOut
    | Move
    | Scale
    | Rotate
    | Draw
    | Highlight
    | Pulse
    | TypeText
    | Transform
    | PacketAnimation,
    Field(discriminator="type"),
]

SupportedAnimation = Annotated[
    FadeIn | FadeOut | Move | Scale | Draw | Highlight | Pulse | TypeText,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------- scenes


class Scene(Strict):
    scene_id: int = Field(ge=1)
    section_id: str | None = None
    narration: str
    estimated_duration_s: float = Field(gt=0)
    visual_type: VisualType
    objects: list[SceneObject] = Field(default_factory=list)
    animations: list[Animation] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)
    transition: Transition = Transition.CUT
    emphasis: list[str] = Field(default_factory=list)

    @field_validator("narration")
    @classmethod
    def _narration_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scene narration must not be blank")
        return value

    @model_validator(mode="after")
    def _references_resolve(self) -> Scene:
        ids = [o.id for o in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError(f"scene {self.scene_id}: object ids must be unique")
        known = set(ids)
        for anim in self.animations:
            refs: list[str] = []
            if isinstance(anim, PacketAnimation):
                refs = [anim.from_id, anim.to]
            else:
                refs = [anim.target]
                if isinstance(anim, Transform):
                    refs.append(anim.into)
            missing = [r for r in refs if r not in known]
            if missing:
                raise ValueError(
                    f"scene {self.scene_id}: {anim.type} animation references unknown "
                    f"object(s) {missing}"
                )
        return self


class ScenePlan(Strict):
    scenes: list[Scene] = Field(min_length=1)

    @field_validator("scenes")
    @classmethod
    def _unique_scene_ids(cls, scenes: list[Scene]) -> list[Scene]:
        ids = [s.scene_id for s in scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene ids must be unique")
        return scenes
