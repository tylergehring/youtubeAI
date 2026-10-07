import pytest

from core.exceptions import InvalidInputError
from core.naming import sanitize_filename, slugify, validate_job_id, validate_reference_url


def test_slugify() -> None:
    assert slugify("How TCP Works!") == "how_tcp_works"
    assert slugify("   ") == "untitled"
    assert slugify("../../etc/passwd") == "etc_passwd"
    assert len(slugify("x" * 200)) <= 40


@pytest.mark.parametrize("job_id", ["2026-10-07_tcp_001", "a", "A_b-c"])
def test_valid_job_ids(job_id: str) -> None:
    assert validate_job_id(job_id) == job_id


@pytest.mark.parametrize(
    "job_id", ["", "..", "../x", "a/b", "a\\b", "-leading", "_leading", "a b", "x" * 101, "a;rm"]
)
def test_invalid_job_ids(job_id: str) -> None:
    with pytest.raises(InvalidInputError):
        validate_job_id(job_id)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("talk.txt", "talk.txt"),
        ("My Great Talk (final).SRT", "my_great_talk_final.srt"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\me\\notes.vtt", "notes.vtt"),
        ("noextension", "noextension"),
        ("weird.t x$t", "weird.txt"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_is_always_a_bare_name() -> None:
    for raw in ("..", "/", "a/b/c", "..\\..\\x", "\x00bad.txt", "ü.txt"):
        name = sanitize_filename(raw)
        assert name and "/" not in name and "\\" not in name and ".." not in name


@pytest.mark.parametrize("url", ["https://example.com/a", "http://example.com/x?y=1"])
def test_valid_reference_urls(url: str) -> None:
    assert validate_reference_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "example.com",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
    ],
)
def test_invalid_reference_urls(url: str) -> None:
    with pytest.raises(InvalidInputError):
        validate_reference_url(url)
