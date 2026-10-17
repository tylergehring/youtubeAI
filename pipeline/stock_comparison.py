"""Fetch, calculate, render, and technically validate a local price-only stock comparison."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.exceptions import InvalidInputError, MarketDataError, RenderError, VidgenError
from core.models.stock_comparison import SamplingFrequency, StockComparison
from core.stock_comparison import (
    PriceDownload,
    build_stock_comparison,
    download_daily_closes,
    latest_completed_history_end,
    validate_ticker,
)
from rendering.ffmpeg import FFmpeg, MediaInfo
from rendering.stock_comparison import (
    ChartBackground,
    StockComparisonGraphics,
    StockComparisonRenderer,
    write_decoded_review_assets,
)

_EASTERN = ZoneInfo("America/New_York")
DEFAULT_AUDIO_DIR = Path(r"C:\Users\tyler\Downloads\AiYoutube\audio")
_AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".aac"})
_YOUTUBE_HASHTAGS = ("StockMarket", "Investing", "FinancialEducation", "Shorts")
_YOUTUBE_TAGS = (
    "stock comparison",
    "historical stock performance",
    "stock market",
    "investing education",
    "price-only returns",
    "financial education",
    "personal finance",
)


@dataclass(frozen=True)
class StockComparisonRun:
    output_dir: Path
    video: Path
    comparison: StockComparison
    media_info: MediaInfo


def _suggested_title(comparison: StockComparison) -> str:
    final = comparison.points[-1]
    title = (
        f"${comparison.initial_investment:,f} in {comparison.label_a} vs. "
        f"{comparison.label_b}: ${final.value_a:,.0f} vs. ${final.value_b:,.0f} "
        f"({comparison.start_date.year}-{comparison.end_date.year}, price only)"
    )
    if len(title) <= 100:
        return title

    year_range = f"{comparison.start_date.year}-{comparison.end_date.year}"
    for name_a, name_b in (
        (comparison.label_a, comparison.label_b),
        (comparison.ticker_a, comparison.ticker_b),
    ):
        title = f"{name_a} vs. {name_b}: Price-only stock comparison ({year_range})"
        if len(title) <= 100:
            return title
    raise InvalidInputError("Could not create a YouTube title within the 100-character limit.")


def _youtube_tags(comparison: StockComparison) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for candidate in (
        comparison.ticker_a,
        comparison.label_a,
        comparison.ticker_b,
        comparison.label_b,
        *_YOUTUBE_TAGS,
    ):
        tag = candidate.strip()
        key = tag.casefold()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
    return tags


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use an ISO calendar date (YYYY-MM-DD).") from exc


def _parse_money(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(
            "Use a positive decimal amount, for example 10000."
        ) from exc
    if not amount.is_finite() or amount <= 0:
        raise argparse.ArgumentTypeError("Initial investment must be a finite amount above zero.")
    return amount


def _select_audio_track(audio_dir: Path) -> Path:
    audio_dir = audio_dir.expanduser().resolve()
    if not audio_dir.is_dir():
        raise InvalidInputError(f"Audio directory does not exist: {audio_dir}")
    try:
        tracks = sorted(
            (
                path
                for path in audio_dir.iterdir()
                if path.is_file() and path.suffix.lower() in _AUDIO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError as exc:
        raise InvalidInputError(f"Could not list audio directory {audio_dir}: {exc}") from exc
    if not tracks:
        extensions = ", ".join(sorted(_AUDIO_EXTENSIONS))
        raise InvalidInputError(
            f"No supported audio files found in {audio_dir}; expected: {extensions}."
        )
    return random.choice(tracks)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _download_rows(
    ticker: str,
    fetch_start_date: date,
    end_exclusive: date,
) -> PriceDownload:
    return download_daily_closes(ticker, fetch_start_date, end_exclusive)


def _trim_prices(download: PriceDownload, start: date, end_exclusive: date) -> dict[date, Decimal]:
    return {
        trading_date: price
        for trading_date, price in download.prices.items()
        if start <= trading_date < end_exclusive
    }


def _write_outputs(
    output_dir: Path,
    comparison: StockComparison,
    *,
    as_of_date: date,
    history_end_exclusive: date,
    yfinance_version: str,
    history_start_by_ticker: Mapping[str, date] | None = None,
    history_cache_used: bool = False,
    selected_audio: Path | None = None,
) -> None:
    if history_start_by_ticker is None:
        history_start_by_ticker = {
            comparison.ticker_a: comparison.expected_first_date_a
            or comparison.requested_start_date,
            comparison.ticker_b: comparison.requested_start_date,
        }
    source_urls = [
        f"https://finance.yahoo.com/quote/{comparison.ticker_a}/history/",
        f"https://finance.yahoo.com/quote/{comparison.ticker_b}/history/",
        "https://github.com/ranaroussi/yfinance",
    ]
    hashtags = list(_YOUTUBE_HASHTAGS)
    final = comparison.points[-1]
    description = "\n\n".join(
        [
            (
                f"A hypothetical ${comparison.initial_investment:,f} invested in each of "
                f"{comparison.label_a} ({comparison.ticker_a}) and "
                f"{comparison.label_b} ({comparison.ticker_b}) from "
                f"{comparison.start_date.isoformat()} through "
                f"{comparison.end_date.isoformat()}."
            ),
            (
                "Ending hypothetical values at the last common close:\n"
                f"{comparison.label_a} ({comparison.ticker_a}): ${final.value_a:,.2f}\n"
                f"{comparison.label_b} ({comparison.ticker_b}): ${final.value_b:,.2f}"
            ),
            (
                "Method: split-adjusted historical closing prices, not dividend-adjusted. "
                "Dividends and reinvestment are excluded. Assumes fractional shares and "
                "excludes fees, taxes, inflation, and execution effects. Values are nominal."
                f" Sampling frequency: {comparison.sampling_frequency}."
            ),
            (
                "Data: Yahoo Finance historical data retrieved through yfinance, an "
                "independent client that is not an official Yahoo Finance product. "
                "Historical data may be revised.\n"
                + "\n".join(source_urls)
            ),
            (
                "Historical illustration only—not total return, investment advice, a "
                "recommendation, or a forecast."
            ),
            " ".join(f"#{tag}" for tag in hashtags),
        ]
    )
    (output_dir / "publishing.json").write_text(
        json.dumps(
            {
                "state": "draft_metadata",
                "platform": "YouTube",
                "title": _suggested_title(comparison),
                "description": description,
                "tags": _youtube_tags(comparison),
                "hashtags": hashtags,
                "data_as_of": comparison.end_date.isoformat(),
                "sampling_frequency": comparison.sampling_frequency,
                "source_urls": source_urls,
                "assets": {
                    "video": "video.mp4",
                    "thumbnail_candidate": "cover.png",
                },
                "review_required": True,
                "review_notes": [
                    "Review the rendered video and metadata before upload.",
                    (
                        "Confirm source attribution and applicable data-use rights "
                        "before external use."
                    ),
                    "Choose audience, category, visibility, and scheduling settings in YouTube.",
                ]
                + (
                    ["Confirm music usage rights and required attribution."]
                    if selected_audio is not None
                    else []
                ),
                "uploaded": False,
                "scheduled": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "comparison.json").write_text(
        comparison.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "acquisition.json").write_text(
        json.dumps(
            {
                "provider": comparison.source,
                "provider_package": "yfinance",
                "provider_package_version": yfinance_version,
                "as_of_eastern_date": as_of_date.isoformat(),
                "history_start_inclusive": history_start_by_ticker[
                    comparison.ticker_a
                ].isoformat(),
                "history_start_inclusive_by_ticker": {
                    ticker: start.isoformat()
                    for ticker, start in history_start_by_ticker.items()
                },
                "history_cache_used": history_cache_used,
                "comparison_start_date": comparison.requested_start_date.isoformat(),
                "sampling_frequency": comparison.sampling_frequency,
                "history_end_exclusive": history_end_exclusive.isoformat(),
                "history_parameters": {
                    "interval": "1d",
                    "auto_adjust": False,
                    "actions": False,
                    "repair": False,
                    "keepna": True,
                    "prepost": False,
                    "timeout_seconds": 30,
                    "raise_errors": True,
                },
                "missing_close_rows": {
                    comparison.ticker_a: comparison.missing_close_rows_a,
                    comparison.ticker_b: comparison.missing_close_rows_b,
                },
                "missing_value_policy": (
                    "Count and omit non-finite Close rows. Intersect exact dates; do not "
                    "forward-fill, interpolate, or infer a price."
                ),
                "source_urls": source_urls,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with (
        output_dir / f"{comparison.sampling_frequency}_values.csv"
    ).open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "date",
                f"{comparison.ticker_a}_close_split_adjusted",
                f"{comparison.ticker_a}_hypothetical_value",
                f"{comparison.ticker_b}_close_split_adjusted",
                f"{comparison.ticker_b}_hypothetical_value",
            ]
        )
        for point in comparison.points:
            writer.writerow(
                [
                    point.date.isoformat(),
                    str(point.close_a),
                    f"{point.value_a:.2f}",
                    str(point.close_b),
                    f"{point.value_b:.2f}",
                ]
            )

    with (output_dir / "year_end_checkpoints.csv").open(
        "w", newline="", encoding="utf-8"
    ) as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "calendar_year",
                "last_common_trading_date",
                f"{comparison.ticker_a}_hypothetical_value",
                f"{comparison.ticker_b}_hypothetical_value",
            ]
        )
        for checkpoint in comparison.year_end_checkpoints:
            writer.writerow(
                [
                    checkpoint.year,
                    checkpoint.date.isoformat(),
                    f"{checkpoint.value_a:.2f}",
                    f"{checkpoint.value_b:.2f}",
                ]
            )

    media_note = (
        f"Background music {selected_audio.name} was selected randomly from "
        f"{selected_audio.parent}, copied into this output, and looped/faded to fit the video. "
        "Confirm usage rights and required attribution before external use."
        if selected_audio is not None
        else "No narration or music."
    )
    methodology = f"""# Method and limitations

