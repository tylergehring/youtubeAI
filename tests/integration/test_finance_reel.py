"""Finance reel pipeline end to end: text-only, no transcripts, no TTS, real FFmpeg."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image, ImageChops

from core.config import Settings, load_settings
from core.exceptions import (
    InvalidInputError,
    RenderError,
    StageError,
    StageNotApplicableError,
)
from core.finance import build_timing, resolve_methodology, resolve_rows
from core.jobs import JobStore
from core.models.finance import ReelLayout, ReelPlan
from core.models.manifest import (
    Audience,
    JobFormat,
    JobManifest,
    JobStatus,
    StageName,
)
from core.models.media import JobCost, RenderResult, ValidationReport
from core.reel_input import prepare_reel
from core.storage import read_json, read_model
from pipeline import artifacts
from pipeline import demo as demo_cli
from pipeline import jobs as jobs_cli
from pipeline.preview import write_preview
from pipeline.run import run_pipeline
from pipeline.stage import Outcome
from providers.registry import build_providers
from providers.tts.mock import MockTTSProvider
from rendering.ffmpeg import FFmpeg
from rendering.reel_layout import FontSet, build_layout, prepare_background_image
from tests.conftest import FFMPEG

FIXTURE = (
    Path(__file__).resolve().parents[2] / "examples" / "finance" / "retirement_withdrawal.yaml"
)
REEL_STAGES = [StageName.FINANCE, StageName.RENDER, StageName.VALIDATE, StageName.PACKAGE]


@pytest.fixture
def reel_settings(tmp_path: Path) -> Settings:
    env = {
        "VIDGEN_WORKSPACE_DIR": str(tmp_path / "workspace"),
        "VIDGEN__finance__width": "270",  # small portrait frame keeps FFmpeg tests fast
        "VIDGEN__finance__height": "480",
    }
    if FFMPEG:
        env["FFMPEG_PATH"] = FFMPEG
    return load_settings(env=env, load_dotenv_file=False)


@pytest.fixture
def reel_store(reel_settings: Settings) -> JobStore:
    return JobStore(reel_settings.workspace_dir)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    directory = tmp_path / "reel"
    directory.mkdir()
    Image.linear_gradient("L").convert("RGB").resize((90, 160)).save(
        directory / "demo_background.png"
    )
    return directory


def write_spec(folder: Path, **overrides: Any) -> Path:
    spec: dict[str, Any] = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    spec["background"]["path"] = "demo_background.png"  # tests supply a tiny image instead
    spec.update(overrides)
    path = folder / "spec.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


def create_job(
    store: JobStore, settings: Settings, spec: Path, job_id: str | None = None
) -> JobManifest:
    return store.create(
        "reel",
        reel=prepare_reel(spec, settings),
        style=settings.defaults.style,
        target_duration_minutes=1,
        audience=Audience.BEGINNER,
        job_id=job_id,
    )


def make_wav(path: Path) -> float:
    return MockTTSProvider().synthesize("a short spoken line for the reel", path).duration_s


def make_video(settings: Settings, path: Path, seconds: float = 1.5) -> Path:
    """A tiny landscape clip made locally with FFmpeg's test pattern."""
    exe = FFmpeg.locate(settings.ffmpeg_path).executable
    subprocess.run(  # noqa: S603 - fixed executable and arguments
        [str(exe), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"testsrc2=size=320x180:rate=15:duration={seconds}", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


def run_all(
    store: JobStore, settings: Settings, job_id: str, **kw: Any
) -> dict[StageName, Outcome]:
    return run_pipeline(store, job_id, settings, build_providers(settings), **kw)


class TestLayoutWithoutFfmpeg:
    def make_plan(self, settings: Settings, folder: Path) -> ReelPlan:
        from core.models.finance import ResolvedBackground
        from core.reel_input import parse_reel_spec

        spec = parse_reel_spec(write_spec(folder).read_text(encoding="utf-8"), ".yaml")
        rows, calc = resolve_rows(spec)
        return ReelPlan(
            title=spec.title,
            methodology=resolve_methodology(spec),
            rows=rows,
            cta=spec.cta,
            disclosure=spec.disclosure,
            background=ResolvedBackground(
                stored_path="x.png",
                sha256="0" * 64,
                media_type="image",
                width=90,
                height=160,
                origin=spec.background.origin,
                license=spec.background.license,
            ),  # fmt: skip
            provenance=spec.provenance,
            calculation=calc,
            timing=build_timing(len(rows), settings.finance.timing, settings.finance.fps),
        )

    def test_text_fits_the_safe_area_at_default_and_small_sizes(
        self, reel_settings: Settings, folder: Path
    ) -> None:
        plan = self.make_plan(reel_settings, folder)
        for width, height in ((1080, 1920), (270, 480), (720, 1280)):
            finance = reel_settings.finance.model_copy(update={"width": width, "height": height})
            result = build_layout(plan, finance, FontSet())
            report = result.report
            assert {b.name for b in report.boxes} >= {"title", "methodology", "cta", "disclosure"}
            for box in report.boxes:
                assert box.x >= report.safe_left and box.x + box.width <= report.safe_right
                assert box.y >= report.safe_top and box.y + box.height <= report.safe_bottom
            assert report.scale >= finance.min_text_scale

    def test_reveal_order_gives_each_row_its_own_moment(
        self, reel_settings: Settings, folder: Path
    ) -> None:
        plan = self.make_plan(reel_settings, folder)
        boxes = {
            b.name: b for b in build_layout(plan, reel_settings.finance, FontSet()).report.boxes
        }
        row_times = [boxes[f"row_{i + 1}"].start_s for i in range(8)]
        assert row_times == sorted(set(row_times))
        assert boxes["cta"].start_s > row_times[-1]
        assert boxes["disclosure"].start_s <= boxes["title"].start_s  # disclosure never hidden
        ys = [boxes[f"row_{i + 1}"].y for i in range(8)]
        assert ys == sorted(ys)

    def test_text_that_cannot_fit_fails_instead_of_overflowing(
        self, reel_settings: Settings, folder: Path
    ) -> None:
        plan = self.make_plan(reel_settings, folder)
        timing = build_timing(len(plan.rows) * 2, reel_settings.finance.timing, 30)
        crowded = plan.model_copy(
            update={"cta": "word " * 24, "rows": plan.rows * 2, "timing": timing}
        )
        finance = reel_settings.finance.model_copy(update={"min_text_scale": 0.9})
        with pytest.raises(RenderError, match="does not fit the safe area"):
            build_layout(crowded, finance, FontSet())

    def test_background_image_is_cover_cropped_not_stretched(self, tmp_path: Path) -> None:
        stripes = Image.new("RGB", (300, 100))
        for index, colour in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
            stripes.paste(colour, (index * 100, 0, index * 100 + 100, 100))
        source, out = tmp_path / "stripes.png", tmp_path / "cover.png"
        stripes.save(source)
        prepare_background_image(source, 100, 200, out)
        result = Image.open(out)
        assert result.size == (100, 200)
        # a stretch would show all three stripes; a centred crop shows only the middle one
        assert {result.getpixel((x, 100)) for x in (2, 50, 97)} == {(0, 255, 0)}

    def test_configured_font_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(RenderError, match="Cannot load configured regular font"):
            FontSet(regular=tmp_path / "missing.ttf")


@pytest.mark.ffmpeg
class TestTextOnlyReel:
    def test_pipeline_renders_without_transcripts_or_tts(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        assert job.input.format is JobFormat.FINANCE_FACTS
        assert job.input.sources == []  # no transcripts anywhere

        providers = build_providers(reel_settings)
        tts = MockTTSProvider()
        outcomes = run_pipeline(
            reel_store, job.job_id, reel_settings, dataclasses.replace(providers, tts=tts)
        )
        assert list(outcomes) == REEL_STAGES
        assert set(outcomes.values()) == {Outcome.EXECUTED}
        assert tts.calls == []

        manifest = reel_store.load(job.job_id)
        assert manifest.status is JobStatus.AWAITING_REVIEW
        assert set(manifest.stages) == set(REEL_STAGES)  # nothing tracked for explainer stages
        paths = reel_store.paths(job.job_id)
        assert not list(paths.subdir("audio").iterdir())
        assert not (paths.subdir("captions") / "captions.srt").exists()
        assert read_model(paths.cost_file, JobCost).records == []  # no provider was called

    def test_output_is_portrait_text_only_video(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        run_all(reel_store, reel_settings, job.job_id)
        paths = reel_store.paths(job.job_id)
        info = FFmpeg.locate(reel_settings.ffmpeg_path).analyze(artifacts.final_video(paths))
        assert (info.width, info.height) == (270, 480)
        assert info.height > info.width  # 9:16
        assert info.fps == pytest.approx(30)
        assert not info.has_audio
        plan = read_model(artifacts.reel_plan_json(paths), ReelPlan)
        assert info.duration_s == pytest.approx(plan.timing.duration_s, abs=0.2)

        report = read_model(artifacts.validation_json(paths), ValidationReport)
        assert report.technical_passed
        by_name = {c.name: c for c in report.checks}
        assert by_name["audio_present"].skipped and by_name["audio_not_silent"].skipped
        for name in ("figures_match_spec", "text_in_safe_area", "all_text_rendered",
                     "background_asset", "provenance_recorded"):  # fmt: skip
            assert by_name[name].passed
        render = read_model(artifacts.render_json(paths), RenderResult)
        assert render.renderer == "ffmpeg_reel" and render.scene_ids == []
        layout = read_model(artifacts.reel_layout_json(paths), ReelLayout)
        assert layout.width == 270

    def test_figures_and_provenance_are_recorded(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        run_all(reel_store, reel_settings, job.job_id)
        paths = reel_store.paths(job.job_id)
        record = read_json(artifacts.reel_provenance_json(paths))
        assert [r["value"] for r in record["rows"]] == [
            "$638K", "$851K", "$1.06M", "$1.28M", "$1.49M", "$1.70M", "$2.13M", "$2.55M",
        ]  # fmt: skip
        assert record["methodology"] == "Using a 4.7% withdrawal rate"
        assert "annual_income" in record["calculation"]["formula"]
        assert record["calculation"]["inputs"]["withdrawal_rate_pct"] == "4.7"
        assert record["provenance"]["verification"] == "illustrative"
        assert record["background"]["origin"] == "generated_placeholder"
        assert "Hypothetical" in record["disclosure"]
        summary = read_json(artifacts.final_summary(paths))
        assert summary["format"] == "finance_facts"
        assert summary["narration"] is False
        assert summary["technical_checks_passed"] is True

    def test_sourced_manual_figures_keep_their_citations(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        spec = write_spec(
            folder,
            figures={"kind": "manual", "rows": [{"label": "Age 65", "value": "$1,234"}]},
            methodology="Median balances, 2022 survey",
            disclosure="Educational only. Not financial advice.",
            provenance={
                "verification": "sourced",
                "sources": [{
                    "title": "Survey of Consumer Finances", "publisher": "Federal Reserve",
                    "url": "https://www.federalreserve.gov/econres/scfindex.htm",
                    "as_of": "2023-10-18",
                }],
            },
        )  # fmt: skip
        job = create_job(reel_store, reel_settings, spec)
        run_all(reel_store, reel_settings, job.job_id)
        record = read_json(artifacts.reel_provenance_json(reel_store.paths(job.job_id)))
        assert record["calculation"] is None
        assert record["provenance"]["verification"] == "sourced"
        source = record["provenance"]["sources"][0]
        assert source["title"] == "Survey of Consumer Finances"
        assert source["url"].startswith("https://www.federalreserve.gov")
        assert source["as_of"] == "2023-10-18"

    def test_rerunning_reuses_everything(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        run_all(reel_store, reel_settings, job.job_id)
        again = run_all(reel_store, reel_settings, job.job_id)
        assert set(again.values()) == {Outcome.REUSED}

    def test_preview_frames_show_the_staged_reveal(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        run_all(reel_store, reel_settings, job.job_id)
        frames, sheet = write_preview(reel_settings, reel_store, job.job_id)
        assert len(frames) >= 5 and sheet.is_file()
        sizes = {Image.open(f).size for f in frames}
        assert sizes == {(270, 480)}
        first, last = (Image.open(frames[0]).convert("RGB"), Image.open(frames[-1]).convert("RGB"))
        assert ImageChops.difference(first, last).getbbox() is not None  # content was revealed
        # the background stays visible: the middle of the last frame is not a solid panel
        assert len({last.getpixel((135, y)) for y in range(300, 460)}) > 5


@pytest.mark.ffmpeg
class TestOptionalNarrationAndVideo:
    def test_voiceover_is_mixed_in_and_can_lengthen_the_reel(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        make_wav(folder / "narration.wav")
        job = create_job(
            reel_store, reel_settings, write_spec(folder, voiceover={"path": "narration.wav"})
        )
        run_all(reel_store, reel_settings, job.job_id)
        paths = reel_store.paths(job.job_id)
        info = FFmpeg.locate(reel_settings.ffmpeg_path).analyze(artifacts.final_video(paths))
        assert info.has_audio
        assert info.mean_volume_db is not None and info.mean_volume_db > -60
        plan = read_model(artifacts.reel_plan_json(paths), ReelPlan)
        assert plan.voiceover is not None
        assert plan.timing.duration_s >= plan.voiceover.duration_s
        report = read_model(artifacts.validation_json(paths), ValidationReport)
        assert report.technical_passed
        assert not {c.name for c in report.checks if c.skipped} & {"audio_present"}
        assert read_json(artifacts.final_summary(paths))["narration"] is True

    def test_video_background_is_looped_and_cropped_to_portrait(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        make_video(reel_settings, folder / "clip.mp4", seconds=1.0)
        data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["background"]
        data.update(path="clip.mp4", origin="user_supplied", license="Own footage")
        job = create_job(reel_store, reel_settings, write_spec(folder, background=data))
        run_all(reel_store, reel_settings, job.job_id)  # background is shorter than the reel
        paths = reel_store.paths(job.job_id)
        plan = read_model(artifacts.reel_plan_json(paths), ReelPlan)
        assert plan.background.media_type == "video"
        assert plan.background.duration_s is not None and plan.background.duration_s < 2
        info = FFmpeg.locate(reel_settings.ffmpeg_path).analyze(artifacts.final_video(paths))
        assert (info.width, info.height) == (270, 480)
        assert info.duration_s == pytest.approx(plan.timing.duration_s, abs=0.2)
        assert not info.has_audio  # the footage's own audio is never used


class TestMissingAndInvalidAssets:
    def test_missing_background_fails_at_job_creation_and_leaves_nothing(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        (folder / "demo_background.png").unlink()
        spec = write_spec(folder)
        with pytest.raises(InvalidInputError, match="Background not found"):
            create_job(reel_store, reel_settings, spec)
        assert reel_store.list_jobs() == []

    def test_background_removed_after_creation_fails_the_finance_stage(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        paths = reel_store.paths(job.job_id)
        paths.absolute(job.input.reel.background.stored_path).unlink()  # type: ignore[union-attr]
        with pytest.raises(StageError, match="missing from the job"):
            run_all(reel_store, reel_settings, job.job_id)
        manifest = reel_store.load(job.job_id)
        assert manifest.status is JobStatus.FAILED
        assert manifest.failed_stage is StageName.FINANCE

    def test_background_changed_after_creation_is_detected(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        stored = reel_store.paths(job.job_id).absolute(
            job.input.reel.background.stored_path  # type: ignore[union-attr]
        )
        stored.write_bytes(stored.read_bytes() + b"tamper")
        with pytest.raises(StageError, match="changed since the job was created"):
            run_all(reel_store, reel_settings, job.job_id)

    @pytest.mark.ffmpeg
    def test_corrupt_video_background_is_rejected_by_the_finance_stage(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        (folder / "clip.mp4").write_bytes(b"definitely not a video" * 100)
        data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["background"]
        data.update(path="clip.mp4", origin="user_supplied", license="Own footage")
        job = create_job(reel_store, reel_settings, write_spec(folder, background=data))
        with pytest.raises(StageError, match="Cannot open media file"):
            run_all(reel_store, reel_settings, job.job_id)
        assert reel_store.load(job.job_id).failed_stage is StageName.FINANCE

    @pytest.mark.ffmpeg
    def test_voiceover_without_audio_is_rejected(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        (folder / "narration.wav").write_bytes(b"RIFF" + b"\x00" * 200)
        job = create_job(
            reel_store, reel_settings, write_spec(folder, voiceover={"path": "narration.wav"})
        )
        with pytest.raises(StageError):
            run_all(reel_store, reel_settings, job.job_id)


class TestFormatBoundaries:
    def test_explainer_only_stages_are_refused_for_a_reel(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        job = create_job(reel_store, reel_settings, write_spec(folder))
        from pipeline import script

        before = reel_store.load(job.job_id)
        code = script.main(["--job", job.job_id, "--workspace", str(reel_settings.workspace_dir)])
        assert code == 2
        assert reel_store.load(job.job_id) == before  # state untouched
        with pytest.raises(StageNotApplicableError):
            run_pipeline(
                reel_store, job.job_id, reel_settings, build_providers(reel_settings),
                start=StageName.SCRIPT,
            )  # fmt: skip

    def test_finance_stage_is_refused_for_an_explainer_job(
        self, reel_settings: Settings, reel_store: JobStore
    ) -> None:
        from pipeline import finance

        job = reel_store.create(
            "How TCP works", style="technical_explainer", target_duration_minutes=1,
            audience=Audience.BEGINNER,
        )  # fmt: skip
        code = finance.main(["--job", job.job_id, "--workspace", str(reel_settings.workspace_dir)])
        assert code == 2
        loaded = reel_store.load(job.job_id)
        assert loaded.status is JobStatus.CREATED
        assert loaded.input.format is JobFormat.EXPLAINER and loaded.input.reel is None

    def test_a_job_cannot_mix_transcripts_and_a_reel(
        self, reel_settings: Settings, reel_store: JobStore, folder: Path
    ) -> None:
        from core.ingest import PreparedSource

        source = PreparedSource("a.txt", "a", None, b"hi", "0" * 64, "txt")
        with pytest.raises(InvalidInputError, match="does not use transcripts"):
            reel_store.create(
                "x", sources=[source], reel=prepare_reel(write_spec(folder), reel_settings),
                style="technical_explainer", target_duration_minutes=1, audience=Audience.BEGINNER,
            )  # fmt: skip


class TestCli:
    def args(self, settings: Settings) -> list[str]:
        return ["--workspace", str(settings.workspace_dir)]

    def test_create_status_and_stage_list(
        self,
        reel_settings: Settings,
        reel_store: JobStore,
        folder: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        spec = write_spec(folder)
        code = jobs_cli.main(
            [*self.args(reel_settings), "create", "--format", "finance_facts",
             "--reel-spec", str(spec)]
        )  # fmt: skip
        assert code == 0
        job_id = capsys.readouterr().out.strip()
        assert job_id.endswith("_how_much_do_you_need_to_retire_001")  # topic defaults to title
        assert jobs_cli.main([*self.args(reel_settings), "status", "--job", job_id]) == 0
        out = capsys.readouterr().out
        for stage in REEL_STAGES:
            assert stage.value in out
        assert "knowledge" not in out and "voice" not in out

    def test_params_file_can_create_a_reel(
        self,
        reel_settings: Settings,
        folder: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        params = tmp_path / "params.json"
        params.write_text(
            json.dumps({"format": "finance_facts", "reel_spec": str(write_spec(folder))}),
            encoding="utf-8",
        )
        assert (
            jobs_cli.main([*self.args(reel_settings), "create", "--params-file", str(params)]) == 0
        )
        assert capsys.readouterr().out.strip()

    @pytest.mark.parametrize(
        "extra",
        [
            ["--format", "finance_facts"],  # no spec
            ["--format", "finance_facts", "--reel-spec", "missing.yaml"],
            ["--topic", "TCP", "--reel-spec", "whatever.yaml"],  # reel spec needs the format
            ["--format", "finance_facts", "--transcript", "a.txt"],
        ],
    )
    def test_bad_combinations_exit_with_usage_error(
        self, reel_settings: Settings, reel_store: JobStore, extra: list[str]
    ) -> None:
        assert jobs_cli.main([*self.args(reel_settings), "create", *extra]) == 2
        assert reel_store.list_jobs() == []

    def test_explainer_creation_still_requires_a_topic(self, reel_settings: Settings) -> None:
        assert jobs_cli.main([*self.args(reel_settings), "create"]) == 2

    def test_missing_background_exits_2_through_the_cli(
        self, reel_settings: Settings, folder: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec = write_spec(folder)
        (folder / "demo_background.png").unlink()
        code = jobs_cli.main(
            [*self.args(reel_settings), "create", "--format", "finance_facts",
             "--reel-spec", str(spec)]
        )  # fmt: skip
        assert code == 2
        assert "Background not found" in capsys.readouterr().err


@pytest.mark.ffmpeg
class TestDemo:
    def test_demo_runs_locally_and_is_deterministic(
        self, reel_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIDGEN__finance__width", "270")
        monkeypatch.setenv("VIDGEN__finance__height", "480")
        assert FFMPEG
        monkeypatch.setenv("FFMPEG_PATH", FFMPEG)
        ws = tmp_path / "demo_ws"
        assert demo_cli.main(["--workspace", str(ws)]) == 0
        store = JobStore(ws)
        (manifest,) = store.list_jobs()
        paths = store.paths(manifest.job_id)
        assert manifest.status is JobStatus.AWAITING_REVIEW
        assert artifacts.final_video(paths).is_file()
        assert (artifacts.reel_preview_dir(paths) / "contact_sheet.png").is_file()
        plan = read_model(artifacts.reel_plan_json(paths), ReelPlan)
        assert [r.value for r in plan.rows][:3] == ["$638K", "$851K", "$1.06M"]
        assert plan.background.origin.value == "generated_placeholder"
        assert plan.background.media_type == "video"  # the animated demo backdrop

        assert (ws / "demo" / "demo_background.mp4").stat().st_size > 1000
        first = ws / "demo" / "a.png"
        demo_cli.generate_demo_background(first, 270, 480)
        demo_cli.generate_demo_background(ws / "demo" / "b.png", 270, 480)
        assert first.read_bytes() == (ws / "demo" / "b.png").read_bytes()  # no randomness
