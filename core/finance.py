"""Deterministic finance calculations for reel figures. Decimal arithmetic, no LLM, no floats."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Context, Decimal, localcontext

from core.config import FinanceTiming
from core.models.finance import (
    PERIODS_PER_YEAR,
    AgeTargetFigures,
    CalculatedRow,
    CalculationRecord,
    ManualFigures,
    ReelRow,
    ReelSpec,
    ReelTiming,
    WithdrawalRateFigures,
)
from core.money import format_money_compact, format_money_label, format_money_whole, plain

PRECISION = 60  # significant digits for intermediate results
_EXACT_PLACES = Decimal("0.000001")


def _context() -> Context:
    return Context(prec=PRECISION, rounding=ROUND_HALF_UP)


def withdrawal_rate_required_capital(annual_income: Decimal, rate_pct: Decimal) -> Decimal:
    """Capital needed so that `rate_pct`% of it per year equals `annual_income`."""
    with localcontext(_context()):
        return annual_income / (rate_pct / 100)


def required_contribution(
    *,
    target_amount: Decimal,
    years: int,
    annual_return_pct: Decimal,
    contribution_frequency: str,
    compounding: str,
    timing: str = "end",
) -> Decimal:
    """Level contribution per period that grows to `target_amount` after `years`.

    The nominal annual rate compounds `compounding` times a year, so the growth over one
    contribution period is (1 + r/m)^(m/f) - 1 where f is contributions per year. With the
    future-value-of-annuity factor ((1+i)^n - 1) / i (times (1+i) when contributing at the start
    of each period) and n = years * f, the contribution is target / factor. Starts from a zero
    balance.
    """
    if years <= 0:
        raise ValueError("years must be positive")
    m = PERIODS_PER_YEAR[compounding]
    f = PERIODS_PER_YEAR[contribution_frequency]
    with localcontext(_context()):
        rate = annual_return_pct / 100
        growth_per_period = (1 + rate / m) ** (Decimal(m) / Decimal(f))
        i = growth_per_period - 1
        n = years * f
        factor = Decimal(n) if i == 0 else (growth_per_period**n - 1) / i
        if timing == "start":
            factor *= growth_per_period
        return target_amount / factor


def _exact(value: Decimal) -> str:
    return str(value.quantize(_EXACT_PLACES, rounding=ROUND_HALF_UP))


def _withdrawal_record(fig: WithdrawalRateFigures) -> CalculationRecord:
    rows: list[CalculatedRow] = []
    for income in fig.annual_incomes:
        capital = withdrawal_rate_required_capital(income, fig.withdrawal_rate_pct)
        rows.append(
            CalculatedRow(
                label=f"{format_money_label(income)}/year",
                display=format_money_compact(capital),
                exact=_exact(capital),
                inputs={"annual_income": plain(income)},
            )
        )
    rate = plain(fig.withdrawal_rate_pct)
    return CalculationRecord(
        kind="withdrawal_rate",
        formula="required_capital = annual_income / (withdrawal_rate_pct / 100)",
        inputs={"withdrawal_rate_pct": rate},
        assumptions=[
            f"{rate}% of the starting balance is withdrawn each year",
            "illustrates a single static withdrawal rate; ignores taxes, fees, inflation, "
            "market returns and longevity",
        ],
        rounding="display: half-up to the nearest $1K below $1M, to $0.01M from $1M",
        rows=rows,
    )


def _age_target_record(fig: AgeTargetFigures) -> CalculationRecord:
    unit = {"annual": "yr", "semiannual": "6mo", "quarterly": "qtr", "monthly": "mo"}[
        fig.contribution_frequency
    ]
    rows: list[CalculatedRow] = []
    for age in fig.starting_ages:
        payment = required_contribution(
            target_amount=fig.target_amount,
            years=fig.target_age - age,
            annual_return_pct=fig.annual_return_pct,
            contribution_frequency=fig.contribution_frequency,
            compounding=fig.compounding,
            timing=fig.contribution_timing,
        )
        rows.append(
            CalculatedRow(
                label=fig.label_template.replace("{age}", str(age)),
                display=f"{format_money_whole(payment, ROUND_CEILING)}/{unit}",
                exact=_exact(payment),
                inputs={"starting_age": str(age), "years": str(fig.target_age - age)},
            )
        )
    rate = plain(fig.annual_return_pct)
    return CalculationRecord(
        kind="age_target",
        formula=(
            "contribution = target_amount / factor; factor = ((1+i)^n - 1) / i "
            "(x (1+i) if contributions are at the start of each period); "
            "i = (1 + r/m)^(m/f) - 1; n = (target_age - starting_age) * f"
        ),
        inputs={
            "target_amount": plain(fig.target_amount),
            "target_age": str(fig.target_age),
            "starting_ages": ", ".join(str(a) for a in fig.starting_ages),
            "annual_return_pct": rate,
            "contribution_frequency": fig.contribution_frequency,
            "compounding": fig.compounding,
            "contribution_timing": fig.contribution_timing,
        },
        assumptions=[
            f"{rate}% nominal annual return, compounded {fig.compounding}",
            f"level {fig.contribution_frequency} contributions at the {fig.contribution_timing} "
            "of each period, starting from a $0 balance",
            "constant return; ignores taxes, fees and inflation",
        ],
        rounding="display: contribution rounded up to the next whole dollar so the target is met",
        rows=rows,
    )


def calculate(spec: ReelSpec) -> CalculationRecord | None:
    """Figures computed from the spec's assumptions; None for manually typed rows."""
    figures = spec.figures
    if isinstance(figures, WithdrawalRateFigures):
        return _withdrawal_record(figures)
    if isinstance(figures, AgeTargetFigures):
        return _age_target_record(figures)
    return None


def resolve_rows(spec: ReelSpec) -> tuple[list[ReelRow], CalculationRecord | None]:
    """The rows to display and, for calculated figures, the record that explains them."""
    figures = spec.figures
    if isinstance(figures, ManualFigures):
        return list(figures.rows), None
    record = calculate(spec)
    assert record is not None  # every non-manual kind is calculated
    return [ReelRow(label=r.label, value=r.display) for r in record.rows], record


def resolve_methodology(spec: ReelSpec) -> str:
    text = spec.methodology or spec.figures.default_methodology()
    assert text  # manual figures require one (enforced by the spec validator)
    return text


def build_timing(
    row_count: int, timing: FinanceTiming, fps: int, voiceover_s: float | None = None
) -> ReelTiming:
    """Reveal schedule: rows one at a time, then the CTA, then a hold. A voiceover can only
    lengthen the reel, never cut the on-screen content short. The duration is a whole number of
    frames."""
    if row_count < 1:
        raise ValueError("a reel needs at least one row")
    row_at = [round(timing.first_row_at_s + i * timing.row_interval_s, 3) for i in range(row_count)]
    cta_at = round(row_at[-1] + timing.cta_delay_s, 3)
    duration = cta_at + timing.fade_s + timing.end_hold_s
    if voiceover_s is not None:
        duration = max(duration, voiceover_s + timing.voiceover_tail_s)
    frames = -(-round(duration * 1000) * fps // 1000)  # ceil(duration * fps) in integers
    return ReelTiming(
        title_at_s=timing.title_at_s,
        methodology_at_s=timing.methodology_at_s,
        row_at_s=row_at,
        cta_at_s=cta_at,
        disclosure_at_s=timing.title_at_s,
        fade_s=timing.fade_s,
        duration_s=round(frames / fps, 3),
    )
