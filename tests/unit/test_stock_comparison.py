from __future__ import annotations

import json
import math
import struct
import wave
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.exceptions import InvalidInputError, MarketDataError
from core.stock_comparison import (
    PriceDownload,
    build_stock_comparison,
    download_daily_closes,
    latest_completed_history_end,
)
from pipeline import stock_comparison_batch
from pipeline.stock_comparison import (
    DEFAULT_AUDIO_DIR,
    _parser,
    _select_audio_track,
    _suggested_title,
    _validate_media,
    _write_outputs,
    run_stock_comparison,
)
from rendering.ffmpeg import FFmpeg
from rendering.stock_comparison import (
    StockComparisonGraphics,
    StockComparisonRenderer,
)
from tests.conftest import FFMPEG


class _FakeCloseSeries:
    def __init__(self, rows: list[tuple[datetime, object]]) -> None:
        self._rows = rows

    def items(self) -> list[tuple[datetime, object]]:
        return self._rows


class _FakeHistoryFrame:
    def __init__(
        self,
        rows: list[tuple[datetime, object]],
        *,
        columns: tuple[str, ...] = ("Close",),
    ) -> None:
        self.columns = columns
        self.empty = not rows
        self._rows = rows

    def __getitem__(self, key: str) -> _FakeCloseSeries:
        if key != "Close":
            raise KeyError(key)
        return _FakeCloseSeries(self._rows)

    def __len__(self) -> int:
        return len(self._rows)


class _FakeTicker:
    def __init__(self, history: _FakeHistoryFrame, calls: list[dict[str, Any]]) -> None:
        self._history = history
        self._calls = calls

    def history(self, **kwargs: Any) -> _FakeHistoryFrame:
        self._calls.append(kwargs)
        return self._history


def _ticker_factory(history: _FakeHistoryFrame, calls: list[dict[str, Any]]) -> Any:
    return lambda _symbol: _FakeTicker(history, calls)


def _comparison(initial_investment: Decimal = Decimal("100")):
    dates = [
        date(2020, 12, 31),
        date(2021, 1, 4),
        date(2021, 12, 31),
        date(2022, 1, 3),
    ]
    return build_stock_comparison(
        ticker_a="AMZN",
        label_a="Amazon",
        prices_a={
            dates[0]: Decimal("10"),
            dates[1]: Decimal("12"),
            dates[2]: Decimal("20"),
            dates[3]: Decimal("25"),
        },
        ticker_b="WMT",
        label_b="Walmart",
        prices_b={
            dates[0]: Decimal("50"),
            dates[1]: Decimal("40"),
            dates[2]: Decimal("25"),
            dates[3]: Decimal("20"),
        },
        requested_start_date=dates[0],
        initial_investment=initial_investment,
        completed_through_year=2021,
        first_available_date_a=dates[0],
        download_rows_a=4,
        download_rows_b=4,
    )


def _write_test_tone(path: Path) -> None:
    sample_rate = 8000
    frames = b"".join(
        struct.pack("<h", round(12000 * math.sin(2 * math.pi * 440 * index / sample_rate)))
        for index in range(sample_rate)
    )
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(frames)


def test_download_daily_closes_uses_close_without_dividend_adjustment_and_omits_missing() -> None:
    history = _FakeHistoryFrame(
        [
            (datetime(1997, 5, 15), 0.09791699796915054),
            (datetime(1997, 5, 16), float("nan")),
            (datetime(1997, 5, 19), 0.085),
        ]
    )
    calls: list[dict[str, Any]] = []

    result = download_daily_closes(
        "amzn",
        date(1997, 5, 15),
        date(1997, 5, 20),
        ticker_factory=_ticker_factory(history, calls),
    )

    assert result.ticker == "AMZN"
    assert result.first_available_date == date(1997, 5, 15)
    assert result.last_available_date == date(1997, 5, 19)
    assert result.missing_close_rows == 1
    assert result.download_rows == 3
    assert result.prices[date(1997, 5, 15)] == Decimal("0.09791699796915054")
    assert calls == [
        {
            "start": "1997-05-15",
            "end": "1997-05-20",
            "interval": "1d",
            "auto_adjust": False,
            "actions": False,
            "repair": False,
            "keepna": True,
            "prepost": False,
            "timeout": 30,
            "raise_errors": True,
        }
    ]


