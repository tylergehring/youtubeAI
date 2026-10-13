"""The contribution_growth, loan_payment, inflation_erosion and doubling_time figure kinds."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from core.finance import (
    contribution_future_value,
    inflation_adjusted_value,
    loan_monthly_payment,
    required_contribution,
    resolve_methodology,
    resolve_rows,
    years_to_double,
)
from core.models.finance import ReelSpec

D = Decimal


def spec(figures: dict[str, Any], **overrides: Any) -> ReelSpec:
    data: dict[str, Any] = {
        "title": "A finance question?",
        "figures": figures,
        "cta": "What would you do differently?",
        "disclosure": "Hypothetical illustration for educational purposes only.",
        "background": {"path": "bg.png", "origin": "user_supplied", "license": "Own footage"},
    }
    data.update(overrides)
    return ReelSpec.model_validate(data)


def rows_of(figures: dict[str, Any]) -> list[tuple[str, str]]:
    rows, _ = resolve_rows(spec(figures))
    return [(r.label, r.value) for r in rows]


class TestContributionGrowth:
    FIGURES = {
        "kind": "contribution_growth",
        "contribution_amount": 500,
        "contribution_frequency": "monthly",
        "annual_return_pct": 7,
        "compounding": "monthly",
        "years": [10, 30],
    }

    def test_rows_match_the_annuity_formula(self) -> None:
        i = 0.07 / 12
        expected = [500 * ((1 + i) ** (12 * y) - 1) / i for y in (10, 30)]
        exact = [float(r.exact) for r in resolve_rows(spec(self.FIGURES))[1].rows]  # type: ignore[union-attr]
        assert exact == pytest.approx(expected, rel=1e-9)
        assert rows_of(self.FIGURES) == [("After 10 years", "$87K"), ("After 30 years", "$610K")]

    def test_start_of_period_is_one_period_more_growth(self) -> None:
        end = contribution_future_value(
            contribution=D(100), years=5, annual_return_pct=D(6),
            contribution_frequency="monthly", compounding="monthly",
        )  # fmt: skip
        start = contribution_future_value(
            contribution=D(100), years=5, annual_return_pct=D(6),
            contribution_frequency="monthly", compounding="monthly", timing="start",
        )  # fmt: skip
        assert start == pytest.approx(end * D("1.005"), rel=D("1e-12"))

    def test_zero_return_is_simple_sum(self) -> None:
        value = contribution_future_value(
            contribution=D(100), years=2, annual_return_pct=D(0),
            contribution_frequency="monthly", compounding="annual",
        )  # fmt: skip
        assert value == 2400

    def test_inverse_of_required_contribution(self) -> None:
        kwargs: dict[str, Any] = {
            "years": 20, "annual_return_pct": D(7),
            "contribution_frequency": "monthly", "compounding": "monthly",
        }  # fmt: skip
        payment = required_contribution(target_amount=D(1_000_000), **kwargs)
        assert contribution_future_value(contribution=payment, **kwargs) == pytest.approx(
            D(1_000_000), rel=D("1e-12")
        )

    def test_default_methodology_shows_assumptions(self) -> None:
        text = resolve_methodology(spec(self.FIGURES))
        assert text == "$500 monthly contributions, 7% annual return, compounded monthly."


class TestLoanPayment:
    FIGURES = {"kind": "loan_payment", "principal": 300000, "term_years": 30,
               "annual_rates_pct": [0, 6, 7.5]}  # fmt: skip

    def test_known_payments(self) -> None:
        assert rows_of(self.FIGURES) == [
            ("0% rate", "$833/mo"),
            ("6% rate", "$1,799/mo"),
            ("7.5% rate", "$2,098/mo"),
        ]

    def test_matches_float_formula(self) -> None:
        i = 0.06 / 12
        expected = 300000 * i / (1 - (1 + i) ** -360)
        assert float(loan_monthly_payment(D(300000), D(6), 30)) == pytest.approx(expected)

    def test_methodology(self) -> None:
        assert resolve_methodology(spec(self.FIGURES)) == (
            "$300K loan, 30-year term, fixed rate, monthly payments."
        )

    def test_rejects_duplicate_or_absurd_rates(self) -> None:
        with pytest.raises(ValidationError):
            spec({**self.FIGURES, "annual_rates_pct": [5, 5]})
        with pytest.raises(ValidationError):
            spec({**self.FIGURES, "annual_rates_pct": [-1]})


class TestInflationErosion:
    FIGURES = {"kind": "inflation_erosion", "amount": 10000, "inflation_rate_pct": 3,
               "years": [10, 20]}  # fmt: skip

    def test_rows(self) -> None:
        assert rows_of(self.FIGURES) == [("After 10 years", "$7,441"), ("After 20 years", "$5,537")]

    def test_value(self) -> None:
        assert float(inflation_adjusted_value(D(10000), D(3), 10)) == pytest.approx(
            10000 / 1.03**10
        )

    def test_requires_positive_inflation(self) -> None:
        with pytest.raises(ValidationError):
            spec({**self.FIGURES, "inflation_rate_pct": 0})


class TestDoublingTime:
    FIGURES = {"kind": "doubling_time", "annual_returns_pct": [3, 7, 10]}

    def test_rows(self) -> None:
        assert rows_of(self.FIGURES) == [
            ("3% return", "23.4 yrs"),
            ("7% return", "10.2 yrs"),
            ("10% return", "7.3 yrs"),
        ]

    def test_value(self) -> None:
        assert float(years_to_double(D(7))) == pytest.approx(math.log(2) / math.log(1.07))

    def test_custom_methodology_must_keep_assumptions(self) -> None:
        with pytest.raises(ValidationError, match="methodology must show"):
            spec(self.FIGURES, methodology="Some rule of thumb")
