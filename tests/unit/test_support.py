import logging
from pathlib import Path

import pytest

from core.config import PriceEntry, load_settings
from core.costs import CostTracker, estimate_cost_usd
from core.exceptions import ConfigError
from core.jobs import JobStore
from core.logging import RedactingFormatter, job_log_file, redact
from core.providers import Usage
from tests.conftest import MakeJob


class TestConfig:
    def test_loads_repo_config(self, tmp_path: Path) -> None:
        s = load_settings(env={"VIDGEN_WORKSPACE_DIR": str(tmp_path)}, load_dotenv_file=False)
        assert s.workspace_dir == tmp_path
        assert s.video.width == 1280 and s.defaults.style == "technical_explainer"
        assert s.style("technical_explainer").palette
        assert s.providers.selected.llm == "mock"

    def test_env_overrides_are_typed(self) -> None:
        s = load_settings(
            env={"VIDGEN__video__width": "640", "VIDGEN__qa__semantic_enabled": "true"},
            load_dotenv_file=False,
        )
        assert s.video.width == 640 and s.qa.semantic_enabled is True

    def test_env_override_names_are_case_insensitive(self) -> None:
        # Windows upper-cases environment variable names.
        s = load_settings(env={"VIDGEN__VIDEO__WIDTH": "800"}, load_dotenv_file=False)
        assert s.video.width == 800

    def test_allowed_dirs_can_be_set_from_a_json_style_env_value(self) -> None:
        s = load_settings(
            env={"VIDGEN__ingest__allowed_dirs": '["C:/jenkins/ws/transcripts"]'},
            load_dotenv_file=False,
        )
        assert s.ingest.allowed_dirs == [Path("C:/jenkins/ws/transcripts")]

    def test_relative_workspace_resolves_against_project_root(self) -> None:
        s = load_settings(env={}, load_dotenv_file=False)
        assert s.workspace_dir.is_absolute()

    def test_invalid_value_is_config_error(self) -> None:
        with pytest.raises(ConfigError):
            load_settings(env={"VIDGEN__video__fps": "0"}, load_dotenv_file=False)

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ConfigError):
            load_settings(env={"VIDGEN__video__bogus": "1"}, load_dotenv_file=False)

    def test_missing_config_dir(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_settings(tmp_path, env={}, load_dotenv_file=False)

    def test_unknown_style(self) -> None:
        with pytest.raises(ConfigError, match="Unknown style"):
            load_settings(env={}, load_dotenv_file=False).style("nope")


class TestLogging:
    def test_redacts_known_patterns_and_env_values(self) -> None:
        env = {"OPENAI_API_KEY": "abcd1234efgh5678"}
        assert "abcd1234efgh5678" not in redact("key=abcd1234efgh5678", env)
        assert "sk-" not in redact("auth sk-ABCDEFGHIJKLMNOPQRSTUV", {})
        assert "hunter2" not in redact("password=hunter2", {})
        assert "Bearer" in redact("Authorization: Bearer abcdefghijklmnopqrstuv", {})
        assert "abcdefghijklmnopqrstuv" not in redact("Bearer abcdefghijklmnopqrstuv", {})
        assert redact("plain message", {}) == "plain message"

    def test_format_has_job_stage_severity(self) -> None:
        from core.logging import DATE_FORMAT, LOG_FORMAT

        record = logging.LogRecord("vidgen.x", logging.INFO, "f", 1, "hello", None, None)
        record.job_id, record.stage = "J1", "SCRIPT"
        line = RedactingFormatter(LOG_FORMAT, DATE_FORMAT).format(record)
        assert "[JOB=J1] [STAGE=SCRIPT] [INFO] hello" in line
        assert line.startswith("[20")

    def test_job_log_file_captures_and_detaches(self, tmp_path: Path) -> None:
        from core.logging import configure_logging, get_logger

        configure_logging("INFO")
        log_file = tmp_path / "logs" / "p.log"
        with job_log_file(log_file):
            get_logger("J2", "VOICE").info("token=supersecretvalue done")
        get_logger("J2", "VOICE").info("after")
        text = log_file.read_text()
        assert "[JOB=J2] [STAGE=VOICE]" in text and "supersecretvalue" not in text
        assert "after" not in text


class TestCosts:
    def test_estimate(self) -> None:
        price = PriceEntry(input_per_1k_tokens=1.0, output_per_1k_tokens=2.0, per_1k_characters=0.5)
        usage = Usage(tokens_in=1000, tokens_out=500, characters=2000, requests=0)
        assert estimate_cost_usd(price, usage) == pytest.approx(1.0 + 1.0 + 1.0)
        assert estimate_cost_usd(None, usage) == 0.0

    def test_tracker_accumulates_in_job_cost_json(self, store: JobStore, make_job: MakeJob) -> None:
        job = make_job()
        paths = store.paths(job.job_id)
        pricing = {"p": {"m": PriceEntry(input_per_1k_tokens=1.0)}}
        tracker = CostTracker(job.job_id, paths, pricing)
        tracker.record(stage="script", provider="p", model="m", usage=Usage(tokens_in=2000))
        tracker.record(stage="scenes", provider="p", model="m", usage=Usage(tokens_in=1000))
        tracker.record(stage="voice", provider="q", model="z", usage=Usage(characters=10))

        cost = tracker.load()
        assert cost.total_estimated_cost_usd == pytest.approx(3.0)
        assert [r.stage for r in cost.records] == ["script", "scenes", "voice"]
        assert "no pricing" in cost.records[2].note
        assert paths.cost_file.is_file()
