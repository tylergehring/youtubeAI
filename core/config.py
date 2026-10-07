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


class ProviderSettings(_Cfg):
    selected: ProviderSelection = Field(default_factory=ProviderSelection)
    models: dict[str, str] = Field(default_factory=lambda: {"llm": "mock-llm-1"})
    tts: dict[str, str] = Field(default_factory=lambda: {"voice": "mock-voice-1"})
    pricing: dict[str, dict[str, PriceEntry]] = Field(default_factory=dict)


class Settings(_Cfg):
    workspace_dir: Path = Path("workspace")
    log_level: str = "INFO"
    ffmpeg_path: str | None = None
    defaults: DefaultsSettings = Field(default_factory=DefaultsSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    qa: QASettings = Field(default_factory=QASettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
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
