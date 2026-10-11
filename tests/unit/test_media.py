import pytest

from core.config import Settings
from core.exceptions import RenderError
from core.jobs import JobStore
from core.models.content import Script, Transcript
from core.models.media import (
    AudioManifest,
    AudioRecord,
    CaptionCue,
    CaptionTrack,
    RenderResult,
    SceneVisual,
    VisualManifest,
)
from core.models.scenes import ScenePlan
from pipeline.captions import format_srt_time, render_srt, split_cue_text
from pipeline.scenes import check_scene_coverage
from pipeline.script import check_claim_references
from pipeline.validate import ngram_overlap, technical_checks
from providers import mock_data
from rendering.ffmpeg import MediaInfo
from rendering.timeline import build_timeline
from tests.conftest import SOURCE_TEXT, MakeJob


def make_audio(plan: ScenePlan, duration: float = 2.0) -> AudioManifest:
    return AudioManifest(
        records=[
            AudioRecord(
                scene_id=s.scene_id, path=f"audio/s{s.scene_id}.wav", duration_s=duration,
                provider="mock", voice="v",
            )
            for s in plan.scenes
        ]
    )  # fmt: skip


class TestTimeline:
    def test_uses_real_audio_duration_not_estimate(self) -> None:
        plan = ScenePlan.model_validate(mock_data.scenes())
        timeline = build_timeline(plan, make_audio(plan, 2.0), padding_s=0.5, fps=10)
        assert [e.start_s for e in timeline.entries] == pytest.approx([0, 2.5, 5, 7.5, 10])
        assert timeline.total_duration_s == pytest.approx(12.5)

    def test_snaps_to_whole_frames(self) -> None:
        plan = ScenePlan.model_validate(mock_data.scenes())
        timeline = build_timeline(plan, make_audio(plan, 1.01), padding_s=0.0, fps=30)
        length = timeline.entries[0].end_s
        assert length * 30 == pytest.approx(round(length * 30))
        assert length >= 1.01

    def test_missing_audio_fails_clearly(self) -> None:
        plan = ScenePlan.model_validate(mock_data.scenes())
        audio = make_audio(plan)
        audio.records.pop()
        with pytest.raises(RenderError, match="scene 5"):
            build_timeline(plan, audio, padding_s=0.4, fps=30)


class TestCaptions:
    def test_split_respects_sentences_and_length(self) -> None:
        long_text = (
            "From here, every byte gets a sequence number. The receiver acknowledges what it "
            "has seen, and the sender retransmits anything that goes unacknowledged, so lost "
            "packets are repaired before the application ever notices."
        )
        chunks = split_cue_text(long_text)
        assert " ".join(chunks).replace("  ", " ") == long_text
        assert all(len(c) <= 80 for c in chunks)
        assert len(chunks) >= 3

    def test_short_text_single_cue(self) -> None:
        assert split_cue_text("Hello there.") == ["Hello there."]

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [(0, "00:00:00,000"), (61.5, "00:01:01,500"), (3661.007, "01:01:01,007")],
    )
    def test_srt_time(self, seconds: float, text: str) -> None:
        assert format_srt_time(seconds) == text

    def test_srt_format(self) -> None:
        srt = render_srt(
            [
                CaptionCue(index=1, start_s=0, end_s=1.5, text="One"),
                CaptionCue(index=2, start_s=1.5, end_s=3, text="Two"),
            ]
        )
        assert (
            srt
            == "1\n00:00:00,000 --> 00:00:01,500\nOne\n\n2\n00:00:01,500 --> 00:00:03,000\nTwo\n"
        )


class TestStageValidators:
    def test_scene_coverage_ok(self) -> None:
        plan = ScenePlan.model_validate(mock_data.scenes())
        script = Script.model_validate(mock_data.script())
        assert check_scene_coverage(plan, script) == []

    def test_scene_coverage_reports_gaps_and_unknown_sections(self) -> None:
        data = mock_data.scenes()
        data["scenes"][1]["section_id"] = "ghost"
        problems = check_scene_coverage(
            ScenePlan.model_validate(data), Script.model_validate(mock_data.script())
        )
        assert any("ghost" in p for p in problems)
        assert any("context" in p and "no scene" in p for p in problems)

    def test_claim_reference_check(self) -> None:
        from core.models.content import KnowledgeBase

        knowledge = KnowledgeBase.model_validate(mock_data.knowledge("TCP"))
        data = mock_data.script()
        data["sections"][0]["claim_ids"] = ["c1", "zzz"]
        problems = check_claim_references(Script.model_validate(data), knowledge)
        assert problems == ["section 'intro' cites unknown claim 'zzz'"]


class TestOverlap:
    def test_identical_and_disjoint(self) -> None:
        text = "the quick brown fox jumps over the lazy dog again"
        assert ngram_overlap(text, text) == 1.0
        assert ngram_overlap(text, "completely different words here and there now") == 0.0

    def test_short_candidate(self) -> None:
        assert ngram_overlap("too short", "too short") == 0.0

    def test_mock_script_is_original(self) -> None:
        source = SOURCE_TEXT
        script = " ".join(s["narration"] for s in mock_data.script()["sections"])
        assert ngram_overlap(script, source) < 0.1


