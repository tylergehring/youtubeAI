"""Research intake and an original narrated bond-risk Short; never uploads automatically."""

from __future__ import annotations

import argparse
import math
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock
from pydantic import ValidationError

from core.config import load_settings
from core.exceptions import InvalidInputError, QualityCheckError, VidgenError
from core.explainer import ExplainerBrief, calculation_context, resolve_text
from core.hashing import file_sha256, fingerprint
from core.logging import redact
from core.models.content import Transcript
from core.models.media import CaptionCue, CheckResult, ValidationReport
from core.naming import validate_job_id
from core.providers import TTSProvider
from core.storage import atomic_write_text, read_json, read_model, write_json, write_model
from core.youtube_transcript import YouTubeSource, fetch_transcript
from pipeline.captions import render_srt
from pipeline.stage import EXIT_OK, handle_errors
from pipeline.validate import ngram_overlap
from providers.tts.openai_provider import OpenAITTSProvider
from rendering.explainer import SCENE_HOLD_SECONDS, ExplainerGraphics, render_scene
from rendering.ffmpeg import FFmpeg
from rendering.reel_preview import contact_sheet, playback_preview

VERSION = 6


@contextmanager
def _run_record(path: Path, identity: str) -> Iterator[None]:
    if path.exists() and read_json(path)["fingerprint"] != identity:
        raise InvalidInputError("This explainer has different inputs; use a new job ID.")
    write_json(path, {"fingerprint": identity, "status": "rendering"})
    try:
        yield
    except (OSError, VidgenError, ValidationError) as exc:
        write_json(
            path,
            {
                "fingerprint": identity,
                "status": "failed",
                "error": redact(f"{type(exc).__name__}: {exc}"),
            },
        )
        raise


def load_brief(path: Path) -> ExplainerBrief:
    try:
        return ExplainerBrief.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise InvalidInputError(f"Cannot load editorial brief {path}: {exc}") from exc


def preflight(brief: ExplainerBrief, brief_path: Path, research: Path) -> tuple[float, list[Path]]:
    source = read_model(research / "source.json", YouTubeSource)
    if source.video_id != brief.source_video_id:
        raise InvalidInputError("The editorial brief does not match the captured source video.")
    if file_sha256(research / "transcript.json") != source.transcript_sha256:
        raise InvalidInputError("The captured transcript has changed since retrieval.")
    transcript = read_model(research / "transcript.json", Transcript)
    context = calculation_context(brief.scenario)
    script = " ".join(resolve_text(s.narration, context) for s in brief.scenes)
    overlap = ngram_overlap(script, transcript.text)
    if overlap > 0.10:
        raise QualityCheckError(
            f"Script/source 5-word overlap is {overlap:.1%}; revise the explanation. "
            "This heuristic is not a copyright or originality determination."
        )
    snapshots = []
    for evidence in brief.evidence:
        if evidence.accessed_on > date.today():
            raise InvalidInputError("Evidence access date is in the future.")
        path = (brief_path.parent / evidence.snapshot).resolve()
        if not path.is_file() or file_sha256(path) != evidence.snapshot_sha256:
            raise InvalidInputError(f"Missing or changed evidence snapshot: {evidence.id}")
        snapshots.append(path)
    graphics = ExplainerGraphics(brief.scenario)
    for index, scene in enumerate(brief.scenes):
        resolved = scene.model_copy(
            update={
                "narration": resolve_text(scene.narration, context),
                "heading": resolve_text(scene.heading, context),
                "supporting_text": resolve_text(scene.supporting_text, context),
            }
        )
        graphics.frame(resolved, index, len(brief.scenes), 1.0)
    return overlap, snapshots


