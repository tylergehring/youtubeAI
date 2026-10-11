"""OpenAI provider tests. A fake client stands in for the SDK, so no network or key is needed."""

from types import SimpleNamespace
from typing import Any

import openai
import pytest

try:  # the SDK's HTTP library is named httpx2 in newer releases
    import httpx2 as httpx
except ImportError:  # pragma: no cover - older SDKs
    import httpx

from core.config import OpenAISettings, Settings
from core.exceptions import ConfigError, ProviderError, StructuredOutputError
from core.models.content import Strict
from core.providers import Usage
from providers.llm.openai_provider import JSON_INSTRUCTION, OpenAILLMProvider, _accepts_temperature
from providers.registry import build_providers

SECRET = "sk-test-SECRETSECRETSECRETSECRET1234567890"


class Tiny(Strict):
    a: int


def reply(
    text: str | None,
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
    finish: str = "stop",
    refusal: str | None = None,
) -> Any:
    message = SimpleNamespace(content=text, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeClient:
    """Plays back queued replies (or raises queued exceptions) and records every request."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(retrieve=self._retrieve)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _retrieve(self, model: str) -> Any:
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(id=model)


def provider(*replies: Any, model: str = "gpt-4.1-mini", **settings: Any) -> tuple[Any, FakeClient]:
    client = FakeClient(*replies)
    return OpenAILLMProvider(model, settings=OpenAISettings(**settings), client=client), client


def api_error(cls: type[openai.APIStatusError], status: int, **body: Any) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(f"error with key {SECRET}", response=response, body=body or None)


class TestConstruction:
    def test_missing_key_is_a_clear_config_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            OpenAILLMProvider("gpt-4.1-mini")

    def test_builds_a_real_client_from_the_environment_without_calling_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", SECRET)
        assert OpenAILLMProvider("gpt-4.1-mini").model == "gpt-4.1-mini"

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("gpt-4.1-mini", True),
            ("gpt-4o", True),
            ("gpt-5.4-mini", False),
            ("o3-mini", False),
            ("gpt-6-luna", False),
            ("gpt-5.2-chat-latest", True),
        ],
    )
    def test_temperature_auto_detection(self, model: str, expected: bool) -> None:
        assert _accepts_temperature(model) is expected


class TestGenerate:
    def test_request_shape_and_usage(self) -> None:
        p, client = provider(reply("hello", 12, 3))
        result = p.generate("Say hi", system="Be brief", temperature=0.3)
        assert result.text == "hello"
        assert (result.usage.tokens_in, result.usage.tokens_out, result.usage.requests) == (
            12,
            3,
            1,
        )
        call = client.calls[0]
        assert call["model"] == "gpt-4.1-mini"
        assert call["messages"] == [
            {"role": "system", "content": "Be brief"},
            {"role": "user", "content": "Say hi"},
        ]
        assert call["temperature"] == 0.3
        assert call["max_completion_tokens"] == 8192
        assert "response_format" not in call

    def test_temperature_omitted_for_reasoning_models_and_overridable(self) -> None:
        p, client = provider(reply("x"), model="gpt-5.4-mini")
        p.generate("hi")
        assert "temperature" not in client.calls[0]
        p2, client2 = provider(reply("x"), model="gpt-5.4-mini", send_temperature=True)
        p2.generate("hi")
        assert "temperature" in client2.calls[0]

    def test_max_tokens_setting_and_override(self) -> None:
        p, client = provider(reply("x"), reply("x"), max_output_tokens=500)
        p.generate("a")
        p.generate("b", max_tokens=50)
        assert [c["max_completion_tokens"] for c in client.calls] == [500, 50]


class TestStructured:
    def test_success_uses_json_mode_and_validates(self) -> None:
        p, client = provider(reply('{"a": 7}', 200, 10))
        result = p.generate_structured("give json", Tiny, system="You are precise.")
        assert result.value.a == 7
        assert (result.usage.tokens_in, result.usage.tokens_out, result.usage.requests) == (
            200,
            10,
            1,
        )
        call = client.calls[0]
        assert call["response_format"] == {"type": "json_object"}
        assert call["messages"][0]["content"] == f"You are precise.\n\n{JSON_INSTRUCTION}"
        assert "JSON" in call["messages"][0]["content"]  # JSON mode requires the word JSON

    def test_json_instruction_present_even_without_a_system_prompt(self) -> None:
        p, client = provider(reply('{"a": 1}'))
        p.generate_structured("x", Tiny)
        assert client.calls[0]["messages"][0] == {"role": "system", "content": JSON_INSTRUCTION}

    def test_markdown_fence_is_tolerated(self) -> None:
        p, _ = provider(reply('```json\n{"a": 2}\n```'))
        assert p.generate_structured("x", Tiny).value.a == 2

    def test_invalid_reply_is_repaired_with_feedback_and_usage_is_summed(self) -> None:
        p, client = provider(reply('{"a": "oops"}', 100, 10), reply('{"a": 5}', 150, 12))
        result = p.generate_structured("x", Tiny)
        assert result.value.a == 5
        assert (result.usage.tokens_in, result.usage.tokens_out, result.usage.requests) == (
            250,
            22,
            2,
        )
        repair = client.calls[1]["messages"]
        assert repair[-2] == {"role": "assistant", "content": '{"a": "oops"}'}
        assert "did not match the required schema" in repair[-1]["content"]
        assert "Tiny" in repair[-1]["content"]  # the validation error is shown to the model

    def test_gives_up_after_repair_attempts_and_reports_all_billed_usage(self) -> None:
        p, client = provider(*(reply("not json", 100, 5) for _ in range(3)), repair_attempts=2)
        with pytest.raises(StructuredOutputError, match="after 3 attempt") as info:
            p.generate_structured("x", Tiny)
        assert len(client.calls) == 3
        assert isinstance(info.value.usage, Usage)
        assert (info.value.usage.tokens_in, info.value.usage.requests) == (300, 3)

    def test_repair_can_be_disabled(self) -> None:
        p, client = provider(reply("nope"), repair_attempts=0)
        with pytest.raises(StructuredOutputError, match="after 1 attempt"):
            p.generate_structured("x", Tiny)
        assert len(client.calls) == 1

    def test_api_failure_during_repair_keeps_earlier_usage(self) -> None:
        p, _ = provider(reply("bad", 100, 5), api_error(openai.AuthenticationError, 401))
        with pytest.raises(ProviderError) as info:
            p.generate_structured("x", Tiny)
        assert isinstance(info.value.usage, Usage) and info.value.usage.tokens_in == 100

    def test_truncated_output_is_not_parsed_and_is_billed(self) -> None:
        p, _ = provider(reply('{"a": 1', 100, 8192, finish="length"))
        with pytest.raises(ProviderError, match="max_output_tokens") as info:
            p.generate_structured("x", Tiny)
        assert isinstance(info.value.usage, Usage) and info.value.usage.tokens_out == 8192

    def test_refusal_and_empty_choices(self) -> None:
        p, _ = provider(reply(None, refusal="I can't help with that"))
        with pytest.raises(ProviderError, match="refused"):
            p.generate_structured("x", Tiny)
        empty = SimpleNamespace(
            choices=[], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0)
        )
        p2, _ = provider(empty)
        with pytest.raises(ProviderError, match="no choices"):
            p2.generate_structured("x", Tiny)

    def test_empty_content_is_a_structured_output_error(self) -> None:
        p, _ = provider(reply(None), reply(None), reply(None))
        with pytest.raises(StructuredOutputError):
            p.generate_structured("x", Tiny)


class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (api_error(openai.AuthenticationError, 401), "rejected the API key"),
            (api_error(openai.PermissionDeniedError, 403), "denied access"),
            (api_error(openai.NotFoundError, 404), "was not found"),
            (
                api_error(openai.RateLimitError, 429, code="insufficient_quota"),
                "insufficient_quota",
            ),
            (api_error(openai.RateLimitError, 429, code="rate_limit_exceeded"), "rate limit"),
            (api_error(openai.BadRequestError, 400), r"rejected the request \(400\)"),
            (api_error(openai.InternalServerError, 500), "HTTP 500"),
        ],
    )
    def test_messages_are_actionable_and_never_contain_the_key(
        self, error: Exception, message: str
    ) -> None:
        p, _ = provider(error)
        with pytest.raises(ProviderError, match=message) as info:
            p.generate("hi")
        assert SECRET not in str(info.value)
        if "401" in message or "rejected the API key" in message:
            assert "OPENAI_API_KEY" in str(info.value)

    def test_network_errors(self) -> None:
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        p, _ = provider(openai.APITimeoutError(request), openai.APIConnectionError(request=request))
        with pytest.raises(ProviderError, match="timed out"):
            p.generate("hi")
        with pytest.raises(ProviderError, match="Could not reach"):
            p.generate("hi")


class TestHealthcheck:
    def test_success_makes_no_chat_request(self) -> None:
        p, client = provider(None)  # the queued item is only consumed by models.retrieve
        status = p.healthcheck()
        assert "gpt-4.1-mini" in status and "no tokens used" in status
        assert client.calls == []

    def test_failure_is_translated(self) -> None:
        p, _ = provider(api_error(openai.AuthenticationError, 401))
        with pytest.raises(ProviderError, match="rejected the API key"):
            p.healthcheck()


class TestRegistryAndConfig:
    def test_selecting_openai_builds_the_provider(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", SECRET)
        chosen = settings.model_copy(deep=True)
        chosen.providers.selected.llm = "openai"
        llm = build_providers(chosen).llm
        assert isinstance(llm, OpenAILLMProvider) and llm.model == "gpt-4.1-mini"

    def test_selecting_openai_without_a_key_fails_fast(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        chosen = settings.model_copy(deep=True)
        chosen.providers.selected.llm = "openai"
        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            build_providers(chosen)

    def test_missing_model_mapping_is_a_config_error(self, settings: Settings) -> None:
        chosen = settings.model_copy(deep=True)
        chosen.providers.selected.llm = "someone-else"
        with pytest.raises(ConfigError, match="providers.models.llm.someone-else"):
            chosen.providers.model_for("llm")

    def test_default_openai_model_has_pricing_so_costs_are_tracked(
        self, settings: Settings
    ) -> None:
        chosen = settings.model_copy(deep=True)
        chosen.providers.selected.llm = "openai"
        price = settings.providers.pricing["openai"][chosen.providers.model_for("llm")]
        assert price.input_per_1k_tokens > 0 and price.output_per_1k_tokens > 0

    def test_env_var_switches_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.config import load_settings

        s = load_settings(
            env={"VIDGEN__providers__selected__llm": "openai"}, load_dotenv_file=False
        )
        assert s.providers.selected.llm == "openai"


def test_usage_addition() -> None:
    total = Usage(tokens_in=1, tokens_out=2, characters=3, requests=1) + Usage(
        tokens_in=10, tokens_out=20, characters=30, requests=2
    )
    assert (total.tokens_in, total.tokens_out, total.characters, total.requests) == (11, 22, 33, 3)
