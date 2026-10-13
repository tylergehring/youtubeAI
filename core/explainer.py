"""Evidence-backed editorial briefs and reproducible fixed-coupon bond calculations."""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from core.exceptions import InvalidInputError
from core.models.content import Strict
from core.models.finance import Number
from core.naming import validate_reference_url


class BondScenario(Strict):
    face_value: Number = Field(default=Decimal("1000"), gt=0, le=1_000_000)
    coupon_pct: Number = Field(default=Decimal("4"), ge=0, le=25)
    initial_yield_pct: Number = Field(default=Decimal("4"), ge=0, le=25)
    changed_yield_pct: Number = Field(default=Decimal("5"), ge=0, le=25)
    short_years: int = Field(default=2, ge=1, le=30)
    long_years: int = Field(default=30, ge=2, le=50)

    @model_validator(mode="after")
    def _scenario(self) -> BondScenario:
        if self.long_years <= self.short_years:
            raise ValueError("long_years must exceed short_years")
        if self.changed_yield_pct <= self.initial_yield_pct:
            raise ValueError("This yield-rise explainer requires a positive yield increase")
        return self


def bond_price(scenario: BondScenario, years: int, yield_pct: Decimal) -> Decimal:
    """Price at a coupon date, semiannual payments; excludes accrued interest and costs."""
    if years <= 0 or not yield_pct.is_finite() or yield_pct < 0:
        raise InvalidInputError("Bond maturity must be positive and yield finite/non-negative.")
    coupon = scenario.face_value * scenario.coupon_pct / 200
    rate = yield_pct / 200
    periods = years * 2
    if not rate:
        return scenario.face_value + coupon * periods
    discount = (1 + rate) ** (-periods)
    return coupon * (1 - discount) / rate + scenario.face_value * discount


def calculation_context(scenario: BondScenario) -> dict[str, str]:
    def money(value: Decimal) -> str:
        return f"${value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"

    result = {
        "face": money(scenario.face_value),
        "coupon_pct": f"{scenario.coupon_pct.normalize():f}%",
        "initial_yield_pct": f"{scenario.initial_yield_pct.normalize():f}%",
        "changed_yield_pct": f"{scenario.changed_yield_pct.normalize():f}%",
        "coupon_payment": money(scenario.face_value * scenario.coupon_pct / 200),
    }
    for label, years in (("short", scenario.short_years), ("long", scenario.long_years)):
        before = bond_price(scenario, years, scenario.initial_yield_pct)
        after = bond_price(scenario, years, scenario.changed_yield_pct)
        loss = (1 - after / before) * 100
        result.update(
            {
                f"{label}_years": str(years),
                f"{label}_price": money(after),
                f"{label}_initial_price": money(before),
                f"{label}_loss_pct": f"{loss.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%",
            }
        )
    return result


def resolve_text(text: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        if match[1] not in context:
            raise InvalidInputError(f"Unknown calculation placeholder: {match[1]}")
        return context[match[1]]

    resolved = re.sub(r"\{([^{}]+)\}", replace, text)
    if "{" in resolved or "}" in resolved:
        raise InvalidInputError("Malformed calculation placeholder")
    return resolved


class EvidenceSource(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=5, max_length=160)
    url: str
    accessed_on: date
    supports: str = Field(min_length=20, max_length=1500)
    snapshot: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return validate_reference_url(value)


class EditorialScene(Strict):
    heading: str = Field(min_length=3, max_length=90)
    narration: str = Field(min_length=15, max_length=500)
    visual: Literal["hook", "yield", "comparison", "curve", "cashflows", "takeaway"]
    supporting_text: str = Field(min_length=3, max_length=60)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("heading")
    @classmethod
    def _short_heading(cls, value: str) -> str:
        if len(value.split()) > 8:
            raise ValueError("heading must contain at most 8 words")
        return value

    @field_validator("supporting_text")
    @classmethod
    def _short_supporting_text(cls, value: str) -> str:
        if len(value.split()) > 7:
            raise ValueError("supporting_text must contain at most 7 words")
        return value

    @field_validator("narration")
    @classmethod
    def _short_narration(cls, value: str) -> str:
        if len(value.split()) > 28:
            raise ValueError("narration must contain at most 28 words")
        return value


class ExplainerBrief(Strict):
    title: str = Field(min_length=10, max_length=100)
    question: str = Field(min_length=15)
    original_contribution: str = Field(min_length=40)
    source_video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    source_interpretation: str = Field(min_length=40)
    excluded_claims: list[str] = Field(min_length=1)
    evidence: list[EvidenceSource] = Field(min_length=1)
    scenario: BondScenario = Field(default_factory=BondScenario)
    scenes: list[EditorialScene] = Field(min_length=4, max_length=8)
    disclosure: str = Field(min_length=20, max_length=180)

    @model_validator(mode="after")
    def _evidence_and_language(self) -> ExplainerBrief:
        ids = {source.id for source in self.evidence}
        if len(ids) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique")
        for scene in self.scenes:
            if not set(scene.evidence_ids) <= ids:
                raise ValueError("Every scene must reference known evidence IDs")
        if "hypothetical" not in self.disclosure.lower():
            raise ValueError("The disclosure must explicitly identify hypothetical yields")
        if len({scene.visual for scene in self.scenes}) < 3:
            raise ValueError("Use at least three visual treatments to explain the mechanism")
        if sum(len(scene.narration.split()) for scene in self.scenes) > 105:
            raise ValueError("Total narration must not exceed 105 words")
        return self
