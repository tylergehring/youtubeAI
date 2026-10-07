"""Rendering tests using tiny scenes."""

from pathlib import Path

import pytest

from core.config import Settings
from core.exceptions import RenderError
from core.jobs import JobStore
from core.models.media import AudioManifest, RenderResult, SceneVisual, VisualManifest
from core.models.scenes import ScenePlan
from providers import mock_data
from providers.tts.mock import MockTTSProvider
from rendering.ffmpeg import FFmpeg, find_ffmpeg
from rendering.renderer import PlaceholderRenderer, RenderRequest, build_renderer, ffmpeg_color
from rendering.timeline import build_timeline
from tests.conftest import FFMPEG, MakeJob


def test_ffmpeg_color_validation() -> None:
    assert ffmpeg_color("#0f172a") == "0x0f172a"
    for bad in ("red", "#12345", "#12345g", "0x0f172a;rm", "#0f172a ,x"):
        with pytest.raises(RenderError):
            ffmpeg_color(bad)


def test_missing_ffmpeg_is_a_clear_error(tmp_path: Path) -> None:
    assert find_ffmpeg(str(tmp_path / "nope.exe")) is None
    with pytest.raises(RenderError, match="FFmpeg not found"):
        FFmpeg.locate(str(tmp_path / "nope.exe"))


@pytest.mark.ffmpeg
class TestRealFfmpeg:
    def render_small(
        self, settings: Settings, store: JobStore, make_job: MakeJob, scene_count: int = 2
    ) -> tuple[RenderResult, Path, FFmpeg]:
        job = make_job()
        paths = store.paths(job.job_id)
        data = mock_data.scenes()
        data["scenes"] = data["scenes"][:scene_count]
        plan = ScenePlan.model_validate(data)

        tts = MockTTSProvider()
        records = []
        for scene in plan.scenes:
            result = tts.synthesize(
                "short test line", paths.subdir("audio") / f"s{scene.scene_id}.wav"
            )
            from core.models.media import AudioRecord

            records.append(
                AudioRecord(scene_id=scene.scene_id, path=paths.relative(result.path),
                            duration_s=result.duration_s, provider="mock", voice=result.voice)
            )  # fmt: skip
        audio = AudioManifest(records=records)
        visuals = VisualManifest(
            scenes=[
                SceneVisual(
                    scene_id=s.scene_id,
                    renderer="placeholder",
                    background_color="#0f172a",
                    accent_color="#38bdf8",
                )
                for s in plan.scenes
            ]  # fmt: skip
        )
        video = settings.video
        timeline = build_timeline(plan, audio, padding_s=video.scene_padding_s, fps=video.fps)
        ffmpeg = FFmpeg.locate(settings.ffmpeg_path)
        out = paths.subdir("render") / "video.mp4"
        result = PlaceholderRenderer(ffmpeg).render(
            RenderRequest(paths=paths, plan=plan, audio=audio, visuals=visuals,
                          timeline=timeline, out_path=out, video=video)
        )  # fmt: skip
        return result, out, ffmpeg

    def test_render_matches_timeline_and_settings(
        self, settings: Settings, store: JobStore, make_job: MakeJob
    ) -> None:
        result, out, ffmpeg = self.render_small(settings, store, make_job)
        info = ffmpeg.analyze(out)
        assert (info.width, info.height) == (settings.video.width, settings.video.height)
        assert info.fps == pytest.approx(settings.video.fps)
        assert info.has_audio and info.has_video
        assert info.duration_s == pytest.approx(result.timeline.total_duration_s, abs=0.2)
        assert info.mean_volume_db is not None and info.mean_volume_db > -60
        assert result.scene_ids == [1, 2]
        assert not list(out.parent.glob("*.partial.mp4"))

    def test_rendering_is_repeatable(
        self, settings: Settings, store: JobStore, make_job: MakeJob
    ) -> None:
        first, _, _ = self.render_small(settings, store, make_job)
        second, _, _ = self.render_small(settings, store, make_job)
        assert first.timeline == second.timeline
        assert first.duration_s == second.duration_s

    def test_analyze_rejects_garbage(self, settings: Settings, tmp_path: Path) -> None:
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"not a video at all" * 50)
        with pytest.raises(RenderError, match="Cannot open"):
            FFmpeg.locate(settings.ffmpeg_path).analyze(bad)

    def test_missing_audio_file_fails_before_encoding(
        self, settings: Settings, store: JobStore, make_job: MakeJob
    ) -> None:
        result, out, ffmpeg = self.render_small(settings, store, make_job, scene_count=1)
        job = make_job()
        paths = store.paths(job.job_id)
        plan = ScenePlan.model_validate({"scenes": [mock_data.scenes()["scenes"][0]]})
        from core.models.media import AudioRecord

        audio = AudioManifest(
            records=[AudioRecord(scene_id=1, path="audio/ghost.wav", duration_s=1.0,
                                 provider="m", voice="v")]
        )  # fmt: skip
        visuals = VisualManifest(
            scenes=[SceneVisual(scene_id=1, renderer="placeholder", background_color="#000000",
                                accent_color="#ffffff")]
        )  # fmt: skip
        timeline = build_timeline(plan, audio, padding_s=0.2, fps=settings.video.fps)
        with pytest.raises(RenderError, match="Audio file missing"):
            PlaceholderRenderer(ffmpeg).render(
                RenderRequest(paths=paths, plan=plan, audio=audio, visuals=visuals,
                              timeline=timeline, out_path=paths.subdir("render") / "v.mp4",
                              video=settings.video)
            )  # fmt: skip

    def test_build_renderer_uses_configured_ffmpeg(self, settings: Settings) -> None:
        assert build_renderer(settings).name == "placeholder"
        assert FFMPEG
