"""Deterministic technical quality control. Semantic (LLM) QA arrives in a later milestone."""

from __future__ import annotations

import re
from collections.abc import Callable

from core.config import Settings
from core.exceptions import QualityCheckError, RenderError, StageError
from core.jobs import JobPaths
from core.models.content import Script, SourceRecord, Transcript
from core.models.finance import ReelLayout, ReelPlan
from core.models.manifest import JobFormat, StageName
from core.models.media import (
    AudioManifest,
    CaptionTrack,
    CheckResult,
    RenderResult,
    ValidationReport,
    VisualManifest,
)
from core.models.scenes import ScenePlan
from core.reel_input import parse_reel_spec
from pipeline import artifacts
from pipeline.reel_checks import reel_checks
from pipeline.stage import Stage, StageContext, StageResult, make_main
from rendering.ffmpeg import FFmpeg, MediaInfo

NGRAM_SIZE = 5
FPS_TOLERANCE = 0.5


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def ngram_overlap(candidate: str, reference: str, n: int = NGRAM_SIZE) -> float:
    """Share of the candidate's word n-grams that also appear in the reference text."""
    cand = _words(candidate)
    ref = _words(reference)
    cand_grams = {tuple(cand[i : i + n]) for i in range(len(cand) - n + 1)}
    if not cand_grams:
        return 0.0
    ref_grams = {tuple(ref[i : i + n]) for i in range(len(ref) - n + 1)}
    return len(cand_grams & ref_grams) / len(cand_grams)


def technical_checks(
    *,
    paths: JobPaths,
    settings: Settings,
    plan: ScenePlan,
    audio: AudioManifest,
    visuals: VisualManifest,
    captions: CaptionTrack,
    render: RenderResult,
    script: Script,
    transcripts: list[Transcript],
    media: MediaInfo | None,
    media_error: str = "",
) -> list[CheckResult]:
    qa = settings.qa
    video = settings.video
    checks: list[CheckResult] = []

    def add(name: str, passed: bool, detail: str = "", skipped: bool = False) -> None:
        checks.append(CheckResult(name=name, passed=passed, detail=detail, skipped=skipped))

    video_path = paths.absolute(render.video_path)
    exists = video_path.is_file() and video_path.stat().st_size > 0
    add("video_exists", exists, str(render.video_path))
    size_mb = video_path.stat().st_size / (1024 * 1024) if exists else 0.0
    add("file_size", 0 < size_mb <= qa.max_file_size_mb, f"{size_mb:.2f} MB")

    unreadable = f"video could not be analysed: {media_error or 'no media info'}"
    add("video_opens", media is not None and media.has_video, "" if media else unreadable)
    if media is None or not media.has_video:
        for name in ("duration_reasonable", "resolution", "frame_rate", "audio_present",
                     "audio_not_silent"):  # fmt: skip
            add(name, False, unreadable)
    else:
        in_range = qa.min_duration_s <= media.duration_s <= qa.max_duration_s
        matches = abs(media.duration_s - render.duration_s) <= qa.duration_tolerance_s
        add(
            "duration_reasonable",
            in_range and matches,
            f"video {media.duration_s:.2f}s, expected {render.duration_s:.2f}s "
            f"(allowed {qa.min_duration_s}-{qa.max_duration_s}s)",
        )
        add(
            "resolution",
            (media.width, media.height) == (video.width, video.height),
            f"{media.width}x{media.height}, expected {video.width}x{video.height}",
        )
        add(
            "frame_rate",
            media.fps is not None and abs(media.fps - video.fps) <= FPS_TOLERANCE,
            f"{media.fps} fps, expected {video.fps}",
        )
        add("audio_present", media.has_audio, "")
        volume = media.mean_volume_db
        add(
            "audio_not_silent",
            volume is not None and volume > qa.min_mean_volume_db,
            f"mean volume {volume} dB, threshold {qa.min_mean_volume_db} dB",
        )

    plan_ids = [s.scene_id for s in plan.scenes]
    add(
        "all_scenes_rendered",
        render.scene_ids == plan_ids,
        f"rendered {render.scene_ids}, planned {plan_ids}",
    )

    referenced = [r.path for r in audio.records] + [captions.path]
    missing = [p for p in referenced if not paths.absolute(p).is_file()]
    add("assets_exist", not missing, f"missing: {missing}" if missing else f"{len(referenced)} ok")
    add(
        "captions_exist",
        bool(captions.cues) and paths.absolute(captions.path).is_file(),
        f"{len(captions.cues)} cue(s)",
    )

    blank = [s.scene_id for s in plan.scenes if not s.narration.strip()]
    add("narration_present", not blank, f"scenes without narration: {blank}" if blank else "")
    bad_durations = [
        e.scene_id
        for e in render.timeline.entries
        if e.end_s <= e.start_s or e.audio_duration_s <= 0
    ]
    add(
        "scene_durations_positive",
        not bad_durations,
        f"scenes with zero duration: {bad_durations}" if bad_durations else "",
    )
    scenes_with_visuals = set(visuals.by_scene())
    add(
        "visuals_cover_scenes",
        scenes_with_visuals >= set(plan_ids),
        f"scenes without visuals: {sorted(set(plan_ids) - scenes_with_visuals)}"
        if not scenes_with_visuals >= set(plan_ids)
        else "",
    )

    if not transcripts:
        add("source_overlap", True, "no source transcripts", skipped=True)
    else:
        script_text = " ".join(s.narration for s in script.sections)
        overlap = ngram_overlap(script_text, " ".join(t.text for t in transcripts))
        add(
            "source_overlap",
            overlap <= qa.source_overlap_max,
            f"{overlap:.1%} of script {NGRAM_SIZE}-grams appear in the source "
            f"(max {qa.source_overlap_max:.0%})",
        )
    return checks