def render_explainer(
    brief_path: Path,
    research: Path,
    output: Path,
    ffmpeg: FFmpeg,
    tts: TTSProvider,
    *,
    evidence_reviewed: bool,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    min_duration: float = 30,
    max_duration: float = 65,
    background_video: Path | None = None,
) -> Path:
    if not evidence_reviewed:
        raise InvalidInputError(
            "Review the evidence, interpretation and original contribution; "
            "then explicitly pass --approve-evidence."
        )
    if (
        width <= 0
        or height <= 0
        or width % 2
        or height % 2
        or width / height != 9 / 16
        or not 1 <= fps <= 60
    ):
        raise InvalidInputError("Explainers require even-sized 9:16 portrait dimensions.")
    if not 0 < min_duration <= max_duration <= 180:
        raise InvalidInputError("Explainer duration bounds must be ordered within 0-180 seconds.")
    background_source = None
    background_info_duration = None
    background_sha256 = None
    if background_video is not None:
        background_source = background_video.expanduser().resolve()
        if not background_source.is_file():
            raise InvalidInputError(f"Background video does not exist: {background_source.name}")
        background_info = ffmpeg.analyze(background_source)
        if not background_info.has_video or background_info.duration_s <= 0:
            raise InvalidInputError("Background media must contain a nonempty video stream.")
        background_info_duration = background_info.duration_s
        background_sha256 = file_sha256(background_source)
    brief = load_brief(brief_path)
    overlap, snapshots = preflight(brief, brief_path, research)
    output.mkdir(parents=True, exist_ok=True)
    identity = fingerprint(
        VERSION,
        brief.model_dump(mode="json"),
        file_sha256(research / "transcript.json"),
        tts.name,
        tts.model,
        tts.default_voice,
        width,
        height,
        fps,
        background_sha256,
    )
    manifest_path = output / "manifest.json"
    with (
        FileLock(str(output / ".render.lock"), timeout=1),
        _run_record(manifest_path, identity),
    ):
        write_model(output / "brief.json", brief)
        for evidence, path in zip(brief.evidence, snapshots, strict=True):
            destination = output / "evidence" / f"{evidence.id}.txt"
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, destination)
        background_snapshot = None
        if background_source is not None and background_sha256 is not None:
            background_snapshot = output / "assets" / "background.mp4"
            background_snapshot.parent.mkdir(exist_ok=True)
            shutil.copyfile(background_source, background_snapshot)
            if file_sha256(background_snapshot) != background_sha256:
                raise InvalidInputError("Background video changed while it was being snapshotted.")
            write_json(
                output / "background_asset.json",
                {
                    "source_name": background_source.name,
                    "sha256": background_sha256,
                    "stored_path": "assets/background.mp4",
                    "duration_s": background_info_duration,
                    "audio_used": False,
                    "permission_note": (
                        "User confirmed copyright-free publishing permission; "
                        "not independently audited."
                    ),
                },
            )
        shutil.copyfile(research / "source.json", output / "source.json")
        shutil.copyfile(research / "transcript.json", output / "source_transcript.json")
        context = calculation_context(brief.scenario)
        write_json(
            output / "calculations.json",
            {
                "inputs": brief.scenario.model_dump(mode="json"),
                "display": context,
                "formula": "sum(C/(1+y/2)^t,t=1..2N) + F/(1+y/2)^(2N); C=F*coupon_pct/200",
                "assumptions": [
                    "Hypothetical nominal annual yields with semiannual compounding",
                    "Fixed coupons, valuation on a coupon date, repayment assumed",
                    "Same yield applied to every payment; no accrued interest, fees or taxes",
                    "An instantaneous parallel yield change, not a holding-period total return",
                ],
            },
        )
        graphics = ExplainerGraphics(brief.scenario, width, height)
        scenes = [
            s.model_copy(
                update={
                    "narration": resolve_text(s.narration, context),
                    "heading": resolve_text(s.heading, context),
                    "supporting_text": resolve_text(s.supporting_text, context),
                }
            )
            for s in brief.scenes
        ]
        records: list[dict[str, Any]] = []
        for i, scene in enumerate(scenes):
            audio = output / "audio" / f"{i + 1:02d}.wav"
            receipt = audio.with_suffix(".json")
            key = fingerprint(scene.narration, tts.name, tts.model, tts.default_voice)
            if audio.exists() != receipt.exists():
                raise InvalidInputError(
                    f"Incomplete narration cache at {audio}. Reconcile it before retrying; "
                    "refusing an untracked repeat synthesis charge."
                )
            if audio.exists() and receipt.exists():
                record = read_json(receipt)
                if record["fingerprint"] != key or record["sha256"] != file_sha256(audio):
                    raise InvalidInputError("Cached narration changed; use a new explainer job.")
                info = ffmpeg.analyze(audio)
                record["duration_s"] = info.duration_s
            else:
                result = tts.synthesize(scene.narration, audio)
                record = {
                    "fingerprint": key,
                    "sha256": file_sha256(audio),
                    "duration_s": result.duration_s,
                    "provider": tts.name,
                    "model": tts.model,
                    "voice": result.voice,
                    "usage": result.usage.model_dump(),
                    "metadata": result.metadata,
                }
                write_json(receipt, record)
            records.append(record)
        expected = sum(
            math.ceil((r["duration_s"] + SCENE_HOLD_SECONDS) * fps) / fps for r in records
        )
        if not min_duration <= expected <= max_duration:
            raise QualityCheckError(
                f"Measured narration produces {expected:.1f}s; expected "
                f"{min_duration:.0f}-{max_duration:.0f}s. Revise the brief, not the facts."
            )
        cursor = 0.0
        cues: list[CaptionCue] = []
        frames: list[Path] = []
        for i, (scene, record) in enumerate(zip(scenes, records, strict=True)):
            audio = output / "audio" / f"{i + 1:02d}.wav"
            segment = output / "scenes" / f"{i + 1:02d}.mp4"
            background_offset = (
                cursor % background_info_duration
                if background_snapshot is not None and background_info_duration is not None
                else 0.0
            )
            duration = render_scene(
                ffmpeg,
                graphics,
                scene,
                i,
                len(scenes),
                audio,
                record["duration_s"],
                segment,
                fps,
                background_video=background_snapshot,
                background_offset_s=background_offset,
            )
            cues.append(
                CaptionCue(
                    index=i + 1,
                    start_s=cursor,
                    end_s=cursor + record["duration_s"],
                    text=scene.narration,
                )
            )
            cursor += duration
            frame = output / "scenes" / f"{i + 1:02d}.png"
            ffmpeg.run_checked(
                [
                    "-y",
                    "-hide_banner",
                    "-nostats",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{min(0.5, duration / 2):.3f}",
                    "-i",
                    str(segment),
                    "-frames:v",
                    "1",
                    str(frame),
                ]
            )
            frames.append(frame)
            print(f"Scene {i + 1}/{len(scenes)}: {duration:.2f}s", flush=True)
        playlist = output / "scenes" / "concat.txt"
        atomic_write_text(
            playlist, "".join(f"file '{i + 1:02d}.mp4'\n" for i in range(len(scenes)))
        )
        final = output / "final"
        final.mkdir(exist_ok=True)
        partial = final / "video.partial.mp4"
        ffmpeg.run_checked(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(playlist),
                "-c:v",
                "copy",
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(partial),
            ]
        )
        info = ffmpeg.analyze(partial)
        checks = [
            CheckResult(name="resolution", passed=(info.width, info.height) == (width, height)),
            CheckResult(
                name="duration",
                passed=abs(info.duration_s - cursor) < 0.3,
                detail=f"{info.duration_s:.3f}s expected {cursor:.3f}s",
            ),
            CheckResult(
                name="audio",
                passed=info.has_audio
                and info.mean_volume_db is not None
                and info.mean_volume_db > -35,
                detail=f"{info.mean_volume_db} dB",
            ),
            CheckResult(name="frame_rate", passed=info.fps == fps),
            CheckResult(
                name="source_phrase_overlap",
                passed=overlap <= 0.10,
                detail=f"{overlap:.1%}; heuristic only, not originality certification",
            ),
        ]
        report = ValidationReport(technical_passed=all(c.passed for c in checks), checks=checks)
        write_model(output / "validation.json", report)
        if not report.technical_passed:
            raise QualityCheckError(f"Explainer QA failed: {[c.name for c in report.failures]}")
        partial.replace(final / "video.mp4")
        atomic_write_text(final / "captions.srt", render_srt(cues))
        write_json(
            final / "narration.json",
            {
                "synthetic_voice": tts.name == "openai",
                "timing": "One caption per synthesized scene, timed by measured audio duration",
                "scenes": [s.model_dump() for s in scenes],
                "audio": records,
            },
        )
        contact_sheet(frames, final / "contact_sheet.png")
        shutil.copyfile(frames[0], final / "cover.png")
        description = (
            brief.original_contribution
            + "\n\n"
            + brief.disclosure
            + "\n\nSources:\n"
            + "\n".join(f"{e.title}: {e.url} (accessed {e.accessed_on})" for e in brief.evidence)
            + "\n\nResearch lead, not reused footage/script: "
            + f"https://www.youtube.com/watch?v={brief.source_video_id}"
            + "\nOriginal charts and calculations. AI-generated narration."
        )
        if background_snapshot is not None:
            description += "\nBackground footage was user-supplied; its source audio is not used."
        write_json(
            final / "publishing.json",
            {
                "title": brief.title,
                "description": description,
                "hashtags": ["BondMath", "FinancialEducation", "Shorts"],
                "review_required": True,
            },
        )
        write_json(
            final / "review_summary.json",
            {
                "status": "awaiting_review",
                "duration_s": info.duration_s,
                "technical_checks_passed": True,
                "source_overlap": overlap,
                "original_contribution": brief.original_contribution,
                "evidence_reviewed": True,
                "uploaded": False,
                "limits": [
                    "Evidence is editorially reviewed, not independently fact-checked by an LLM.",
                    "Phrase overlap is not proof of originality, fair use "
                    "or monetization eligibility.",
                    "Listen for TTS number pronunciation and inspect charts before publication.",
                ]
                + (
                    [
                        "Background footage is user-supplied; publishing permission "
                        "was confirmed by the user, not independently audited."
                    ]
                    if background_snapshot is not None
                    else []
                ),
            },
        )
        playback_preview(ffmpeg, final / "video.mp4", final / "vscode_preview.mp4")
        write_json(
            manifest_path,
            {
                "fingerprint": identity,
                "status": "awaiting_review",
                "video_sha256": file_sha256(final / "video.mp4"),
            },
        )
        return final / "video.mp4"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    intake = sub.add_parser("intake", help="Retrieve public captions as attributed research input")
    intake.add_argument("--youtube", required=True)
    intake.add_argument("--out", required=True, type=Path)
    intake.add_argument("--languages", nargs="+", default=["en"])
    render = sub.add_parser("render", help="Render a reviewed original editorial brief")
    render.add_argument("--brief", required=True, type=Path)
    render.add_argument("--research", required=True, type=Path)
    render.add_argument("--job-id", required=True)
    render.add_argument("--approve-evidence", action="store_true")
    render.add_argument("--voice", default="marin")
    render.add_argument("--model", default="gpt-4o-mini-tts")
    render.add_argument("--background-video", type=Path)

    def action() -> int:
        args = parser.parse_args(argv)
        if args.command == "intake":
            source = fetch_transcript(args.youtube, args.out, args.languages)
            print(f"Captured {source.video_id}: {source.language}, generated={source.is_generated}")
            print(f"Research transcript: {args.out / 'transcript.json'}")
        else:
            settings = load_settings()
            ffmpeg = FFmpeg.locate(settings.ffmpeg_path)
            output = settings.workspace_dir / "explainers" / validate_job_id(args.job_id)
            video = render_explainer(
                args.brief,
                args.research,
                output,
                ffmpeg,
                OpenAITTSProvider(ffmpeg, model=args.model, voice=args.voice),
                evidence_reviewed=args.approve_evidence,
                background_video=args.background_video,
            )
            print(f"Video: {video}\nAwaiting editorial review. Nothing uploaded or scheduled.")
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
