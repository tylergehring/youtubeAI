"""Real narration through OpenAI; only the supplied narration is sent to the service."""

from __future__ import annotations

from pathlib import Path

import openai

from core.exceptions import ConfigError, ProviderError
from core.providers import TTSProvider, TTSResult, Usage
from rendering.ffmpeg import FFmpeg


class OpenAITTSProvider(TTSProvider):
    name = "openai"

    def __init__(
        self,
        ffmpeg: FFmpeg,
        *,
        model: str = "gpt-4o-mini-tts",
        voice: str = "marin",
    ) -> None:
        import os

        if not os.environ.get("OPENAI_API_KEY"):
            raise ConfigError("OPENAI_API_KEY is required for OpenAI narration.")
        self.model = model
        self.default_voice = voice
        self.ffmpeg = ffmpeg
        self.client = openai.OpenAI(timeout=90, max_retries=2)

    def synthesize(self, text: str, out_path: Path, *, voice: str | None = None) -> TTSResult:
        voice = voice or self.default_voice
        if not text.strip():
            raise ProviderError("Narration cannot be empty.")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        temp = out_path.with_name(out_path.name + ".partial.wav")
        try:
            with self.client.audio.speech.with_streaming_response.create(
                model=self.model,
                voice=voice,
                input=text,
                instructions=(
                    "An articulate, warm financial educator. Conversational, confident but not "
                    "sensational. Speak at a relaxed, unhurried pace with clear enunciation, "
                    "natural emphasis and brief pauses between ideas. "
                    "Read the supplied words exactly; do not add a preamble or change numbers."
                ),
                response_format="wav",
            ) as response:
                response.stream_to_file(temp)
            info = self.ffmpeg.analyze(temp)
            if not info.has_audio or info.mean_volume_db is None or info.mean_volume_db < -45:
                raise ProviderError("Narration service returned missing or inaudible audio.")
            temp.replace(out_path)
        except openai.OpenAIError as exc:
            raise ProviderError(
                f"OpenAI narration failed ({type(exc).__name__}); no mock audio substituted."
            ) from exc
        finally:
            temp.unlink(missing_ok=True)
        return TTSResult(
            path=out_path,
            duration_s=info.duration_s,
            voice=voice,
            metadata={"kind": "synthetic_speech", "model": self.model},
            usage=Usage(characters=len(text)),
        )
