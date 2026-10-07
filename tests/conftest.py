"""Shared fixtures. Tests never touch the real workspace or any external service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from core.config import Settings, load_settings
from core.ingest import SourceSpec, prepare_sources
from core.jobs import JobStore
from core.models.manifest import Audience, JobManifest
from providers.registry import ProviderSet, build_providers
from rendering.ffmpeg import find_ffmpeg


def _discover_ffmpeg() -> str | None:
    found = find_ffmpeg()
    if found:
        return str(found)
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # noqa: BLE001 - optional dev dependency
        return None


FFMPEG = _discover_ffmpeg()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if FFMPEG:
        return
    skip = pytest.mark.skip(reason="ffmpeg not available (install it or `pip install -e .[dev]`)")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    env = {"VIDGEN_WORKSPACE_DIR": str(tmp_path / "workspace")}
    if FFMPEG:
        env["FFMPEG_PATH"] = FFMPEG
    # Smaller video keeps ffmpeg tests fast.
    env["VIDGEN__video__width"] = "320"
    env["VIDGEN__video__height"] = "180"
    return load_settings(env=env, load_dotenv_file=False)


@pytest.fixture
def store(settings: Settings) -> JobStore:
    return JobStore(settings.workspace_dir)


@pytest.fixture
def providers(settings: Settings) -> ProviderSet:
    return build_providers(settings)


# A short "creator transcript" in different words from the mock script (for overlap checks).
SOURCE_TEXT = (
    "okay so TCP basically makes sure your data actually gets where it is going\n"
    "first the client says hi with a SYN and the server says hi back\n"
    "after that they are connected and can start swapping data\n"
    "every packet is numbered so nothing gets lost or shows up out of order\n"
)
SOURCE_SRT = (
    "1\n00:00:00,000 --> 00:00:04,000\nTCP makes sure data arrives.\n\n"
    "2\n00:00:04,000 --> 00:00:08,500\nPackets are numbered <i>so nothing is lost</i>.\n"
)

WriteFile = Callable[[str, str], Path]
MakeJob = Callable[..., JobManifest]


@pytest.fixture
def write_file(tmp_path: Path) -> WriteFile:
    """Create a user-style upload (name, text) under tmp_path/uploads and return its path."""

    def _write(name: str, text: str) -> Path:
        path = tmp_path / "uploads" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # exact bytes: no newline translation
        return path

    return _write


@pytest.fixture
def make_job(store: JobStore, settings: Settings, write_file: WriteFile) -> MakeJob:
    def _make(
        topic: str = "How TCP works",
        transcripts: list[tuple[str, str]] | None = None,
        job_id: str | None = None,
    ) -> JobManifest:
        specs = [SourceSpec(write_file(name, text)) for name, text in transcripts or []]
        return store.create(
            topic,
            sources=prepare_sources(specs, settings.ingest),
            style="technical_explainer",
            target_duration_minutes=3,
            audience=Audience.BEGINNER,
            job_id=job_id,
        )

    return _make
