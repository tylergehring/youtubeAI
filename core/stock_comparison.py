"""Yahoo Finance acquisition and deterministic price-only comparison calculations."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from core.exceptions import MarketDataError
from core.models.stock_comparison import (
    ComparisonPoint,
    StockComparison,
    YearEndCheckpoint,
)

_EASTERN = ZoneInfo("America/New_York")
_MARKET_CLOSE_CUTOFF = time(16, 15)
_MONEY_QUANTUM = Decimal("0.01")
_TICKER_RE = re.compile(r"^[A-Z0-9.^=-]{1,20}$")


@dataclass(frozen=True)
class PriceDownload:
    """Validated daily closing prices and download-quality counts."""

    ticker: str
    prices: dict[date, Decimal]
    first_available_date: date
    last_available_date: date
    missing_close_rows: int
    download_rows: int


def validate_ticker(ticker: str) -> str:
    """Return a normalized ticker or reject characters that do not fit a Yahoo symbol."""
    normalized = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(normalized):
        raise MarketDataError(
            f"Invalid ticker {ticker!r}; use 1-20 uppercase letters, digits, '.', '^', '=', or '-'."
        )
    return normalized


def latest_completed_history_end(
    now: datetime | None = None,
) -> tuple[date, date]:
    """Return (inclusive completed-date cutoff, exclusive yfinance end date).

    Yahoo may expose an in-progress daily row before the regular U.S. close. Before 4:15 p.m.
    Eastern on a weekday, request through the previous calendar day; after that conservative
    cutoff, today's completed bar may be included. Non-trading dates are handled by the data
    intersection, not guessed or forward-filled.
    """
    current = now or datetime.now(_EASTERN)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware.")
    eastern = current.astimezone(_EASTERN)
    today = eastern.date()
    if eastern.weekday() < 5 and eastern.time() >= _MARKET_CLOSE_CUTOFF:
        inclusive_cutoff = today
    else:
        inclusive_cutoff = today - timedelta(days=1)
    return inclusive_cutoff, inclusive_cutoff + timedelta(days=1)


def _history_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    date_text = str(value)[:10]
    try:
        return date.fromisoformat(date_text)
    except ValueError as exc:
        raise MarketDataError(f"Yahoo history returned an invalid date index: {value!r}") from exc


def download_daily_closes(
    ticker: str,
    start_date: date,
    end_exclusive: date,
    *,
    ticker_factory: Callable[[str], Any] | None = None,
) -> PriceDownload:
    """Fetch daily Yahoo ``Close`` values without dividend adjustment or imputation.

    yfinance's installed ``PriceHistory.history`` documentation defines ``start`` as inclusive,
    ``end`` as exclusive, and ``auto_adjust=False`` as leaving OHLC unadjusted by dividends.
    Yahoo's historical Close series reflects stock splits while excluding cash dividends.
    Missing Close rows are counted and omitted; other malformed prices fail closed.
    """
    normalized = validate_ticker(ticker)
    if start_date >= end_exclusive:
        raise MarketDataError("The history start date must be before its exclusive end date.")

    try:
        if ticker_factory is None:
            yahoo = importlib.import_module("yfinance")
            ticker_factory = yahoo.Ticker
        history = ticker_factory(normalized).history(
            start=start_date.isoformat(),
            end=end_exclusive.isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
            repair=False,
            keepna=True,
            prepost=False,
            timeout=30,
            raise_errors=True,
        )
    except ModuleNotFoundError as exc:
        raise MarketDataError(
            "The optional yfinance dependency is not installed. Install with "
            '`pip install -e ".[market-data]"`.'
        ) from exc
    except Exception as exc:
        raise MarketDataError(
            f"Could not fetch {normalized} daily history ({type(exc).__name__}): {exc}"
        ) from exc

    if getattr(history, "empty", True):
        raise MarketDataError(
            f"Yahoo Finance returned no daily history for {normalized} "
            f"from {start_date} through {end_exclusive} (end is exclusive)."
        )
    columns = getattr(history, "columns", ())
    if "Close" not in columns:
        raise MarketDataError(
            f"Yahoo Finance history for {normalized} has no 'Close' column; "
            f"received columns: {list(columns)!r}."
        )

    closes: dict[date, Decimal] = {}
    missing_count = 0
    try:
        values = history["Close"].items()
        for raw_date, raw_value in values:
            trading_date = _history_date(raw_date)
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise MarketDataError(
                    f"{normalized} has a non-numeric Close value on {trading_date}: {raw_value!r}."
                ) from exc
            if math.isnan(numeric_value):
                missing_count += 1
                continue
            if not math.isfinite(numeric_value) or numeric_value <= 0:
                raise MarketDataError(
                    f"{normalized} has an invalid Close value on {trading_date}: {raw_value!r}."
                )
            if trading_date in closes:
                raise MarketDataError(
                    f"{normalized} history has duplicate daily observations for {trading_date}."
                )
            closes[trading_date] = Decimal(str(raw_value))
    except MarketDataError:
        raise
    except Exception as exc:
        raise MarketDataError(
            f"Could not read the Close series for {normalized} ({type(exc).__name__}): {exc}"
        ) from exc

    if not closes:
        raise MarketDataError(
            f"Yahoo Finance returned no finite, positive Close values for {normalized}."
        )
    sorted_closes = dict(sorted(closes.items()))
    return PriceDownload(
        ticker=normalized,
        prices=sorted_closes,
        first_available_date=next(iter(sorted_closes)),
        last_available_date=next(reversed(sorted_closes)),
        missing_close_rows=missing_count,
        download_rows=len(history),
    )


def _market_value(initial: Decimal, price: Decimal, entry_price: Decimal) -> Decimal:
    with localcontext(Context(prec=50, rounding=ROUND_HALF_UP)):
        return (initial * price / entry_price).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def build_stock_comparison(
    *,
    ticker_a: str,
    label_a: str,
    prices_a: Mapping[date, Decimal],
    ticker_b: str,
    label_b: str,
    prices_b: Mapping[date, Decimal],
    requested_start_date: date,
    initial_investment: Decimal,
    completed_through_year: int,
    expected_first_date_a: date | None = None,
    first_available_date_a: date | None = None,
    missing_close_rows_a: int = 0,
    missing_close_rows_b: int = 0,
    download_rows_a: int | None = None,
    download_rows_b: int | None = None,
) -> StockComparison:
    """Build common-date fractional-share holdings and completed year-end checkpoints."""
    symbol_a = validate_ticker(ticker_a)
    symbol_b = validate_ticker(ticker_b)
    if symbol_a == symbol_b:
        raise MarketDataError("The two comparison tickers must be different.")
    if not label_a.strip() or not label_b.strip():
        raise MarketDataError("Both display labels must be non-empty.")
    if initial_investment <= 0:
        raise MarketDataError("The initial investment must be greater than zero.")
    if expected_first_date_a is not None:
        if first_available_date_a is None:
            raise MarketDataError(
                "An expected first date was supplied but the first available date was not recorded."
            )
        if first_available_date_a != expected_first_date_a:
            raise MarketDataError(
                f"{symbol_a}'s first valid fetched Close is {first_available_date_a}; "
                f"expected {expected_first_date_a}."
            )

    common_dates = sorted(
        trading_date
        for trading_date in prices_a.keys() & prices_b.keys()
        if trading_date >= requested_start_date
    )
    if not common_dates:
        raise MarketDataError(
            f"{symbol_a} and {symbol_b} have no common valid daily closes on or after "
            f"{requested_start_date}; no missing date will be forward-filled."
        )
    if expected_first_date_a is not None and common_dates[0] != expected_first_date_a:
        raise MarketDataError(
            f"The first common close is {common_dates[0]}, not the verified expected start "
            f"{expected_first_date_a}; both securities must have an observed close on the "
            "chosen starting date."
        )

    entry_a = prices_a[common_dates[0]]
    entry_b = prices_b[common_dates[0]]
    if entry_a <= 0 or entry_b <= 0:
        raise MarketDataError("Entry closes must be positive.")
    points = [
        ComparisonPoint(
            date=trading_date,
            close_a=prices_a[trading_date],
            value_a=_market_value(initial_investment, prices_a[trading_date], entry_a),
            close_b=prices_b[trading_date],
            value_b=_market_value(initial_investment, prices_b[trading_date], entry_b),
        )
        for trading_date in common_dates
    ]

    checkpoints_by_year: dict[int, ComparisonPoint] = {}
    for point in points:
        if point.date.year <= completed_through_year:
            checkpoints_by_year[point.date.year] = point
    checkpoints = [
        YearEndCheckpoint(
            year=year,
            date=point.date,
            value_a=point.value_a,
            value_b=point.value_b,
        )
        for year, point in sorted(checkpoints_by_year.items())
    ]

    return StockComparison(
        ticker_a=symbol_a,
        label_a=label_a.strip(),
        ticker_b=symbol_b,
        label_b=label_b.strip(),
        requested_start_date=requested_start_date,
        start_date=common_dates[0],
        end_date=common_dates[-1],
        expected_first_date_a=expected_first_date_a,
        first_available_date_a=first_available_date_a or min(prices_a),
        initial_investment=initial_investment,
        entry_price_a=entry_a,
        entry_price_b=entry_b,
        completed_through_year=completed_through_year,
        missing_close_rows_a=missing_close_rows_a,
        missing_close_rows_b=missing_close_rows_b,
        download_rows_a=download_rows_a or len(prices_a),
        download_rows_b=download_rows_b or len(prices_b),
        common_observation_count=len(points),
        points=points,
        year_end_checkpoints=checkpoints,
    )
