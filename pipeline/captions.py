"""Generate SRT captions timed from the real audio durations."""

from __future__ import annotations

import re

from core.models.manifest import StageName
from core.models.media import AudioManifest, CaptionCue, CaptionTrack
from core.models.scenes import ScenePlan
from core.storage import atomic_write_text
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main
from rendering.timeline import build_timeline

MAX_CUE_CHARS = 80
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def split_cue_text(text: str, max_chars: int = MAX_CUE_CHARS) -> list[str]:
    """Split narration into caption-sized chunks at sentence ends, then near the middle."""
    pieces: list[str] = []
    for sentence in _SENTENCE_RE.split(text.strip()):
        pieces.extend(_split_long(sentence.strip(), max_chars))
    return [p for p in pieces if p]


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    middle = len(text) // 2
    # Prefer breaking after punctuation near the middle; otherwise at the nearest space.
    punct = [
        m.end() - 1
        for m in re.finditer(r"[,;:]\s", text)
        if 0.3 * len(text) < m.end() < 0.7 * len(text)
    ]
    spaces = [i for i in (text.rfind(" ", 0, middle + 1), text.find(" ", middle)) if i > 0]
    candidates = punct or spaces
    if not candidates:
        return [text]
    cut = min(candidates, key=lambda i: abs(i - middle))
    return _split_long(text[:cut].strip(), max_chars) + _split_long(text[cut:].strip(), max_chars)


def format_srt_time(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def render_srt(cues: list[CaptionCue]) -> str:
    blocks = [
        f"{c.index}\n{format_srt_time(c.start_s)} --> {format_srt_time(c.end_s)}\n{c.text}\n"
        for c in cues
    ]
    return "\n".join(blocks)


class CaptionsStage(Stage):
    """Create SRT captions aligned to the scene timeline."""

    name = StageName.CAPTIONS

    def config_parts(self, ctx: StageContext) -> object:
        return ctx.settings.video.model_dump()

    def run(self, ctx: StageContext) -> StageResult:
        plan = ctx.load(artifacts.scenes_json(ctx.paths), ScenePlan)
        audio = ctx.load(artifacts.audio_json(ctx.paths), AudioManifest)
        video = ctx.settings.video
        timeline = build_timeline(plan, audio, padding_s=video.scene_padding_s, fps=video.fps)
        entries = {e.scene_id: e for e in timeline.entries}

        cues: list[CaptionCue] = []
        for scene in plan.scenes:
            entry = entries[scene.scene_id]
            chunks = split_cue_text(scene.narration)
            total_chars = sum(len(c) for c in chunks)
            cursor = entry.start_s
            for chunk in chunks:
                span = entry.audio_duration_s * len(chunk) / total_chars
                cues.append(
                    CaptionCue(
                        index=len(cues) + 1,
                        start_s=round(cursor, 3),
                        end_s=round(cursor + span, 3),
                        text=chunk,
                    )
                )
                cursor += span

        srt_path = artifacts.captions_srt(ctx.paths)
        atomic_write_text(srt_path, render_srt(cues))
        track = CaptionTrack(path=ctx.paths.relative(srt_path), cues=cues)
        return StageResult([srt_path, ctx.save(artifacts.captions_json(ctx.paths), track)])


main = make_main(CaptionsStage)

if __name__ == "__main__":
    raise SystemExit(main())
