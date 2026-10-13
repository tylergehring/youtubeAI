"""Configuration loading: YAML files + environment overrides. Contains no secrets."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import ConfigError
from core.models.manifest import Audience

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PREFIX = "VIDGEN__"


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DefaultsSettings(_Cfg):
    style: str = "technical_explainer"
    target_duration_minutes: float = Field(default=3, gt=0)
    audience: Audience = Audience.BEGINNER


class VideoSettings(_Cfg):
    width: int = Field(default=1280, ge=16)
    height: int = Field(default=720, ge=16)
    fps: int = Field(default=30, ge=1, le=120)
    scene_padding_s: float = Field(default=0.4, ge=0)


class QASettings(_Cfg):
    min_duration_s: float = 3
    max_duration_s: float = 1800
    max_file_size_mb: float = 500
    min_mean_volume_db: float = -60
    duration_tolerance_s: float = 1.0
    source_overlap_max: float = Field(default=0.35, ge=0, le=1)
    semantic_enabled: bool = False


class StyleConfig(_Cfg):
    description: str = ""
    palette: list[str] = Field(min_length=1)
    accent_color: str
    text_color: str


class IngestSettings(_Cfg):
    """Rules for user-supplied transcript files."""

    allowed_dirs: list[Path] = Field(default_factory=list)  # empty = any location
    extensions: list[str] = Field(default_factory=lambda: [".txt", ".srt", ".vtt"])
    max_file_mb: float = Field(default=5, gt=0)
    max_sources: int = Field(default=10, ge=0)


class KnowledgeSettings(_Cfg):
    # Hard ceiling on transcript text sent to the LLM. Text is never silently truncated: if the
    # supplied transcripts exceed this, the knowledge stage fails and says so.
    max_source_chars: int = Field(default=60_000, ge=1000)


class SafeMargins(_Cfg):
    """Fractions of the frame kept clear of text so platform UI (status bar, caption, buttons)
    never covers it."""

    top: float = Field(default=0.10, ge=0, lt=0.5)
    bottom: float = Field(default=0.20, ge=0, lt=0.5)
    left: float = Field(default=0.07, ge=0, lt=0.5)
    right: float = Field(default=0.07, ge=0, lt=0.5)


class FinanceColors(_Cfg):
    text: str = "#FFFFFF"
    muted: str = "#E8ECF4"
    accent: str = "#FFD166"
    panel: str = "#000000"
    scrim: str = "#000000"


class FinanceTiming(_Cfg):
    """Reveal schedule in seconds. Rows appear one at a time so the list can be read."""

    title_at_s: float = Field(default=0.3, ge=0)
    methodology_at_s: float = Field(default=1.2, ge=0)
    first_row_at_s: float = Field(default=2.2, ge=0)
    row_interval_s: float = Field(default=0.9, gt=0)
    cta_delay_s: float = Field(default=1.4, ge=0)  # after the last row
    end_hold_s: float = Field(default=3.0, ge=0)  # everything stays on screen after the CTA
    fade_s: float = Field(default=0.35, gt=0)
    slide_px: int = Field(default=26, ge=0)  # at 1080 px width; scaled with the frame
    voiceover_tail_s: float = Field(default=0.6, ge=0)


class FinanceSettings(_Cfg):
    """Finance-facts reel output. Portrait 9:16 by default; the explainer's `video` is untouched."""

    width: int = Field(default=1080, ge=16)
    height: int = Field(default=1920, ge=16)
    fps: int = Field(default=30, ge=1, le=120)
    encoder_threads: int = Field(default=0, ge=0, le=64)  # 0 retains FFmpeg's automatic choice
    filter_threads: int = Field(default=0, ge=0, le=64)
    safe_margins: SafeMargins = Field(default_factory=SafeMargins)
    colors: FinanceColors = Field(default_factory=FinanceColors)
    timing: FinanceTiming = Field(default_factory=FinanceTiming)
    scrim_max_opacity: float = Field(default=0.62, ge=0, le=1)  # at the top and bottom edges
    scrim_min_opacity: float = Field(default=0.22, ge=0, le=1)  # mid-frame, keeps footage visible
    panel_opacity: float = Field(default=0.55, ge=0, le=1)  # behind each figure row
    min_text_scale: float = Field(default=0.6, gt=0, le=1)  # how far text may shrink to fit
    font_regular: Path | None = None  # None = search common system fonts
    font_bold: Path | None = None
    image_extensions: list[str] = Field(default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp"])
    video_extensions: list[str] = Field(default_factory=lambda: [".mp4", ".mov", ".m4v", ".webm"])
    audio_extensions: list[str] = Field(default_factory=lambda: [".wav", ".mp3", ".m4a", ".aac"])
    max_asset_mb: float = Field(default=500, gt=0)
    min_background_short_side_px: int = Field(default=480, ge=1)  # below this, a warning is logged


class ProviderSelection(_Cfg):
    llm: str = "mock"
    tts: str = "mock"
    image: str = "mock"
    research: str = "mock"


class PriceEntry(_Cfg):
    input_per_1k_tokens: float = 0.0
    output_per_1k_tokens: float = 0.0
    per_1k_characters: float = 0.0
    per_request: float = 0.0


class OpenAISettings(_Cfg):
    timeout_s: float = Field(default=120, gt=0)
    max_retries: int = Field(default=2, ge=0)  # SDK-level retries for transient network/5xx errors
    repair_attempts: int = Field(default=2, ge=0)  # extra tries when output fails schema checks
    max_output_tokens: int = Field(default=8192, ge=256)
    send_temperature: bool | None = None  # None = auto (omitted for reasoning-style models)
    base_url: str | None = None


class ProviderSettings(_Cfg):
    selected: ProviderSelection = Field(default_factory=ProviderSelection)
    # models[kind][provider_name] -> model id, so switching providers switches models too
    models: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {"llm": {"mock": "mock-llm-1"}}
    )
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    tts: dict[str, str] = Field(default_factory=lambda: {"voice": "mock-voice-1"})
    pricing: dict[str, dict[str, PriceEntry]] = Field(default_factory=dict)

    def model_for(self, kind: str) -> str:
        """The model id configured for the selected provider of this kind."""
        provider = getattr(self.selected, kind)
        try:
            return self.models[kind][provider]
        except KeyError:
            raise ConfigError(
                f"No model configured for {kind} provider {provider!r}: "
                f"add providers.models.{kind}.{provider} to config/providers.yaml"
            ) from None


