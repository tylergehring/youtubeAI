import wave
from pathlib import Path

import pytest
from pydantic import BaseModel

from core.config import Settings
from core.exceptions import ConfigError, ProviderError, StructuredOutputError
from core.models.content import Claim, KnowledgeBase, LessonPlan, Script, Strict
from core.models.scenes import ScenePlan
from core.providers import (
    ImageProvider,
    LLMProvider,
    ResearchProvider,
    TTSProvider,
    parse_structured,
)
from providers.llm.mock import MockLLMProvider
from providers.registry import ProviderSet, build_providers
from providers.research.mock import MockResearchProvider
from providers.tts.mock import MockTTSProvider


class Tiny(Strict):
    a: int


class TestParseStructured:
    def test_valid(self) -> None:
        assert parse_structured('{"a": 1}', Tiny).a == 1

    def test_code_fence_tolerated(self) -> None:
        assert parse_structured('```json\n{"a": 2}\n```', Tiny).a == 2

    @pytest.mark.parametrize("raw", ["", "not json", '{"a": "x"}', '{"a": 1, "b": 2}', "[1]"])
    def test_malformed_rejected(self, raw: str) -> None:
        with pytest.raises(StructuredOutputError):
            parse_structured(raw, Tiny)

    def test_prose_around_json_rejected(self) -> None:
        with pytest.raises(StructuredOutputError):
            parse_structured('Sure! Here you go: {"a": 1}', Tiny)


class TestProviderInterfaces:
    def test_registry_builds_all_interfaces(self, providers: ProviderSet) -> None:
        assert isinstance(providers.llm, LLMProvider)
        assert isinstance(providers.tts, TTSProvider)
        assert isinstance(providers.image, ImageProvider)
        assert isinstance(providers.research, ResearchProvider)

    def test_interfaces_are_abstract(self) -> None:
        for abstract in (LLMProvider, TTSProvider, ResearchProvider):
            with pytest.raises(TypeError):
                abstract()  # type: ignore[abstract]

    def test_unknown_provider_is_config_error(self, settings: Settings) -> None:
        bad = settings.model_copy(deep=True)
        bad.providers.selected.llm = "does-not-exist"
        with pytest.raises(ConfigError, match="does-not-exist"):
            build_providers(bad)


class TestMockLLM:
    @pytest.mark.parametrize("schema", [KnowledgeBase, LessonPlan, Script, ScenePlan])
    def test_structured_outputs_validate(self, schema: type[BaseModel]) -> None:
        llm = MockLLMProvider()
        response = llm.generate_structured("TOPIC: Git\nAUDIENCE: beginner", schema)
        assert isinstance(response.value, schema)
        assert response.usage.tokens_in > 0 and llm.calls == [schema.__name__]

    def test_topic_is_taken_from_prompt(self) -> None:
        llm = MockLLMProvider()
        value = llm.generate_structured("TOPIC: Git\nrest", KnowledgeBase).value
        assert value.topic == "Git"

    def test_malformed_output_is_rejected_not_ignored(self) -> None:
        llm = MockLLMProvider(raw_overrides={"KnowledgeBase": '{"topic": "x"}'})
        with pytest.raises(StructuredOutputError):
            llm.generate_structured("TOPIC: x", KnowledgeBase)

    def test_unsupported_schema(self) -> None:
        with pytest.raises(ProviderError):
            MockLLMProvider().generate_structured("x", Tiny)

    def test_plain_generate(self) -> None:
        assert MockLLMProvider().generate("hi").text


class TestMockTTS:
    def test_writes_wav_with_reported_duration(self, tmp_path: Path) -> None:
        tts = MockTTSProvider()
        result = tts.synthesize("one two three four five six seven eight", tmp_path / "a.wav")
        with wave.open(str(result.path)) as wav:
            actual = wav.getnframes() / wav.getframerate()
        assert actual == pytest.approx(result.duration_s)
        assert result.voice == "mock-voice-1"
        assert result.usage.characters == len("one two three four five six seven eight")

    def test_minimum_duration_and_determinism(self, tmp_path: Path) -> None:
        tts = MockTTSProvider()
        a = tts.synthesize("hi", tmp_path / "a.wav")
        tts.synthesize("hi", tmp_path / "b.wav")
        assert a.duration_s == pytest.approx(1.0)
        assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()

    def test_failure_injection(self, tmp_path: Path) -> None:
        tts = MockTTSProvider(fail_on_text_containing="BOOM")
        with pytest.raises(ProviderError):
            tts.synthesize("BOOM", tmp_path / "a.wav")


class TestMockResearch:
    def test_research_verifies_or_flags(self) -> None:
        provider = MockResearchProvider()
        ok = provider.verify_claim(Claim(id="a", text="fine", confidence=0.5), "t")
        bad = provider.verify_claim(Claim(id="b", text="UNVERIFIABLE thing", confidence=0.5), "t")
        assert ok.verification.status.value == "VERIFIED_FACT" and ok.verification.evidence
        assert bad.verification.disputed and not bad.verification.evidence


class TestPassthroughResearch:
    def test_leaves_claims_attributed_to_their_source_and_costs_nothing(self) -> None:
        from core.models.content import ClaimStatus
        from providers.research.passthrough import PassthroughResearchProvider

        claim = Claim(id="c1", text="Something the video said", confidence=0.8)
        result = PassthroughResearchProvider().verify_claim(claim, "t")
        v = result.verification
        assert v.status is ClaimStatus.SOURCE_CLAIM and v.confidence == 0.8
        assert v.evidence == [] and not v.disputed
        assert result.usage.requests == 0

    def test_selectable_by_name(self, settings: Settings) -> None:
        chosen = settings.model_copy(deep=True)
        chosen.providers.selected.research = "passthrough"
        assert build_providers(chosen).research.name == "passthrough"
