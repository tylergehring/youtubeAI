"""OpenAI LLM provider (Chat Completions).

Structured output strategy: JSON mode plus strict Pydantic validation, with a bounded number of
repair attempts that show the model exactly what was wrong. OpenAI's native strict
`json_schema` mode cannot express several of our models (tuple positions, some constraints), and
validating ourselves keeps behaviour identical across providers.

Every attempt's token usage is summed and returned (or attached to the raised error), so the
job cost reflects what OpenAI actually billed, including retries.
"""

from __future__ import annotations

import os
from typing import Any

import openai

from core.config import OpenAISettings
from core.exceptions import ConfigError, ProviderError, StructuredOutputError
from core.logging import redact
from core.providers import (
    LLMProvider,
    LLMResponse,
    StructuredResponse,
    T,
    Usage,
    parse_structured,
)

API_KEY_ENV = "OPENAI_API_KEY"
JSON_INSTRUCTION = (
    "Respond with a single valid JSON object and nothing else: no prose, no markdown fences."
)
_NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-6")
_MAX_ERROR_CHARS = 1500


def _accepts_temperature(model: str) -> bool:
    """Reasoning-style models reject a custom temperature; chat-tuned variants accept it."""
    name = model.lower()
    return "chat" in name or not name.startswith(_NO_TEMPERATURE_PREFIXES)


class OpenAILLMProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str,
        *,
        settings: OpenAISettings | None = None,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._settings = settings or OpenAISettings()
        if client is None:
            key = api_key or os.environ.get(API_KEY_ENV)
            if not key:
                raise ConfigError(
                    f"{API_KEY_ENV} is not set. Put it in .env (see .env.example) or the "
                    "environment; never in a config file."
                )
            client = openai.OpenAI(
                api_key=key,
                base_url=self._settings.base_url,
                timeout=self._settings.timeout_s,
                max_retries=self._settings.max_retries,
            )
        self._client = client
        send = self._settings.send_temperature
        self._send_temperature = _accepts_temperature(model) if send is None else send

    # ------------------------------------------------------------ public API

    def healthcheck(self) -> str:
        """Free check: authenticates and confirms this account can use the model."""
        try:
            info = self._client.models.retrieve(self.model)
        except openai.OpenAIError as exc:
            raise self._translate(exc) from exc
        return f"openai: API key accepted and model '{info.id}' is available (no tokens used)"

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        messages = self._messages(system, prompt)
        text, usage = self._chat(messages, temperature, max_tokens, json_mode=False)
        return LLMResponse(text, usage)

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> StructuredResponse[T]:
        system_text = f"{system}\n\n{JSON_INSTRUCTION}" if system else JSON_INSTRUCTION
        messages = self._messages(system_text, prompt)
        total = Usage(requests=0)
        last_error: StructuredOutputError | None = None
        attempts = 1 + self._settings.repair_attempts

        for attempt in range(1, attempts + 1):
            try:
                text, usage = self._chat(messages, temperature, max_tokens, json_mode=True)
            except ProviderError as exc:
                spent = total + exc.usage if isinstance(exc.usage, Usage) else total
                exc.usage = spent if spent.requests > 0 else None
                raise
            total = total + usage
            try:
                return StructuredResponse(value=parse_structured(text, schema), usage=total)
            except StructuredOutputError as exc:
                last_error = exc
                if attempt == attempts:
                    break
                messages = [
                    *messages,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply did not match the required schema. Problems:\n"
                            f"{str(exc)[:_MAX_ERROR_CHARS]}\n\n"
                            "Return the complete corrected JSON object only."
                        ),
                    },
                ]

        raise StructuredOutputError(
            f"OpenAI output still did not match {schema.__name__} after {attempts} attempt(s): "
            f"{last_error}",
            usage=total,
        )

    # ------------------------------------------------------------ internals

    @staticmethod
    def _messages(system: str | None, prompt: str) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": system}] if system else []
        return [*messages, {"role": "user", "content": prompt}]

    def _chat(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        *,
        json_mode: bool,
    ) -> tuple[str, Usage]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens or self._settings.max_output_tokens,
        }
        if self._send_temperature:
            params["temperature"] = temperature
        if json_mode:
            params["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**params)
        except openai.OpenAIError as exc:
            raise self._translate(exc) from exc

        usage = Usage(
            tokens_in=getattr(response.usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(response.usage, "completion_tokens", 0) or 0,
            requests=1,
        )
        if not response.choices:
            raise ProviderError("OpenAI returned no choices.", usage=usage)
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ProviderError(
                f"OpenAI refused the request: {choice.message.refusal}", usage=usage
            )
        if choice.finish_reason == "length":
            raise ProviderError(
                "OpenAI output was cut off at the token limit. Raise "
                "providers.openai.max_output_tokens in config/providers.yaml.",
                usage=usage,
            )
        return choice.message.content or "", usage

    def _translate(self, exc: openai.OpenAIError) -> ProviderError:
        """Turn SDK errors into short, actionable messages that never include the API key."""
        if isinstance(exc, openai.AuthenticationError):
            return ProviderError(f"OpenAI rejected the API key (401). Check {API_KEY_ENV} in .env.")
        if isinstance(exc, openai.PermissionDeniedError):
            return ProviderError(
                f"OpenAI denied access (403); this key or project may not be allowed to use "
                f"model '{self.model}'."
            )
        if isinstance(exc, openai.NotFoundError):
            return ProviderError(
                f"OpenAI model '{self.model}' was not found or is not available to this key. "
                "Change providers.models.llm.openai in config/providers.yaml."
            )
        if isinstance(exc, openai.RateLimitError):
            if getattr(exc, "code", None) == "insufficient_quota":
                return ProviderError(
                    "OpenAI reports insufficient_quota: the account has no remaining credit or "
                    "has hit its spend limit. Add credit at platform.openai.com (Billing)."
                )
            return ProviderError("OpenAI rate limit hit and retries were exhausted; retry later.")
        if isinstance(exc, openai.APITimeoutError):
            return ProviderError(
                f"OpenAI request timed out after {self._settings.timeout_s:.0f}s "
                "(providers.openai.timeout_s)."
            )
        if isinstance(exc, openai.APIConnectionError):
            return ProviderError("Could not reach the OpenAI API (network or proxy problem).")
        if isinstance(exc, openai.BadRequestError):
            return ProviderError(f"OpenAI rejected the request (400): {redact(exc.message)}")
        if isinstance(exc, openai.APIStatusError):
            return ProviderError(
                f"OpenAI API error (HTTP {exc.status_code}): {redact(exc.message)}"
            )
        return ProviderError(f"OpenAI client error: {type(exc).__name__}")