@pytest.mark.parametrize("value", [0, -1, float("inf"), "not-a-price"])
def test_download_daily_closes_rejects_invalid_prices(value: object) -> None:
    history = _FakeHistoryFrame([(datetime(2020, 1, 2), value)])

    with pytest.raises(MarketDataError, match="invalid Close|non-numeric Close"):
        download_daily_closes(
            "AMZN",
            date(2020, 1, 1),
            date(2020, 1, 3),
            ticker_factory=_ticker_factory(history, []),
        )


def test_download_daily_closes_rejects_empty_or_missing_close_column() -> None:
    empty_history = _FakeHistoryFrame([])
    with pytest.raises(MarketDataError, match="no daily history"):
        download_daily_closes(
            "AMZN",
            date(2020, 1, 1),
            date(2020, 1, 3),
            ticker_factory=_ticker_factory(empty_history, []),
        )

    wrong_column = _FakeHistoryFrame([(datetime(2020, 1, 2), 10)], columns=("Adj Close",))
    with pytest.raises(MarketDataError, match="no 'Close' column"):
        download_daily_closes(
            "AMZN",
            date(2020, 1, 1),
            date(2020, 1, 3),
            ticker_factory=_ticker_factory(wrong_column, []),
        )


def test_build_stock_comparison_uses_exact_common_dates_and_completed_year_ends() -> None:
    comparison = _comparison()

    assert comparison.start_date == date(2020, 12, 31)
    assert comparison.end_date == date(2022, 1, 3)
    assert comparison.common_observation_count == 4
    assert comparison.points[2].value_a == Decimal("200.00")
    assert comparison.points[2].value_b == Decimal("50.00")
    assert [(row.year, row.date) for row in comparison.year_end_checkpoints] == [
        (2020, date(2020, 12, 31)),
        (2021, date(2021, 12, 31)),
    ]
    assert comparison.points[-1].date not in {
        checkpoint.date for checkpoint in comparison.year_end_checkpoints
    }


def test_weekly_sampling_uses_completed_weeks_and_daily_year_end_checkpoints() -> None:
    dates = [
        date(2024, 12, 23),
        date(2024, 12, 27),
        date(2024, 12, 30),
        date(2024, 12, 31),
        date(2025, 1, 2),
        date(2025, 1, 3),
        date(2025, 1, 6),
        date(2025, 1, 7),
    ]
    comparison = build_stock_comparison(
        ticker_a="AMZN",
        label_a="Amazon",
        prices_a={
            trading_date: Decimal(str(index * 10))
            for index, trading_date in enumerate(dates, start=1)
        },
        ticker_b="WMT",
        label_b="Walmart",
        prices_b={
            trading_date: Decimal(str(index * 100))
            for index, trading_date in enumerate(dates, start=1)
        },
        requested_start_date=dates[0],
        initial_investment=Decimal("100"),
        completed_through_year=2024,
        frequency="weekly",
        completed_through_date=date(2025, 1, 7),
    )

    assert comparison.sampling_frequency == "weekly"
    assert [point.date for point in comparison.points] == [
        date(2024, 12, 27),
        date(2025, 1, 3),
    ]
    assert comparison.points[1].value_a == Decimal("300.00")
    assert [(row.year, row.date) for row in comparison.year_end_checkpoints] == [
        (2024, date(2024, 12, 31))
    ]
    assert comparison.year_end_checkpoints[0].value_a == Decimal("200.00")

    starts_after_2024_checkpoint = build_stock_comparison(
        ticker_a="AMZN",
        label_a="Amazon",
        prices_a={trading_date: Decimal("10") for trading_date in dates[3:6]},
        ticker_b="WMT",
        label_b="Walmart",
        prices_b={trading_date: Decimal("20") for trading_date in dates[3:6]},
        requested_start_date=date(2024, 12, 31),
        initial_investment=Decimal("100"),
        completed_through_year=2024,
        frequency="weekly",
        completed_through_date=date(2025, 1, 3),
    )

    assert starts_after_2024_checkpoint.start_date == date(2025, 1, 3)
    assert starts_after_2024_checkpoint.year_end_checkpoints == []


