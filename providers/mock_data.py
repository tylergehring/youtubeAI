"""Canned placeholder content for the mock providers.

Everything is templated on the job's topic and clearly labelled as mock output, so a fake run
never looks like real research about some other subject. Nothing here calls a service.
"""

from __future__ import annotations

from typing import Any

MOCK_NOTE = "[mock]"


def knowledge(topic: str, source_labels: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    return {
        "topic": topic,
        "concepts": [
            {
                "name": "Core question",
                "description": f"{MOCK_NOTE} What is going on with: {topic}",
                "importance": 0.95,
            },
            {
                "name": "Key drivers",
                "description": f"{MOCK_NOTE} The main causes the sources point to.",
                "importance": 0.9,
            },
            {
                "name": "Practical implications",
                "description": f"{MOCK_NOTE} What this means for a viewer.",
                "importance": 0.85,
            },
        ],
        "entities": [{"name": "Viewer", "kind": "audience"}],
        "relationships": [
            {"subject": "Key drivers", "predicate": "explain", "object": "Core question"}
        ],
        "claims": [
            {
                "id": "c1",
                "text": f"{MOCK_NOTE} The sources make a central claim about {topic}.",
                "status": "SOURCE_CLAIM",
                "confidence": 0.7,
            },
            {
                "id": "c2",
                "text": f"{MOCK_NOTE} The sources give supporting evidence for {topic}.",
                "status": "SOURCE_CLAIM",
                "confidence": 0.7,
            },
            {
                "id": "c3",
                "text": f"{MOCK_NOTE} The sources describe consequences of {topic}.",
                "status": "SOURCE_CLAIM",
                "confidence": 0.6,
            },
            {
                "id": "c4",
                "text": f"{MOCK_NOTE} A viewer could act on these points.",
                "status": "INFERENCE",
                "confidence": 0.6,
            },
        ],
        "definitions": [],
        "examples": [{"title": "Placeholder example", "description": f"{MOCK_NOTE} {topic}"}],
        "chronology": [],
        "uncertainties": [f"{MOCK_NOTE} Nothing was actually read; this is canned output."],
        "source_refs": [{"label": label} for label in source_labels],
    }


def lesson(topic: str, audience: str) -> dict[str, Any]:
    return {
        "title": topic,
        "hook": f"{MOCK_NOTE} A hook that makes a {audience} viewer care about: {topic}",
        "audience": audience,
        "prerequisites": [],
        "objectives": [
            f"Understand the core question behind: {topic}",
            "See the main drivers and what they mean in practice",
        ],
        "sections": [
            {"id": "intro", "title": "Why this matters", "concepts": []},
            {"id": "context", "title": "The background", "concepts": ["Core question"]},
            {
                "id": "example",
                "title": "A concrete example",
                "concepts": ["Key drivers"],
                "examples": ["Placeholder example"],
            },
            {
                "id": "takeaway",
                "title": "What to do with it",
                "concepts": ["Practical implications"],
            },
            {"id": "conclusion", "title": "Wrapping up", "concepts": []},
        ],
        "conclusion": f"{MOCK_NOTE} Closing thoughts on {topic}.",
        "estimated_duration_s": 60,
    }


def narrations(topic: str) -> dict[str, str]:
    return {
        "intro": (
            f"This is the mock opening hook for a video about {topic}. A real run would write "
            "an original, attention-grabbing opening from the research."
        ),
        "context": (
            f"This mock section would explain the background to {topic}. It stands in for the "
            "facts and context the lesson plan calls for."
        ),
        "example": (
            "This mock section would walk through a concrete example, with numbers or a "
            "scenario a beginner could follow."
        ),
        "takeaway": (
            "This mock section would turn the facts into practical takeaways, kept clearly "
            "separate from the claims the sources made."
        ),
        "conclusion": (
            f"This mock closing would recap the main points about {topic} and leave the viewer "
            "with one thing to remember."
        ),
    }


def script(topic: str = "the topic") -> dict[str, Any]:
    text = narrations(topic)
    spec = [
        ("intro", "hook", ["c1"]),
        ("context", "explanation", ["c2"]),
        ("example", "example", ["c2", "c3"]),
        ("takeaway", "explanation", ["c3", "c4"]),
        ("conclusion", "conclusion", []),
    ]
    return {
        "sections": [
            {"id": sid, "purpose": purpose, "narration": text[sid], "claim_ids": claims}
            for sid, purpose, claims in spec
        ]
    }


def scenes(topic: str = "the topic") -> dict[str, Any]:
    text = narrations(topic)
    return {
        "scenes": [
            {
                "scene_id": 1,
                "section_id": "intro",
                "narration": text["intro"],
                "estimated_duration_s": 8,
                "visual_type": "title",
                "objects": [{"type": "text", "id": "title", "text": topic, "position": [0.5, 0.5]}],
                "animations": [{"type": "fade_in", "target": "title", "start": 0.0, "end": 0.3}],
                "text": [topic],
            },
            {
                "scene_id": 2,
                "section_id": "context",
                "narration": text["context"],
                "estimated_duration_s": 8,
                "visual_type": "text_slide",
                "objects": [
                    {
                        "type": "text",
                        "id": "heading",
                        "text": "The background",
                        "position": [0.5, 0.3],
                    },
                    {
                        "type": "text",
                        "id": "point",
                        "text": "Key facts go here",
                        "position": [0.5, 0.6],
                    },
                ],
                "animations": [
                    {"type": "fade_in", "target": "heading", "start": 0.0, "end": 0.2},
                    {"type": "type", "target": "point", "start": 0.3, "end": 0.8},
                ],
                "text": ["The background"],
            },
            {
                "scene_id": 3,
                "section_id": "example",
                "narration": text["example"],
                "estimated_duration_s": 8,
                "visual_type": "graph",
                "objects": [
                    {
                        "type": "graph",
                        "id": "chart",
                        "position": [0.5, 0.5],
                        "points": [[0, 1], [1, 2], [2, 2.5], [3, 4]],
                        "x_label": "Time",
                        "y_label": "Value",
                    }
                ],
                "animations": [{"type": "draw", "target": "chart", "start": 0.1, "end": 0.9}],
            },
            {
                "scene_id": 4,
                "section_id": "takeaway",
                "narration": text["takeaway"],
                "estimated_duration_s": 10,
                "visual_type": "timeline",
                "objects": [
                    {
                        "type": "timeline",
                        "id": "steps",
                        "position": [0.5, 0.5],
                        "events": [
                            {"label": "Learn", "t": 0.0},
                            {"label": "Plan", "t": 0.5},
                            {"label": "Act", "t": 1.0},
                        ],
                    }
                ],
                "animations": [{"type": "fade_in", "target": "steps", "start": 0.0, "end": 0.5}],
            },
            {
                "scene_id": 5,
                "section_id": "conclusion",
                "narration": text["conclusion"],
                "estimated_duration_s": 8,
                "visual_type": "text_slide",
                "objects": [
                    {"type": "text", "id": "recap", "text": "Remember this", "position": [0.5, 0.5]}
                ],
                "animations": [{"type": "type", "target": "recap", "start": 0.0, "end": 0.5}],
                "text": ["Remember this"],
                "transition": "fade",
            },
        ]
    }
