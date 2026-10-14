from __future__ import annotations

import array
import math
import wave
from pathlib import Path

import pytest

from core.config import Settings, load_settings
from core.exceptions import InvalidInputError
from core.hashing import file_sha256
from core.jobs import JobPaths
from core.models.content import Transcript, TranscriptSegment
from core.models.editorial import (
    ShortScript,
    ShortScriptScene,
    TopicCandidate,
    VisualPlan,
    VisualPlanScene,
)
from core.models.media import JobCost, ValidationReport
from core.models.scenes import GraphObject, TextObject, VisualType
from core.providers import TTSProvider, TTSResult, Usage
from core.storage import read_json, read_model, write_json, write_model
from core.youtube_transcript import YouTubeSource
from pipeline.editorial import render_editorial_topic
from providers.image.mock import MockImageProvider
from providers.llm.mock import MockLLMProvider
from providers.registry import ProviderSet
from providers.research.mock import MockResearchProvider
from rendering.ffmpeg import FFmpeg
from tests.conftest import FFMPEG


def _write_tone(path: Path, seconds: float) -> None:
    sample_rate = 48_000
    samples = array.array(
        "h",
        (
            round(6000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            for index in range(round(seconds * sample_rate))
        ),
    )
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


class FakeTTSProvider(TTSProvider):
    name = "test"
    model = "fixed-tone"
    default_voice = "test-voice"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, out_path: Path, *, voice: str | None = None) -> TTSResult:
        self.calls += 1
        _write_tone(out_path, 7)
        return TTSResult(
            path=out_path,
            duration_s=7,
            voice=voice or self.default_voice,
            metadata={"kind": "test"},
            usage=Usage(characters=len(text)),
        )


