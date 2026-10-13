"""Real single-reel assembly with music, typography snapshots, framing and publishing copy."""

from __future__ import annotations

import array
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageFont

from core.config import Settings, load_settings
from core.exceptions import InvalidInputError, StageError
from core.finance import build_timing, resolve_methodology, resolve_rows
from core.jobs import JobStore
from core.models.finance import ReelPlan, ReelSpec, ResolvedBackground
from core.models.manifest import Audience, StageName
from core.reel_input import prepare_reel
from core.reel_metadata import build_reel_metadata
from core.storage import read_model
from pipeline import artifacts
from pipeline.reel import main
from pipeline.run import run_pipeline
from providers.registry import build_providers
from rendering.ffmpeg import FFmpeg
from rendering.reel_layout import FontSet, build_layout, prepare_background_image, wrap_text
from rendering.reel_preview import playback_preview
from tests.conftest import FFMPEG

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "finance" / "editorial_compound.yaml"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    env = {
        "VIDGEN_WORKSPACE_DIR": str(tmp_path / "workspace"),
        "VIDGEN__finance__width": "270",
        "VIDGEN__finance__height": "480",
    }
    if FFMPEG:
        env["FFMPEG_PATH"] = FFMPEG
    return load_settings(env=env, load_dotenv_file=False)


def tone(path: Path, hz: int = 220, seconds: float = 1) -> None:
    samples = array.array(
        "h",
        (
            round(7000 * math.sin(2 * math.pi * hz * n / 44100))
            for n in range(round(seconds * 44100))
        ),
    )
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
@pytest.mark.parametrize("with_audio", [False, True])
def test_vscode_playback_preserves_video_and_music(tmp_path: Path, with_audio: bool) -> None:
    ffmpeg = FFmpeg.locate(FFMPEG)
    source = tmp_path / "upload.mp4"
    args = ["-y", "-f", "lavfi", "-i", "color=size=64x96:rate=30:duration=1"]
    if with_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
    ffmpeg.run_checked([*args, "-c:v", "libx264", str(source)])
    original_bytes = source.read_bytes()
    preview = playback_preview(ffmpeg, source, tmp_path / "preview" / "vscode_preview.mp4")
    assert source.read_bytes() == original_bytes
    info = ffmpeg.analyze(preview)
    assert info.has_audio is with_audio
    assert (info.width, info.height, info.fps) == (64, 96, 30)
    assert info.duration_s == pytest.approx(1, abs=0.1)
    if with_audio:
        assert info.mean_volume_db is not None and info.mean_volume_db > -30
        streams = ffmpeg.run(["-hide_banner", "-i", str(preview)]).stderr
        assert "Audio: mp3" in streams
    hashes = [
        ffmpeg.run(
            ["-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
             "-f", "hash", "-hash", "sha256", "-"]
        ).stdout
        for path in (source, preview)
    ]
    assert hashes[0].startswith("SHA256=") and hashes[0] == hashes[1]
    assert not list(preview.parent.glob("*.partial.mp4"))


@pytest.fixture
def spec_path(tmp_path: Path) -> Path:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    Image.new("RGB", (320, 180), (25, 70, 55)).save(tmp_path / "background.png")
    data["background"]["path"] = "background.png"
    path = tmp_path / "reel.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def make_plan(spec: ReelSpec, settings: Settings) -> ReelPlan:
    rows, calculation = resolve_rows(spec)
    return ReelPlan(
        title=spec.title,
        methodology=resolve_methodology(spec),
        rows=rows,
        cta=spec.cta,
        disclosure=spec.disclosure,
        style=spec.style,
        background=ResolvedBackground(
            stored_path="background.png",
            sha256="0" * 64,
            media_type="image",
            width=320,
            height=180,
            origin=spec.background.origin,
            license=spec.background.license,
        ),
        provenance=spec.provenance,
        calculation=calculation,
        timing=build_timing(len(rows), settings.finance.timing, settings.finance.fps),
    )


