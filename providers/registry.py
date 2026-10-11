"""Select concrete providers by name from configuration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.config import Settings
from core.exceptions import ConfigError
from core.providers import (
    ImageProvider,
    LLMProvider,
    ResearchProvider,
    TTSProvider,
)
from providers.image.mock import MockImageProvider
from providers.llm.mock import MockLLMProvider
from providers.research.mock import MockResearchProvider
from providers.research.passthrough import PassthroughResearchProvider
from providers.tts.mock import MockTTSProvider


@dataclass
class ProviderSet:
    llm: LLMProvider
    tts: TTSProvider
    image: ImageProvider
    research: ResearchProvider


def _openai_llm(settings: Settings) -> LLMProvider:
    # Imported lazily so mock-only runs never need the openai package or a key.
    from providers.llm.openai_provider import OpenAILLMProvider

    return OpenAILLMProvider(
        settings.providers.model_for("llm"), settings=settings.providers.openai
    )


# Register real providers here in later milestones: _FACTORIES["llm"]["openai"] = ...
_FACTORIES: dict[str, dict[str, Callable[[Settings], Any]]] = {
    "llm": {
        "mock": lambda s: MockLLMProvider(s.providers.model_for("llm")),
        "openai": lambda s: _openai_llm(s),
    },
    "tts": {
        "mock": lambda s: MockTTSProvider(
            voice=s.providers.tts.get("voice", "mock-voice-1"),
        )
    },
    "image": {"mock": lambda s: MockImageProvider()},
    "research": {
        "mock": lambda s: MockResearchProvider(),
        "passthrough": lambda s: PassthroughResearchProvider(),
    },
}


def _build(kind: str, settings: Settings) -> Any:
    name = getattr(settings.providers.selected, kind)
    try:
        factory = _FACTORIES[kind][name]
    except KeyError:
        raise ConfigError(
            f"Unknown {kind} provider {name!r}. Available: {sorted(_FACTORIES[kind])}"
        ) from None
    return factory(settings)


def build_providers(settings: Settings) -> ProviderSet:
    return ProviderSet(
        llm=_build("llm", settings),
        tts=_build("tts", settings),
        image=_build("image", settings),
        research=_build("research", settings),
    )
