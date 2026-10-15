"""Typed inputs and outputs for historical, price-only two-stock comparisons."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ComparisonPoint(BaseModel):
    """One common trading date and the marked-to-market value of each hypothetical holding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: date
    close_a: Decimal = Field(gt=0)
    value_a: Decimal = Field(gt=0)
    close_b: Decimal = Field(gt=0)
    value_b: Decimal = Field(gt=0)


class YearEndCheckpoint(BaseModel):
    """Last common observed close in a completed calendar year."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int = Field(ge=1900, le=9999)
    date: date
    value_a: Decimal = Field(gt=0)
    value_b: Decimal = Field(gt=0)


class StockComparison(BaseModel):
    """Auditable result of a historical price-only investment illustration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker_a: str = Field(min_length=1, max_length=20)
    label_a: str = Field(min_length=1, max_length=40)
    ticker_b: str = Field(min_length=1, max_length=20)
    label_b: str = Field(min_length=1, max_length=40)
    requested_start_date: date
    start_date: date
    end_date: date
    expected_first_date_a: date | None = None
    first_available_date_a: date
    initial_investment: Decimal = Field(gt=0)
    entry_price_a: Decimal = Field(gt=0)
    entry_price_b: Decimal = Field(gt=0)
    completed_through_year: int = Field(ge=1900, le=9999)
    source: str = "Yahoo Finance historical data via yfinance"
    price_field: str = "Close"
    adjustment_method: str = (
        "auto_adjust=False; Yahoo Finance historical Close prices are used as supplied "
        "(split-adjusted, not dividend-adjusted); dividends are excluded."
    )
    missing_close_rows_a: int = Field(ge=0)
    missing_close_rows_b: int = Field(ge=0)
    download_rows_a: int = Field(ge=1)
    download_rows_b: int = Field(ge=1)
    common_observation_count: int = Field(ge=1)
    points: list[ComparisonPoint] = Field(min_length=1)
    year_end_checkpoints: list[YearEndCheckpoint]

    @model_validator(mode="after")
    def dates_and_symbols_are_consistent(self) -> StockComparison:
        if self.ticker_a.casefold() == self.ticker_b.casefold():
            raise ValueError("The two comparison tickers must be different.")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date.")
        if self.points[0].date != self.start_date or self.points[-1].date != self.end_date:
            raise ValueError("The first and last points must match the comparison date range.")
        if len(self.points) != self.common_observation_count:
            raise ValueError("common_observation_count must match the daily points.")
        if any(
            earlier.date >= later.date
            for earlier, later in zip(self.points, self.points[1:], strict=False)
        ):
            raise ValueError("Daily comparison dates must be strictly increasing.")
        if any(checkpoint.date.year != checkpoint.year for checkpoint in self.year_end_checkpoints):
            raise ValueError("Each year-end checkpoint must fall within its stated year.")
        return self
