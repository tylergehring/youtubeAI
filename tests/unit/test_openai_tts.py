from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

from core.exceptions import ConfigError, ProviderError
from providers.tts.openai_provider import OpenAITTSProvider
from rendering.ffmpeg import FFmpeg, MediaInfo


def test_openai_speech_missing_key_fails_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        OpenAITTSProvider(FFmpeg(Path("unused")))


@pytest.mark.parametrize("audible", [True, False])
def test_speech_stream_is_checked_and_written_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audible: bool,
) -> None:
    calls = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream_to_file(self, path):
            path.write_bytes(b"fake wav for the unit test")

    def create(**kwargs):
        calls.append(kwargs)
        return Stream()

    client = SimpleNamespace(
        audio=SimpleNamespace(
            speech=SimpleNamespace(with_streaming_response=SimpleNamespace(create=create))
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-only")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(
        FFmpeg,
        "analyze",
        lambda self, path: MediaInfo(
            duration_s=2,
            has_video=False,
            has_audio=True,
            width=None,
            height=None,
            fps=None,
            mean_volume_db=-20 if audible else -100,
        ),
    )
    provider = OpenAITTSProvider(FFmpeg(Path("unused")))
    destination = tmp_path / "voice.wav"
    if audible:
        result = provider.synthesize("Only our original narration.", destination)
        assert result.duration_s == 2 and result.usage.characters == 28
        assert destination.read_bytes() == b"fake wav for the unit test"
    else:
        with pytest.raises(ProviderError, match="inaudible"):
            provider.synthesize("Only our original narration.", destination)
        assert not destination.exists()
    assert calls[0]["input"] == "Only our original narration."
    assert calls[0]["response_format"] == "wav"
    assert not list(tmp_path.glob("*.partial.wav"))