class TestTechnicalChecks:
    def build(
        self,
        store: JobStore,
        make_job: MakeJob,
        settings: Settings,
        *,
        media: MediaInfo | None,
        transcripts: list[Transcript] | None = None,
        create_files: bool = True,
        script: Script | None = None,
    ):  # type: ignore[no-untyped-def]
        job = make_job()
        paths = store.paths(job.job_id)
        plan = ScenePlan.model_validate(mock_data.scenes())
        audio = make_audio(plan)
        timeline = build_timeline(plan, audio, padding_s=0.4, fps=settings.video.fps)
        if create_files:
            (paths.subdir("render") / "video.mp4").write_bytes(b"x" * 2048)
            (paths.subdir("captions") / "captions.srt").write_text("1\n")
            for r in audio.records:
                paths.absolute(r.path).write_bytes(b"w")
        render = RenderResult(
            renderer="t", video_path="render/video.mp4", duration_s=timeline.total_duration_s,
            width=settings.video.width, height=settings.video.height, fps=settings.video.fps,
            scene_ids=[s.scene_id for s in plan.scenes], timeline=timeline,
        )  # fmt: skip
        return technical_checks(
            paths=paths,
            settings=settings,
            plan=plan,
            audio=audio,
            visuals=VisualManifest(
                scenes=[
                    SceneVisual(
                        scene_id=s.scene_id,
                        renderer="t",
                        background_color="#000000",
                        accent_color="#ffffff",
                    )
                    for s in plan.scenes
                ]  # fmt: skip
            ),
            captions=CaptionTrack(
                path="captions/captions.srt",
                cues=[CaptionCue(index=1, start_s=0, end_s=1, text="a")],
            ),
            render=render,
            script=script or Script.model_validate(mock_data.script()),
            transcripts=transcripts or [],
            media=media,
        ), render

    def good_media(self, settings: Settings, render_duration: float) -> MediaInfo:
        return MediaInfo(
            duration_s=render_duration, has_video=True, has_audio=True,
            width=settings.video.width, height=settings.video.height,
            fps=float(settings.video.fps), mean_volume_db=-20.0,
        )  # fmt: skip

    def failing(self, checks: list) -> set[str]:  # type: ignore[type-arg]
        return {c.name for c in checks if not c.passed and not c.skipped}

    def test_all_pass(self, store: JobStore, make_job: MakeJob, settings: Settings) -> None:
        _, render = self.build(store, make_job, settings, media=None)
        checks, _ = self.build(
            store, make_job, settings, media=self.good_media(settings, render.duration_s)
        )
        assert self.failing(checks) == set()

    def test_unreadable_video(self, store: JobStore, make_job: MakeJob, settings: Settings) -> None:
        checks, _ = self.build(store, make_job, settings, media=None)
        assert {"video_opens", "duration_reasonable", "resolution", "frame_rate",
                "audio_present", "audio_not_silent"} <= self.failing(checks)  # fmt: skip

    def test_wrong_resolution_fps_and_silence(
        self, store: JobStore, make_job: MakeJob, settings: Settings
    ) -> None:
        _, render = self.build(store, make_job, settings, media=None)
        bad = MediaInfo(render.duration_s, True, True, 640, 360, 24.0, -91.0)
        checks, _ = self.build(store, make_job, settings, media=bad)
        assert self.failing(checks) == {"resolution", "frame_rate", "audio_not_silent"}

    def test_no_audio_stream(self, store: JobStore, make_job: MakeJob, settings: Settings) -> None:
        _, render = self.build(store, make_job, settings, media=None)
        media = MediaInfo(render.duration_s, True, False, settings.video.width,
                          settings.video.height, float(settings.video.fps), None)  # fmt: skip
        checks, _ = self.build(store, make_job, settings, media=media)
        assert self.failing(checks) == {"audio_present", "audio_not_silent"}

    def test_duration_mismatch(
        self, store: JobStore, make_job: MakeJob, settings: Settings
    ) -> None:
        _, render = self.build(store, make_job, settings, media=None)
        checks, _ = self.build(
            store, make_job, settings, media=self.good_media(settings, render.duration_s + 5)
        )
        assert self.failing(checks) == {"duration_reasonable"}

    def test_missing_files(self, store: JobStore, make_job: MakeJob, settings: Settings) -> None:
        checks, _ = self.build(store, make_job, settings, media=None, create_files=False)
        failing = self.failing(checks)
        assert {"video_exists", "assets_exist", "captions_exist", "file_size"} <= failing

    def test_copied_source_fails_overlap(
        self, store: JobStore, make_job: MakeJob, settings: Settings
    ) -> None:
        narration = " ".join(s["narration"] for s in mock_data.script()["sections"])
        copied = Transcript.model_validate(
            {"format": "txt", "segments": [{"start_s": 0, "end_s": 1, "text": narration}]}
        )
        _, render = self.build(store, make_job, settings, media=None)
        checks, _ = self.build(
            store, make_job, settings, media=self.good_media(settings, render.duration_s),
            transcripts=[copied],
        )  # fmt: skip
        assert self.failing(checks) == {"source_overlap"}
