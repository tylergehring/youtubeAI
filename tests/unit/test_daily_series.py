from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from core.campaign import Publishing, expand, load_campaign
from core.config import Settings
from core.exceptions import InvalidInputError
from core.finance import build_timing, resolve_methodology, resolve_rows
from core.models.finance import ReelPlan, ReelSpec, ResolvedBackground
from core.publish import publish_time
from pipeline.batch import write_plan
from rendering.reel_layout import FontSet, build_layout

CAMPAIGN = Path(__file__).resolve().parents[2] / "config" / "campaigns" / "finance_daily_100.yaml"


def test_daily_calendar_crosses_dst_at_five_pm() -> None:
    publishing = Publishing(
        start_date=date(2026, 10, 14), posts_per_day=1, first_post_hour=17,
        timezone="America/Los_Angeles",
    )
    calendar = [datetime.fromisoformat(publish_time(i, publishing)) for i in range(1, 101)]
    assert calendar[0].isoformat() == "2026-10-14T17:00:00-07:00"
    assert calendar[-1].isoformat() == "2027-01-21T17:00:00-08:00"
    assert calendar[17].utcoffset() == timedelta(hours=-7)
    assert calendar[18].utcoffset() == timedelta(hours=-8)
    for index, scheduled in enumerate(calendar):
        local = scheduled.astimezone(ZoneInfo(publishing.timezone))
        assert local.date() == publishing.start_date + timedelta(days=index)
        assert (local.hour, local.minute) == (17, 0)


def test_unknown_zone_is_an_explicit_error() -> None:
    publishing = Publishing(start_date=date(2026, 10, 14), timezone="Not/AZone")
    with pytest.raises(InvalidInputError, match="time zone"):
        publish_time(1, publishing)


def test_legacy_naive_schedule_still_works() -> None:
    publishing = Publishing(start_date=date(2026, 10, 14))
    assert publish_time(2, publishing) == "2026-10-14T13:00"


def test_all_100_topics_have_distinct_facts_and_legible_layouts() -> None:
    campaign = load_campaign(CAMPAIGN.read_text(encoding="utf-8"))
    videos, report = expand(campaign)
    assert report.invalid == 0
    assert len(videos) == 100
    assert len({v.spec["title"] for v in videos}) == 100
    assert report.selected_per_family == {
        family.id: family.max_videos for family in campaign.families
    }
    assert [v.key for v in expand(campaign)[0]] == [v.key for v in videos]
    facts: set[str] = set()
    settings = Settings()
    fonts = FontSet()
    for video in videos:
        spec = ReelSpec.model_validate(video.spec)
        rows, calculation = resolve_rows(spec)
        fact = json.dumps([resolve_methodology(spec), [r.model_dump() for r in rows]])
        assert fact not in facts
        facts.add(fact)
        timing = settings.finance.timing.model_copy(update=spec.pacing.model_dump())
        plan = ReelPlan(
            title=spec.title, methodology=resolve_methodology(spec), rows=rows,
            cta=spec.cta, disclosure=spec.disclosure, style=spec.style,
            accent_color=spec.accent_color, provenance=spec.provenance, calculation=calculation,
            timing=build_timing(len(rows), timing, settings.finance.fps),
            background=ResolvedBackground(
                stored_path="background.mp4", sha256="0" * 64, media_type="video",
                width=1080, height=1920, origin="user_supplied", license="User permission",
            ),
        )
        layout = build_layout(plan, settings.finance, fonts)
        assert layout.report.scale >= 0.85
        assert all(b.y + b.height <= layout.report.safe_bottom for b in layout.report.boxes)


def campaign_text(tmp_path: Path) -> str:
    data = yaml.safe_load(CAMPAIGN.read_text(encoding="utf-8"))
    video = tmp_path / "video"
    audio = tmp_path / "audio"
    video.mkdir(exist_ok=True)
    audio.mkdir(exist_ok=True)
    for i in range(26):
        (video / f"clip{i}.mp4").touch()
    for i in range(11):
        (audio / f"song{i}.mp3").touch()
    data["backgrounds"]["library"] = str(video)
    data["music"]["library"] = str(audio)
    return yaml.safe_dump(data)


def test_plan_is_immutable_and_random_pairs_reproducible(tmp_path: Path) -> None:
    settings = Settings(workspace_dir=tmp_path / "workspace")
    text = campaign_text(tmp_path)
    name, _ = write_plan(settings, text, None)
    root = settings.workspace_dir / "batches" / name
    plan = json.loads((root / "plan.json").read_text())
    before = (root / "plan.json").read_bytes()
    assert len({(v["background"], v["music"]) for v in plan["items"]}) == 100
    assert len({v["background"] for v in plan["items"]}) == 26
    assert len({v["music"] for v in plan["items"]}) == 11
    write_plan(settings, text, None)
    assert (root / "plan.json").read_bytes() == before
    with pytest.raises(InvalidInputError, match="immutable"):
        write_plan(settings, text.replace("20261014", "20261015"), None)
    with pytest.raises(InvalidInputError, match="immutable"):
        write_plan(settings, text, 99)
    assert (root / "plan.json").read_bytes() == before
    other = Settings(workspace_dir=tmp_path / "another_workspace")
    write_plan(other, text, None)
    assert (other.workspace_dir / "batches" / name / "plan.json").read_bytes() == before


def test_plan_requires_exact_count(tmp_path: Path) -> None:
    settings = Settings(workspace_dir=tmp_path / "workspace")
    with pytest.raises(InvalidInputError, match="exact requested"):
        write_plan(settings, campaign_text(tmp_path), 101)
    assert not (settings.workspace_dir / "batches" / "finance_daily_100").exists()


@pytest.mark.parametrize("asset", ["backgrounds", "music"])
def test_missing_supplied_library_is_not_silently_replaced(tmp_path: Path, asset: str) -> None:
    data = yaml.safe_load(campaign_text(tmp_path))
    data[asset]["library"] = str(tmp_path / "missing")
    with pytest.raises(InvalidInputError):
        write_plan(Settings(workspace_dir=tmp_path / "workspace"), yaml.safe_dump(data), None)


def test_duplicate_financial_scenarios_across_families_are_skipped() -> None:
    data = yaml.safe_load(CAMPAIGN.read_text())
    family = data["families"][0]
    data["families"] = [family, {**family, "id": "duplicate", "titles": ["Alt {initial_amount}"]}]
    data["count"] = 20
    videos, report = expand(load_campaign(yaml.safe_dump(data)))
    assert len(videos) == 10
    assert report.duplicates == 10
