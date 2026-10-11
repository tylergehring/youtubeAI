"""Pipeline stages driven through the real OpenAI provider code and a scripted fake client."""

import json
import re
from typing import Any

import pytest

from core.config import Settings
from core.exceptions import StageError
from core.jobs import JobStore
from core.models.manifest import StageName as S
from core.models.manifest import StageState
from core.models.media import JobCost
from pipeline import artifacts
from pipeline.run import run_pipeline
from providers.llm.mock import MockLLMProvider
from providers.llm.openai_provider import OpenAILLMProvider
from providers.registry import ProviderSet
from tests.conftest import SOURCE_TEXT, MakeJob
from tests.unit.test_openai_provider import FakeClient, reply

IN_TOKENS, OUT_TOKENS = 1000, 500
# gpt-4.1-mini: $0.0004 / 1k in, $0.0016 / 1k out (config/providers.yaml)
COST_PER_CALL = IN_TOKENS / 1000 * 0.0004 + OUT_TOKENS / 1000 * 0.0016


class ScriptedClient(FakeClient):
    """Answers each structured request with canned JSON for whichever schema the prompt names."""

    def __init__(self, garbage_for: str | None = None) -> None:
        super().__init__()
        self.garbage_for = garbage_for
        self.canned = MockLLMProvider()

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        prompt = next(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        name = re.search(r"matching the (\w+) schema", prompt)
        assert name, "unexpected prompt"
        if name.group(1) == self.garbage_for:
            return reply("this is not json", IN_TOKENS, OUT_TOKENS)
        payload = self.canned._canned(name.group(1), prompt)
        return reply(json.dumps(payload), IN_TOKENS, OUT_TOKENS)


def use_openai(providers: ProviderSet, settings: Settings, client: ScriptedClient) -> None:
    providers.llm = OpenAILLMProvider(
        "gpt-4.1-mini", settings=settings.providers.openai, client=client
    )


def test_stages_run_through_the_openai_provider_and_costs_are_tracked(
    store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
) -> None:
    client = ScriptedClient()
    use_openai(providers, settings, client)
    job = make_job("Why Americans are running out of money", [("a.txt", SOURCE_TEXT)])
    run_pipeline(store, job.job_id, settings, providers, end=S.SCENES)

    assert len(client.calls) == 4  # knowledge, lesson, script, scenes
    assert all(c["response_format"] == {"type": "json_object"} for c in client.calls)
    assert all(c["model"] == "gpt-4.1-mini" for c in client.calls)
    first_prompt = client.calls[0]["messages"][-1]["content"]
    assert "Why Americans are running out of money" in first_prompt
    assert "TCP basically makes sure" in first_prompt  # the uploaded transcript was sent

    cost = JobCost.model_validate_json(store.paths(job.job_id).cost_file.read_text())
    openai_records = [r for r in cost.records if r.provider == "openai"]
    assert [r.stage for r in openai_records] == ["knowledge", "lesson", "script", "scenes"]
    assert all(r.model == "gpt-4.1-mini" and r.tokens_in == IN_TOKENS for r in openai_records)
    assert cost.total_estimated_cost_usd == pytest.approx(4 * COST_PER_CALL, abs=1e-5)


def test_rerun_after_success_makes_no_further_billable_calls(
    store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
) -> None:
    client = ScriptedClient()
    use_openai(providers, settings, client)
    job = make_job()
    run_pipeline(store, job.job_id, settings, providers, end=S.SCENES)
    calls = len(client.calls)
    run_pipeline(store, job.job_id, settings, providers, end=S.SCENES)
    assert len(client.calls) == calls


def test_failed_structured_output_stops_the_stage_but_the_spend_is_still_recorded(
    store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
) -> None:
    client = ScriptedClient(garbage_for="LessonPlan")
    use_openai(providers, settings, client)
    job = make_job()
    with pytest.raises(StageError, match="StructuredOutputError"):
        run_pipeline(store, job.job_id, settings, providers, end=S.SCENES)

    manifest = store.load(job.job_id)
    assert manifest.failed_stage is S.LESSON
    assert manifest.stage(S.KNOWLEDGE).state is StageState.SUCCEEDED
    assert manifest.stage(S.SCRIPT).state is StageState.PENDING
    assert not artifacts.lesson_json(store.paths(job.job_id)).exists()

    cost = JobCost.model_validate_json(store.paths(job.job_id).cost_file.read_text())
    failed = [r for r in cost.records if r.note.startswith("failed call")]
    assert len(failed) == 1 and failed[0].stage == "lesson"
    assert failed[0].requests == 3  # first try plus two repair attempts, all billed
    assert failed[0].tokens_in == 3 * IN_TOKENS
    assert cost.total_estimated_cost_usd == pytest.approx(4 * COST_PER_CALL, abs=1e-5)  # 1 + 3

    # Fixing the model's behaviour and rerunning resumes at the lesson stage only.
    use_openai(providers, settings, ScriptedClient())
    run_pipeline(store, job.job_id, settings, providers, end=S.LESSON)
    assert store.load(job.job_id).stage(S.KNOWLEDGE).attempts == 1


def test_api_errors_fail_the_stage_with_an_actionable_message(
    store: JobStore, settings: Settings, providers: ProviderSet, make_job: MakeJob
) -> None:
    import openai

    try:
        import httpx2 as httpx
    except ImportError:  # older SDKs
        import httpx

    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    error = openai.RateLimitError(
        "quota", response=httpx.Response(429, request=request), body={"code": "insufficient_quota"}
    )
    client = FakeClient(error)
    providers.llm = OpenAILLMProvider("gpt-4.1-mini", client=client)
    job = make_job()
    with pytest.raises(StageError, match="insufficient_quota"):
        run_pipeline(store, job.job_id, settings, providers, end=S.KNOWLEDGE)
    manifest = store.load(job.job_id)
    assert manifest.failed_stage is S.KNOWLEDGE and "Add credit" in (manifest.error or "")
    assert not store.paths(job.job_id).cost_file.exists()  # nothing was billed, nothing recorded
