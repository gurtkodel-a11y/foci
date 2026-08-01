"""Feed maintenance: item expiry/decay and daily consumption tracking."""

from __future__ import annotations

import datetime

from foci import db
from foci.config import Settings


# ---------------------------------------------------------------------------
# Item expiry
# ---------------------------------------------------------------------------

def expire_items(settings: Settings) -> int:
    """
    Archive unread items whose expires_at date has passed.
    Items in the download queue are not archived — the queue is a commitment.
    Returns the count of items archived.
    """
    now = datetime.datetime.utcnow()

    # Find IDs of items that are pending or downloading in the queue —
    # these should not be silently archived out from under the user.
    queued_item_ids = (
        db.QueueEntry.select(db.QueueEntry.item_id)
        .where(db.QueueEntry.status.in_(["pending", "downloading"]))
        .tuples()
    )
    protected = {row[0] for row in queued_item_ids}

    query = (
        db.Item.select()
        .where(
            db.Item.status == "unread",
            db.Item.expires_at.is_null(False),
            db.Item.expires_at <= now,
        )
    )

    count = 0
    for item in query:
        if item.id not in protected:
            item.status = "archived"
            item.save()
            count += 1

    return count


# ---------------------------------------------------------------------------
# Daily consumption tracking
# ---------------------------------------------------------------------------

def get_daily_usage() -> db.DailyUsage:
    """Return today's usage record, creating it if it doesn't exist yet."""
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    usage, _ = db.DailyUsage.get_or_create(
        date=today,
        defaults={"articles_read": 0, "videos_watched": 0, "audio_played": 0},
    )
    return usage


def record_consumption(item: db.Item) -> db.DailyUsage:
    """
    Mark an item as read/watched and increment today's usage counter.
    Returns the updated DailyUsage for the caller to inspect.
    """
    usage = get_daily_usage()

    field_map = {
        "article": db.DailyUsage.articles_read,
        "video": db.DailyUsage.videos_watched,
        "audio": db.DailyUsage.audio_played,
    }
    field = field_map.get(item.media_type)
    if field is not None:
        db.DailyUsage.update({field: field + 1}).where(
            db.DailyUsage.id == usage.id
        ).execute()

    item.status = "read"
    item.save()

    return db.DailyUsage.get_by_id(usage.id)


def budget_status(settings: Settings) -> tuple[int, int, bool]:
    """
    Return (articles_read_today, soft_limit, at_or_over_limit).
    Does not modify any state.
    """
    usage = get_daily_usage()
    limit = settings.budget.daily_article_soft_limit
    return usage.articles_read, limit, usage.articles_read >= limit


# ---------------------------------------------------------------------------
# Mute helpers
# ---------------------------------------------------------------------------

def mute_source(source: db.Source, days: int) -> datetime.datetime:
    """Mute a source for the given number of days. Returns the muted_until datetime."""
    until = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    source.muted_until = until
    source.save()
    return until


def unmute_source(source: db.Source) -> None:
    """Clear the mute on a source immediately."""
    source.muted_until = None
    source.save()