- **Question:** What would a hypothetical {comparison.initial_investment:,.2f} invested in
  {comparison.ticker_a} and the same amount in {comparison.ticker_b} on
  {comparison.start_date.isoformat()} be worth at the last common observed close,
  {comparison.end_date.isoformat()}?
- **Data:** Yahoo Finance daily historical `Close` values retrieved with yfinance
  {yfinance_version}; start dates are inclusive and end dates exclusive. The observed first
  valid {comparison.ticker_a} date in the fetched maximum history was
  {comparison.first_available_date_a.isoformat()}.
- **Sampling frequency:** {comparison.sampling_frequency}. Daily mode uses every common
  observed close. Weekly mode uses the last common observed close in each completed
  Monday-to-Friday week and omits an incomplete current week. Source histories are still
  fetched daily so weekly observations use exact common trading dates and year-end checkpoints
  retain the last common close in each completed calendar year. Weekly sampling reduces
  comparison points and rendering work, not the amount of history downloaded.
- **Calculation:** Fractional shares are assumed. For each symbol, hypothetical value is
  initial investment × that date's historical Close ÷ the entry-date Close. Decimal
  arithmetic is rounded half-up to cents for recorded market values.
- **Adjustment:** `auto_adjust=False`; the Yahoo historical `Close` series is used as
  supplied. It reflects stock splits but not dividend reinvestment. Cash dividends are
  excluded, so this is a price-only comparison rather than total return.