class Settings(_Cfg):
    workspace_dir: Path = Path("workspace")
    log_level: str = "INFO"
    ffmpeg_path: str | None = None
    defaults: DefaultsSettings = Field(default_factory=DefaultsSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    qa: QASettings = Field(default_factory=QASettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    finance: FinanceSettings = Field(default_factory=FinanceSettings)
    styles: dict[str, StyleConfig] = Field(default_factory=dict)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)

    def style(self, name: str) -> StyleConfig:
        try:
            return self.styles[name]
        except KeyError:
            raise ConfigError(
                f"Unknown style {name!r}. Defined styles: {sorted(self.styles)}"
            ) from None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level.")
    return data


def _apply_env_overrides(data: dict[str, Any], env: Mapping[str, str]) -> None:
    """VIDGEN__a__b=value sets data['a']['b'] (value parsed as YAML for typing).

    Matching is case-insensitive because Windows upper-cases environment variable names;
    all setting names are lower-case.
    """
    for key, raw in env.items():
        if not key.upper().startswith(ENV_PREFIX):
            continue
        path = [p.lower() for p in key[len(ENV_PREFIX) :].split("__") if p]
        if not path:
            continue
        node = data
        for part in path[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ConfigError(f"Cannot apply {key}: {part!r} is not a section.")
            node = child
        node[path[-1]] = yaml.safe_load(raw)


def load_settings(
    config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    load_dotenv_file: bool = True,
) -> Settings:
    """Load settings.yaml, styles.yaml and providers.yaml, then apply environment overrides."""
    if load_dotenv_file and env is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    env = os.environ if env is None else env

    if config_dir is None:
        config_dir = Path(env.get("VIDGEN_CONFIG_DIR") or PROJECT_ROOT / "config")
    config_dir = config_dir if config_dir.is_absolute() else PROJECT_ROOT / config_dir

    data = _read_yaml(config_dir / "settings.yaml")
    data["styles"] = _read_yaml(config_dir / "styles.yaml").get("styles", {})
    data["providers"] = _read_yaml(config_dir / "providers.yaml")

    if env.get("VIDGEN_WORKSPACE_DIR"):
        data["workspace_dir"] = env["VIDGEN_WORKSPACE_DIR"]
    if env.get("FFMPEG_PATH") and not data.get("ffmpeg_path"):
        data["ffmpeg_path"] = env["FFMPEG_PATH"]
    _apply_env_overrides(data, env)

    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    if not settings.workspace_dir.is_absolute():
        settings = settings.model_copy(
            update={"workspace_dir": (PROJECT_ROOT / settings.workspace_dir).resolve()}
        )
    return settings