def test_build_stock_comparison_fails_on_unverified_start_or_no_common_data() -> None:
    with pytest.raises(MarketDataError, match="first valid fetched Close"):
        build_stock_comparison(
            ticker_a="AMZN",
            label_a="Amazon",
            prices_a={date(1997, 5, 15): Decimal("1")},
            ticker_b="WMT",
            label_b="Walmart",
            prices_b={date(1997, 5, 15): Decimal("2")},
            requested_start_date=date(1997, 5, 15),
            initial_investment=Decimal("10000"),
            completed_through_year=2025,
            expected_first_date_a=date(1997, 5, 15),
            first_available_date_a=date(1997, 5, 16),
        )

    with pytest.raises(MarketDataError, match="no common valid daily closes"):
        build_stock_comparison(
            ticker_a="AMZN",
            label_a="Amazon",
            prices_a={date(2020, 1, 2): Decimal("1")},
            ticker_b="WMT",
            label_b="Walmart",
            prices_b={date(2020, 1, 3): Decimal("2")},
            requested_start_date=date(2020, 1, 1),
            initial_investment=Decimal("10000"),
            completed_through_year=2019,
        )


def test_history_cutoff_excludes_an_in_progress_us_trading_day() -> None:
    cutoff, end_exclusive = latest_completed_history_end(
        datetime.fromisoformat("2026-10-13T15:59:00-04:00")
    )
    assert cutoff == date(2026, 10, 12)
    assert end_exclusive == date(2026, 10, 13)

    cutoff, end_exclusive = latest_completed_history_end(
        datetime.fromisoformat("2026-10-13T16:16:00-04:00")
    )
    assert cutoff == date(2026, 10, 13)
    assert end_exclusive == date(2026, 10, 14)


def test_batch_manifest_loads_windows_powershell_utf8_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "ticker_a": "AAA",
                    "label_a": "Alpha",
                    "ticker_b": "BBB",
                    "label_b": "Beta",
                    "start_date": "2020-01-02",
                    "video_name": "alpha_beta",
                    "initial_investment": "10000",
                }
            ]
        ),
        encoding="utf-8-sig",
    )

    requests = stock_comparison_batch.load_manifest(manifest_path)

    assert len(requests) == 1
    assert requests[0].ticker_a == "AAA"
    assert requests[0].start_date == date(2020, 1, 2)
    assert requests[0].initial_investment == Decimal("10000")