def _draft(workspace: Path) -> tuple[Settings, ProviderSet, FakeTTSProvider, Path]:
    settings = load_settings(
        env={
            "VIDGEN_WORKSPACE_DIR": str(workspace),
            "VIDGEN__finance__width": "270",
            "VIDGEN__finance__height": "480",
            "VIDGEN__finance__fps": "1",
            "FFMPEG_PATH": FFMPEG or "",
        },
        load_dotenv_file=False,
    )
    tts = FakeTTSProvider()
    providers = ProviderSet(
        llm=MockLLMProvider("unused"),
        tts=tts,
        image=MockImageProvider(),
        research=MockResearchProvider(),
    )
    batch_dir = workspace / "editorial_batches" / "sample_batch"
    topic_dir = batch_dir / "topic-1"
    topic_dir.mkdir(parents=True)
    research_dir = batch_dir / "research"
    research_dir.mkdir()
    transcript = Transcript(
        format="youtube",
        segments=[
            TranscriptSegment(
                start_s=0,
                end_s=2,
                text="The host describes several recent economic and policy developments.",
            )
        ],
    )
    transcript_path = research_dir / "transcript.json"
    write_model(transcript_path, transcript)
    transcript_sha256 = file_sha256(transcript_path)
    write_model(
        research_dir / "source.json",
        YouTubeSource(
            video_id="5GRH9EeH-DQ",
            reference_url="https://www.youtube.com/watch?v=5GRH9EeH-DQ",
            language="en",
            is_generated=True,
            transcript_sha256=transcript_sha256,
        ),
    )
    topic = TopicCandidate(
        id="topic-1",
        title="Why bond prices move",
        question="Why can a bond price change before a payment is missed?",
        original_angle=(
            "Explain how a fixed payment can become less attractive when market rates rise."
        ),
        transcript_segment_indices=[1],
        source_leads=["Verify the bond pricing explanation."],
        research_questions=["How are bond cash flows discounted?"],
        visual_concept="Show a falling price curve as the yield rises.",
    )
    script = ShortScript(
        title="Why bond prices can fall",
        hook="A bond can lose value without missing a payment.",
        scenes=[
            ShortScriptScene(
                id=f"scene-{index}",
                purpose="explanation",
                narration=(
                    f"Scene {index} explains how fixed payments can become less attractive "
                    "when interest rates change."
                ),
            )
            for index in range(1, 5)
        ],
        claims_to_verify=["Check a primary source for bond pricing."],
        disclosure="Educational explanation only; not personal financial advice.",
    )
    visual_plan = VisualPlan(
        creative_direction="Show the price response as rates change.",
        scenes=[
            VisualPlanScene(
                scene_id=index,
                script_scene_id=f"scene-{index}",
                visual_goal="Keep the rate and price relationship simple.",
                visual_type=VisualType.GRAPH,
                objects=[
                    TextObject(
                        id="heading",
                        position=(0.1, 0.23),
                        text="Rates rise, price can fall",
                        font_size=0.05,
                    ),
                    GraphObject(
                        id="curve",
                        position=(0.14, 0.36),
                        points=[(0.0, 0.9), (0.5, 0.55), (1.0, 0.2)],
                        x_label="Yield",
                        y_label="Price",
                    ),
                ],
                text=["Prices follow rates"],
            )
            for index in range(1, 5)
        ],
    )
    write_model(topic_dir / "topic.json", topic)
    write_model(topic_dir / "script.json", script)
    write_model(topic_dir / "visual_plan.json", visual_plan)
    write_json(
        batch_dir / "plan.json",
        {
            "batch_id": "sample_batch",
            "source_video_id": "5GRH9EeH-DQ",
            "source_url": "https://www.youtube.com/watch?v=5GRH9EeH-DQ",
            "transcript_sha256": transcript_sha256,
            "topics": [{"topic_id": "topic-1", "status": "script_and_visual_plan_ready"}],
        },
    )
    return settings, providers, tts, batch_dir


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_editorial_topic_render_requires_consent_and_reuses_narration(
    tmp_path: Path,
) -> None:
    settings, providers, tts, batch_dir = _draft(tmp_path / "workspace")
    ffmpeg = FFmpeg.locate(FFMPEG)
    background = tmp_path / "background.mp4"
    music = tmp_path / "music.wav"
    ffmpeg.run_checked(
        [
            "-y", "-hide_banner", "-nostats", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=#24404a:size=270x480:rate=2:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(background),
        ]
    )
    _write_tone(music, 1)

    with pytest.raises(InvalidInputError, match="--approve-ai-usage"):
        render_editorial_topic(
            "sample_batch",
            "topic-1",
            settings,
            providers,
            approve_ai_usage=False,
        )
    assert tts.calls == 0
    with pytest.raises(InvalidInputError, match="Declare the background asset license"):
        render_editorial_topic(
            "sample_batch",
            "topic-1",
            settings,
            providers,
            approve_ai_usage=True,
            background_video=background,
        )
    assert tts.calls == 0

    video = render_editorial_topic(
        "sample_batch",
        "topic-1",
        settings,
        providers,
        approve_ai_usage=True,
        background_video=background,
        background_license="Test fixture permission",
        background_attribution="Test author",
        music=music,
        music_license="Test fixture permission",
    )

    assert video.is_file()
    assert tts.calls == 4
    assert read_json(batch_dir / "topic-1" / "render_manifest.json")["status"] == "awaiting_review"
    assert read_model(
        batch_dir / "topic-1" / "validation.json", ValidationReport
    ).technical_passed
    assert (batch_dir / "topic-1" / "final" / "captions.srt").is_file()
    assert (batch_dir / "topic-1" / "final" / "publishing.json").is_file()
    assets = read_json(batch_dir / "topic-1" / "assets.json")["assets"]
    assert {asset["kind"] for asset in assets} == {"background", "music"}
    assert all((batch_dir / "topic-1" / asset["stored_path"]).is_file() for asset in assets)
    assert read_json(batch_dir / "plan.json")["topics"][0]["status"] == "video_draft_ready"
    assert read_model(JobPaths(batch_dir).cost_file, JobCost).total_estimated_cost_usd == 0

    assert render_editorial_topic(
        "sample_batch",
        "topic-1",
        settings,
        providers,
        approve_ai_usage=True,
        background_video=background,
        background_license="Test fixture permission",
        background_attribution="Test author",
        music=music,
        music_license="Test fixture permission",
    ) == video
    assert tts.calls == 4
