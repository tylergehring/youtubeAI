"""Mock TTS: writes a quiet deterministic tone whose length follows the word count."""

from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

from core.exceptions import ProviderError
from core.providers import TTSProvider, TTSResult, Usage

SAMPLE_RATE = 22050
WORDS_PER_SECOND = 2.6
MIN_DURATION_S = 1.0
TONE_HZ = 220.0
AMPLITUDE = 0.15


class MockTTSProvider(TTSProvider):
    name = "mock"

    def __init__(
        self,
        model: str = "mock-voice-1",
        voice: str = "mock-voice-1",
        *,
        fail_on_text_containing: str | None = None,
    ) -> None:
        self.model = model
        self.default_voice = voice
        self.calls: list[str] = []
        self._fail_marker = fail_on_text_containing

    def synthesize(self, text: str, out_path: Path, *, voice: str | None = None) -> TTSResult:
        self.calls.append(text)
        if self._fail_marker and self._fail_marker in text:
            raise ProviderError("MockTTSProvider configured to fail")
        voice = voice or self.default_voice
        duration = max(MIN_DURATION_S, len(text.split()) / WORDS_PER_SECOND)
        frames = int(round(duration * SAMPLE_RATE))
        fade = int(0.02 * SAMPLE_RATE)
        samples = array("h")
        for i in range(frames):
            envelope = min(1.0, i / fade, (frames - 1 - i) / fade)
            value = AMPLITUDE * envelope * math.sin(2 * math.pi * TONE_HZ * i / SAMPLE_RATE)
            samples.append(int(value * 32767))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(out_path.name + ".tmp")
        with wave.open(str(tmp), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(samples.tobytes())
        tmp.replace(out_path)

        return TTSResult(
            path=out_path,
            duration_s=frames / SAMPLE_RATE,
            voice=voice,
            metadata={"sample_rate": SAMPLE_RATE, "kind": "sine_tone", "words": len(text.split())},
            usage=Usage(characters=len(text)),
        )
