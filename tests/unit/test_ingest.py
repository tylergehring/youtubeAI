from pathlib import Path

import pytest

from core.config import IngestSettings, Settings
from core.exceptions import InvalidInputError
from core.ingest import SourceSpec, prepare_sources
from tests.conftest import SOURCE_SRT, SOURCE_TEXT, WriteFile


def prep(settings: Settings, *specs: SourceSpec):  # type: ignore[no-untyped-def]
    return prepare_sources(list(specs), settings.ingest)


def test_valid_sources(settings: Settings, write_file: WriteFile) -> None:
    a, b = write_file("Talk One.txt", SOURCE_TEXT), write_file("two.srt", SOURCE_SRT)
    first, second = prep(
        settings, SourceSpec(a, "Creator A", "https://example.com/a"), SourceSpec(b)
    )
    assert (first.name, first.label, first.format) == ("talk_one.txt", "Creator A", "txt")
    assert first.reference_url == "https://example.com/a"
    assert first.data == SOURCE_TEXT.encode() and len(first.sha256) == 64
    assert (second.label, second.format, second.reference_url) == ("two", "srt", None)


def test_no_sources_is_fine(settings: Settings) -> None:
    assert prep(settings) == []


def test_utf8_bom_accepted(settings: Settings, tmp_path: Path) -> None:
    f = tmp_path / "bom.txt"
    f.write_bytes(b"\xef\xbb\xbfhello world")
    assert prep(settings, SourceSpec(f))[0].format == "txt"


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("notes.pdf", b"hello", "unsupported file type"),
        ("notes", b"hello", "unsupported file type"),
        ("empty.txt", b"", "non-empty"),
        ("binary.txt", b"\xff\xfe\x00\x01\x80\x81", "UTF-8"),
        ("blank.txt", b"   \n\n", "no usable text"),
        ("bad.srt", b"not a subtitle file", "no usable text"),
    ],
)
def test_rejected_files(
    settings: Settings, tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    f = tmp_path / name
    f.write_bytes(content)
    with pytest.raises(InvalidInputError, match=message):
        prep(settings, SourceSpec(f))


def test_missing_file_and_directory(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match="not found"):
        prep(settings, SourceSpec(tmp_path / "nope.txt"))
    with pytest.raises(InvalidInputError, match="not found"):
        prep(settings, SourceSpec(tmp_path))


def test_size_limit(settings: Settings, write_file: WriteFile) -> None:
    ingest = IngestSettings(max_file_mb=0.001)
    big = write_file("big.txt", "word " * 1000)
    with pytest.raises(InvalidInputError, match="at most"):
        prepare_sources([SourceSpec(big)], ingest)


def test_too_many_and_duplicates(settings: Settings, write_file: WriteFile) -> None:
    a, dup = write_file("a.txt", SOURCE_TEXT), write_file("copy.txt", SOURCE_TEXT)
    with pytest.raises(InvalidInputError, match="duplicate"):
        prep(settings, SourceSpec(a), SourceSpec(dup))
    with pytest.raises(InvalidInputError, match="Too many"):
        prepare_sources([SourceSpec(a)] * 3, IngestSettings(max_sources=2))


def test_bad_reference_url(settings: Settings, write_file: WriteFile) -> None:
    f = write_file("a.txt", SOURCE_TEXT)
    with pytest.raises(InvalidInputError, match="http"):
        prep(settings, SourceSpec(f, reference_url="javascript:alert(1)"))


def test_allowed_dirs_restrict_where_files_may_come_from(
    write_file: WriteFile, tmp_path: Path
) -> None:
    inside = write_file("ok.txt", SOURCE_TEXT)
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("hello world")
    ingest = IngestSettings(allowed_dirs=[inside.parent])
    assert prepare_sources([SourceSpec(inside)], ingest)
    with pytest.raises(InvalidInputError, match="allowed transcript directory"):
        prepare_sources([SourceSpec(outside)], ingest)
    sneaky = inside.parent / ".." / "elsewhere.txt"  # ../ cannot escape the allow-list
    with pytest.raises(InvalidInputError, match="allowed transcript directory"):
        prepare_sources([SourceSpec(sneaky)], ingest)
