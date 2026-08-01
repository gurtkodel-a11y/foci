"""Fetch feeds and ingest new items into the database."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable

import feedparser

from foci import db
from foci.config import Settings, Source as ConfigSource
from foci.feeds.normalizer import normalize_entry

logger = logging.getLogger(__name__)


def sync_sources(config_sources: list[ConfigSource]) -> None:
    """
    Ensure every source in the config exists in the DB.
    Existing rows are not overwritten — only new ones are inserted.
    """
    for s in config_sources:
        db.Source.get_or_create(
            url=s.url,
            defaults={
                "name": s.name,
                "type": s.type,
                "tags": ",".join(s.tags),
                "active": s.active,
            },
        )


def poll_all(
    settings: Settings,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """
    Poll every active, non-muted source.
    Returns {source_name: new_item_count}; count is -1 on error.
    """
    sources = db.Source.select().where(db.Source.active == True)
    results: dict[str, int] = {}

    for source in sources:
        if source.is_muted():
            _emit(on_progress, f"  Skipping {source.name} (muted until {source.muted_until:%Y-%m-%d})")
            continue

        _emit(on_progress, f"  Polling {source.name}...")
        try:
            count = poll_source(source, settings)
            results[source.name] = count
            _emit(on_progress, f"    → {count} new item(s)")
        except Exception as exc:
            logger.warning("Failed to poll %s: %s", source.name, exc)
            results[source.name] = -1
            _emit(on_progress, f"    → error: {exc}")

    return results


def poll_source(source: db.Source, settings: Settings) -> int:
    """Poll one source. Returns the number of new items inserted."""
    if source.type == "youtube":
        return _poll_youtube(source, settings)
    return _poll_rss(source, settings)


# ---------------------------------------------------------------------------
# RSS / Atom
# ---------------------------------------------------------------------------

def _poll_rss(source: db.Source, settings: Settings) -> int:
    parsed = feedparser.parse(source.url)

    # bozo=True means the feed is malformed, but entries may still be usable
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Unparseable feed: {parsed.bozo_exception}")

    new_count = 0
    expires_at = _expires(settings)

    for entry in parsed.entries:
        data = normalize_entry(entry, source.type)
        if data is None:
            continue

        _, created = db.Item.get_or_create(
            guid=data["guid"],
            defaults={
                "source": source,
                "title": data["title"],
                "url": data["url"],
                "description": data["description"],
                "media_type": data["media_type"],
                "estimated_size": data["estimated_size"],
                "duration": data["duration"],
                "published_at": data["published_at"],
                "expires_at": expires_at,
                "status": "unread",
            },
        )
        if created:
            new_count += 1

    _mark_polled(source)
    return new_count


# ---------------------------------------------------------------------------
# YouTube (via yt-dlp flat-playlist extraction)
# ---------------------------------------------------------------------------

_YTDLP_OPTS = {
    "extract_flat": True,
    "quiet": True,
    "no_warnings": True,
    "playlistend": 20,  # only the 20 most recent uploads per poll
}


def _poll_youtube(source: db.Source, settings: Settings) -> int:
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("yt-dlp is not installed. Run: pip install yt-dlp")

    with yt_dlp.YoutubeDL(_YTDLP_OPTS) as ydl:
        info = ydl.extract_info(source.url, download=False)

    if info is None:
        raise ValueError("yt-dlp returned no data for this URL")

    entries = info.get("entries") or []
    if not entries:
        # A single video was returned (shouldn't happen for a channel, but handle it)
        entries = [info]

    new_count = 0
    expires_at = _expires(settings)

    for entry in entries:
        if not entry:
            continue

        video_id = entry.get("id") or entry.get("url") or ""
        if not video_id:
            continue

        guid = f"youtube:{video_id}"
        url = _yt_video_url(entry, video_id)
        title = (entry.get("title") or url).strip()
        description = entry.get("description") or ""
        duration = entry.get("duration")  # already an int (seconds) from yt-dlp
        published_at = _parse_yt_date(entry.get("upload_date"))

        _, created = db.Item.get_or_create(
            guid=guid,
            defaults={
                "source": source,
                "title": title,
                "url": url,
                "description": description,
                "media_type": "video",
                "estimated_size": None,
                "duration": duration,
                "published_at": published_at,
                "expires_at": expires_at,
                "status": "unread",
            },
        )
        if created:
            new_count += 1

    _mark_polled(source)
    return new_count


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _expires(settings: Settings) -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(
        days=settings.feed.item_expiry_days
    )


def _mark_polled(source: db.Source) -> None:
    source.last_polled = datetime.datetime.utcnow()
    source.save()


def _yt_video_url(entry: dict, video_id: str) -> str:
    url = entry.get("webpage_url") or entry.get("url") or ""
    if url.startswith("http"):
        return url
    return f"https://www.youtube.com/watch?v={video_id}"


def _parse_yt_date(upload_date: str | None) -> datetime.datetime | None:
    if upload_date and len(upload_date) == 8:
        try:
            return datetime.datetime.strptime(upload_date, "%Y%m%d")
        except ValueError:
            pass
    return None


def _emit(fn: Callable[[str], None] | None, msg: str) -> None:
    if fn:
        fn(msg)
