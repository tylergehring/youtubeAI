"""Provider interfaces. The rest of the application depends only on these abstractions.

Concrete implementations (mock now, real later) live under providers/<kind>/ and are
selected by name through providers.registry.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from core.exceptions import StructuredOutputError
from core.models.content import Claim, ClaimVerification

T = TypeVar("T", bound=BaseModel)


class Usage(BaseModel):
    """What an external call consumed. Cost is derived from this plus the configured pricing."""

    tokens_in: int = 0
    tokens_out: int = 0
    characters: int = 0
    requests: int = 1

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            characters=self.characters + other.characters,
            requests=self.requests + other.requests,
        )


# ---------------------------------------------------------------- LLM


@dataclass
class LLMResponse:
    text: str
    usage: Usage


@dataclass
class StructuredResponse(Generic[T]):  # noqa: UP046 - PEP 695 syntax needs Python 3.12
    value: T
    usage: Usage


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


def parse_structured(raw: str, schema: type[T]) -> T:
    """Strictly validate raw model output against a schema.

    Only a surrounding markdown code fence is tolerated. Anything else that does not
    validate raises StructuredOutputError; callers must not silently continue.
    """
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return schema.model_validate_json(text)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"Model output does not match {schema.__name__}: {exc.error_count()} error(s): {exc}"
        ) from exc


class LLMProvider(ABC):
    name: str
    model: str

    def healthcheck(self) -> str:
        """Verify credentials/model access WITHOUT a billable call; return a status line."""
        return f"{self.name}: no connectivity check available"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> StructuredResponse[T]:
        """Return a validated instance of `schema` or raise StructuredOutputError."""


# ---------------------------------------------------------------- TTS


@dataclass
class TTSResult:
    path: Path
    duration_s: float
    voice: str
    metadata: dict[str, Any]
    usage: Usage


class TTSProvider(ABC):
    name: str
    model: str
    default_voice: str

    @abstractmethod
    def synthesize(self, text: str, out_path: Path, *, voice: str | None = None) -> TTSResult:
        """Write narration audio to out_path and report its true duration."""


# ---------------------------------------------------------------- images


@dataclass
class ImageResult:
    path: Path
    usage: Usage


class ImageProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate(self, prompt: str, out_path: Path, *, width: int, height: int) -> ImageResult: ...


# ---------------------------------------------------------------- research


@dataclass
class ResearchResult:
    verification: ClaimVerification
    usage: Usage


class ResearchProvider(ABC):
    name: str

    @abstractmethod
    def verify_claim(self, claim: Claim, topic: str) -> ResearchResult:
        """Gather evidence for one claim and classify it."""
