"""Publishing copy grounded only in the resolved reel; no generated statistics or SEO promises."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.models.finance import ReelPlan

_TOPIC_TAGS = {
    "withdrawal_rate": ["RetirementPlanning", "WithdrawalRate"],
    "compound_growth": ["CompoundInterest", "LongTermSaving"],
    "age_target": ["SavingGoals", "RetirementPlanning"],
    "contribution_growth": ["MonthlySaving", "CompoundInterest"],
    "loan_payment": ["LoanPayments", "InterestRates"],
    "inflation_erosion": ["Inflation", "PurchasingPower"],
    "doubling_time": ["CompoundInterest", "DoublingTime"],
}


class ReelMetadata(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    hashtags: list[str]
    review_required: bool = True


def build_reel_metadata(plan: ReelPlan) -> ReelMetadata:
    kind = plan.calculation.kind if plan.calculation else "manual"
    tags = [*_TOPIC_TAGS.get(kind, []), "FinancialEducation", "Shorts"]
    parts = [
        plan.title.replace("\n", " "),
        plan.methodology,
        "\n".join(f"{row.label}: {row.value}" for row in plan.rows),
        plan.cta,
        plan.disclosure,
    ]
    if plan.calculation:
        parts.append("Calculation assumptions:\n" + "\n".join(plan.calculation.assumptions))
    if plan.provenance.sources:
        parts.append(
            "Sources:\n"
            + "\n".join(
                f"{s.title} ({s.as_of.isoformat()}): {s.url}" for s in plan.provenance.sources
            )
        )
    credits = []
    if plan.background.attribution:
        credits.append(f"Background: {plan.background.attribution}")
    if plan.music and plan.music.settings.attribution:
        credits.append(f"Music: {plan.music.settings.attribution}")
    if credits:
        parts.append("\n".join(credits))
    parts.append(" ".join(f"#{tag}" for tag in tags))
    return ReelMetadata(
        title=plan.title.replace("\n", " "),
        description="\n\n".join(parts),
        hashtags=tags,
    )
