"""Batch-fetch shared ticker histories and render stock comparisons with bounded workers."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import date, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import InvalidInputError, MarketDataError, VidgenError
from core.models.stock_comparison import SamplingFrequency, StockComparison
from core.stock_comparison import (
    PriceDownload,
    build_stock_comparison,
    download_daily_closes,
    latest_completed_history_end,
    validate_ticker,
)
from pipeline.stock_comparison import (
    DEFAULT_AUDIO_DIR,
    _render_prepared_comparison,
    _select_audio_track,
    _trim_prices,
)
from rendering.ffmpeg import FFmpeg
from rendering.stock_comparison import ChartBackground, validate_content_scale

_EASTERN = ZoneInfo("America/New_York")
_MAX_RENDER_WORKERS = 4


class BatchComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker_a: str = Field(min_length=1, max_length=20)
    label_a: str = Field(min_length=1, max_length=40)
    ticker_b: str = Field(min_length=1, max_length=20)
    label_b: str = Field(min_length=1, max_length=40)
    start_date: date
    video_name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    initial_investment: Decimal = Field(default=Decimal("10000"), gt=0)


def load_manifest(path: Path) -> list[BatchComparisonRequest]:
    try:
        content = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInputError(f"Could not read batch manifest {path}: {exc}") from exc
    if not isinstance(content, list) or not content:
        raise InvalidInputError("The batch manifest must contain a non-empty JSON array.")
    try:
        requests = [BatchComparisonRequest.model_validate(item) for item in content]
    except ValidationError as exc:
        raise InvalidInputError(f"Invalid batch manifest: {exc}") from exc
    names = [request.video_name for request in requests]
    if len(set(names)) != len(names):
        raise InvalidInputError("The batch manifest contains duplicate video names.")
    return requests


def _render_worker(payload: dict[str, Any]) -> dict[str, Any]:
    comparison = StockComparison.model_validate_json(payload["comparison_json"])
    history_starts = {
        ticker: date.fromisoformat(start)
        for ticker, start in payload["history_start_by_ticker"].items()
    }
    ffmpeg = FFmpeg.locate(payload["ffmpeg_path"])
    result = _render_prepared_comparison(
        comparison,
        Path(payload["output_dir"]),
        as_of_date=date.fromisoformat(payload["as_of_date"]),
        history_end_exclusive=date.fromisoformat(payload["history_end_exclusive"]),
        yfinance_version=payload["yfinance_version"],
        history_start_by_ticker=history_starts,
        history_cache_used=True,
        ffmpeg=ffmpeg,
        fps=payload["fps"],
        width=payload["width"],
        height=payload["height"],
        background=payload["background"],
        content_scale=payload["content_scale"],
        selected_audio=(
            Path(payload["selected_audio"]) if payload["selected_audio"] is not None else None
        ),
        chart_s=payload["chart_s"],
        end_hold_s=payload["end_hold_s"],
    )
    return {
        "video": str(result.video),
        "duration_seconds": result.media_info.duration_s,
        "width": result.media_info.width,
        "height": result.media_info.height,
        "fps": result.media_info.fps,
    }


def run_stock_comparison_batch(
    requests: list[BatchComparisonRequest],
    output_root: Path,
    *,
    workers: int = 2,
    ffmpeg_path: str | None = None,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    background: ChartBackground = "white",
    content_scale: float = 1.0,
    audio: bool = False,
    audio_dir: Path = DEFAULT_AUDIO_DIR,
    frequency: SamplingFrequency = "daily",
    chart_s: float = 36.0,
    end_hold_s: float = 3.0,
) -> dict[str, Any]:
    if not 1 <= workers <= _MAX_RENDER_WORKERS:
        raise InvalidInputError(
            f"Render workers must be between 1 and {_MAX_RENDER_WORKERS}; received {workers}."
        )
    if not requests:
        raise InvalidInputError("At least one comparison is required.")
    if frequency not in ("daily", "weekly"):
        raise InvalidInputError(
            f"Unsupported sampling frequency {frequency!r}; choose 'daily' or 'weekly'."
        )
    content_scale = validate_content_scale(content_scale)
    names = [request.video_name for request in requests]
    if len(set(names)) != len(names):
        raise InvalidInputError("The batch contains duplicate video names.")

    output_root = output_root.resolve()
    now_eastern = datetime.now(_EASTERN)
    inclusive_cutoff, end_exclusive = latest_completed_history_end(now_eastern)
    results: dict[int, dict[str, Any]] = {}
    valid_requests: list[tuple[int, BatchComparisonRequest, str, str, Path]] = []
    fetch_starts: dict[str, date] = {}

    for index, request in enumerate(requests):
        output_dir = output_root / request.video_name
        try:
            ticker_a = validate_ticker(request.ticker_a)
            ticker_b = validate_ticker(request.ticker_b)
            if ticker_a == ticker_b:
                raise InvalidInputError("Choose two different ticker symbols.")
            if request.start_date >= end_exclusive:
                raise InvalidInputError(
                    "The requested start date is later than the completed-history cutoff."
                )
            if output_dir.exists():
                raise InvalidInputError(
                    f"Output directory already exists and will not be overwritten: {output_dir}"
                )
        except VidgenError as exc:
            results[index] = {
                "video_name": request.video_name,
                "output_dir": str(output_dir),
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            continue

        valid_requests.append((index, request, ticker_a, ticker_b, output_dir))
        for ticker in (ticker_a, ticker_b):
            if ticker not in fetch_starts or request.start_date < fetch_starts[ticker]:
                fetch_starts[ticker] = request.start_date

    selected_audio_by_index = {}
    if audio:
        for index, *_ in valid_requests:
            selected_audio_by_index[index] = _select_audio_track(audio_dir)

    downloads: dict[str, PriceDownload] = {}
    download_errors: dict[str, str] = {}
    print(
        f"Fetching daily histories for {len(fetch_starts)} distinct tickers "
        f"({frequency} comparison sampling).",
        flush=True,
    )
    for ticker, start in sorted(fetch_starts.items()):
        print(f"  {ticker}: from {start.isoformat()}", flush=True)
        try:
            downloads[ticker] = download_daily_closes(ticker, start, end_exclusive)
        except VidgenError as exc:
            download_errors[ticker] = f"{type(exc).__name__}: {exc}"

    if valid_requests:
        render_workers = min(workers, len(valid_requests))
        print(
            f"Rendering up to {len(valid_requests)} comparisons with "
            f"{render_workers} worker(s) ({frequency} sampling).",
            flush=True,
        )
        history_end = end_exclusive.isoformat()
        as_of = now_eastern.date().isoformat()
        yfinance_version = version("yfinance")
        selected_ffmpeg_path = (
            ffmpeg_path if ffmpeg_path is not None else os.environ.get("FFMPEG_PATH")
        )
        with ProcessPoolExecutor(max_workers=render_workers) as executor:
            future_jobs = {}
            next_request = 0
            while next_request < len(valid_requests) or future_jobs:
                while next_request < len(valid_requests) and len(future_jobs) < render_workers:
                    index, request, ticker_a, ticker_b, output_dir = valid_requests[next_request]
                    next_request += 1
                    if ticker_a in download_errors or ticker_b in download_errors:
                        failed_ticker = ticker_a if ticker_a in download_errors else ticker_b
                        error = (
                            f"{failed_ticker} history fetch failed: "
                            f"{download_errors[failed_ticker]}"
                        )
                        results[index] = {
                            "video_name": request.video_name,
                            "output_dir": str(output_dir),
                            "success": False,
                            "error": error,
                        }
                        print(f"FAILED: {request.video_name}: {error}", flush=True)
                        continue

                    download_a = downloads[ticker_a]
                    download_b = downloads[ticker_b]
                    try:
                        comparison = build_stock_comparison(
                            ticker_a=ticker_a,
                            label_a=request.label_a,
                            prices_a=_trim_prices(
                                download_a, request.start_date, end_exclusive
                            ),
                            ticker_b=ticker_b,
                            label_b=request.label_b,
                            prices_b=_trim_prices(
                                download_b, request.start_date, end_exclusive
                            ),
                            requested_start_date=request.start_date,
                            initial_investment=request.initial_investment,
                            completed_through_year=now_eastern.year - 1,
                            first_available_date_a=download_a.first_available_date,
                            missing_close_rows_a=download_a.missing_close_rows,
                            missing_close_rows_b=download_b.missing_close_rows,
                            download_rows_a=download_a.download_rows,
                            download_rows_b=download_b.download_rows,
                            frequency=frequency,
                            completed_through_date=inclusive_cutoff,
                        )
                        if comparison.end_date > inclusive_cutoff:
                            raise MarketDataError(
                                f"The provider returned a close on {comparison.end_date} "
                                f"beyond the completed history cutoff "
                                f"{inclusive_cutoff.isoformat()}."
                            )
                    except VidgenError as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        results[index] = {
                            "video_name": request.video_name,
                            "output_dir": str(output_dir),
                            "success": False,
                            "error": error,
                        }
                        print(f"FAILED: {request.video_name}: {error}", flush=True)
                        continue

                    history_starts = {
                        ticker_a: fetch_starts[ticker_a],
                        ticker_b: fetch_starts[ticker_b],
                    }
                    payload = {
                        "comparison_json": comparison.model_dump_json(),
                        "output_dir": str(output_dir),
                        "as_of_date": as_of,
                        "history_end_exclusive": history_end,
                        "history_start_by_ticker": {
                            ticker: start.isoformat()
                            for ticker, start in history_starts.items()
                        },
                        "yfinance_version": yfinance_version,
                        "ffmpeg_path": selected_ffmpeg_path,
                        "fps": fps,
                        "width": width,
                        "height": height,
                        "background": background,
                        "content_scale": content_scale,
                        "selected_audio": (
                            str(selected_audio_by_index[index])
                            if index in selected_audio_by_index
                            else None
                        ),
                        "chart_s": chart_s,
                        "end_hold_s": end_hold_s,
                    }
                    future_jobs[executor.submit(_render_worker, payload)] = (
                        index,
                        request,
                        output_dir,
                    )

                if not future_jobs:
                    continue
                completed, _ = wait(future_jobs, return_when=FIRST_COMPLETED)
                for future in completed:
                    index, request, output_dir = future_jobs.pop(future)
                    try:
                        render_result = future.result()
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        results[index] = {
                            "video_name": request.video_name,
                            "output_dir": str(output_dir),
                            "success": False,
                            "error": error,
                        }
                        print(f"FAILED: {request.video_name}: {error}", flush=True)
                    else:
                        results[index] = {
                            "video_name": request.video_name,
                            "output_dir": str(output_dir),
                            "success": True,
                            **render_result,
                        }
                        print(f"SUCCESS: {request.video_name}", flush=True)

    ordered_results = [results[index] for index in range(len(requests))]
    succeeded = sum(result["success"] for result in ordered_results)
    return {
        "state": "complete",
        "total": len(requests),
        "succeeded": succeeded,
        "failed": len(requests) - succeeded,
        "distinct_tickers": len(fetch_starts),
        "workers": workers,
        "sampling_frequency": frequency,
        "results": ordered_results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.stock_comparison_batch",
        description=__doc__,
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--frequency",
        choices=("daily", "weekly"),
        default="daily",
        help="Comparison sampling frequency; weekly uses completed weeks (default: daily).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, _MAX_RENDER_WORKERS + 1),
        default=2,
        help=f"Concurrent render processes (1-{_MAX_RENDER_WORKERS}; default: 2).",
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
        help="Add randomly selected local music to each rendered video.",
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=DEFAULT_AUDIO_DIR,
        help=f"Music folder used with --audio (default: {DEFAULT_AUDIO_DIR}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requests = load_manifest(args.manifest)
        report = run_stock_comparison_batch(
            requests,
            args.output_root,
            workers=args.workers,
            background=args.background,
            content_scale=args.content_scale,
            audio=args.audio,
            audio_dir=args.audio_dir,
            frequency=args.frequency,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (VidgenError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2

    print(
        f"Batch complete: {report['succeeded']} succeeded, {report['failed']} failed "
        f"({report['distinct_tickers']} distinct ticker histories, {report['workers']} "
        f"render workers; {report['sampling_frequency']} sampling).",
        flush=True,
    )
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
