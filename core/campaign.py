"""Campaigns: turn a short declarative file into hundreds of validated reel specs.

A campaign lists *families*. A family fixes a figure kind, a grid of assumptions and some wording
templates; the cartesian product of the grid is the set of videos. Code (not an LLM) expands the
grid, writes the wording, validates every spec against `ReelSpec` and calculates every number, so
an LLM only has to author this small file instead of each video.

Template placeholders: `{field}` or `{field|filter|filter}` where `field` is any figure field
(`initial_amount`, `years`, ...). Filters: money ($10K), pct (7%), title (Monthly), max, min, count.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from core.exceptions import InvalidInputError
from core.finance import resolve_methodology, resolve_rows
from core.models.finance import ReelPacing, ReelSpec, ReelStyle
from core.money import format_money_label, plain, to_decimal

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_]{0,39}$")]
Hashtag = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_]{2,30}$")]
KINDS = (
    "withdrawal_rate",
    "compound_growth",
    "age_target",
    "contribution_growth",
    "loan_payment",
    "inflation_erosion",
    "doubling_time",
)

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)((?:\|[a-z]+)*)\}")
_DUMMY_BACKGROUND = {"path": "background.mp4", "origin": "generated_placeholder", "license": "n/a"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Family(_Model):
    id: Slug
    kind: str
    mood: Slug = "growth"  # picks the background: library/<mood>/ clips, else a generated palette
    grid: dict[str, list[Any]] = Field(default_factory=dict)  # field -> alternatives
    fixed: dict[str, Any] = Field(default_factory=dict)  # field -> constant value
    titles: list[str] = Field(min_length=1)
    ctas: list[str] = Field(min_length=1)
    disclosure: str | None = None  # overrides the campaign default
    hashtags: list[Hashtag] = Field(default_factory=list)
    weight: int = Field(default=1, ge=1, le=100)  # relative share of the final selection
    max_videos: int | None = Field(default=None, ge=1)  # cap on this family's videos
    eyebrow: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def _check(self) -> Family:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {list(KINDS)}, not {self.kind!r}")
        overlap = set(self.grid) & set(self.fixed)
        if overlap:
            raise ValueError(f"fields in both grid and fixed: {sorted(overlap)}")
        empty = [name for name, values in self.grid.items() if not values]
        if empty:
            raise ValueError(f"grid fields with no values: {empty}")
        return self


class Backgrounds(_Model):
    """Where backgrounds come from. Your own clips are used when present; otherwise abstract
    placeholders are generated locally (never real footage, never downloaded)."""

    library: str | None = None  # folder; clips in <library>/<mood>/ or directly in <library>
    license: str | None = Field(default=None, min_length=1)  # the licence your library clips carry
    origin: Literal["user_supplied", "licensed_project_asset"] = "user_supplied"
    require_library: bool = False
    attribution: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _library_needs_license(self) -> Backgrounds:
        if self.library and not self.license:
            raise ValueError("backgrounds.license is required when backgrounds.library is set")
        return self


class Publishing(_Model):
    start_date: date | None = None
    posts_per_day: int = Field(default=3, ge=1, le=48)
    first_post_hour: int = Field(default=9, ge=0, le=23)
    common_hashtags: list[Hashtag] = Field(default_factory=lambda: ["shorts", "personalfinance"])
    max_hashtags: int = Field(default=6, ge=0, le=30)
    timezone: str | None = None


class MusicLibrary(_Model):
    library: str
    license: str = Field(min_length=1, max_length=300)
    gain_db: float = Field(default=-12, ge=-40, le=0)
    fade_in_s: float = Field(default=0.5, gt=0, le=5)
    fade_out_s: float = Field(default=1.5, gt=0, le=5)


class Campaign(_Model):
    name: Slug
    count: int = Field(default=1000, ge=1, le=100_000)
    seed: int = 1
    disclosure: str = Field(
        default="Hypothetical illustration for educational purposes only. Not financial advice.",
        min_length=10,
        max_length=220,
    )
    accent_colors: list[str] = Field(default_factory=list)
    backgrounds: Backgrounds = Field(default_factory=Backgrounds)
    publishing: Publishing = Field(default_factory=Publishing)
    families: list[Family] = Field(min_length=1)
    style: ReelStyle = Field(default_factory=ReelStyle)
    pacing: ReelPacing | None = None
    music: MusicLibrary | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> Campaign:
        ids = [f.id for f in self.families]
        if len(set(ids)) != len(ids):
            raise ValueError("family ids must be unique")
        return self


@dataclass
class PlannedReel:
    index: int
    job_id: str
    family: str
    mood: str
    key: str  # stable hash of the figures: the same assumptions always give the same key
    spec: dict[str, Any]  # a ReelSpec as plain data, background filled in at materialise time
    hashtags: list[str]


@dataclass
class ExpansionReport:
    candidates: int = 0
    duplicates: int = 0
    invalid: int = 0
    selected: int = 0
    per_family: dict[str, int] = field(default_factory=dict)  # candidates per family
    selected_per_family: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # first few distinct validation messages
    title_collisions: int = 0

    def lines(self, requested: int) -> list[str]:
        out = [
            f"requested {requested}, selected {self.selected} "
            f"({self.candidates} valid candidates, {self.duplicates} duplicates, "
            f"{self.invalid} invalid)",
        ]
        for family, count in self.per_family.items():
            out.append(f"  {family}: {self.selected_per_family.get(family, 0)}/{count}")
        if self.title_collisions:
            out.append(
                f"note: {self.title_collisions} candidates skipped because every title "
                "variant was already used (add title templates that mention more assumptions)"
            )
        out += [f"problem: {p}" for p in self.problems]
        return out


# ------------------------------------------------------------------ templates


def _apply_filter(value: Any, name: str) -> Any:
    if name == "max" and isinstance(value, list):
        return max(to_decimal(v) for v in value)
    if name == "min" and isinstance(value, list):
        return min(to_decimal(v) for v in value)
    if name == "count" and isinstance(value, list):
        return len(value)
    if name == "money":
        return format_money_label(to_decimal(value))
    if name == "pct":
        return f"{plain(to_decimal(value))}%"
    if name == "title" and isinstance(value, str):
        return value.replace("_", " ").title()
    raise InvalidInputError(f"Unknown or inapplicable template filter {name!r} for {value!r}")


def _plain_text(value: Any) -> str:
    if isinstance(value, Decimal):
        return plain(value)
    if isinstance(value, float):
        return plain(to_decimal(value))
    if isinstance(value, list):
        return ", ".join(_plain_text(v) for v in value)
    return str(value)


def render_template(template: str, values: dict[str, Any]) -> str:
    """Fill `{field|filter}` placeholders from a figures dict."""

    def replace(match: re.Match[str]) -> str:
        name, filters = match.group(1), [f for f in match.group(2).split("|") if f]
        if name not in values:
            raise InvalidInputError(
                f"Template {template!r} uses {{{name}}}, which is not a figure field "
                f"(available: {sorted(values)})"
            )
        value = values[name]
        for filter_name in filters:
            value = _apply_filter(value, filter_name)
        return _plain_text(value)

    return _PLACEHOLDER.sub(replace, template)


# ------------------------------------------------------------------ expansion


def _stable_hash(*parts: object) -> str:
    text = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pick(options: list[str], digest: str, salt: str) -> str:
    return options[int(_stable_hash(digest, salt)[:8], 16) % len(options)]


def _first_line(exc: ValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err["loc"])
    return f"{where}: {str(err['msg']).removeprefix('Value error, ')}"


@dataclass
class _Candidate:
    key: str
    spec: dict[str, Any]
    titles: list[str]  # valid title variants, preferred first; one is used per campaign
    hashtags: list[str]


def _candidates(campaign: Campaign, family: Family, report: ExpansionReport) -> list[_Candidate]:
    """Every valid grid combination of one family."""
    names = list(family.grid)
    found: list[_Candidate] = []
    seen: set[str] = set()
    for combo in itertools.product(*(family.grid[n] for n in names)):
        figures: dict[str, Any] = {
            "kind": family.kind,
            **family.fixed,
            **dict(zip(names, combo, strict=True)),
        }
        key = _stable_hash(family.id, figures)[:10]
        if key in seen:
            report.duplicates += 1
            continue
        seen.add(key)
        values = {k: v for k, v in figures.items() if k != "kind"}
        start = int(_stable_hash(key, "title")[:8], 16)
        variants = [
            family.titles[(start + i) % len(family.titles)] for i in range(len(family.titles))
        ]
        spec: dict[str, Any] = {
            "title": "",
            "figures": figures,
            "cta": render_template(_pick(family.ctas, key, "cta"), values),
            "disclosure": family.disclosure or campaign.disclosure,
            "background": _DUMMY_BACKGROUND,
            "style": {
                **campaign.style.model_dump(exclude_none=True),
                **({"eyebrow": family.eyebrow} if family.eyebrow else {}),
            },
        }
        if campaign.pacing:
            spec["pacing"] = campaign.pacing.model_dump()
        if campaign.accent_colors:
            spec["accent_color"] = _pick(campaign.accent_colors, key, "accent")
        titles: list[str] = []
        for template in variants:
            title = render_template(template, values)
            try:
                ReelSpec.model_validate({**spec, "title": title})
            except ValidationError as exc:
                message = f"{family.id}: {_first_line(exc)}"
                if message not in report.problems and len(report.problems) < 8:
                    report.problems.append(message)
                continue
            if title not in titles:
                titles.append(title)
        if not titles:
            report.invalid += 1
            continue
        spec["title"] = titles[0]
        found.append(_Candidate(key, spec, titles, family.hashtags))
    return found


def expand(
    campaign: Campaign, *, count: int | None = None
) -> tuple[list[PlannedReel], ExpansionReport]:
    """Expand every family, drop duplicates and invalid combinations, and pick `count` reels.

    Selection is deterministic for a given campaign: each family is shuffled with the seed, then
    families take turns (weighted), so any prefix of the result mixes topics instead of
    exhausting one grid before the next. Every video gets a title no other video has: when a
    candidate's preferred title is taken it tries its other title templates, and is skipped if
    all are taken.
    """
    wanted = count or campaign.count
    report = ExpansionReport()
    pools: dict[str, list[_Candidate]] = {}
    for family in campaign.families:
        pool = _candidates(campaign, family, report)
        random.Random(f"{campaign.seed}:{family.id}").shuffle(pool)
        if family.max_videos is not None:
            pool = pool[: family.max_videos]
        pools[family.id] = pool
        report.per_family[family.id] = len(pool)
        report.candidates += len(pool)

    cursors = dict.fromkeys(pools, 0)
    used_titles: set[str] = set()
    used_facts: set[str] = set()
    planned: list[PlannedReel] = []
    while len(planned) < wanted:
        progressed = False
        for family in campaign.families:
            for _ in range(family.weight):
                pool = pools[family.id]
                while cursors[family.id] < len(pool) and len(planned) < wanted:
                    candidate = pool[cursors[family.id]]
                    cursors[family.id] += 1
                    progressed = True
                    title = next((t for t in candidate.titles if t not in used_titles), None)
                    if title is None:
                        report.title_collisions += 1
                        continue
                    parsed = ReelSpec.model_validate(candidate.spec)
                    rows, _ = resolve_rows(parsed)
                    facts = _stable_hash(
                        resolve_methodology(parsed), [row.model_dump() for row in rows]
                    )
                    if facts in used_facts:
                        report.duplicates += 1
                        continue
                    used_facts.add(facts)
                    used_titles.add(title)
                    planned.append(
                        PlannedReel(
                            index=len(planned) + 1,
                            job_id=f"{campaign.name}_{candidate.key}",
                            family=family.id,
                            mood=family.mood,
                            key=candidate.key,
                            spec={**candidate.spec, "title": title},
                            hashtags=list(candidate.hashtags),
                        )
                    )
                    report.selected_per_family[family.id] = (
                        report.selected_per_family.get(family.id, 0) + 1
                    )
                    break
        if not progressed:
            break
    report.selected = len(planned)
    return planned, report


def load_campaign(text: str) -> Campaign:
    """Parse and validate campaign YAML/JSON text with readable errors."""
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InvalidInputError(f"Campaign is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("Campaign must be a mapping at the top level.")
    data = {k: v for k, v in data.items() if v is not None}  # a key with only comments is null
    try:
        return Campaign.model_validate(data)
    except ValidationError as exc:
        lines = [f"  - {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise InvalidInputError("Invalid campaign:\n" + "\n".join(lines)) from exc