class ValidateStage(Stage):
    """Run deterministic technical checks and write the validation report."""

    name = StageName.VALIDATE

    def __init__(
        self,
        ffmpeg_factory: Callable[[Settings], FFmpeg] = lambda s: FFmpeg.locate(s.ffmpeg_path),
    ) -> None:
        self._ffmpeg_factory = ffmpeg_factory

    def config_parts(self, ctx: StageContext) -> object:
        if ctx.manifest.input.format is JobFormat.FINANCE_FACTS:
            return {
                "qa": ctx.settings.qa.model_dump(),
                "finance": ctx.settings.finance.model_dump(mode="json"),
            }
        return {"qa": ctx.settings.qa.model_dump(), "video": ctx.settings.video.model_dump()}

    def _reel_checks(
        self, ctx: StageContext, render: RenderResult, media: MediaInfo | None, media_error: str
    ) -> list[CheckResult]:
        paths = ctx.paths
        reel = ctx.manifest.input.reel
        if reel is None:
            raise StageError("This job has no reel spec.")
        spec_path = paths.absolute(reel.spec.stored_path)
        return reel_checks(
            paths=paths,
            settings=ctx.settings,
            plan=ctx.load(artifacts.reel_plan_json(paths), ReelPlan),
            spec=parse_reel_spec(spec_path.read_text(encoding="utf-8-sig"), spec_path.suffix),
            layout=ctx.load(artifacts.reel_layout_json(paths), ReelLayout),
            render=render,
            media=media,
            media_error=media_error,
        )

    def run(self, ctx: StageContext) -> StageResult:
        paths = ctx.paths
        render = ctx.load(artifacts.render_json(paths), RenderResult)
        is_reel = ctx.manifest.input.format is JobFormat.FINANCE_FACTS

        media: MediaInfo | None = None
        media_error = ""
        try:
            media = self._ffmpeg_factory(ctx.settings).analyze(paths.absolute(render.video_path))
        except RenderError as exc:
            media_error = str(exc)
            ctx.log.error("Could not analyse video: %s", media_error)

        if is_reel:
            checks = self._reel_checks(ctx, render, media, media_error)
        else:
            source = ctx.load(artifacts.source_json(paths), SourceRecord)
            checks = technical_checks(
                paths=paths,
                settings=ctx.settings,
                plan=ctx.load(artifacts.scenes_json(paths), ScenePlan),
                audio=ctx.load(artifacts.audio_json(paths), AudioManifest),
                visuals=ctx.load(artifacts.visuals_json(paths), VisualManifest),
                captions=ctx.load(artifacts.captions_json(paths), CaptionTrack),
                render=render,
                script=ctx.load(artifacts.script_json(paths), Script),
                transcripts=[
                    ctx.load(paths.absolute(entry.transcript_path), Transcript)
                    for entry in source.sources
                ],
                media=media,
                media_error=media_error,
            )
        report = ValidationReport(
            technical_passed=all(c.passed or c.skipped for c in checks), checks=checks
        )
        out = ctx.save(artifacts.validation_json(paths), report)

        for check in checks:
            level = ctx.log.info if check.passed else ctx.log.error
            level("check %-26s %s %s", check.name, "PASS" if check.passed else "FAIL", check.detail)
        if ctx.settings.qa.semantic_enabled:
            ctx.log.warning("Semantic QA is not implemented yet; skipping.")

        if not report.technical_passed:
            names = ", ".join(c.name for c in report.failures)
            raise QualityCheckError(f"Technical checks failed: {names} (see {out.name})")
        return StageResult([out])


main = make_main(ValidateStage)

if __name__ == "__main__":
    raise SystemExit(main())
