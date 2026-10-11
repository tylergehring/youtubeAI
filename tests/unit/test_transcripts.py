import re
from pathlib import Path

import pytest

from core.exceptions import InvalidInputError
from core.transcripts import MAX_SEGMENT_CHARS, parse_transcript
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


class TestNoiseAndChunking:
    def test_music_markers_and_speaker_changes_removed_from_txt(self) -> None:
        t = parse_transcript(
            "Americans tap their 401Ks [music] for emergencies. >> A family needs $140,000. "
            ">> New data [Music] shows less saving.",
            "txt",
        )
        assert [s.text for s in t.segments] == [
            "Americans tap their 401Ks for emergencies.",
            "A family needs $140,000.",
            "New data shows less saving.",
        ]
        assert t.markers_removed == 2
        assert ">>" not in t.text and "[" not in t.text

    def test_numeric_citations_are_kept_but_sound_cues_and_bleeps_are_not(self) -> None:
        t = parse_transcript(
            "See source [1] for this. [Applause] That was [ __ ] great \u266a", "txt"
        )
        assert t.text == "See source [1] for this. That was great"
        assert t.markers_removed == 3

    def test_srt_and_vtt_cleaned_too(self) -> None:
        srt = "1\n00:00:00,000 --> 00:00:02,000\n>> [music] hello there\n"
        t = parse_transcript(srt, "srt")
        assert t.segments[0].text == "hello there" and t.markers_removed == 1
        vtt = "WEBVTT\n\n00:00.000 --> 00:02.000\n[Music]\n\n00:02.000 --> 00:04.000\nonly speech\n"
        t2 = parse_transcript(vtt, "vtt")
        assert [s.text for s in t2.segments] == ["only speech"]  # a cue that was only a marker

    def test_only_markers_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="no usable text"):
            parse_transcript("[music] >> [applause]", "txt")

    def test_long_single_block_is_chunked_on_sentence_boundaries(self) -> None:
        sentences = [f"This is sentence number {i} about money." for i in range(200)]
        t = parse_transcript(" ".join(sentences), "txt")
        assert len(t.segments) > 5
        assert all(len(s.text) <= MAX_SEGMENT_CHARS for s in t.segments)
        assert all(s.text.endswith("money.") for s in t.segments)  # never mid-sentence
        assert " ".join(s.text for s in t.segments) == " ".join(sentences)  # nothing lost

    def test_text_without_punctuation_is_hard_split_without_losing_words(self) -> None:
        words = [f"w{i}" for i in range(2000)]
        t = parse_transcript(" ".join(words), "txt")
        assert all(len(s.text) <= MAX_SEGMENT_CHARS for s in t.segments)
        assert " ".join(s.text for s in t.segments).split() == words

    def test_timestamp_attaches_to_first_chunk_only(self) -> None:
        body = " ".join(f"Sentence {i} is here." for i in range(150))
        t = parse_transcript(f"0:30\n{body}", "txt")
        assert t.segments[0].start_s == 30.0
        assert all(s.start_s is None for s in t.segments[1:])

    REAL = Path(__file__).resolve().parents[1] / "transcipts" / "stephenGramVideo.txt"

    @pytest.mark.skipif(not REAL.is_file(), reason="sample transcript not present")
    def test_real_world_auto_transcript(self) -> None:
        raw = self.REAL.read_text(encoding="utf-8")
        t = parse_transcript(raw, "txt")
        assert "[music]" not in t.text and ">>" not in t.text
        assert t.markers_removed >= 1 and len(t.segments) > 10
        assert all(len(s.text) <= MAX_SEGMENT_CHARS for s in t.segments)
        before = len(re.sub(r"\[music\]|>>", " ", raw).split())
        assert len(t.text.split()) == before  # only markers were removed, no words lost
        assert t.text.endswith("Thanks so much. And until next time.")  # the end is preserved
