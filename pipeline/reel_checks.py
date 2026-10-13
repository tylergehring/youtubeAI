"""Deterministic technical and content-integrity checks for finance reels."""

from __future__ import annotations

from core.config import Settings
from core.finance import resolve_methodology, resolve_rows
from core.jobs import JobPaths
from core.models.finance import ReelLayout, ReelPlan, ReelSpec, Verification
from core.models.manifest import JobManifest
from core.models.media import CheckResult, RenderResult
from core.reel_input import sha256_of
from core.storage import read_model
from rendering.ffmpeg import MediaInfo

FPS_TOLERANCE = 0.5


def reel_checks(
    *,
    paths: JobPaths,
    settings: Settings,
    plan: ReelPlan,
    spec: ReelSpec,
    layout: ReelLayout,
    render: RenderResult,
    media: MediaInfo | None,
    media_error: str = "",
) -> list[CheckResult]:
    qa, finance = settings.qa, settings.finance
    checks: list[CheckResult] = []

    def add(name: str, passed: bool, detail: str = "", skipped: bool = False) -> None:
        checks.append(CheckResult(name=name, passed=passed, detail=detail, skipped=skipped))

    video_path = paths.absolute(render.video_path)
    exists = video_path.is_file() and video_path.stat().st_size > 0
    add("video_exists", exists, render.video_path)
    size_mb = video_path.stat().st_size / (1024 * 1024) if exists else 0.0
    add("file_size", 0 < size_mb <= qa.max_file_size_mb, f"{size_mb:.2f} MB")

    unreadable = f"video could not be analysed: {media_error or 'no media info'}"
    add("video_opens", media is not None and media.has_video, "" if media else unreadable)
    if media is None or not media.has_video:
        for name in ("duration_reasonable", "resolution", "frame_rate"):
            add(name, False, unreadable)
    else:
        expected = plan.timing.duration_s
        add(
            "duration_reasonable",
            qa.min_duration_s <= media.duration_s <= qa.max_duration_s
            and abs(media.duration_s - expected) <= qa.duration_tolerance_s,
            f"video {media.duration_s:.2f}s, expected {expected:.2f}s "
            f"(allowed {qa.min_duration_s}-{qa.max_duration_s}s)",
        )
        add(
            "resolution",
            (media.width, media.height) == (finance.width, finance.height),
            f"{media.width}x{media.height}, expected {finance.width}x{finance.height}",
        )
        add(
            "frame_rate",
            media.fps is not None and abs(media.fps - finance.fps) <= FPS_TOLERANCE,
            f"{media.fps} fps, expected {finance.fps}",
        )

    if plan.voiceover is None and plan.music is None:
        add("audio_present", True, "text-only reel: narration not required", skipped=True)
        add("audio_not_silent", True, "text-only reel: narration not required", skipped=True)
    else:
        add("audio_present", bool(media and media.has_audio), "voiceover or music supplied")
        volume = media.mean_volume_db if media else None
        add(
            "audio_not_silent",
            volume is not None and volume > qa.min_mean_volume_db,
            f"mean volume {volume} dB, threshold {qa.min_mean_volume_db} dB",
        )

    background = paths.absolute(plan.background.stored_path)
    background_ok = background.is_file() and sha256_of(background) == plan.background.sha256
    add(
        "background_asset",
        background_ok,
        f"{plan.background.origin.value}: {plan.background.license}",
    )
    if plan.music:
        music = paths.absolute(plan.music.stored_path)
        add(
            "music_asset",
            music.is_file()
            and sha256_of(music) == plan.music.sha256
            and plan.music.settings == spec.music,
            plan.music.settings.license,
        )
    add("music_matches_spec", (plan.music is not None) == (spec.music is not None))
    add(
        "presentation_matches_spec",
        plan.style.model_dump(exclude={"font_regular", "font_bold"})
        == spec.style.model_dump(exclude={"font_regular", "font_bold"})
        and plan.background.crop_x == spec.background.crop_x
        and plan.background.crop_y == spec.background.crop_y,
    )
    if paths.manifest.is_file():
        source = read_model(paths.manifest, JobManifest).input.reel
        if source and source.fonts:
            add(
                "font_assets",
                all(
                    paths.absolute(font.stored_path).is_file()
                    and sha256_of(paths.absolute(font.stored_path)) == font.sha256
                    and getattr(plan.style, name) == font.stored_path
                    for name, font in source.fonts.items()
                ),
                "Supplied fonts retained in the job with hashes and licence",
            )

    expected_rows, expected_calc = resolve_rows(spec)
    consistent = (
        plan.rows == expected_rows
        and plan.calculation == expected_calc
        and plan.methodology == resolve_methodology(spec)
    )
    add(
        "figures_match_spec",
        consistent,
        "plan figures reproduce exactly from the stored spec"
        if consistent
        else "plan figures differ from a fresh calculation of the stored spec",
    )

    sourced = plan.provenance.verification is Verification.SOURCED
    provenance_ok = (not sourced or bool(plan.provenance.sources)) and (
        plan.provenance == spec.provenance
    )
    add("provenance_recorded", provenance_ok, plan.provenance.verification.value)

    names = {box.name for box in layout.boxes}
    needed = {"title", "methodology", "cta", "disclosure"} | {
        f"row_{i + 1}" for i in range(len(plan.rows))
    }
    if plan.style.eyebrow:
        needed.add("eyebrow")
    missing = sorted(needed - names)
    add(
        "all_text_rendered",
        not missing,
        f"missing: {missing}" if missing else f"{len(names)} elements",
    )
    outside = [
        b.name
        for b in layout.boxes
        if b.x < layout.safe_left
        or b.y < layout.safe_top
        or b.x + b.width > layout.safe_right
        or b.y + b.height > layout.safe_bottom
    ]
    add(
        "text_in_safe_area",
        not outside,
        f"outside the safe area: {outside}" if outside else f"text scale {layout.scale:.2f}",
    )
    return checks
