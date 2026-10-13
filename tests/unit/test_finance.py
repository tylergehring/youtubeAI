"""Finance reel maths, money formatting and spec validation (no FFmpeg needed)."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from core.config import FinanceTiming
from core.finance import (
    build_timing,
    calculate,
    required_contribution,
    resolve_methodology,
    resolve_rows,
    withdrawal_rate_required_capital,
)
from core.models.finance import ReelSpec
from core.money import format_money_compact, format_money_label, format_money_whole, to_decimal

D = Decimal

DEMO_INCOMES = [30000, 40000, 50000, 60000, 70000, 80000, 100000, 120000]
DEMO_EXPECTED = ["$638K", "$851K", "$1.06M", "$1.28M", "$1.49M", "$1.70M", "$2.13M", "$2.55M"]


def spec_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "How Much Do You Need to Retire?",
        "figures": {
            "kind": "withdrawal_rate",
            "withdrawal_rate_pct": 4.7,
            "annual_incomes": DEMO_INCOMES,
        },
        "cta": "How much annual retirement income would make you feel financially secure?",
        "disclosure": "Hypothetical illustration for educational purposes only.",
        "background": {"path": "bg.png", "origin": "user_supplied", "license": "Own footage"},
    }
    data.update(overrides)
    return data


def age_target(**overrides: Any) -> dict[str, Any]:
    figures: dict[str, Any] = {
        "kind": "age_target",
        "target_amount": 1_000_000,
        "target_age": 65,
        "starting_ages": [25, 45],
        "annual_return_pct": 7,
        "contribution_frequency": "monthly",
        "compounding": "monthly",
    }
    figures.update(overrides)
    return spec_data(figures=figures)


class TestMoneyFormatting:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("638297.87", "$638K"),
            ("1063829.79", "$1.06M"),
            ("1700000", "$1.70M"),
            ("999600", "$1.00M"),  # rounds up across the unit boundary
            ("999400", "$999K"),
            ("1500", "$2K"),  # half-up, not banker's rounding
            ("2500", "$3K"),
            ("950", "$950"),
            ("999.6", "$1,000"),
            ("2127659.57", "$2.13M"),
            ("3.2e9", "$3.20B"),
        ],
    )
    def test_compact(self, amount: str, expected: str) -> None:
        assert format_money_compact(D(amount)) == expected

    def test_compact_trim(self) -> None:
        assert format_money_compact(D(10_000_000), trim=True) == "$10M"
        assert format_money_compact(D(1_500_000), trim=True) == "$1.5M"
        assert format_money_compact(D(10_000_000)) == "$10.00M"

    @pytest.mark.parametrize(
        ("amount", "expected"),
        [("30000", "$30K"), ("32500", "$32,500"), ("1000000", "$1M"), ("750", "$750"),
         ("49.5", "$49.50")],
    )  # fmt: skip
    def test_label_never_rounds_the_input(self, amount: str, expected: str) -> None:
        assert format_money_label(D(amount)) == expected

    def test_whole_dollars(self) -> None:
        assert format_money_whole(D("1234.2")) == "$1,234"
        assert format_money_whole(D("1234.2"), "ROUND_CEILING") == "$1,235"

    def test_negative_amounts_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            format_money_compact(D(-1))

    def test_floats_are_read_via_their_text(self) -> None:
        assert to_decimal(4.7) == D("4.7")
        assert to_decimal("4.70") == D("4.70")
        with pytest.raises(ValueError, match="boolean"):
            to_decimal(True)
        with pytest.raises(ValueError, match="not a number"):
            to_decimal("abc")
        with pytest.raises(ValueError, match="finite"):
            to_decimal(float("inf"))


class TestWithdrawalRate:
    def test_demo_figures_match_the_reference(self) -> None:
        spec = ReelSpec.model_validate(spec_data())
        rows, record = resolve_rows(spec)
        assert [r.label for r in rows] == [
            "$30K/year", "$40K/year", "$50K/year", "$60K/year",
            "$70K/year", "$80K/year", "$100K/year", "$120K/year",
        ]  # fmt: skip
        assert [r.value for r in rows] == DEMO_EXPECTED
        assert record is not None
        assert record.rows[0].exact == "638297.872340"
        assert record.inputs == {"withdrawal_rate_pct": "4.7"}
        assert "annual_income / (withdrawal_rate_pct / 100)" in record.formula
        assert any("4.7%" in a for a in record.assumptions)

    def test_exact_division_has_no_float_error(self) -> None:
        # 0.1 + 0.2 style traps: 4.7% of 638297.8723404... must reproduce the income exactly
        capital = withdrawal_rate_required_capital(D(30000), D("4.7"))
        assert (capital * D("0.047")).quantize(D("0.0001")) == D("30000.0000")

    def test_methodology_is_generated_and_shows_the_rate(self) -> None:
        spec = ReelSpec.model_validate(spec_data())
        assert resolve_methodology(spec) == "Using a 4.7% withdrawal rate"

    def test_custom_methodology_must_still_show_the_assumption(self) -> None:
        ok = ReelSpec.model_validate(spec_data(methodology="Assuming a 4.7% withdrawal rate"))
        assert resolve_methodology(ok) == "Assuming a 4.7% withdrawal rate"
        with pytest.raises(ValidationError, match=r"missing \['4.7%'\]"):
            ReelSpec.model_validate(spec_data(methodology="Using a safe withdrawal rate"))

    def test_label_for_an_uneven_income_is_exact(self) -> None:
        data = spec_data()
        data["figures"]["annual_incomes"] = [32500]
        rows, _ = resolve_rows(ReelSpec.model_validate(data))
        assert rows[0].label == "$32,500/year"
        assert rows[0].value == "$691K"  # 32500 / 0.047 = 691,489.36, rounded half-up to the $K

    @pytest.mark.parametrize("rate", [0, -1, 101])
    def test_rate_must_be_a_percentage(self, rate: int) -> None:
        data = spec_data()
        data["figures"]["withdrawal_rate_pct"] = rate
        with pytest.raises(ValidationError):
            ReelSpec.model_validate(data)

    def test_incomes_must_be_positive_and_unique(self) -> None:
        for incomes in ([0], [-5], [30000, 30000]):
            data = spec_data()
            data["figures"]["annual_incomes"] = incomes
            with pytest.raises(ValidationError):
                ReelSpec.model_validate(data)


class TestAgeTarget:
    def test_zero_return_is_straight_division(self) -> None:
        payment = required_contribution(
            target_amount=D(120_000), years=10, annual_return_pct=D(0),
            contribution_frequency="monthly", compounding="monthly",
        )  # fmt: skip
        assert payment == D(1000)

    def test_matches_the_closed_form_for_monthly_compounding(self) -> None:
        payment = required_contribution(
            target_amount=D(1_000_000), years=40, annual_return_pct=D(7),
            contribution_frequency="monthly", compounding="monthly",
        )  # fmt: skip
        i = 0.07 / 12
        factor = ((1 + i) ** 480 - 1) / i
        assert float(payment) == pytest.approx(1_000_000 / factor, rel=1e-9)
        assert 380 < float(payment) < 384  # sanity: about $382/month

    def test_contribution_start_is_cheaper_than_end(self) -> None:
        common = {
            "target_amount": D(500_000), "years": 20, "annual_return_pct": D(6),
            "contribution_frequency": "monthly", "compounding": "monthly",
        }  # fmt: skip
        assert required_contribution(**common, timing="start") < required_contribution(
            **common, timing="end"
        )

    def test_annual_compounding_with_monthly_contributions_uses_the_equivalent_rate(self) -> None:
        payment = required_contribution(
            target_amount=D(100_000), years=10, annual_return_pct=D(6),
            contribution_frequency="monthly", compounding="annual",
        )  # fmt: skip
        i = 1.06 ** (1 / 12) - 1
        assert float(payment) == pytest.approx(100_000 / (((1 + i) ** 120 - 1) / i), rel=1e-9)

    def test_rows_use_templates_ceiling_and_record_the_assumptions(self) -> None:
        spec = ReelSpec.model_validate(age_target())
        rows, record = resolve_rows(spec)
        assert [r.label for r in rows] == ["Start at 25", "Start at 45"]
        assert all(r.value.endswith("/mo") for r in rows)
        assert record is not None
        # rounded up, so contributing the displayed amount does reach the target
        for row, calc in zip(rows, record.rows, strict=True):
            shown = D(row.value.removeprefix("$").removesuffix("/mo").replace(",", ""))
            assert shown >= D(calc.exact)
            assert shown - D(calc.exact) < 1
        assert record.inputs["target_age"] == "65"
        assert record.inputs["compounding"] == "monthly"
        assert any("compounded monthly" in a for a in record.assumptions)
        assert "starting from a $0 balance" in " ".join(record.assumptions)

    def test_the_visible_methodology_states_every_assumption(self) -> None:
        text = resolve_methodology(ReelSpec.model_validate(age_target()))
        for fragment in ("$1M", "65", "7%", "monthly"):
            assert fragment in text

    @pytest.mark.parametrize(
        "missing",
        ["target_amount", "target_age", "starting_ages", "annual_return_pct",
         "contribution_frequency", "compounding"],
    )  # fmt: skip
    def test_every_assumption_is_required(self, missing: str) -> None:
        data = age_target()
        del data["figures"][missing]
        with pytest.raises(ValidationError, match=missing):
            ReelSpec.model_validate(data)

    def test_ages_must_be_before_the_target_and_unique(self) -> None:
        for ages in ([65], [70], [30, 30]):
            with pytest.raises(ValidationError):
                ReelSpec.model_validate(age_target(starting_ages=ages))

    def test_unknown_compounding_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReelSpec.model_validate(age_target(compounding="daily"))

    def test_label_template_needs_a_placeholder(self) -> None:
        with pytest.raises(ValidationError, match="age"):
            ReelSpec.model_validate(age_target(label_template="Start early"))

    def test_calculation_is_deterministic(self) -> None:
        spec = ReelSpec.model_validate(age_target())
        assert calculate(spec) == calculate(spec)


class TestCompoundGrowth:
    def figures(self, **overrides: Any) -> dict[str, Any]:
        figures: dict[str, Any] = {
            "kind": "compound_growth",
            "initial_amount": 10000,
            "annual_return_pct": 7,
            "compounding": "annual",
            "years": [1, 10, 30],
        }
        figures.update(overrides)
        return spec_data(figures=figures)

    def test_known_values(self) -> None:
        rows, record = resolve_rows(ReelSpec.model_validate(self.figures()))
        assert [(r.label, r.value) for r in rows] == [
            ("After 1 year", "$10,700"),
            ("After 10 years", "$19,672"),  # 10000 * 1.07^10 = 19,671.51
            ("After 30 years", "$76,123"),  # 10000 * 1.07^30 = 76,122.55
        ]
        assert record is not None and record.rows[1].exact == "19671.513573"
        assert "initial_amount * (1 + r/m)^(m * years)" in record.formula

    def test_monthly_compounding(self) -> None:
        rows, _ = resolve_rows(ReelSpec.model_validate(self.figures(compounding="monthly")))
        assert rows[1].value == "$20,097"  # 10000 * (1 + 0.07/12)^120 = 20,096.61

    def test_zero_return_never_grows(self) -> None:
        rows, _ = resolve_rows(ReelSpec.model_validate(self.figures(annual_return_pct=0)))
        assert {r.value for r in rows} == {"$10,000"}

    def test_assumptions_are_on_screen(self) -> None:
        text = resolve_methodology(ReelSpec.model_validate(self.figures()))
        assert text == "$10K start, 7% annual return, compounded annually, no added contributions."
        bad = self.figures()
        bad["methodology"] = "Watch your money grow"
        with pytest.raises(ValidationError, match="methodology must show"):
            ReelSpec.model_validate(bad)

    @pytest.mark.parametrize(
        "missing", ["initial_amount", "annual_return_pct", "compounding", "years"]
    )
    def test_assumptions_are_required(self, missing: str) -> None:
        data = self.figures()
        del data["figures"][missing]
        with pytest.raises(ValidationError, match=missing):
            ReelSpec.model_validate(data)

    @pytest.mark.parametrize("years", [[0], [101], [5, 5]])
    def test_years_must_be_sensible(self, years: list[int]) -> None:
        with pytest.raises(ValidationError):
            ReelSpec.model_validate(self.figures(years=years))


class TestSpecValidation:
    def test_manual_rows_need_a_methodology(self) -> None:
        figures = {"kind": "manual", "rows": [{"label": "Age 25", "value": "$500/mo"}]}
        with pytest.raises(ValidationError, match="methodology is required"):
            ReelSpec.model_validate(spec_data(figures=figures))
        ok = ReelSpec.model_validate(spec_data(figures=figures, methodology="Source: my sheet"))
        assert resolve_rows(ok) == (ok.figures.rows, None)  # type: ignore[union-attr]

    def test_unsourced_figures_must_say_they_are_hypothetical(self) -> None:
        with pytest.raises(ValidationError, match="hypothetical or illustrative"):
            ReelSpec.model_validate(spec_data(disclosure="Thanks for watching this reel."))

    def test_sourced_requires_citations_with_url_and_date(self) -> None:
        with pytest.raises(ValidationError, match="requires at least one source"):
            ReelSpec.model_validate(spec_data(provenance={"verification": "sourced"}))
        source = {"title": "SSA Trustees Report", "url": "https://www.ssa.gov/oact/tr/",
                  "as_of": "2025-06-18"}  # fmt: skip
        ok = ReelSpec.model_validate(
            spec_data(
                provenance={"verification": "sourced", "sources": [source]},
                disclosure="Educational only. Not financial advice.",
            )
        )
        assert ok.provenance.sources[0].as_of.isoformat() == "2025-06-18"
        for bad in ({**source, "url": "ftp://x"}, {**source, "url": "not a url"}):
            with pytest.raises(ValidationError, match="http"):
                ReelSpec.model_validate(spec_data(provenance={"sources": [bad]}))
        with pytest.raises(ValidationError, match="as_of"):
            ReelSpec.model_validate(
                spec_data(
                    provenance={"sources": [{k: v for k, v in source.items() if k != "as_of"}]}
                )
            )

    @pytest.mark.parametrize(
        "phrase", ["Guaranteed returns", "Risk-free retirement", "You should invest now"]
    )
    def test_promises_and_advice_are_rejected(self, phrase: str) -> None:
        with pytest.raises(ValidationError, match="unsupported promise"):
            ReelSpec.model_validate(spec_data(title=phrase))
        with pytest.raises(ValidationError, match="unsupported promise"):
            ReelSpec.model_validate(spec_data(cta=phrase))

    def test_disclosure_may_say_there_is_no_guarantee(self) -> None:
        ReelSpec.model_validate(
            spec_data(disclosure="Hypothetical example; there is no guarantee of results.")
        )

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ReelSpec.model_validate(spec_data(hype="max"))

    def test_row_limits(self) -> None:
        figures = {"kind": "manual", "rows": [{"label": f"L{i}", "value": "$1"} for i in range(13)]}
        with pytest.raises(ValidationError):
            ReelSpec.model_validate(spec_data(figures=figures, methodology="Own sheet"))
        figures = {"kind": "manual", "rows": [{"label": "x" * 33, "value": "$1"}]}
        with pytest.raises(ValidationError):
            ReelSpec.model_validate(spec_data(figures=figures, methodology="Own sheet"))

    def test_background_must_be_local_and_licensed(self) -> None:
        for path in ("https://example.com/beach.mp4", "ftp://x/y.mp4"):
            data = spec_data()
            data["background"]["path"] = path
            with pytest.raises(ValidationError, match="local file"):
                ReelSpec.model_validate(data)
        data = spec_data()
        del data["background"]["license"]
        with pytest.raises(ValidationError, match="license"):
            ReelSpec.model_validate(data)
        data = spec_data()
        data["background"]["origin"] = "scraped"
        with pytest.raises(ValidationError, match="origin"):
            ReelSpec.model_validate(data)

    def test_accent_color_format(self) -> None:
        with pytest.raises(ValidationError, match="RRGGBB"):
            ReelSpec.model_validate(spec_data(accent_color="gold"))
        assert ReelSpec.model_validate(spec_data(accent_color="#FFD166")).accent_color == "#FFD166"

    def test_spec_models_do_not_share_state(self) -> None:
        data = spec_data()
        before = copy.deepcopy(data)
        ReelSpec.model_validate(data)
        assert data == before


class TestTiming:
    def test_rows_are_revealed_one_at_a_time(self) -> None:
        t = build_timing(8, FinanceTiming(), fps=30)
        gaps = [b - a for a, b in zip(t.row_at_s, t.row_at_s[1:], strict=False)]
        assert gaps == [pytest.approx(0.9)] * 7
        assert t.cta_at_s > t.row_at_s[-1]
        assert t.duration_s > t.cta_at_s + t.fade_s

    def test_duration_is_a_whole_number_of_frames(self) -> None:
        t = build_timing(5, FinanceTiming(), fps=30)
        assert t.duration_s * 30 == pytest.approx(round(t.duration_s * 30), abs=0.02)

    def test_voiceover_can_only_lengthen(self) -> None:
        short = build_timing(3, FinanceTiming(), fps=30, voiceover_s=1.0)
        assert short.duration_s == build_timing(3, FinanceTiming(), fps=30).duration_s
        long = build_timing(3, FinanceTiming(), fps=30, voiceover_s=40.0)
        assert long.duration_s >= 40.0

    def test_needs_at_least_one_row(self) -> None:
        with pytest.raises(ValueError, match="at least one row"):
            build_timing(0, FinanceTiming(), fps=30)
