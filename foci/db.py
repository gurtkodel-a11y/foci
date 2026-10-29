"""Database schema and access layer using peewee + SQLite."""

from __future__ import annotations

import datetime
from pathlib import Path

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

_db = SqliteDatabase(None)  # initialized lazily via init()


class BaseModel(Model):
    class Meta:
        database = _db


class Source(BaseModel):
    """A subscribed feed source."""

    name = CharField()
    url = CharField(unique=True)
    type = CharField()  # "rss" | "youtube"
    tags = TextField(default="")  # comma-separated
    active = BooleanField(default=True)
    muted_until = DateTimeField(null=True)
    last_polled = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.utcnow)

    class Meta:
        table_name = "sources"

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    def is_muted(self) -> bool:
        if self.muted_until is None:
            return False
        return datetime.datetime.utcnow() < self.muted_until


class Item(BaseModel):
    """A single piece of content from a feed."""

    source = ForeignKeyField(Source, backref="items")
    guid = CharField(unique=True)
    title = CharField()
    url = CharField()
    description = TextField(default="")
    media_type = CharField()  # "article" | "audio" | "video" | "other"
    estimated_size = IntegerField(null=True)  # bytes
    duration = IntegerField(null=True)  # seconds
    enclosure_url = CharField(null=True)  # direct media file URL (podcast audio)
    published_at = DateTimeField(null=True)
    fetched_at = DateTimeField(default=datetime.datetime.utcnow)
    expires_at = DateTimeField(null=True)
    status = CharField(default="unread")  # unread | read | archived | dismissed

    class Meta:
        table_name = "items"


class CacheEntry(BaseModel):
    """A locally cached piece of content (article text, audio file, etc.)."""

    item = ForeignKeyField(Item, backref="cache_entries", unique=True)
    local_path = CharField()
    content_type = CharField()  # "text/html" | "audio/mpeg" | etc.
    cached_at = DateTimeField(default=datetime.datetime.utcnow)

    class Meta:
        table_name = "cache_entries"


class QueueEntry(BaseModel):
    """A video download queued by the user."""

    item = ForeignKeyField(Item, backref="queue_entries", unique=True)
    queued_at = DateTimeField(default=datetime.datetime.utcnow)
    eligible_at = DateTimeField()  # queued_at + minimum_queue_age
    priority = IntegerField(default=0)
    status = CharField(default="pending")  # pending | downloading | completed | cancelled | failed
    started_at = DateTimeField(null=True)
    completed_at = DateTimeField(null=True)
    local_path = CharField(null=True)

    class Meta:
        table_name = "queue_entries"

    def is_eligible(self) -> bool:
        return datetime.datetime.utcnow() >= self.eligible_at


class DailyUsage(BaseModel):
    """Tracks daily consumption count for the soft budget feature."""

    date = CharField()  # ISO date string "YYYY-MM-DD"
    articles_read = IntegerField(default=0)
    videos_watched = IntegerField(default=0)
    audio_played = IntegerField(default=0)

    class Meta:
        table_name = "daily_usage"


MODELS = [Source, Item, CacheEntry, QueueEntry, DailyUsage]


def init(db_path: Path) -> None:
    """Initialize the database, creating tables if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db.init(str(db_path))
    _db.connect(reuse_if_open=True)
    _db.create_tables(MODELS, safe=True)
    _migrate()


def _migrate() -> None:
    """Add columns introduced after a database was first created."""
    columns = {c.name for c in _db.get_columns("items")}
    if "enclosure_url" not in columns:
        _db.execute_sql("ALTER TABLE items ADD COLUMN enclosure_url VARCHAR(255)")


def get_db() -> SqliteDatabase:
    return _db
