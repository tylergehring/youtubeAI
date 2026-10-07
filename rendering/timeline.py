"""Resolve the final scene timeline from real audio durations.

Both captions and the renderer use this one function so they can never disagree.
"""

from __future__ import annotations

import math

from core.exceptions import RenderError
from core.models.media import AudioManifest, Timeline, TimelineEntry
from core.models.scenes import ScenePlan


def build_timeline(
    plan: ScenePlan,
    audio: AudioManifest,
    *,
    padding_s: float,
    fps: int,
) -> Timeline:
    """Scene length = actual narration duration + padding, snapped up to whole frames."""
    by_scene = audio.by_scene()
    entries: list[TimelineEntry] = []
    cursor = 0.0
    for scene in plan.scenes:
        record = by_scene.get(scene.scene_id)
        if record is None:
            raise RenderError(f"No audio recorded for scene {scene.scene_id}")
        frames = math.ceil((record.duration_s + padding_s) * fps)
        length = frames / fps
        entries.append(
            TimelineEntry(
                scene_id=scene.scene_id,
                start_s=round(cursor, 6),
                end_s=round(cursor + length, 6),
                audio_duration_s=record.duration_s,
            )
        )
        cursor += length
    return Timeline(entries=entries)
