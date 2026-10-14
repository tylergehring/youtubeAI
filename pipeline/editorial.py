"""Create three source-led short-form scripts and topic-specific visual plans."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import ValidationError

from core.config import Settings, load_settings
from core.costs import CostTracker
from core.exceptions import (
    InvalidInputError,
    ProviderError,
    QualityCheckError,
    VidgenError,
)
from core.hashing import file_sha256, fingerprint
from core.jobs import JobPaths
from core.logging import configure_logging
from core.models.content import Transcript
from core.models.editorial import ShortScript, TopicCandidate, VisualPlan
from core.models.media import CaptionCue, CheckResult, ValidationReport
from core.naming import validate_job_id
from core.providers import StructuredResponse, Usage
from core.reel_metadata import ReelMetadata
from core.storage import atomic_write_text, read_json, read_model, write_json, write_model
from core.youtube_transcript import YouTubeSource, fetch_transcript, video_id_from_url
from pipeline.captions import render_srt
from pipeline.stage import EXIT_OK, handle_errors
from pipeline.topics import generate_script, generate_topics, generate_visual_plan
from pipeline.validate import ngram_overlap
from providers.registry import ProviderSet, build_providers
from rendering.ffmpeg import FFmpeg
from rendering.reel_layout import FontSet
from rendering.reel_preview import contact_sheet, extract_frames, playback_preview
from rendering.shortform import ShortFormGraphics, render_short_video


def _record_failed_call(
    costs: CostTracker,
    *,
    stage: str,
    provider: str,
    model: str,
    error: ProviderError,
) -> None:
    if isinstance(error.usage, Usage) and error.usage.requests > 0:
        costs.record(
            stage=stage,
            provider=provider,
            model=model,
            usage=error.usage,
            note="failed structured-output call",
        )


def _record_success(
    costs: CostTracker,
    *,
    stage: str,
    provider: str,
    model: str,
    response: StructuredResponse[Any],
) -> None:
    costs.record(
        stage=stage,
        provider=provider,
        model=model,
        usage=response.usage,
    )


def _plan_topic(topic: TopicCandidate) -> dict[str, Any]:
    return {
        "topic_id": topic.id,
        "title": topic.title,
        "question": topic.question,
        "original_angle": topic.original_angle,
        "source_leads": topic.source_leads,
        "research_questions": topic.research_questions,
        "visual_concept": topic.visual_concept,
        "status": "planned",
    }


def _save_failure(
    plan_path: Path,
    plan: dict[str, Any],
    topic_id: str,
    error: Exception,
) -> None:
    for item in plan["topics"]:
        if item["topic_id"] == topic_id:
            item["status"] = "failed"
            item["error"] = f"{type(error).__name__}: {error}"
            break
    write_json(plan_path, plan)


def _script_text(script: ShortScript) -> str:
    return " ".join(scene.narration for scene in script.scenes)


def create_editorial_drafts(
    source_url: str,
    batch_id: str,
    settings: Settings,
    providers: ProviderSet,
    *,
    approve_ai_usage: bool,
) -> Path:
    """Capture a transcript, then generate three scripts and three visual plans.

    Topic generation receives the full transcript once. Each script receives only the source
    segments selected for its topic, and each visual plan receives only its own script. This
    avoids repeating the full transcript in every downstream prompt. Results stop at editorial
    drafts; no speech, rendering or publication is performed.
    """
    batch_id = validate_job_id(batch_id)
    if not approve_ai_usage:
        raise InvalidInputError(
            "This command sends transcript material to the configured LLM for topic, script and "
            "visual-plan generation. Re-run with --approve-ai-usage after reviewing provider "
            "selection and pricing."
        )
    if providers.llm.name == "mock":
        raise InvalidInputError(
            "Editorial topic generation requires a configured real LLM provider; "
            "the mock provider has no topic-generation output."
        )
    video_id_from_url(source_url)
    batch_dir = settings.workspace_dir / "editorial_batches" / batch_id
    if batch_dir.exists():
        raise InvalidInputError(
            f"Editorial batch already exists: {batch_id}. Use a new batch ID; "
            "captures and generated drafts are immutable."
        )
    batch_dir.mkdir(parents=True)
    research_dir = batch_dir / "research"
    source = fetch_transcript(source_url, research_dir, ["en"])
    transcript = read_model(research_dir / "transcript.json", Transcript)
    costs = CostTracker(batch_id, JobPaths(batch_dir), settings.providers.pricing)

    try:
        topic_response = generate_topics(
            providers.llm,
            source,
            transcript,
            max_source_chars=settings.knowledge.max_source_chars,
        )
    except ProviderError as exc:
        _record_failed_call(
            costs,
            stage="topics",
            provider=providers.llm.name,
            model=providers.llm.model,
            error=exc,
        )
        raise
    _record_success(
        costs,
        stage="topics",
        provider=providers.llm.name,
        model=providers.llm.model,
        response=topic_response,
    )
    write_model(batch_dir / "topics.json", topic_response.value)

    plan: dict[str, Any] = {
        "batch_id": batch_id,
        "source_video_id": source.video_id,
        "source_url": source.reference_url,
        "transcript_sha256": source.transcript_sha256,
        "topics": [_plan_topic(topic) for topic in topic_response.value.topics],
        "status": "generating_drafts",
        "publication_status": "not_authorized",
        "note": (
            "Captions and generated text are research leads, not verified facts or a reuse "
            "licence. Independently verify claims and review rights before publication."
        ),
    }
    plan_path = batch_dir / "plan.json"
    write_json(plan_path, plan)

    for topic in topic_response.value.topics:
        topic_dir = batch_dir / topic.id
        topic_dir.mkdir()
        write_model(topic_dir / "topic.json", topic)

        try:
            script_response = generate_script(providers.llm, topic, transcript)
        except ProviderError as exc:
            _record_failed_call(
                costs,
                stage=f"script_{topic.id}",
                provider=providers.llm.name,
                model=providers.llm.model,
                error=exc,
            )
            _save_failure(plan_path, plan, topic.id, exc)
            raise
        _record_success(
            costs,
            stage=f"script_{topic.id}",
            provider=providers.llm.name,
            model=providers.llm.model,
            response=script_response,
        )
        write_model(topic_dir / "script.json", script_response.value)

        overlap = ngram_overlap(_script_text(script_response.value), transcript.text)
        write_json(
            topic_dir / "source_overlap.json",
            {
                "five_word_overlap": overlap,
                "threshold": 0.10,
                "is_originality_proof": False,
            },
        )
        if overlap > 0.10:
            error = QualityCheckError(
                f"Script {topic.id} has {overlap:.1%} five-word overlap with its source; "
                "revise it before visual planning. This heuristic is not a legal determination."
            )
            _save_failure(plan_path, plan, topic.id, error)
            raise error

        try:
            visual_response = generate_visual_plan(providers.llm, script_response.value)
        except ProviderError as exc:
            _record_failed_call(
                costs,
                stage=f"visuals_{topic.id}",
                provider=providers.llm.name,
                model=providers.llm.model,
                error=exc,
            )
            _save_failure(plan_path, plan, topic.id, exc)
            raise
        _record_success(
            costs,
            stage=f"visuals_{topic.id}",
            provider=providers.llm.name,
            model=providers.llm.model,
            response=visual_response,
        )
        write_model(topic_dir / "visual_plan.json", visual_response.value)
        for item in plan["topics"]:
            if item["topic_id"] == topic.id:
                item["status"] = "script_and_visual_plan_ready"
                item["claims_to_verify"] = script_response.value.claims_to_verify
                break
        write_json(plan_path, plan)

    plan["status"] = "awaiting_evidence_review_and_rendering"
    write_json(plan_path, plan)
    return plan_path


def _snapshot_editorial_asset(
    source: Path,
    topic_dir: Path,
    kind: str,
    license_note: str | None,
    attribution: str | None,
    ffmpeg: FFmpeg,
    allowed_extensions: list[str],
    max_asset_bytes: int,
) -> tuple[Path, dict[str, Any]]:
    resolved = source.expanduser().resolve()
    if not resolved.is_file():
        raise InvalidInputError(f"{kind.capitalize()} asset does not exist: {resolved.name}")
    if not license_note or not license_note.strip():
        raise InvalidInputError(
            f"Declare the {kind} asset license or permission before using it in a video draft."
        )
    if resolved.suffix.lower() not in allowed_extensions:
        raise InvalidInputError(
            f"Unsupported {kind} asset extension {resolved.suffix!r}; "
            f"allowed: {sorted(allowed_extensions)}."
        )
    if resolved.stat().st_size > max_asset_bytes:
        raise InvalidInputError(f"{kind.capitalize()} asset exceeds the configured size limit.")
    sha256 = file_sha256(resolved)
    destination = topic_dir / "assets" / f"{kind}{resolved.suffix.lower()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_sha256(destination) != sha256:
            raise InvalidInputError(
                f"Stored {kind} asset differs from this input; use a new editorial batch."
            )
    else:
        shutil.copyfile(resolved, destination)
        if file_sha256(destination) != sha256:
            raise InvalidInputError(f"{kind.capitalize()} asset changed while being copied.")
    info = ffmpeg.analyze(destination)
    if kind == "background" and (not info.has_video or info.duration_s <= 0):
        raise InvalidInputError("Background asset must contain nonempty video.")
    if kind == "music" and (not info.has_audio or info.duration_s <= 0):
        raise InvalidInputError("Music asset must contain nonempty audio.")
    return destination, {
        "kind": kind,
        "source_name": resolved.name,
        "stored_path": destination.relative_to(topic_dir).as_posix(),
        "sha256": sha256,
        "license_or_permission": license_note.strip(),
        "attribution": attribution,
        "duration_s": info.duration_s,
        "source_audio_used": False if kind == "background" else None,
    }


def _update_batch_topic(
    batch_dir: Path,
    topic_id: str,
    status: str,
    *,
    video_path: str | None = None,
) -> None:
    plan_path = batch_dir / "plan.json"
    with FileLock(str(batch_dir / ".plan.lock"), timeout=30):
        plan = read_json(plan_path)
        topic = next(
            (item for item in plan["topics"] if item["topic_id"] == topic_id),
            None,
        )
        if topic is None:
            raise InvalidInputError(f"Editorial batch has no topic {topic_id}.")
        topic["status"] = status
        if video_path is not None:
            topic["video_path"] = video_path
        statuses = [item["status"] for item in plan["topics"]]
        if all(status == "scheduled" for status in statuses):
            plan["status"] = "scheduled"
        elif any(status == "scheduled" for status in statuses):
            plan["status"] = "partially_scheduled"
        elif all(status == "video_draft_ready" for status in statuses):
            plan["status"] = "awaiting_review"
        else:
            plan["status"] = "drafting_and_rendering"
        write_json(plan_path, plan)


def render_editorial_topic(
    batch_id: str,
    topic_id: str,
    settings: Settings,
    providers: ProviderSet,
    *,
    approve_ai_usage: bool,
    background_video: Path | None = None,
    background_license: str | None = None,
    background_attribution: str | None = None,
    music: Path | None = None,
    music_license: str | None = None,
    music_attribution: str | None = None,
) -> Path:
    """Synthesize narration, render, validate and package one editorial topic draft."""
    batch_id = validate_job_id(batch_id)
    if topic_id not in {"topic-1", "topic-2", "topic-3"}:
        raise InvalidInputError("Topic must be topic-1, topic-2, or topic-3.")
    if not approve_ai_usage:
        raise InvalidInputError(
            "This command may send narration text to the configured TTS provider. "
            "Re-run with --approve-ai-usage after reviewing provider selection and pricing."
        )
    tts = providers.tts
    if tts.name == "mock":
        raise InvalidInputError("Video drafts require a configured real speech provider.")
    if background_video is None and (background_license or background_attribution):
        raise InvalidInputError("Background licence/attribution requires --background-video.")
    if music is None and (music_license or music_attribution):
        raise InvalidInputError("Music licence/attribution requires --music.")

    batch_dir = settings.workspace_dir / "editorial_batches" / batch_id
    topic_dir = batch_dir / topic_id
    topic_path = topic_dir / "topic.json"
    script_path = topic_dir / "script.json"
    visual_path = topic_dir / "visual_plan.json"
    for path in (batch_dir / "plan.json", topic_path, script_path, visual_path):
        if not path.is_file():
            raise InvalidInputError(f"Editorial draft input is missing: {path.name}")
    batch_plan = read_json(batch_dir / "plan.json")
    if not any(item.get("topic_id") == topic_id for item in batch_plan.get("topics", [])):
        raise InvalidInputError(f"Editorial batch has no topic {topic_id}.")
    source_url = batch_plan.get("source_url")
    source_video_id = batch_plan.get("source_video_id")
    if not isinstance(source_url, str) or not isinstance(source_video_id, str):
        raise InvalidInputError("Editorial batch source identity is missing.")
    if video_id_from_url(source_url) != source_video_id:
        raise InvalidInputError("Editorial batch source URL and video ID do not match.")
    research_dir = batch_dir / "research"
    source = read_model(research_dir / "source.json", YouTubeSource)
    transcript_path = research_dir / "transcript.json"
    if (
        source.video_id != source_video_id
        or source.transcript_sha256 != batch_plan.get("transcript_sha256")
        or not transcript_path.is_file()
        or file_sha256(transcript_path) != source.transcript_sha256
    ):
        raise InvalidInputError("Captured source or transcript changed since topic generation.")
    transcript = read_model(transcript_path, Transcript)
    topic = read_model(topic_path, TopicCandidate)
    script = read_model(script_path, ShortScript)
    visual_plan = read_model(visual_path, VisualPlan)
    if [scene.script_scene_id for scene in visual_plan.scenes] != [
        scene.id for scene in script.scenes
    ]:
        raise InvalidInputError("Visual plan scene order does not match the script.")
    overlap = ngram_overlap(_script_text(script), transcript.text)
    write_json(
        topic_dir / "source_overlap.json",
        {
            "five_word_overlap": overlap,
            "threshold": 0.10,
            "is_originality_proof": False,
        },
    )
    if overlap > 0.10:
        raise QualityCheckError(
            f"Script {topic_id} has {overlap:.1%} five-word overlap with its source; "
            "revise it before rendering. This heuristic is not a legal determination."
        )

    width = settings.finance.width
    height = settings.finance.height
    fps = settings.finance.fps
    if (
        width <= 0 or height <= 0 or width % 2 or height % 2
        or width * 16 != height * 9
        or not 1 <= fps <= 60
    ):
        raise InvalidInputError(
            "Editorial video settings must be even-sized 9:16 portrait at 1-60 fps."
        )
    ffmpeg = FFmpeg.locate(settings.ffmpeg_path)
    with FileLock(str(topic_dir / ".render.lock"), timeout=1):
        asset_records: list[dict[str, Any]] = []
        background_snapshot = None
        music_snapshot = None
        if background_video is not None:
            background_snapshot, record = _snapshot_editorial_asset(
                background_video, topic_dir, "background", background_license,
                background_attribution, ffmpeg,
                settings.finance.video_extensions,
                round(settings.finance.max_asset_mb * 1024 * 1024),
            )
            asset_records.append(record)
        if music is not None:
            music_snapshot, record = _snapshot_editorial_asset(
                music, topic_dir, "music", music_license, music_attribution, ffmpeg,
                settings.finance.audio_extensions,
                round(settings.finance.max_asset_mb * 1024 * 1024),
            )
            asset_records.append(record)

        fonts = FontSet()
        graphics = ShortFormGraphics(width, height, fonts=fonts)
        input_fingerprint = fingerprint(
            "editorial-short-v2",
            topic.model_dump(mode="json"),
            script.model_dump(mode="json"),
            visual_plan.model_dump(mode="json"),
            source_url,
            source_video_id,
            source.transcript_sha256,
            overlap,
            tts.name,
            tts.model,
            tts.default_voice,
            width,
            height,
            fps,
            fonts.regular_name,
            fonts.bold_name,
            asset_records,
        )
        render_manifest_path = topic_dir / "render_manifest.json"
        final_dir = topic_dir / "final"
        final_video = final_dir / "video.mp4"
        if render_manifest_path.is_file():
            prior = read_json(render_manifest_path)
            if prior.get("input_fingerprint") != input_fingerprint:
                raise InvalidInputError(
                    "This topic already has different rendered inputs; use a new batch ID."
                )
            required_outputs = (
                final_video,
                final_dir / "captions.srt",
                final_dir / "publishing.json",
                final_dir / "review_summary.json",
                final_dir / "vscode_preview.mp4",
                final_dir / "contact_sheet.png",
                final_dir / "cover.png",
                topic_dir / "validation.json",
            )
            if (
                prior.get("status") == "awaiting_review"
                and all(path.is_file() for path in required_outputs)
            ):
                if file_sha256(final_video) != prior.get("video_sha256"):
                    raise InvalidInputError("Rendered video changed after its review manifest.")
                cached_validation = read_model(
                    topic_dir / "validation.json", ValidationReport
                )
                if not cached_validation.technical_passed:
                    raise InvalidInputError("Cached editorial video no longer passes technical QA.")
                _update_batch_topic(
                    batch_dir,
                    topic_id,
                    "video_draft_ready",
                    video_path=f"{topic_id}/final/video.mp4",
                )
                return final_video

        write_json(
            render_manifest_path,
            {"input_fingerprint": input_fingerprint, "status": "rendering"},
        )
        costs = CostTracker(batch_id, JobPaths(batch_dir), settings.providers.pricing)
        try:
            audio_dir = topic_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_paths: list[Path] = []
            audio_records: list[dict[str, Any]] = []
            for index, scene in enumerate(script.scenes, start=1):
                audio = audio_dir / f"scene_{index:02d}.wav"
                receipt = audio.with_suffix(".json")
                cache_key = fingerprint(
                    scene.narration, tts.name, tts.model, tts.default_voice
                )
                if audio.exists() != receipt.exists():
                    raise InvalidInputError(
                        f"Incomplete narration cache at {audio.name}; reconcile it before "
                        "retrying to avoid an untracked repeat service charge."
                    )
                if audio.exists():
                    record = read_json(receipt)
                    if (
                        record.get("status") != "complete"
                        or record.get("fingerprint") != cache_key
                        or record.get("sha256") != file_sha256(audio)
                    ):
                        raise InvalidInputError(
                            f"Cached narration at {audio.name} is incomplete or changed."
                        )
                    duration_s = ffmpeg.analyze(audio).duration_s
                else:
                    write_json(
                        receipt,
                        {
                            "status": "started",
                            "fingerprint": cache_key,
                            "provider": tts.name,
                            "model": tts.model,
                            "voice": tts.default_voice,
                        },
                    )
                    try:
                        result = tts.synthesize(scene.narration, audio)
                    except ProviderError as exc:
                        _record_failed_call(
                            costs,
                            stage=f"narration_{topic_id}_{index:02d}",
                            provider=tts.name,
                            model=tts.model,
                            error=exc,
                        )
                        raise
                    costs.record(
                        stage=f"narration_{topic_id}_{index:02d}",
                        provider=tts.name,
                        model=tts.model,
                        usage=result.usage,
                    )
                    if not audio.is_file() or not audio.stat().st_size:
                        raise ProviderError(
                            "Speech provider did not create a nonempty narration asset."
                        )
                    duration_s = ffmpeg.analyze(audio).duration_s
                    record = {
                        "status": "complete",
                        "fingerprint": cache_key,
                        "sha256": file_sha256(audio),
                        "duration_s": duration_s,
                        "provider": tts.name,
                        "model": tts.model,
                        "voice": result.voice,
                        "usage": result.usage.model_dump(),
                        "metadata": result.metadata,
                    }
                    write_json(receipt, record)
                audio_paths.append(audio)
                audio_records.append(
                    {
                        "scene_id": scene.id,
                        "path": audio.relative_to(topic_dir).as_posix(),
                        "duration_s": duration_s,
                        "sha256": file_sha256(audio),
                        "provider": tts.name,
                        "model": tts.model,
                        "voice": tts.default_voice,
                    }
                )

            video_dir = topic_dir / "render"
            video_dir.mkdir(parents=True, exist_ok=True)
            video = video_dir / "video.mp4"
            duration_s, scene_durations = render_short_video(
                ffmpeg,
                graphics,
                script,
                visual_plan,
                audio_paths,
                video,
                fps=fps,
                background_video=background_snapshot,
                music=music_snapshot,
            )
            info = ffmpeg.analyze(video)
            cues: list[CaptionCue] = []
            cursor = 0.0
            for index, (scene, record, scene_duration) in enumerate(
                zip(script.scenes, audio_records, scene_durations, strict=True),
                start=1,
            ):
                cues.append(
                    CaptionCue(
                        index=index,
                        start_s=round(cursor, 3),
                        end_s=round(cursor + record["duration_s"], 3),
                        text=scene.narration,
                    )
                )
                cursor += scene_duration
            final_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(final_dir / "captions.srt", render_srt(cues))
            times: list[float] = []
            cursor = 0.0
            for record, scene_duration in zip(audio_records, scene_durations, strict=True):
                times.append(cursor + min(record["duration_s"] / 2, scene_duration / 2))
                cursor += scene_duration
            frames = extract_frames(ffmpeg, video, times, topic_dir / "preview" / "frames")
            contact_sheet(frames, final_dir / "contact_sheet.png")
            shutil.copyfile(frames[0], final_dir / "cover.png")
            shutil.copyfile(video, final_video.with_suffix(".partial.mp4"))
            final_video.with_suffix(".partial.mp4").replace(final_video)
            playback_preview(ffmpeg, final_video, final_dir / "vscode_preview.mp4")
            write_json(final_dir / "narration.json", {"scenes": audio_records})
            write_json(topic_dir / "assets.json", {"assets": asset_records})
            description_parts = [
                topic.original_angle,
                script.disclosure,
                "Claims requiring independent verification:\n"
                + "\n".join(f"- {claim}" for claim in script.claims_to_verify),
                (
                    f"Research lead only: {source_url}. The source video was not used as "
                    "footage or audio. Generated narration and original code-rendered visuals "
                    "require human review."
                ),
            ]
            if asset_records:
                credits = [
                    f"{record['kind'].capitalize()} license/permission: "
                    f"{record['license_or_permission']}"
                    + (
                        f" | Attribution: {record['attribution']}"
                        if record["attribution"]
                        else ""
                    )
                    for record in asset_records
                ]
                description_parts.append("\n".join(credits))
            metadata = ReelMetadata(
                title=script.title,
                description="\n\n".join(description_parts),
                hashtags=["FinancialEducation", "MoneyBasics", "Shorts"],
                review_required=True,
            )
            write_model(final_dir / "publishing.json", metadata)
            checks = [
                CheckResult(name="video_opens", passed=info.has_video),
                CheckResult(
                    name="portrait_resolution",
                    passed=(info.width, info.height) == (width, height),
                ),
                CheckResult(name="frame_rate", passed=info.fps == fps),
                CheckResult(
                    name="duration",
                    passed=30 <= info.duration_s <= 65
                    and abs(info.duration_s - duration_s) <= max(0.25, 1 / fps),
                    detail=f"{info.duration_s:.3f}s; expected {duration_s:.3f}s",
                ),
                CheckResult(
                    name="audio_present_and_audible",
                    passed=info.has_audio
                    and info.mean_volume_db is not None
                    and info.mean_volume_db > -35,
                    detail=f"mean volume: {info.mean_volume_db} dB",
                ),
                CheckResult(
                    name="visual_plan_coverage",
                    passed=len(visual_plan.scenes) == len(script.scenes),
                ),
                CheckResult(
                    name="captions",
                    passed=(final_dir / "captions.srt").is_file()
                    and (final_dir / "captions.srt").stat().st_size > 0
                    and len(cues) == len(script.scenes),
                ),
                CheckResult(
                    name="review_assets",
                    passed=all(
                        (final_dir / name).is_file() and (final_dir / name).stat().st_size > 0
                        for name in (
                            "vscode_preview.mp4",
                            "contact_sheet.png",
                            "cover.png",
                            "publishing.json",
                        )
                    ),
                ),
            ]
            report = ValidationReport(technical_passed=all(c.passed for c in checks), checks=checks)
            write_model(topic_dir / "validation.json", report)
            if not report.technical_passed:
                raise QualityCheckError(
                    f"Editorial video QA failed: {[check.name for check in report.failures]}"
                )
            write_json(
                final_dir / "review_summary.json",
                {
                    "status": "awaiting_review",
                    "duration_s": info.duration_s,
                    "technical_checks_passed": True,
                    "evidence_review_required": True,
                    "human_review_required": True,
                    "uploaded": False,
                    "assets": asset_records,
                    "limitations": [
                        "Transcript-derived topic ideas are research leads, not verified facts.",
                        "Claims must be independently checked before publication.",
                        "Phrase-overlap checks do not establish originality or rights clearance.",
                        "Inspect the encoded frames and listen to the complete narration.",
                    ],
                },
            )
            complete_fingerprint = fingerprint(
                input_fingerprint,
                [(record["sha256"], record["duration_s"]) for record in audio_records],
            )
            write_json(
                render_manifest_path,
                {
                    "input_fingerprint": input_fingerprint,
                    "fingerprint": complete_fingerprint,
                    "status": "awaiting_review",
                    "video_path": "final/video.mp4",
                    "duration_s": info.duration_s,
                    "video_sha256": file_sha256(final_video),
                    "tts_provider": tts.name,
                    "tts_model": tts.model,
                    "voice": tts.default_voice,
                    "font_regular": fonts.regular_name,
                    "font_bold": fonts.bold_name,
                },
            )
            _update_batch_topic(
                batch_dir,
                topic_id,
                "video_draft_ready",
                video_path=f"{topic_id}/final/video.mp4",
            )
            return final_video
        except (OSError, VidgenError, ValidationError) as exc:
            write_json(
                render_manifest_path,
                {
                    "input_fingerprint": input_fingerprint,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.editorial",
        description=(
            "Capture and author source-led finance videos. Rendering and publishing remain "
            "separately approval-gated."
        ),
    )
    parser.add_argument("--youtube", help="Public YouTube URL or video ID; required for drafting")
    parser.add_argument("--batch-id", required=True, help="New immutable editorial batch ID")
    parser.add_argument(
        "--topic",
        choices=["topic-1", "topic-2", "topic-3"],
        help="Render this topic from an existing editorial batch instead of creating drafts",
    )
    parser.add_argument(
        "--approve-ai-usage",
        action="store_true",
        help="Confirm transcript/drafts may go to the LLM and narration text to configured TTS",
    )
    parser.add_argument("--background-video", type=Path)
    parser.add_argument("--background-license")
    parser.add_argument("--background-attribution")
    parser.add_argument("--music", type=Path)
    parser.add_argument("--music-license")
    parser.add_argument("--music-attribution")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--log-level")
    args = parser.parse_args(argv)

    def action() -> int:
        if not args.approve_ai_usage:
            raise InvalidInputError(
                "LLM calls require explicit cost/content approval; pass --approve-ai-usage."
            )
        settings = load_settings(args.config_dir)
        if args.workspace:
            settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
        configure_logging(args.log_level or settings.log_level)
        providers = build_providers(settings)
        if args.topic:
            if args.youtube:
                raise InvalidInputError(
                    "--youtube cannot be used while rendering an existing topic."
                )
            video = render_editorial_topic(
                args.batch_id,
                args.topic,
                settings,
                providers,
                approve_ai_usage=args.approve_ai_usage,
                background_video=args.background_video,
                background_license=args.background_license,
                background_attribution=args.background_attribution,
                music=args.music,
                music_license=args.music_license,
                music_attribution=args.music_attribution,
            )
            print(f"Video draft: {video}")
            print("Awaiting evidence and editorial review. Nothing uploaded or scheduled.")
        else:
            if not args.youtube:
                raise InvalidInputError(
                    "--youtube is required when creating a new editorial batch."
                )
            if any(
                (
                    args.background_video,
                    args.background_license,
                    args.background_attribution,
                    args.music,
                    args.music_license,
                    args.music_attribution,
                )
            ):
                raise InvalidInputError(
                    "Background and music can only be set when rendering a topic."
                )
            plan = create_editorial_drafts(
                args.youtube,
                args.batch_id,
                settings,
                providers,
                approve_ai_usage=args.approve_ai_usage,
            )
            print(f"Editorial plan: {plan}")
            print("Three scripts and visual plans are ready for evidence review.")
            print("No voice, video render or upload was created.")
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