def test_batch_parser_accepts_explicit_background() -> None:
    required_args = [
        "--manifest",
        "manifest.json",
        "--output-root",
        "output",
        "--report",
        "report.json",
    ]

    args = stock_comparison_batch._parser().parse_args(
        [
            *required_args,
            "--frequency",
            "weekly",
            "--background",
            "black",
            "--content-scale",
            "0.9",
            "--audio",
            "--audio-dir",
            "music",
        ]
    )

    assert args.frequency == "weekly"
    assert args.background == "black"
    assert args.content_scale == 0.9
    assert args.audio is True
    assert args.audio_dir == Path("music")


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_batch_reuses_ticker_histories_and_renders_with_process_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]
    prices = {
        "AAA": dict(zip(dates, map(Decimal, ("10", "11", "12")), strict=True)),
        "BBB": dict(zip(dates, map(Decimal, ("20", "19", "18")), strict=True)),
        "CCC": dict(zip(dates, map(Decimal, ("30", "31", "32")), strict=True)),
    }
    calls: list[tuple[str, date, date]] = []

    def fake_download(ticker: str, start: date, end: date) -> PriceDownload:
        calls.append((ticker, start, end))
        selected = {day: close for day, close in prices[ticker].items() if start <= day < end}
        return PriceDownload(
            ticker=ticker,
            prices=selected,
            first_available_date=min(selected),
            last_available_date=max(selected),
            missing_close_rows=0,
            download_rows=len(selected),
        )

    monkeypatch.setattr(stock_comparison_batch, "download_daily_closes", fake_download)
    monkeypatch.setattr(stock_comparison_batch, "version", lambda _package: "0.2.66")
    requests = [
        stock_comparison_batch.BatchComparisonRequest(
            ticker_a="AAA",
            label_a="Alpha",
            ticker_b="BBB",
            label_b="Beta",
            start_date=dates[0],
            video_name="alpha_beta",
        ),
        stock_comparison_batch.BatchComparisonRequest(
            ticker_a="AAA",
            label_a="Alpha",
            ticker_b="CCC",
            label_b="Gamma",
            start_date=dates[1],
            video_name="alpha_gamma",
        ),
    ]
    output_root = tmp_path / "batch"
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    _write_test_tone(audio_dir / "tone.wav")

    report = stock_comparison_batch.run_stock_comparison_batch(
        requests,
        output_root,
        workers=2,
        ffmpeg_path=str(FFMPEG),
        fps=5,
        width=270,
        height=480,
        background="black",
        content_scale=0.9,
        audio=True,
        audio_dir=audio_dir,
        frequency="weekly",
        chart_s=3,
        end_hold_s=1,
    )

    assert len(calls) == 3
    assert {(ticker, start) for ticker, start, _end in calls} == {
        ("AAA", dates[0]),
        ("BBB", dates[0]),
        ("CCC", dates[1]),
    }
    assert report["distinct_tickers"] == 3
    assert report["sampling_frequency"] == "weekly"
    assert report["succeeded"] == 2
    assert report["failed"] == 0
    for name in ("alpha_beta", "alpha_gamma"):
        output = output_root / name
        assert (output / "video.mp4").is_file()
        acquisition = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
        assert acquisition["history_cache_used"]
        assert acquisition["sampling_frequency"] == "weekly"
        assert acquisition["history_start_inclusive_by_ticker"]["AAA"] == "2020-01-02"
        comparison_data = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
        assert comparison_data["sampling_frequency"] == "weekly"
        assert (output / "weekly_values.csv").is_file()
        summary = json.loads((output / "review_summary.json").read_text(encoding="utf-8"))
        assert summary["data"]["sampling_frequency"] == "weekly"
        assert summary["audio_stream_present"]
        assert summary["visual_style"] == {"background": "black", "content_scale": 0.9}
        assert summary["audio_track"]["selected_file"] == "tone.wav"
        assert (output / "audio_track.wav").is_file()


def test_run_refuses_to_overwrite_an_existing_directory(tmp_path: Path) -> None:
    output = tmp_path / "already-used"
    output.mkdir()
    (output / "keep.txt").write_text("existing state", encoding="utf-8")

    with pytest.raises(InvalidInputError, match="will not be overwritten"):
        run_stock_comparison(
            ticker_a="AMZN",
            label_a="Amazon",
            ticker_b="WMT",
            label_b="Walmart",
            start_date=date(1997, 5, 15),
            output_dir=output,
        )

    assert (output / "keep.txt").read_text(encoding="utf-8") == "existing state"


def test_chart_starts_immediately_and_axes_expand_with_visible_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison = build_stock_comparison(
        ticker_a="AMZN",
        label_a="Amazon",
        prices_a={
            date(2020, 12, 31): Decimal("10"),
            date(2021, 1, 4): Decimal("1000"),
            date(2022, 1, 3): Decimal("25000"),
        },
        ticker_b="WMT",
        label_b="Walmart",
        prices_b={
            date(2020, 12, 31): Decimal("50"),
            date(2021, 1, 4): Decimal("45"),
            date(2022, 1, 3): Decimal("20"),
        },
        requested_start_date=date(2020, 12, 31),
        initial_investment=Decimal("10000"),
        completed_through_year=2021,
    )
    graphics = StockComparisonGraphics(270, 480)
    labels: list[str] = []

    def capture_text(_draw: object, text: str, *_args: object, **_kwargs: object) -> None:
        labels.append(text)

    monkeypatch.setattr(graphics, "_text", capture_text)
    start = graphics.frame(comparison, 0, chart_s=3, end_hold_s=1)
    start_labels = set(labels)
    labels.clear()
    middle = graphics.frame(comparison, 1.5, chart_s=3, end_hold_s=1)
    labels.clear()
    end = graphics.frame(comparison, 2.9, chart_s=3, end_hold_s=1)
    end_labels = set(labels)
    labels.clear()
    held = graphics.frame(comparison, 3.5, chart_s=3, end_hold_s=1)

    assert start.size == (270, 480)
    assert start.mode == "RGB"
    assert start.getpixel((0, 0)) == (255, 255, 255)
    assert len({frame.tobytes() for frame in (start, middle, end)}) == 3
    assert held.tobytes() == end.tobytes()
    assert start.getpixel((38, 410)) == (40, 120, 184)
    assert middle.getpixel((45, 85)) == (197, 34, 34)
    assert end.getpixel((205, 85)) == (197, 34, 34)
    assert "Amazon and Walmart since 2020" in start_labels
    assert "$10,000" in start_labels
    assert "$1K" not in start_labels
    assert "$10,000" in end_labels
    assert "$25.0M" in end_labels
    assert "$1B" not in end_labels
    assert "2022" in end_labels
    assert not any(
        "Historical illustration" in label or "Price-only" in label or "Log scale" in label
        for label in start_labels
    )


