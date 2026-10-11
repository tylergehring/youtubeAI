"""Finance-facts reel models: the user-supplied spec, and the resolved plan the renderer consumes.

The spec is validated, structured data. Figures are either typed in as display strings or
calculated deterministically from explicit assumptions (see core/finance.py); nothing here is
ever produced by an LLM.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from core.models.content import Strict
from core.money import format_money_compact, plain, to_decimal

MAX_ROWS = 12

# Number as a Decimal: JSON/YAML floats are read via their text (4.7 -> Decimal("4.7")).
Number = Annotated[Decimal, BeforeValidator(to_decimal)]

PERIODS_PER_YEAR: dict[str, int] = {"annual": 1, "semiannual": 2, "quarterly": 4, "monthly": 12}
Frequency = Literal["annual", "semiannual", "quarterly", "monthly"]
_COMPOUNDED = {
    "annual": "annually",
    "semiannual": "semiannually",
    "quarterly": "quarterly",
    "monthly": "monthly",
}

# Wording that promises outcomes or tells viewers what to do with their money. Checked on the
# viewer-facing text (not the disclosure, which legitimately says "no guarantee").
_FORBIDDEN_PHRASES = (
    "guarantee",
    "risk-free",
    "risk free",
    "can't lose",
    "cannot lose",
    "sure thing",
    "get rich quick",
    "you must invest",
    "you should invest",
    "you should buy",
    "you should sell",
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _reject_guarantees(text: str, where: str) -> None:
    lowered = text.lower()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            raise ValueError(
                f"{where} uses unsupported promise/advice wording ({phrase!r}); "
                "describe the assumption instead of promising an outcome"
            )


# ------------------------------------------------------------------ figures


class ReelRow(_Spec):
    """One line of the list: a label and the value shown for it, both plain display text."""

    label: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=16)


class ManualFigures(_Spec):
    """Rows typed in as display text. Nothing is calculated, so they are only as good as their
    provenance: mark the reel `sourced` (with citations) or leave it `illustrative`."""

    kind: Literal["manual"]
    rows: list[ReelRow] = Field(min_length=1, max_length=MAX_ROWS)

    def default_methodology(self) -> str | None:
        return None

    def required_tokens(self) -> list[str]:
        return []


class WithdrawalRateFigures(_Spec):
    """Capital needed to fund each annual income: annual_income / withdrawal_rate."""

    kind: Literal["withdrawal_rate"]
    withdrawal_rate_pct: Number = Field(gt=0, le=100)
    annual_incomes: list[Number] = Field(min_length=1, max_length=MAX_ROWS)

    @field_validator("annual_incomes")
    @classmethod
    def _positive_unique(cls, values: list[Decimal]) -> list[Decimal]:
        if any(v <= 0 for v in values):
            raise ValueError("annual incomes must be greater than zero")
        if len(set(values)) != len(values):
            raise ValueError("annual incomes must be unique")
        return values

    def default_methodology(self) -> str:
        return f"Using a {plain(self.withdrawal_rate_pct)}% withdrawal rate"

    def required_tokens(self) -> list[str]:
        return [f"{plain(self.withdrawal_rate_pct)}%"]


class AgeTargetFigures(_Spec):
    """Recurring contribution needed from each starting age to reach a target by a target age.

    Every assumption is required and has no default, so none can be silently implied.
    """

    kind: Literal["age_target"]
    target_amount: Number = Field(gt=0)
    target_age: int = Field(ge=1, le=120)
    starting_ages: list[int] = Field(min_length=1, max_length=MAX_ROWS)
    annual_return_pct: Number = Field(ge=0, le=50)  # nominal annual rate
    contribution_frequency: Frequency
    compounding: Frequency
    contribution_timing: Literal["end", "start"] = "end"  # of each contribution period
    label_template: str = Field(default="Start at {age}", min_length=1, max_length=24)

    @field_validator("label_template")
    @classmethod
    def _has_age_placeholder(cls, value: str) -> str:
        if "{age}" not in value:
            raise ValueError("label_template must contain {age}")
        return value

    @model_validator(mode="after")
    def _ages_make_sense(self) -> AgeTargetFigures:
        if len(set(self.starting_ages)) != len(self.starting_ages):
            raise ValueError("starting_ages must be unique")
        if any(a < 0 for a in self.starting_ages):
            raise ValueError("starting_ages must not be negative")
        late = [a for a in self.starting_ages if a >= self.target_age]
        if late:
            raise ValueError(f"starting ages {late} must be below target_age {self.target_age}")
        return self

    def default_methodology(self) -> str:
        return (
            f"Target {format_money_compact(self.target_amount, trim=True)} by age "
            f"{self.target_age}. {plain(self.annual_return_pct)}% annual return, compounded "
            f"{_COMPOUNDED[self.compounding]}, {self.contribution_frequency} contributions."
        )

    def required_tokens(self) -> list[str]:
        return [
            format_money_compact(self.target_amount, trim=True),
            f"{self.target_age}",
            f"{plain(self.annual_return_pct)}%",
            self.compounding,
            self.contribution_frequency,
        ]


Figures = Annotated[
    ManualFigures | WithdrawalRateFigures | AgeTargetFigures, Field(discriminator="kind")
]


# ------------------------------------------------------------------ assets and provenance


class BackgroundOrigin(StrEnum):
    USER_SUPPLIED = "user_supplied"
    LICENSED_PROJECT_ASSET = "licensed_project_asset"
    GENERATED_PLACEHOLDER = "generated_placeholder"  # abstract art made locally, not real footage


class BackgroundSpec(_Spec):
    """The one background image or video. It is never downloaded or generated for you."""

    path: str = Field(min_length=1, max_length=500)  # relative paths resolve against the spec file
    origin: BackgroundOrigin
    license: str = Field(min_length=3, max_length=200)  # e.g. "Own footage", "Pexels License"
    attribution: str | None = Field(default=None, max_length=200)

    @field_validator("path")
    @classmethod
    def _local_path(cls, value: str) -> str:
        if re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
            raise ValueError("background must be a local file; URLs are never fetched")
        return value


class VoiceoverSpec(_Spec):
    """Optional narration audio you recorded or licensed. Text-on-screen is the default."""

    path: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def _local_path(cls, value: str) -> str:
        if re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
            raise ValueError("voiceover must be a local file; URLs are never fetched")
        return value


class SourceCitation(_Spec):
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=500)
    as_of: date  # publication or retrieval date of the cited data
    publisher: str | None = Field(default=None, max_length=120)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source url must be a valid http(s) URL (it is cited, never fetched)")
        return value


class Verification(StrEnum):
    ILLUSTRATIVE = "illustrative"  # demo/hypothetical numbers; not researched or verified
    SOURCED = "sourced"  # the facts/assumptions are backed by the cited sources


class Provenance(_Spec):
    verification: Verification = Verification.ILLUSTRATIVE
    sources: list[SourceCitation] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _sourced_needs_sources(self) -> Provenance:
        if self.verification is Verification.SOURCED and not self.sources:
            raise ValueError(
                "verification 'sourced' requires at least one source (title, url, as_of); "
                "otherwise use 'illustrative'"
            )
        return self


# ------------------------------------------------------------------ the spec


class ReelSpec(_Spec):
    """Everything a finance-facts reel needs. Content only: layout and timing live in settings."""

    schema_version: Literal[1] = 1
    template: Literal["finance_facts"] = "finance_facts"
    title: str = Field(min_length=3, max_length=90)
    methodology: str | None = Field(default=None, min_length=3, max_length=160)
    figures: Figures
    cta: str = Field(min_length=3, max_length=140)
    disclosure: str = Field(min_length=10, max_length=220)
    background: BackgroundSpec
    voiceover: VoiceoverSpec | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    accent_color: str | None = None

    @field_validator("accent_color")
    @classmethod
    def _hex(cls, value: str | None) -> str | None:
        if value is not None and not _HEX_COLOR.match(value):
            raise ValueError("accent_color must look like #RRGGBB")
        return value

    @model_validator(mode="after")
    def _content_rules(self) -> ReelSpec:
        _reject_guarantees(self.title, "title")
        _reject_guarantees(self.cta, "cta")
        if self.methodology:
            _reject_guarantees(self.methodology, "methodology")
        if isinstance(self.figures, ManualFigures):
            for row in self.figures.rows:
                _reject_guarantees(f"{row.label} {row.value}", "row")
            if not self.methodology:
                raise ValueError(
                    "methodology is required for manual figures: say where the numbers come from"
                )
        elif self.methodology:
            lowered = self.methodology.lower()
            missing = [t for t in self.figures.required_tokens() if t.lower() not in lowered]
            if missing:
                raise ValueError(
                    f"methodology must show every calculation assumption; missing {missing}. "
                    f"Omit it to use: {self.figures.default_methodology()!r}"
                )
        if self.provenance.verification is Verification.ILLUSTRATIVE:
            lowered = self.disclosure.lower()
            if "hypothetical" not in lowered and "illustrative" not in lowered:
                raise ValueError(
                    "unsourced (illustrative) figures need a disclosure that says they are "
                    "hypothetical or illustrative"
                )
        return self


# ------------------------------------------------------------------ calculation record


class CalculatedRow(Strict):
    label: str
    display: str
    exact: str  # unrounded result as a decimal string
    inputs: dict[str, str]


class CalculationRecord(Strict):
    """Everything needed to reproduce the figures by hand."""

    kind: str
    formula: str
    inputs: dict[str, str]
    assumptions: list[str]
    rounding: str
    rows: list[CalculatedRow]


# ------------------------------------------------------------------ plan (resolved, renderable)


class ResolvedBackground(Strict):
    stored_path: str  # relative to the job directory
    sha256: str
    media_type: Literal["image", "video"]
    width: int
    height: int
    duration_s: float | None = None  # videos only
    origin: BackgroundOrigin
    license: str
    attribution: str | None = None


class ResolvedVoiceover(Strict):
    stored_path: str
    sha256: str
    duration_s: float = Field(gt=0)


class ReelTiming(Strict):
    """When each element appears (seconds from the start). Rows are revealed one at a time."""

    title_at_s: float
    methodology_at_s: float
    row_at_s: list[float]
    cta_at_s: float
    disclosure_at_s: float
    fade_s: float
    duration_s: float


class ReelPlan(Strict):
    schema_version: int = 1
    title: str
    methodology: str
    rows: list[ReelRow]
    cta: str
    disclosure: str
    accent_color: str | None = None
    background: ResolvedBackground
    voiceover: ResolvedVoiceover | None = None
    provenance: Provenance
    calculation: CalculationRecord | None = None
    timing: ReelTiming
    warnings: list[str] = Field(default_factory=list)


class LayoutBox(Strict):
    name: str
    x: int
    y: int
    width: int
    height: int
    start_s: float


class ReelLayout(Strict):
    """Where the renderer put each text element; the validate stage checks it against the
    safe area."""

    width: int
    height: int
    safe_left: int
    safe_top: int
    safe_right: int
    safe_bottom: int
    scale: float
    font_regular: str
    font_bold: str
    boxes: list[LayoutBox]