- **Calendar:** Only exact dates with finite, positive closes for both symbols are compared.
  Missing rows are counted and omitted; no forward-fill, interpolation, added contributions,
  or synthetic prices are used. Each year-end checkpoint is the last common daily trading
  observation in that completed calendar year.
- **Other exclusions:** Nominal dollars; no fees, taxes, inflation adjustment, or claim that
  an investor could have executed at the displayed closing price. History can be corrected
  by the data provider. Past price movement does not establish future performance.
- **Source attribution:** Yahoo Finance historical data for {comparison.ticker_a} and
  {comparison.ticker_b}, retrieved using the independent `yfinance` Python client. The client
  is not an official Yahoo Finance product. See `acquisition.json` for source URLs and
  request parameters.
- **Media:** Original locally rendered graphics; {media_note} No generative AI, stock footage,
  upload, or schedule. Technical QA is not human editorial or legal review.
"""
    (output_dir / "methodology.md").write_text(methodology, encoding="utf-8")


def _render_prepared_comparison(
    comparison: StockComparison,
    output_dir: Path,
    *,
    as_of_date: date,
    history_end_exclusive: date,
    yfinance_version: str,
    history_start_by_ticker: Mapping[str, date] | None = None,
    history_cache_used: bool = False,
    ffmpeg: FFmpeg | None = None,
    graphics: StockComparisonGraphics | None = None,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    background: ChartBackground = "white",
    content_scale: float = 1.0,
    selected_audio: Path | None = None,
    chart_s: float = 36.0,
    end_hold_s: float = 3.0,
) -> StockComparisonRun:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise InvalidInputError(
            f"Output directory already exists and will not be overwritten: {output_dir}"
        )
    graphics = graphics or StockComparisonGraphics(
        width,
        height,
        background=background,
        content_scale=content_scale,
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    state_path = output_dir / "run_state.json"
    state_path.write_text(
        json.dumps({"state": "rendering", "video_uploaded": False}, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_outputs(
        output_dir,
        comparison,
        as_of_date=as_of_date,
        history_end_exclusive=history_end_exclusive,
        yfinance_version=yfinance_version,
        history_start_by_ticker=history_start_by_ticker,
        history_cache_used=history_cache_used,
        selected_audio=selected_audio,
    )

    renderer_ffmpeg = ffmpeg or FFmpeg.locate()
    renderer = StockComparisonRenderer(
        renderer_ffmpeg,
        graphics,
        fps=fps,
        chart_s=chart_s,
        end_hold_s=end_hold_s,
    )
    video = output_dir / "video.mp4"
    audio_snapshot: Path | None = None
    audio_track_record: dict[str, str] | None = None
    try:
        if selected_audio is not None:
            audio_snapshot = output_dir / f"audio_track{selected_audio.suffix.lower()}"
            shutil.copy2(selected_audio, audio_snapshot)
            audio_track_record = {
                "selected_file": selected_audio.name,
                "source_directory": str(selected_audio.parent),
                "snapshot": str(audio_snapshot),
                "sha256": _sha256_file(audio_snapshot),
            }
        duration_s = renderer.render(comparison, video, audio_path=audio_snapshot)
        media_info = _validate_media(
            renderer_ffmpeg,
            video,
            duration_s,
            width,
            height,
            fps,
            expected_audio=audio_snapshot is not None,
        )
        cover, contact_sheet, decoded_frames = write_decoded_review_assets(
            renderer_ffmpeg,
            video,
            output_dir / "review_frames",
            duration_s=duration_s,
            chart_s=renderer.chart_s,
            background=background,
        )
        summary: dict[str, Any] = {
            "state": "awaiting_human_review",
            "technical_validation": "passed",
            "suggested_title": _suggested_title(comparison),
            "uploaded": False,
            "scheduled": False,
            "audio_stream_present": media_info.has_audio,
            "audio_track": audio_track_record,
            "visual_style": {
                "background": background,
                "content_scale": content_scale,
            },
            "media": {
                "video": str(video),
                "duration_seconds": media_info.duration_s,
                "width": media_info.width,
                "height": media_info.height,
                "fps": media_info.fps,
                "audio": media_info.has_audio,
                "mean_volume_db": media_info.mean_volume_db,
            },
            "actual_decoded_frame_review": {
                "decoded_frame_paths": [str(path) for path in decoded_frames],
                "cover": str(cover),
                "contact_sheet": str(contact_sheet),
                "frame_source": "decoded from the encoded video.mp4 with FFmpeg",
            },
            "data": {
                "start_date": comparison.start_date.isoformat(),
                "end_date": comparison.end_date.isoformat(),
                "sampling_frequency": comparison.sampling_frequency,
                "observations": comparison.common_observation_count,
                "year_end_checkpoints": len(comparison.year_end_checkpoints),
                "entry_close_a": str(comparison.entry_price_a),
                "entry_close_b": str(comparison.entry_price_b),
                "ending_value_a": f"{comparison.points[-1].value_a:.2f}",
                "ending_value_b": f"{comparison.points[-1].value_b:.2f}",
            },
            "human_review_still_required": [
                "Review editorial framing and readability.",
                "Confirm Yahoo Finance data attribution and applicable data-use rights "
                "before any external use.",
                "No upload, publication, or scheduling was attempted.",
            ]
            + (
                ["Confirm music usage rights and any required attribution."]
                if audio_track_record is not None
                else []
            ),
        }
        (output_dir / "review_summary.json").write_text(
            json.dumps(summary, indent=2) + "\n",
            encoding="utf-8",
        )
        state_path.write_text(
            json.dumps(
                {
                    "state": "awaiting_human_review",
                    "video_uploaded": False,
                    "video_scheduled": False,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        state_path.write_text(
            json.dumps(
                {
                    "state": "failed",
                    "video_uploaded": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    return StockComparisonRun(output_dir, video, comparison, media_info)


def _validate_media(
    ffmpeg: FFmpeg,
    video: Path,
    expected_duration: float,
    expected_width: int,
    expected_height: int,
    expected_fps: int,
    expected_audio: bool = False,
) -> MediaInfo:
    info = ffmpeg.analyze(video)
    if not info.has_video:
        raise RenderError("Rendered MP4 must contain a video stream.")
    if info.has_audio != expected_audio:
        expectation = "an audio stream" if expected_audio else "no audio stream"
        raise RenderError(f"Rendered MP4 must contain {expectation}.")
    if expected_audio and (info.mean_volume_db is None or info.mean_volume_db <= -60):
        raise RenderError("Rendered audio is silent or below the -60 dB mean-volume threshold.")
    if (info.width, info.height) != (expected_width, expected_height):
        raise RenderError(
            f"Unexpected video dimensions {(info.width, info.height)}; "
            f"expected {(expected_width, expected_height)}."
        )
    if info.fps is None or abs(info.fps - expected_fps) > 0.1:
        raise RenderError(f"Unexpected video frame rate {info.fps}; expected {expected_fps} fps.")
    if abs(info.duration_s - expected_duration) > 0.15:
        raise RenderError(
            f"Unexpected video duration {info.duration_s:.3f}s; expected {expected_duration:.3f}s."
        )
    return info


def run_stock_comparison(
    *,
    ticker_a: str,
    label_a: str,
    ticker_b: str,
    label_b: str,
    start_date: date,
    output_dir: Path,
    initial_investment: Decimal = Decimal("10000"),
    expected_first_date_a: date | None = None,
    through_date: date | None = None,
    ffmpeg: FFmpeg | None = None,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    background: ChartBackground = "white",
    content_scale: float = 1.0,
    audio: bool = False,
    audio_dir: Path = DEFAULT_AUDIO_DIR,
    frequency: SamplingFrequency = "daily",
) -> StockComparisonRun:
    """Run a new one-off local job. Existing output directories are never overwritten."""
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise InvalidInputError(
            f"Output directory already exists and will not be overwritten: {output_dir}"
        )
    graphics = StockComparisonGraphics(
        width,
        height,
        background=background,
        content_scale=content_scale,
    )
    selected_audio = _select_audio_track(audio_dir) if audio else None
    symbol_a = validate_ticker(ticker_a)
    symbol_b = validate_ticker(ticker_b)
    if symbol_a == symbol_b:
        raise InvalidInputError("Choose two different ticker symbols.")
    if start_date > date.today():
        raise InvalidInputError("The requested start date must not be in the future.")

    now_eastern = datetime.now(_EASTERN)
    inclusive_cutoff, default_end_exclusive = latest_completed_history_end(now_eastern)
    if through_date is not None:
        if through_date > inclusive_cutoff:
            raise InvalidInputError(
                f"through-date must not be later than the completed-history cutoff "
                f"{inclusive_cutoff.isoformat()}."
            )
        end_exclusive = through_date + timedelta(days=1)
        completed_through_year = min(through_date.year, now_eastern.year - 1)
    else:
        end_exclusive = default_end_exclusive
        completed_through_year = now_eastern.year - 1
    if start_date >= end_exclusive:
        raise InvalidInputError("The history end must be after the requested start date.")
    if frequency not in ("daily", "weekly"):
        raise InvalidInputError(
            f"Unsupported sampling frequency {frequency!r}; choose 'daily' or 'weekly'."
        )

    verify_start = date(1900, 1, 1) if expected_first_date_a is not None else start_date
    download_a = _download_rows(symbol_a, verify_start, end_exclusive)
    download_b = _download_rows(symbol_b, start_date, end_exclusive)
    if (
        expected_first_date_a is not None
        and download_a.first_available_date != expected_first_date_a
    ):
        raise MarketDataError(
            f"First valid {symbol_a} Close in the full fetched history is "
            f"{download_a.first_available_date}; expected {expected_first_date_a}."
        )
    prices_a = _trim_prices(download_a, start_date, end_exclusive)
    prices_b = _trim_prices(download_b, start_date, end_exclusive)
    comparison = build_stock_comparison(
        ticker_a=symbol_a,
        label_a=label_a or symbol_a,
        prices_a=prices_a,
        ticker_b=symbol_b,
        label_b=label_b or symbol_b,
        prices_b=prices_b,
        requested_start_date=start_date,
        initial_investment=initial_investment,
        completed_through_year=completed_through_year,
        frequency=frequency,
        completed_through_date=through_date or inclusive_cutoff,
        expected_first_date_a=expected_first_date_a,
        first_available_date_a=download_a.first_available_date,
        missing_close_rows_a=download_a.missing_close_rows,
        missing_close_rows_b=download_b.missing_close_rows,
        download_rows_a=download_a.download_rows,
        download_rows_b=download_b.download_rows,
    )
    if comparison.end_date > inclusive_cutoff:
        raise MarketDataError(
            f"The provider returned a close on {comparison.end_date} beyond the completed "
            f"history cutoff {inclusive_cutoff}."
        )

    return _render_prepared_comparison(
        comparison,
        output_dir,
        as_of_date=now_eastern.date(),
        history_end_exclusive=end_exclusive,
        yfinance_version=version("yfinance"),
        history_start_by_ticker={symbol_a: verify_start, symbol_b: start_date},
        ffmpeg=ffmpeg,
        graphics=graphics,
        fps=fps,
        width=width,
        height=height,
        background=background,
        content_scale=content_scale,
        selected_audio=selected_audio,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.stock_comparison",
        description=__doc__,
    )
    parser.add_argument("--ticker-a", required=True, help="First Yahoo Finance ticker.")
    parser.add_argument("--label-a", default="", help="Optional on-screen label for ticker A.")
    parser.add_argument("--ticker-b", required=True, help="Second Yahoo Finance ticker.")
    parser.add_argument("--label-b", default="", help="Optional on-screen label for ticker B.")
    parser.add_argument(
        "--start-date", required=True, type=_parse_date, help="Inclusive first date."
    )
    parser.add_argument(
        "--expected-first-date-a",
        type=_parse_date,
        help="If supplied, fetch ticker A's full history and require this first valid date.",
    )
    parser.add_argument(
        "--through-date",
        type=_parse_date,
        help="Optional inclusive date cap; defaults to the latest completed U.S. session.",
    )
    parser.add_argument("--initial-investment", type=_parse_money, default=Decimal("10000"))
    parser.add_argument(
        "--frequency",
        choices=("daily", "weekly"),
        default="daily",
        help="Comparison sampling frequency; weekly uses completed weeks (default: daily).",
    )
    parser.add_argument(
        "--background",
        choices=("white", "black"),
        default="white",
        help="Chart background color (default: white).",
    )
    parser.add_argument(
        "--content-scale",
        type=float,
        default=1.0,
        help="Scale chart content within the 9:16 canvas (0.5-1.0; default: 1.0).",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Randomly select a local music track and add it to the video.",
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=DEFAULT_AUDIO_DIR,
        help=f"Music folder used with --audio (default: {DEFAULT_AUDIO_DIR}).",
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="New, non-existing output directory."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        ffmpeg = FFmpeg.locate(os.environ.get("FFMPEG_PATH"))
        result = run_stock_comparison(
            ticker_a=args.ticker_a,
            label_a=args.label_a,
            ticker_b=args.ticker_b,
            label_b=args.label_b,
            start_date=args.start_date,
            output_dir=args.out,
            initial_investment=args.initial_investment,
            expected_first_date_a=args.expected_first_date_a,
            through_date=args.through_date,
            ffmpeg=ffmpeg,
            background=args.background,
            content_scale=args.content_scale,
            audio=args.audio,
            audio_dir=args.audio_dir,
            frequency=args.frequency,
        )
    except (VidgenError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2

    final = result.comparison.points[-1]
    print(f"output: {result.output_dir}")
    print(f"video: {result.video}")
    print(
        f"dates: {result.comparison.start_date.isoformat()} through "
        f"{result.comparison.end_date.isoformat()} "
        f"({result.comparison.common_observation_count} {result.comparison.sampling_frequency} "
        "observations)"
    )
    print(
        f"{result.comparison.ticker_a}: ${final.value_a:,.2f}; "
        f"{result.comparison.ticker_b}: ${final.value_b:,.2f}"
    )
    print(
        f"media: {result.media_info.width}x{result.media_info.height}, "
        f"{result.media_info.fps:g} fps, {result.media_info.duration_s:.2f}s, "
        f"{'with audio' if result.media_info.has_audio else 'silent'}"
    )
    print(f"contact sheet: {result.output_dir / 'contact_sheet.png'}")
    print("Awaiting human review. Nothing has been uploaded, published, or scheduled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