def test_black_background_uses_contrasting_palette_and_no_bottom_subtext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graphics = StockComparisonGraphics(270, 480, background="black")
    labels: list[str] = []

    def capture_text(_draw: object, text: str, *_args: object, **_kwargs: object) -> None:
        labels.append(text)

    monkeypatch.setattr(graphics, "_text", capture_text)
    frame = graphics.frame(_comparison(), 0.0)

    assert frame.getpixel((0, 0)) == (0, 0, 0)
    assert frame.getpixel((38, 410)) == (96, 165, 250)
    assert "Amazon and Walmart since 2020" in labels
    assert not any(
        "Historical illustration" in label or "Price-only" in label or "Log scale" in label
        for label in labels
    )


def test_cli_background_defaults_to_white_and_accepts_black() -> None:
    common_args = [
        "--ticker-a",
        "AMZN",
        "--ticker-b",
        "WMT",
        "--start-date",
        "2020-01-01",
        "--out",
        "new-output",
    ]

    defaults = _parser().parse_args(common_args)
    assert defaults.frequency == "daily"
    assert defaults.background == "white"
    assert defaults.content_scale == 1.0
    assert defaults.audio is False
    assert defaults.audio_dir == DEFAULT_AUDIO_DIR

    black_with_audio = _parser().parse_args(
        [
            *common_args,
            "--background",
            "black",
            "--frequency",
            "weekly",
            "--content-scale",
            "0.9",
            "--audio",
            "--audio-dir",
            "music",
        ]
    )
    assert black_with_audio.background == "black"
    assert black_with_audio.frequency == "weekly"
    assert black_with_audio.content_scale == 0.9
    assert black_with_audio.audio is True
    assert black_with_audio.audio_dir == Path("music")

    with pytest.raises(SystemExit):
        _parser().parse_args([*common_args, "--background", "gray"])


def test_audio_selector_picks_from_sorted_supported_tracks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "z-last.MP3").write_bytes(b"z")
    (audio_dir / "a-first.wav").write_bytes(b"a")
    (audio_dir / "ignore.txt").write_text("not audio", encoding="utf-8")

    seen: list[list[Path]] = []

    def choose(tracks: list[Path]) -> Path:
        seen.append(tracks)
        return tracks[-1]

    monkeypatch.setattr("pipeline.stock_comparison.random.choice", choose)

    assert _select_audio_track(audio_dir) == audio_dir / "z-last.MP3"
    assert seen == [[audio_dir / "a-first.wav", audio_dir / "z-last.MP3"]]


def test_audio_selector_reports_missing_or_empty_directories(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match="Audio directory does not exist"):
        _select_audio_track(tmp_path / "missing")

    with pytest.raises(InvalidInputError, match="No supported audio files"):
        _select_audio_track(tmp_path)


def test_chart_header_uses_configured_investment_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison = _comparison(Decimal("2500.50"))
    graphics = StockComparisonGraphics(540, 960)
    labels: list[str] = []

    def capture_text(_draw: object, text: str, *_args: object, **_kwargs: object) -> None:
        labels.append(text)

    monkeypatch.setattr(graphics, "_text", capture_text)
    graphics.frame(comparison, 0.0)

    assert "Hypothetical $2,500.50 each • Dec 31, 2020 start" in labels


