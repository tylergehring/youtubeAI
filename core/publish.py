"""Short-form post metadata (title, caption, hashtags, schedule) derived from a reel spec.

Everything here is deterministic text assembly from the validated spec; no LLM, no network. The
caption repeats the methodology and disclosure so the assumptions travel with the post.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from core.campaign import Publishing
from core.finance import resolve_methodology
from core.models.finance import ReelSpec

TITLE_LIMIT = 100  # YouTube Shorts title limit; TikTok/Instagram captions are longer


@dataclass(frozen=True)
class Post:
    title: str
    caption: str
    hashtags: list[str]
    publish_at: str  # local ISO time, or "" when no start_date is configured


def hashtags_for(family_tags: list[str], publishing: Publishing) -> list[str]:
    seen: list[str] = []
    for tag in [*family_tags, *publishing.common_hashtags]:
        if tag.lower() not in {t.lower() for t in seen}:
            seen.append(tag)
    return seen[: publishing.max_hashtags]


def publish_time(index: int, publishing: Publishing) -> str:
    """Slot `index` (1-based) spread over the day: `posts_per_day` posts, ~12 hours of spread."""
    if publishing.start_date is None:
        return ""
    day, slot = divmod(index - 1, publishing.posts_per_day)
    spacing = max(1, 12 // publishing.posts_per_day)
    start = datetime.combine(publishing.start_date, time(publishing.first_post_hour))
    return (start + timedelta(days=day, hours=slot * spacing)).strftime("%Y-%m-%dT%H:%M")


def build_post(
    spec: dict[str, Any], family_tags: list[str], publishing: Publishing, index: int
) -> Post:
    parsed = ReelSpec.model_validate(spec)
    tags = hashtags_for(family_tags, publishing)
    title = parsed.title
    if len(title) > TITLE_LIMIT:
        title = title[: TITLE_LIMIT - 1] + "…"
    parts = [
        parsed.title,
        resolve_methodology(parsed),
        parsed.cta,
        parsed.disclosure,
        " ".join(f"#{t}" for t in tags),
    ]
    return Post(
        title=title,
        caption="\n\n".join(p for p in parts if p),
        hashtags=tags,
        publish_at=publish_time(index, publishing),
    )
