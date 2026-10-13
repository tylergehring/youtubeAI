import json
from pathlib import Path

import pytest

from core.exceptions import InvalidInputError
from core.hashing import file_sha256
from core.models.media import ValidationReport
from core.storage import read_model
from pipeline.explainer import render_explainer
from providers.tts.mock import MockTTSProvider
from rendering.ffmpeg import FFmpeg
from tests.conftest import FFMPEG
from tests.unit.test_research_short import BRIEF, research_fixture


@pytest.mark.ffmpeg
def test_original_short_has_video_audio_evidence_and_captions(tmp_path: Path) -> None:
    ffmpeg = FFmpeg.locate(FFMPEG)
    tts = MockTTSProvider()
    research = research_fixture(tmp_path / "research")
    output = tmp_path / "output"
    with pytest.raises(InvalidInputError, match="approve-evidence"):
        render_explainer(BRIEF, research, output, ffmpeg, tts, evidence_reviewed=False)
    assert not output.exists()
    video = render_explainer(
        BRIEF,
        research,
        output,
        ffmpeg,
        tts,
        evidence_reviewed=True,
        width=180,
        height=320,
        fps=10,
    )
    info = ffmpeg.analyze(video)
    assert info.has_audio and info.has_video
    assert info.width == 180 and info.height == 320 and 30 <= info.duration_s <= 65
    assert read_model(output / "validation.json", ValidationReport).technical_passed
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "awaiting_review"
    assert manifest["video_sha256"] == file_sha256(video)
    assert len(tts.calls) == 5
    assert (output / "final" / "vscode_preview.mp4").is_file()
    captions = (output / "final" / "captions.srt").read_text()
    assert captions.count("-->") == 5 and "$845" in captions and "$981" in captions
    publishing = json.loads((output / "final" / "publishing.json").read_text())
    assert "treasurydirect.gov" in publishing["description"]
    assert publishing["review_required"]
    assert not (output / "youtube_upload.json").exists()
    tts.default_voice = "changed"
    with pytest.raises(InvalidInputError, match="different inputs"):
        render_explainer(
            BRIEF,
            research,
            output,
            ffmpeg,
            tts,
            evidence_reviewed=True,
            width=180,
            height=320,
            fps=10,
        )


@pytest.mark.ffmpeg
def test_explainer_snapshots_and_composites_background_video(tmp_path: Path) -> None:
    ffmpeg = FFmpeg.locate(FFMPEG)
    background = tmp_path / "background.mp4"
    ffmpeg.run_checked(
        [
            "-y",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=360x640:rate=10:duration=3",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(background),
        ]
    )
    output = tmp_path / "with_background"
    video = render_explainer(
        BRIEF,
        research_fixture(tmp_path / "research"),
        output,
        ffmpeg,
        MockTTSProvider(),
        evidence_reviewed=True,
        width=180,
        height=320,
        fps=10,
        background_video=background,
    )
    info = ffmpeg.analyze(video)
    asset = json.loads((output / "background_asset.json").read_text())
    report = json.loads((output / "final" / "review_summary.json").read_text())
    assert info.has_video and info.has_audio
    assert asset["sha256"] == file_sha256(background)
    assert asset["audio_used"] is False
    assert (output / "assets" / "background.mp4").is_file()
    assert report["status"] == "awaiting_review"
    assert any("Background footage" in note for note in report["limits"])
