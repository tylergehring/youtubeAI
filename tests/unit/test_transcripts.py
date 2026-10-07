import pytest

from core.exceptions import InvalidInputError
from core.transcripts import parse_transcript
from tests.conftest import SOURCE_SRT, SOURCE_TEXT


class TestPlainText:
    def test_paragraphs_become_segments(self) -> None:
        t = parse_transcript("First idea\ncontinues here.\n\nSecond idea.\n", "txt")
        assert [s.text for s in t.segments] == ["First idea continues here.", "Second idea."]
        assert all(s.start_s is None for s in t.segments)

    def test_standalone_timestamp_lines_are_used_not_kept_as_text(self) -> None:
        t = parse_transcript("0:00\nhello there\n0:05\nsecond line\n1:02:03\nlate", "txt")
        assert [(s.start_s, s.text) for s in t.segments] == [
            (0.0, "hello there"),
            (5.0, "second line"),
            (3723.0, "late"),
        ]

    def test_bom_and_windows_newlines(self) -> None:
        t = parse_transcript("\ufeffone\r\n\r\ntwo\r\n", "txt")
        assert [s.text for s in t.segments] == ["one", "two"]

    def test_plain_text_fixture(self) -> None:
        assert "TCP basically" in parse_transcript(SOURCE_TEXT, "txt").text


class TestSrt:
    def test_parses_times_and_strips_markup(self) -> None:
        t = parse_transcript(SOURCE_SRT, "srt")
        assert t.format == "srt"
        assert (t.segments[1].start_s, t.segments[1].end_s) == (4.0, 8.5)
        assert t.segments[1].text == "Packets are numbered so nothing is lost."

    def test_multiline_cue_and_hours(self) -> None:
        srt = "1\n01:02:03,500 --> 01:02:05,000\nline one\nline two\n"
        seg = parse_transcript(srt, "srt").segments[0]
        assert seg.start_s == pytest.approx(3723.5) and seg.text == "line one line two"

    def test_blocks_without_timing_are_ignored(self) -> None:
        assert len(parse_transcript("garbage\n\n" + SOURCE_SRT, "srt").segments) == 2


class TestVtt:
    VTT = (
        "WEBVTT\n\nNOTE a comment\n\n"
        "intro\n00:01.000 --> 00:03.500 align:start position:0%\n<c.colorE5E5E5>Hello</c> world\n\n"
        "00:00:04.000 --> 00:00:06.000\nSecond cue\n"
    )

    def test_header_notes_and_settings_ignored(self) -> None:
        t = parse_transcript(self.VTT, "vtt")
        assert [s.text for s in t.segments] == ["Hello world", "Second cue"]
        assert (t.segments[0].start_s, t.segments[0].end_s) == (1.0, 3.5)


class TestRejections:
    @pytest.mark.parametrize("content", ["", "   \n\n  ", "0:05\n0:10\n"])
    def test_no_usable_text(self, content: str) -> None:
        with pytest.raises(InvalidInputError, match="no usable text"):
            parse_transcript(content, "txt")

    def test_srt_without_cues(self) -> None:
        with pytest.raises(InvalidInputError):
            parse_transcript("just words", "srt")

    def test_unknown_format(self) -> None:
        with pytest.raises(InvalidInputError, match="Unsupported"):
            parse_transcript("x", "docx")
