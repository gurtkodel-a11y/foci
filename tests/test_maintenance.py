"""Tests for foci.feeds.maintenance — in-memory DB, no network calls."""

from __future__ import annotations

import datetime
import itertools

import pytest

from foci import db
from foci.config import Settings, BudgetConfig, FeedConfig
from foci.feeds.maintenance import (
    budget_status,
    expire_items,
    get_daily_usage,
    mute_source,
    record_consumption,
    unmute_source,
)


@pytest.fixture(autouse=True)
def in_memory_db():
    db._db.init(":memory:")
    db._db.connect(reuse_if_open=True)
    db._db.create_tables(db.MODELS, safe=True)
    yield
    db._db.drop_tables(db.MODELS, safe=True)
    db._db.close()


@pytest.fixture
def settings():
    return Settings(
        feed=FeedConfig(item_expiry_days=14),
        budget=BudgetConfig(daily_article_soft_limit=5),
    )


@pytest.fixture
def source():
    return db.Source.create(name="Test Blog", url="https://blog.com/feed", type="rss")


@pytest.fixture
def yt_source():
    return db.Source.create(name="Test Channel", url="https://yt.com/@c", type="youtube")


_guid_counter = itertools.count()


def _article(source, *, days_old=0, expires_in=14, status="unread"):
    now = datetime.datetime.utcnow()
    pub = now - datetime.timedelta(days=days_old)
    expires = now + datetime.timedelta(days=expires_in)
    return db.Item.create(
        source=source,
        guid=f"guid-{days_old}-{status}-{expires_in}-{next(_guid_counter)}",
        title=f"Article {days_old}d old",
        url="https://blog.com/1",
        media_type="article",
        status=status,
        published_at=pub,
        expires_at=expires,
    )


def _video(source, *, status="unread"):
    return db.Item.create(
        source=source, guid="v1", title="A Video",
        url="https://yt.com/v", media_type="video", status=status,
    )


# ---------------------------------------------------------------------------
# expire_items
# ---------------------------------------------------------------------------

class TestExpireItems:
    def test_archives_expired_unread_items(self, source, settings):
        item = _article(source, expires_in=-1)  # expired yesterday
        expired = expire_items(settings)
        assert expired == 1
        assert db.Item.get_by_id(item.id).status == "archived"

    def test_leaves_fresh_items_alone(self, source, settings):
        item = _article(source, expires_in=7)  # expires in 7 days
        expired = expire_items(settings)
        assert expired == 0
        assert db.Item.get_by_id(item.id).status == "unread"

    def test_does_not_archive_already_read_items(self, source, settings):
        item = _article(source, expires_in=-1, status="read")
        expired = expire_items(settings)
        assert expired == 0
        assert db.Item.get_by_id(item.id).status == "read"

    def test_does_not_archive_queued_videos(self, source, yt_source, settings):
        video = db.Item.create(
            source=yt_source, guid="vq1", title="Queued Video",
            url="https://yt.com/v", media_type="video", status="unread",
            expires_at=datetime.datetime.utcnow() - datetime.timedelta(days=1),
        )
        now = datetime.datetime.utcnow()
        db.QueueEntry.create(
            item=video,
            queued_at=now,
            eligible_at=now + datetime.timedelta(hours=2),
            status="pending",
        )
        expired = expire_items(settings)
        assert expired == 0
        assert db.Item.get_by_id(video.id).status == "unread"

    def test_archives_multiple_expired_items(self, source, settings):
        for i in range(5):
            _article(source, expires_in=-1)
        assert expire_items(settings) == 5


# ---------------------------------------------------------------------------
# get_daily_usage
# ---------------------------------------------------------------------------

class TestGetDailyUsage:
    def test_creates_record_if_not_exists(self):
        usage = get_daily_usage()
        assert usage.articles_read == 0
        assert usage.videos_watched == 0
        assert usage.audio_played == 0

    def test_idempotent(self):
        get_daily_usage()
        get_daily_usage()
        assert db.DailyUsage.select().count() == 1


# ---------------------------------------------------------------------------
# record_consumption
# ---------------------------------------------------------------------------

class TestRecordConsumption:
    def test_increments_articles_read(self, source):
        item = _article(source)
        record_consumption(item)
        assert get_daily_usage().articles_read == 1

    def test_increments_videos_watched(self, yt_source):
        video = _video(yt_source)
        record_consumption(video)
        assert get_daily_usage().videos_watched == 1

    def test_marks_item_as_read(self, source):
        item = _article(source)
        record_consumption(item)
        assert db.Item.get_by_id(item.id).status == "read"

    def test_multiple_reads_accumulate(self, source):
        for i in range(3):
            item = _article(source, expires_in=14 + i)
            # give each a unique guid
            item.guid = f"unique-{i}"
            item.save()
            record_consumption(item)
        assert get_daily_usage().articles_read == 3

    def test_unknown_media_type_does_not_crash(self, source):
        item = db.Item.create(
            source=source, guid="other-1", title="Other",
            url="https://blog.com/other", media_type="other", status="unread",
        )
        usage = record_consumption(item)
        # Counters unchanged but item still marked read
        assert usage.articles_read == 0
        assert db.Item.get_by_id(item.id).status == "read"


# ---------------------------------------------------------------------------
# budget_status
# ---------------------------------------------------------------------------

class TestBudgetStatus:
    def test_not_over_initially(self, settings):
        articles, limit, over = budget_status(settings)
        assert articles == 0
        assert limit == 5
        assert over is False

    def test_over_when_at_limit(self, source, settings):
        for i in range(5):
            item = _article(source, expires_in=14 + i)
            item.guid = f"b{i}"
            item.save()
            record_consumption(item)
        _, _, over = budget_status(settings)
        assert over is True

    def test_not_over_when_one_below_limit(self, source, settings):
        for i in range(4):
            item = _article(source, expires_in=14 + i)
            item.guid = f"c{i}"
            item.save()
            record_consumption(item)
        _, _, over = budget_status(settings)
        assert over is False


# ---------------------------------------------------------------------------
# mute / unmute
# ---------------------------------------------------------------------------

class TestMuteSource:
    def test_sets_muted_until(self, source):
        until = mute_source(source, days=7)
        refreshed = db.Source.get_by_id(source.id)
        assert refreshed.is_muted()
        assert abs((refreshed.muted_until - until).total_seconds()) < 2

    def test_is_muted_returns_false_after_unmute(self, source):
        mute_source(source, days=7)
        unmute_source(source)
        refreshed = db.Source.get_by_id(source.id)
        assert not refreshed.is_muted()

    def test_zero_days_expires_immediately(self, source):
        mute_source(source, days=0)
        refreshed = db.Source.get_by_id(source.id)
        # muted_until is in the past, so is_muted() returns False
        assert not refreshed.is_muted()
