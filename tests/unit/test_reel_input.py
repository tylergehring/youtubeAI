"""Reel spec loading and asset validation: bad input must fail clearly, before any job exists."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image

from core.config import Settings
from core.exceptions import InvalidInputError
from core.reel_input import parse_reel_spec, prepare_reel

FIXTURE = (
    Path(__file__).resolve().parents[2] / "examples" / "finance" / "retirement_withdrawal.yaml"
)


def base_spec(**overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    spec["background"]["path"] = "demo_background.png"  # tests supply a tiny image instead
    spec.update(overrides)
    return spec


def write_spec(directory: Path, spec: dict[str, Any], name: str = "spec.yaml") -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


def make_image(path: Path, size: tuple[int, int] = (64, 96)) -> Path:
    Image.new("RGB", size, (30, 60, 120)).save(path)
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    directory = tmp_path / "reel"
    directory.mkdir()
    make_image(directory / "demo_background.png")
    return directory


class TestSpecParsing:
    def test_the_demo_fixture_is_valid(self) -> None:
        spec = parse_reel_spec(FIXTURE.read_text(encoding="utf-8"), ".yaml")
        assert spec.title == "How Much Do You Need to Retire?"
        assert spec.provenance.verification.value == "illustrative"
        assert spec.background.origin.value == "generated_placeholder"

    def test_json_and_yaml_are_equivalent(self) -> None:
        data = base_spec()
        assert parse_reel_spec(json.dumps(data), ".json") == parse_reel_spec(
            yaml.safe_dump(data), ".yaml"
        )

    def test_all_problems_are_reported_with_their_location(self) -> None:
        data = base_spec(title="x", cta="")
        data["background"]["license"] = ""
        with pytest.raises(InvalidInputError) as caught:
            parse_reel_spec(yaml.safe_dump(data), ".yaml")
        message = str(caught.value)
        assert "title" in message and "cta" in message and "background.license" in message

    def test_malformed_files_are_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="not valid JSON"):
            parse_reel_spec("{nope", ".json")
        with pytest.raises(InvalidInputError, match="not valid YAML"):
            parse_reel_spec("a: [", ".yaml")
        with pytest.raises(InvalidInputError, match="mapping"):
            parse_reel_spec("- just\n- a list\n", ".yaml")


class TestPrepareReel:
    def test_valid_reel(self, settings: Settings, folder: Path) -> None:
        prepared = prepare_reel(write_spec(folder, base_spec()), settings)
        assert prepared.background.path == folder / "demo_background.png"
        assert prepared.voiceover is None
        assert prepared.spec_name == "spec.yaml"

    def test_missing_spec(self, settings: Settings, tmp_path: Path) -> None:
        with pytest.raises(InvalidInputError, match="Reel spec not found"):
            prepare_reel(tmp_path / "nope.yaml", settings)

    def test_wrong_spec_extension(self, settings: Settings, folder: Path) -> None:
        path = folder / "spec.txt"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(InvalidInputError, match="must be one of"):
            prepare_reel(path, settings)

    def test_missing_background_is_a_clear_error(self, settings: Settings, folder: Path) -> None:
        (folder / "demo_background.png").unlink()
        with pytest.raises(
            InvalidInputError, match=r"Background not found: .*demo_background\.png"
        ):
            prepare_reel(write_spec(folder, base_spec()), settings)

    def test_background_is_never_fetched_from_a_url(self, settings: Settings, folder: Path) -> None:
        data = base_spec()
        data["background"]["path"] = "https://example.com/beach.mp4"
        with pytest.raises(InvalidInputError, match="local file"):
            prepare_reel(write_spec(folder, data), settings)

    def test_unsupported_background_type(self, settings: Settings, folder: Path) -> None:
        (folder / "clip.avi").write_bytes(b"x" * 100)
        data = base_spec()
        data["background"]["path"] = "clip.avi"
        with pytest.raises(InvalidInputError, match="unsupported file type"):
            prepare_reel(write_spec(folder, data), settings)

    def test_empty_background(self, settings: Settings, folder: Path) -> None:
        (folder / "demo_background.png").write_bytes(b"")
        with pytest.raises(InvalidInputError, match="non-empty"):
            prepare_reel(write_spec(folder, base_spec()), settings)

    def test_corrupt_image_background(self, settings: Settings, folder: Path) -> None:
        (folder / "demo_background.png").write_bytes(b"this is not a png" * 20)
        with pytest.raises(InvalidInputError, match="not a valid image"):
            prepare_reel(write_spec(folder, base_spec()), settings)

    def test_oversized_background(self, settings: Settings, folder: Path) -> None:
        small = settings.model_copy(
            update={"finance": settings.finance.model_copy(update={"max_asset_mb": 0.0001})}
        )
        with pytest.raises(InvalidInputError, match="at most"):
            prepare_reel(write_spec(folder, base_spec()), small)

    def test_missing_voiceover(self, settings: Settings, folder: Path) -> None:
        data = base_spec(voiceover={"path": "narration.wav"})
        with pytest.raises(InvalidInputError, match="Voiceover not found"):
            prepare_reel(write_spec(folder, data), settings)

    def test_voiceover_must_be_audio(self, settings: Settings, folder: Path) -> None:
        (folder / "narration.txt").write_text("hi", encoding="utf-8")
        data = base_spec(voiceover={"path": "narration.txt"})
        with pytest.raises(InvalidInputError, match="unsupported file type"):
            prepare_reel(write_spec(folder, data), settings)

    def test_allowed_dirs_also_restrict_reel_files(
        self, settings: Settings, folder: Path, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        restricted = settings.model_copy(
            update={"ingest": settings.ingest.model_copy(update={"allowed_dirs": [elsewhere]})}
        )
        with pytest.raises(InvalidInputError, match="allowed"):
            prepare_reel(write_spec(folder, base_spec()), restricted)