def test_content_scale_centers_chart_on_padded_canvas() -> None:
    comparison = _comparison()
    outer = StockComparisonGraphics(540, 960, background="black", content_scale=0.9)
    inner = StockComparisonGraphics(486, 864, background="black")

    frame = outer.frame(comparison, 0.0)
    expected_content = inner.frame(comparison, 0.0)

    assert frame.size == (540, 960)
    assert frame.crop((27, 48, 513, 912)).tobytes() == expected_content.tobytes()
    assert frame.getpixel((0, 480)) == (0, 0, 0)


def test_output_metadata_title_uses_computed_final_values(tmp_path: Path) -> None:
    comparison = _comparison()

    _write_outputs(
        tmp_path,
        comparison,
        as_of_date=date(2022, 1, 4),
        history_end_exclusive=date(2022, 1, 5),
        yfinance_version="0.2.66",
    )

    metadata = json.loads((tmp_path / "publishing.json").read_text(encoding="utf-8"))
    assert _suggested_title(comparison) == (
        "$100 in Amazon vs. Walmart: $250 vs. $40 (2020-2022, price only)"
    )
    assert metadata["title"] == _suggested_title(comparison)
    assert len(metadata["title"]) <= 100
    assert metadata["platform"] == "YouTube"
    assert metadata["sampling_frequency"] == "daily"
    assert metadata["tags"][:4] == ["AMZN", "Amazon", "WMT", "Walmart"]
    assert metadata["hashtags"] == [
        "StockMarket",
        "Investing",
        "FinancialEducation",
        "Shorts",
    ]
    assert "#StockMarket" in metadata["description"]
    assert "$250.00" in metadata["description"]
    assert "$40.00" in metadata["description"]
    assert "Dividends and reinvestment are excluded" in metadata["description"]
    assert metadata["source_urls"] == [
        "https://finance.yahoo.com/quote/AMZN/history/",
        "https://finance.yahoo.com/quote/WMT/history/",
        "https://github.com/ranaroussi/yfinance",
    ]
    assert metadata["assets"] == {
        "video": "video.mp4",
        "thumbnail_candidate": "cover.png",
    }
    assert metadata["review_required"]
    assert metadata["uploaded"] is False
    assert metadata["scheduled"] is False


def test_suggested_youtube_title_stays_within_limit_for_long_labels() -> None:
    comparison = _comparison().model_copy(
        update={"label_a": "A" * 40, "label_b": "B" * 40}
    )

    title = _suggested_title(comparison)

    assert len(title) <= 100
    assert title.startswith("AMZN vs. WMT:")


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_stock_comparison_renderer_encodes_silent_vertical_media(tmp_path: Path) -> None:
    ffmpeg = FFmpeg.locate(FFMPEG)
    renderer = StockComparisonRenderer(
        ffmpeg,
        StockComparisonGraphics(270, 480),
        fps=2,
        chart_s=3,
        end_hold_s=2,
    )
    destination = tmp_path / "stock_comparison.mp4"

    duration = renderer.render(_comparison(), destination)
    info = _validate_media(ffmpeg, destination, duration, 270, 480, 2)

    assert duration == 5
    assert info.has_video
    assert not info.has_audio
    assert (info.width, info.height) == (270, 480)
    assert info.fps == 2
    assert info.duration_s == pytest.approx(5, abs=0.1)
    assert not list(tmp_path.glob("*.partial.mp4"))


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_stock_comparison_renderer_loops_and_encodes_optional_audio(tmp_path: Path) -> None:
    ffmpeg = FFmpeg.locate(FFMPEG)
    renderer = StockComparisonRenderer(
        ffmpeg,
        StockComparisonGraphics(270, 480),
        fps=2,
        chart_s=3,
        end_hold_s=2,
    )
    audio_path = tmp_path / "tone.wav"
    _write_test_tone(audio_path)
    destination = tmp_path / "stock_comparison_with_audio.mp4"

    duration = renderer.render(_comparison(), destination, audio_path=audio_path)
    info = _validate_media(
        ffmpeg,
        destination,
        duration,
        270,
        480,
        2,
        expected_audio=True,
    )

    assert duration == 5
    assert info.has_video
    assert info.has_audio
    assert info.mean_volume_db is not None
    assert info.mean_volume_db > -60
    assert (info.width, info.height) == (270, 480)
    assert info.duration_s == pytest.approx(5, abs=0.1)
    assert not list(tmp_path.glob("*.partial.mp4"))
