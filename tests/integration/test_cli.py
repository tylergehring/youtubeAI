"""Command-line behaviour, including real `python -m` invocations as Jenkins will make them."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.jobs import JobStore
from core.models.manifest import JobStatus
from pipeline import jobs as jobs_cli
from pipeline import knowledge, script
from pipeline.acquire import main as acquire_main
from tests.conftest import FFMPEG, SOURCE_SRT, SOURCE_TEXT, WriteFile

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def python_m(module: str, *args: str, workspace: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "VIDGEN_WORKSPACE_DIR": str(workspace),
        "VIDGEN__video__width": "320",
        "VIDGEN__video__height": "180",
    }
    if FFMPEG:
        env["FFMPEG_PATH"] = FFMPEG
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


class TestCreateCommand:
    def test_create_with_transcripts_then_status_list_show(
        self, tmp_path: Path, write_file: WriteFile, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ws = ["--workspace", str(tmp_path / "w")]
        a, b = write_file("a.txt", SOURCE_TEXT), write_file("b.srt", SOURCE_SRT)
        args = [
            *ws,
            "create",
            "--topic",
            "How Git works",
            "--transcript",
            str(a),
            "--transcript",
            str(b),
        ]
        assert jobs_cli.main(args) == 0
        job_id = capsys.readouterr().out.strip()
        assert job_id.endswith("_how_git_works_001")

        manifest = JobStore(tmp_path / "w").load(job_id)
        assert [s.name for s in manifest.input.sources] == ["a.txt", "b.srt"]
        assert jobs_cli.main([*ws, "status", "--job", job_id]) == 0
        assert "status:  created" in capsys.readouterr().out
        assert jobs_cli.main([*ws, "list"]) == 0
        assert job_id in capsys.readouterr().out
        assert jobs_cli.main([*ws, "show", "--job", job_id]) == 0
        assert json.loads(capsys.readouterr().out)["input"]["topic"] == "How Git works"

    def test_topic_only(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        ws = ["--workspace", str(tmp_path)]
        assert jobs_cli.main([*ws, "create", "--topic", "How Git works"]) == 0
        assert capsys.readouterr().out.strip()

    def test_bad_input_exits_2_and_creates_no_job(
        self, tmp_path: Path, write_file: WriteFile, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ws = ["--workspace", str(tmp_path / "w")]
        good = write_file("a.txt", SOURCE_TEXT)
        pdf = write_file("a.pdf", "x")
        base = [*ws, "create", "--topic", "t"]
        assert jobs_cli.main([*ws, "create"]) == 2  # no topic
        assert jobs_cli.main([*base, "--transcript", str(tmp_path / "missing.txt")]) == 2
        assert jobs_cli.main([*base, "--transcript", str(pdf)]) == 2
        assert jobs_cli.main([*base, "--transcript", str(good), "--transcript", str(pdf)]) == 2
        assert jobs_cli.main([*base, "--style", "nope"]) == 2
        assert jobs_cli.main([*ws, "status", "--job", "missing"]) == 2
        assert jobs_cli.main([*ws, "status", "--job", "../etc"]) == 2
        assert "ERROR" in capsys.readouterr().err
        assert JobStore(tmp_path / "w").list_jobs() == []

    def test_params_file_with_labelled_transcripts_avoids_shell_quoting(
        self, tmp_path: Path, write_file: WriteFile, capsys: pytest.CaptureFixture[str]
    ) -> None:
        nasty = 'How "TCP" works & `calc` $(rm -rf /) ; <x>'
        a, b = write_file("a.txt", SOURCE_TEXT), write_file("b.srt", SOURCE_SRT)
        params = tmp_path / "params.json"
        params.write_text(
            json.dumps(
                {
                    "topic": nasty,
                    "audience": "advanced",
                    "duration": "5",
                    "transcripts": [
                        {"path": str(a), "label": "Creator A", "url": "https://example.com/a"},
                        str(b),
                    ],
                }
            )
        )
        ws = ["--workspace", str(tmp_path / "w")]
        assert jobs_cli.main([*ws, "create", "--params-file", str(params)]) == 0
        job_id = capsys.readouterr().out.strip()
        manifest = JobStore(tmp_path / "w").load(job_id)
        assert manifest.input.topic == nasty
        assert manifest.audience.value == "advanced" and manifest.target_duration_minutes == 5
        first, second = manifest.input.sources
        assert (first.label, first.reference_url) == ("Creator A", "https://example.com/a")
        assert second.label == "b"
        assert "$" not in job_id and " " not in job_id

    def test_params_file_validation(self, tmp_path: Path) -> None:
        ws = ["--workspace", str(tmp_path / "w")]
        bad = tmp_path / "bad.json"
        contents = [
            '{"topic": "x", "evil": 1}',
            "[1]",
            "{",
            "{}",
            '{"topic": "x", "audience": "guru"}',
            '{"topic": "x", "transcripts": "a.txt"}',
            '{"topic": "x", "transcripts": [{"label": "no path"}]}',
            '{"topic": "x", "transcripts": [{"path": "a.txt", "evil": 1}]}',
            '{"topic": "x", "transcripts": [42]}',
        ]
        for content in contents:
            bad.write_text(content)
            assert jobs_cli.main([*ws, "create", "--params-file", str(bad)]) == 2, content
        assert jobs_cli.main([*ws, "create", "--params-file", str(tmp_path / "nope.json")]) == 2


class TestStageCommands:
    def test_stage_cli_runs_reuses_and_enforces_order(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ws = ["--workspace", str(tmp_path)]
        jobs_cli.main([*ws, "create", "--topic", "How Git works"])
        job_id = capsys.readouterr().out.strip()

        assert script.main(["--job", job_id, *ws]) == 2  # upstream not ready
        assert acquire_main(["--job", job_id, *ws]) == 0
        assert "acquire: executed" in capsys.readouterr().out
        assert acquire_main(["--job", job_id, *ws]) == 0
        assert "acquire: reused" in capsys.readouterr().out
        assert acquire_main(["--job", job_id, "--force", *ws]) == 0
        assert "acquire: executed" in capsys.readouterr().out
        assert knowledge.main(["--job", job_id, *ws]) == 0
        assert JobStore(tmp_path).load(job_id).status is JobStatus.EXTRACTING_KNOWLEDGE


class TestPythonDashM:
    def test_jenkins_style_stage_by_stage_run(self, tmp_path: Path, write_file: WriteFile) -> None:
        a, b = write_file("a.txt", SOURCE_TEXT), write_file("b.srt", SOURCE_SRT)
        created = python_m(
            "pipeline.jobs", "create", "--topic", "How binary search works",
            "--transcript", str(a), "--transcript", str(b),
            workspace=tmp_path,
        )  # fmt: skip
        assert created.returncode == 0, created.stderr
        job_id = created.stdout.strip()

        for stage in ("acquire", "knowledge", "research", "lesson", "script"):
            result = python_m(f"pipeline.{stage}", "--job", job_id, workspace=tmp_path)
            assert result.returncode == 0, f"{stage}: {result.stderr}"
            assert f"{stage}:" in result.stdout

        rerun = python_m("pipeline.script", "--job", job_id, workspace=tmp_path)
        assert rerun.returncode == 0 and "script: reused" in rerun.stdout
        assert "[JOB=" in rerun.stderr and "[STAGE=script]" in rerun.stderr

        premature = python_m("pipeline.render", "--job", job_id, workspace=tmp_path)
        assert premature.returncode == 2 and "needs" in premature.stderr

    @pytest.mark.ffmpeg
    def test_full_run_via_cli(self, tmp_path: Path, write_file: WriteFile) -> None:
        a = write_file("a.txt", SOURCE_TEXT)
        created = python_m(
            "pipeline.jobs", "create", "--topic", "How a CPU works", "--transcript", str(a),
            workspace=tmp_path,
        )  # fmt: skip
        job_id = created.stdout.strip()
        result = python_m("pipeline.run", "--job", job_id, workspace=tmp_path)
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "jobs" / job_id / "final" / "video.mp4").is_file()
        status = python_m("pipeline.jobs", "status", "--job", job_id, workspace=tmp_path)
        assert "status:  awaiting_review" in status.stdout
