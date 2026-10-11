"""Canonical artifact locations inside a job directory."""

from __future__ import annotations

from pathlib import Path

from core.jobs import JobPaths


def source_json(p: JobPaths) -> Path:
    return p.subdir("source") / "source.json"


def transcript_json(p: JobPaths, index: int) -> Path:
    return p.subdir("transcript") / f"source_{index:02d}.json"


def knowledge_json(p: JobPaths) -> Path:
    return p.subdir("knowledge") / "knowledge.json"


def research_json(p: JobPaths) -> Path:
    return p.subdir("research") / "research.json"


def lesson_json(p: JobPaths) -> Path:
    return p.subdir("lesson") / "lesson.json"


def script_json(p: JobPaths) -> Path:
    return p.subdir("script") / "script.json"


def scenes_json(p: JobPaths) -> Path:
    return p.subdir("scenes") / "scenes.json"


def visuals_json(p: JobPaths) -> Path:
    return p.subdir("visuals") / "visuals.json"


def audio_json(p: JobPaths) -> Path:
    return p.subdir("audio") / "audio.json"


def scene_audio(p: JobPaths, scene_id: int) -> Path:
    return p.subdir("audio") / f"scene_{scene_id:03d}.wav"


def captions_srt(p: JobPaths) -> Path:
    return p.subdir("captions") / "captions.srt"


def captions_json(p: JobPaths) -> Path:
    return p.subdir("captions") / "captions.json"


def render_video(p: JobPaths) -> Path:
    return p.subdir("render") / "video.mp4"


def reel_plan_json(p: JobPaths) -> Path:
    return p.subdir("finance") / "reel_plan.json"


def reel_layout_json(p: JobPaths) -> Path:
    return p.subdir("render") / "layout.json"


def reel_preview_dir(p: JobPaths) -> Path:
    return p.subdir("render") / "preview"


def reel_provenance_json(p: JobPaths) -> Path:
    return p.subdir("final") / "provenance.json"


def render_json(p: JobPaths) -> Path:
    return p.subdir("render") / "render.json"


def validation_json(p: JobPaths) -> Path:
    return p.subdir("validation") / "report.json"


def final_video(p: JobPaths) -> Path:
    return p.subdir("final") / "video.mp4"


def final_captions(p: JobPaths) -> Path:
    return p.subdir("final") / "captions.srt"


def final_summary(p: JobPaths) -> Path:
    return p.subdir("final") / "review_summary.json"