def test_editorial_type_and_explicit_line_breaks(settings: Settings) -> None:
    spec = ReelSpec.model_validate(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")))
    plan = make_plan(spec, settings)
    fonts = FontSet()
    assert wrap_text("First line\nSecond line", fonts.get(True, 30), 1000) == [
        "First line",
        "Second line",
    ]
    for width, height in ((1080, 1920), (270, 480)):
        finance = settings.finance.model_copy(update={"width": width, "height": height})
        layout = build_layout(plan, finance, fonts)
        assert layout.report.scale >= 0.85
        assert {b.name for b in layout.blocks} >= {"eyebrow", "title", "row_5", "disclosure"}
        assert all(b.y + b.height <= layout.report.safe_bottom for b in layout.report.boxes)
    metadata = build_reel_metadata(plan)
    assert len(metadata.title) <= 100
    assert "\n" not in metadata.title
    assert "7% annual return" in metadata.description
    assert all(row.value in metadata.description for row in plan.rows)
    assert spec.disclosure in metadata.description
    assert metadata.review_required


def test_off_center_image_crop(tmp_path: Path) -> None:
    source = tmp_path / "wide.png"
    image = Image.new("RGB", (600, 200), "red")
    image.paste("blue", (300, 0, 600, 200))
    image.save(source)
    for x, expected in ((0, (255, 0, 0)), (1, (0, 0, 255))):
        out = tmp_path / f"crop{x}.png"
        prepare_background_image(source, 100, 200, out, centering=(x, 0.5))
        with Image.open(out) as result:
            assert result.getpixel((50, 100)) == expected


def test_music_and_fonts_reject_bad_inputs(spec_path: Path, settings: Settings) -> None:
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    data["music"] = {"path": "missing.wav", "license": "Test fixture"}
    spec_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="Music not found"):
        prepare_reel(spec_path, settings)
    del data["music"]
    data["style"]["font_regular"] = "missing.ttf"
    spec_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="font_license"):
        prepare_reel(spec_path, settings)
    data["style"]["font_license"] = "Test fixture"
    spec_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="font_regular not found"):
        prepare_reel(spec_path, settings)


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
@pytest.mark.parametrize("with_voice", [False, True])
def test_music_is_looped_faded_and_packaged(
    spec_path: Path,
    settings: Settings,
    with_voice: bool,
) -> None:
    tone(spec_path.parent / "music.wav")
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    data["music"] = {
        "path": "music.wav",
        "license": "Original test signal",
        "attribution": "Generated test tone",
        "start_s": 0.2,
        "gain_db": -6,
    }
    if with_voice:
        tone(spec_path.parent / "voice.wav", hz=880, seconds=3)
        data["voiceover"] = {"path": "voice.wav"}
    spec_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    store = JobStore(settings.workspace_dir)
    prepared = prepare_reel(spec_path, settings)
    job = store.create(
        "Test",
        reel=prepared,
        style=settings.defaults.style,
        target_duration_minutes=1,
        audience=Audience.BEGINNER,
    )
    run_pipeline(store, job.job_id, settings, build_providers(settings))
    paths = store.paths(job.job_id)
    plan = read_model(artifacts.reel_plan_json(paths), ReelPlan)
    video = artifacts.final_video(paths)
    info = FFmpeg.locate(settings.ffmpeg_path).analyze(video)
    assert info.has_audio and info.mean_volume_db is not None and info.mean_volume_db > -60
    assert abs(info.duration_s - plan.timing.duration_s) < 0.2
    assert plan.music and plan.music.settings.start_s == 0.2
    assert plan.timing.row_at_s[1] - plan.timing.row_at_s[0] == pytest.approx(1.2)
    assert plan.timing.duration_s - plan.timing.cta_at_s >= 4
    metadata = json.loads((paths.subdir("final") / "publishing.json").read_text(encoding="utf-8"))
    assert "Generated test tone" in metadata["description"]
    assert "Not financial advice" in metadata["description"]
    record = json.loads(artifacts.reel_provenance_json(paths).read_text(encoding="utf-8"))
    assert record["music"]["settings"]["license"] == "Original test signal"
    if not with_voice:
        # Measure actual decoded audio, not just the presence of fade filter arguments.
        raw = subprocess.run(
            [
                str(FFMPEG),
                "-v",
                "error",
                "-i",
                str(video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-f",
                "s16le",
                "-",
            ],
            check=True,
            capture_output=True,
        ).stdout
        samples = array.array("h", raw)

        def rms(start: float, end: float) -> float:
            window = samples[round(start * 44100) : round(end * 44100)]
            return math.sqrt(sum(s * s for s in window) / len(window))

        middle = rms(3, 3.2)  # beyond the 1 s source: looping must really work
        assert middle > 50
        assert rms(0, 0.05) < middle * 0.25
        assert rms(plan.timing.duration_s - 0.1, plan.timing.duration_s - 0.05) < middle * 0.25


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_custom_font_snapshot_and_tampering(spec_path: Path, settings: Settings) -> None:
    font = ImageFont.truetype(FontSet().regular_name, 24)
    path = spec_path.parent / "custom.ttf"
    shutil.copyfile(font.path, path)
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    data["style"].update(font_regular="custom.ttf", font_license="Local test font")
    spec_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    store = JobStore(settings.workspace_dir)
    job = store.create(
        "Fonts",
        reel=prepare_reel(spec_path, settings),
        style=settings.defaults.style,
        target_duration_minutes=1,
        audience=Audience.BEGINNER,
    )
    path.unlink()
    run_pipeline(store, job.job_id, settings, build_providers(settings), end=StageName.FINANCE)
    assert job.input.reel
    stored = store.paths(job.job_id).absolute(job.input.reel.fonts["font_regular"].stored_path)
    assert stored.is_file()
    stored.write_bytes(b"corrupted")
    with pytest.raises(StageError, match="Font .* changed"):
        run_pipeline(
            store,
            job.job_id,
            settings,
            build_providers(settings),
            start=StageName.RENDER,
        )


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_single_reel_cli(spec_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FFMPEG_PATH", str(FFMPEG))
    monkeypatch.setenv("VIDGEN__finance__width", "270")
    monkeypatch.setenv("VIDGEN__finance__height", "480")
    workspace = tmp_path / "cli"
    assert (
        main(
            [
                "--spec",
                str(spec_path),
                "--workspace",
                str(workspace),
                "--job-id",
                "editorial",
            ]
        )
        == 0
    )
    final = workspace / "jobs" / "editorial" / "final"
    assert all(
        (final / name).is_file()
        for name in (
            "video.mp4",
            "cover.png",
            "publishing.json",
            "provenance.json",
            "review_summary.json",
        )
    )


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
@pytest.mark.parametrize("invalid", ["offset", "silent", "corrupt"])
def test_unusable_music_fails_before_render(
    spec_path: Path,
    settings: Settings,
    invalid: str,
) -> None:
    path = spec_path.parent / "music.wav"
    tone(path, hz=0 if invalid == "silent" else 220)
    if invalid == "corrupt":
        path.write_bytes(b"not audio")
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    data["music"] = {
        "path": "music.wav",
        "license": "Test signal",
        "start_s": 1 if invalid == "offset" else 0,
    }
    spec_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    store = JobStore(settings.workspace_dir)
    job = store.create(
        "Invalid music",
        reel=prepare_reel(spec_path, settings),
        style=settings.defaults.style,
        target_duration_minutes=1,
        audience=Audience.BEGINNER,
    )
    with pytest.raises(StageError):
        run_pipeline(store, job.job_id, settings, build_providers(settings))
    assert not artifacts.render_video(store.paths(job.job_id)).exists()
