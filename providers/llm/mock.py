"""Mock LLM: deterministic canned output, strictly validated like a real provider's would be."""

from __future__ import annotations

import json
from typing import Any

from core.exceptions import ProviderError
from core.providers import (
    LLMProvider,
    LLMResponse,
    StructuredResponse,
    T,
    Usage,
    parse_structured,
)
from providers import mock_data


def _topic_from_prompt(prompt: str, default: str = "TCP") -> str:
    for line in prompt.splitlines():
        if line.startswith("TOPIC:"):
            return line.split(":", 1)[1].strip() or default
    return default


def _audience_from_prompt(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("AUDIENCE:"):
            return line.split(":", 1)[1].strip() or "beginner"
    return "beginner"


class MockLLMProvider(LLMProvider):
    name = "mock"

    def __init__(
        self,
        model: str = "mock-llm-1",
        *,
        raw_overrides: dict[str, str] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self.model = model
        self.calls: list[str] = []  # schema names (or "text") of every call made
        self.prompts: list[str] = []
        self._raw_overrides = raw_overrides or {}
        self._fail_on = fail_on or set()

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append("text")
        text = "[mock llm response]"
        return LLMResponse(text, Usage(tokens_in=len(prompt) // 4, tokens_out=len(text) // 4))

    def _canned(self, schema_name: str, prompt: str) -> dict[str, Any]:
        topic = _topic_from_prompt(prompt)
        if schema_name == "KnowledgeBase":
            return mock_data.knowledge(topic)
        if schema_name == "LessonPlan":
            return mock_data.lesson(topic, _audience_from_prompt(prompt))
        if schema_name == "Script":
            return mock_data.script()
        if schema_name == "ScenePlan":
            return mock_data.scenes()
        raise ProviderError(f"MockLLMProvider has no canned output for {schema_name}")

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> StructuredResponse[T]:
        name = schema.__name__
        self.calls.append(name)
        self.prompts.append(prompt)
        if name in self._fail_on:
            raise ProviderError(f"MockLLMProvider configured to fail on {name}")
        raw = self._raw_overrides.get(name) or json.dumps(self._canned(name, prompt))
        value = parse_structured(raw, schema)
        usage = Usage(tokens_in=len(prompt) // 4, tokens_out=len(raw) // 4)
        return StructuredResponse(value=value, usage=usage)
