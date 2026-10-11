"""Load and validate a finance reel spec and its user-supplied files before a job is created.

Mirrors core/ingest.py: everything is checked up front so a bad spec or a missing background
never leaves a half-created job behind. Files are only ever read from local disk.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image
from pydantic import ValidationError

from core.config import IngestSettings, Settings
from core.exceptions import InvalidInputError
from core.ingest import check_location
from core.models.finance import ReelSpec
from core.naming import sanitize_filename

SPEC_EXTENSIONS = (".json", ".yaml", ".yml")
MAX_SPEC_BYTES = 256 * 1024


@dataclass(frozen=True)
class PreparedAsset:
    """A validated local file. It is copied (streamed, never held in memory) into the job."""

    path: Path
    name: str  # sanitised file name
    size_bytes: int


@dataclass(frozen=True)
class PreparedReel:
    spec: ReelSpec
    spec_data: bytes
    spec_name: str
    background: PreparedAsset
    voiceover: PreparedAsset | None


def format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors(include_url=False):
        where = ".".join(str(p) for p in err["loc"]) or "spec"
        message = str(err["msg"]).removeprefix("Value error, ")
        lines.append(f"  - {where}: {message}")
    return "\n".join(lines)


def parse_reel_spec(text: str, suffix: str) -> ReelSpec:
    """Parse and validate spec text (JSON or YAML). Raises InvalidInputError with all problems."""
    kind = "JSON" if suffix.lower() == ".json" else "YAML"
    try:
        data = json.loads(text) if kind == "JSON" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise InvalidInputError(f"Reel spec is not valid {kind}: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("Reel spec must be an object/mapping at the top level.")
    try:
        return ReelSpec.model_validate(data)
    except ValidationError as exc:
        raise InvalidInputError(f"Invalid reel spec:\n{format_validation_error(exc)}") from exc


def media_type_for(path: str | Path, settings: Settings) -> str | None:
    """'image' or 'video' from the file extension, None when it is neither."""
    suffix = Path(path).suffix.lower()
    if suffix in {e.lower() for e in settings.finance.image_extensions}:
        return "image"
    if suffix in {e.lower() for e in settings.finance.video_extensions}:
        return "video"
    return None


def _resolve(spec_dir: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else spec_dir / path


def _check_file(
    path: Path, what: str, allowed: list[str], max_mb: float, ingest: IngestSettings
) -> PreparedAsset:
    if not path.is_file():
        raise InvalidInputError(f"{what} not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in {e.lower() for e in allowed}:
        raise InvalidInputError(
            f"{what} {path.name}: unsupported file type {suffix!r}; allowed: {sorted(allowed)}"
        )
    check_location(path, ingest)
    size = path.stat().st_size
    if size == 0 or size > max_mb * 1024 * 1024:
        raise InvalidInputError(f"{what} {path.name}: must be non-empty and at most {max_mb} MB")
    return PreparedAsset(path=path, name=sanitize_filename(path.name), size_bytes=size)


def verify_image(path: Path) -> tuple[int, int]:
    """Open an image fully enough to know it is a real, decodable picture; returns (w, h)."""
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:  # verify() leaves the file unusable, so reopen
            return image.size
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for corrupt files
        raise InvalidInputError(
            f"Background image {path.name} is not a valid image: {exc}"
        ) from exc


def prepare_reel(spec_path: Path, settings: Settings) -> PreparedReel:
    """Validate the spec file, its background and optional voiceover, without creating anything."""
    spec_path = Path(spec_path)
    if not spec_path.is_file():
        raise InvalidInputError(f"Reel spec not found: {spec_path}")
    if spec_path.suffix.lower() not in SPEC_EXTENSIONS:
        raise InvalidInputError(f"Reel spec must be one of {SPEC_EXTENSIONS}: {spec_path.name}")
    check_location(spec_path, settings.ingest)
    if spec_path.stat().st_size > MAX_SPEC_BYTES:
        raise InvalidInputError(f"Reel spec is larger than {MAX_SPEC_BYTES // 1024} KB")
    data = spec_path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidInputError("Reel spec must be UTF-8 text") from exc
    spec = parse_reel_spec(text, spec_path.suffix)

    finance = settings.finance
    spec_dir = spec_path.resolve().parent
    background_path = _resolve(spec_dir, spec.background.path)
    allowed = [*finance.image_extensions, *finance.video_extensions]
    background = _check_file(
        background_path, "Background", allowed, finance.max_asset_mb, settings.ingest
    )
    if media_type_for(background_path, settings) == "image":
        verify_image(background_path)

    voiceover: PreparedAsset | None = None
    if spec.voiceover:
        voiceover = _check_file(
            _resolve(spec_dir, spec.voiceover.path),
            "Voiceover",
            finance.audio_extensions,
            finance.max_asset_mb,
            settings.ingest,
        )
    return PreparedReel(spec, data, sanitize_filename(spec_path.name), background, voiceover)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
