"""Tests for foci.ui.web.app — uses FastAPI TestClient with an in-memory DB."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from foci import db
from foci.config import Settings
from foci.ui.web.app import create_app, _fmt_duration, _fmt_humantime, _pluralize


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def in_memory_db(tmp_path):
    # TestClient serves requests on another thread; a ":memory:" DB is per-connection,
    # so use a file-backed DB to keep the schema visible to the app.
    db._db.init(str(tmp_path / "test.db"))
    db._db.connect(reuse_if_open=True)
    db._db.create_tables(db.MODELS, safe=True)
    yield
    db._db.drop_tables(db.MODELS, safe=True)
    db._db.close()


@pytest.fixture
def client():
    return TestClient(create_app(Settings()), raise_server_exceptions=True)


@pytest.fixture
def yt_source():
    return db.Source.create(name="Test Channel", url="https://yt.com/@test", type="youtube")


@pytest.fixture
def blog_source():
    return db.Source.create(name="Test Blog", url="https://blog.com/feed", type="rss")


def _video_item(source, guid="g1", title="A Great Video"):
    return db.Item.create(
        source=source, guid=guid, title=title,
        url="https://yt.com/watch?v=x", media_type="video", status="unread",
    )


def _article_item(source, guid="a1", title="An Interesting Article"):
    return db.Item.create(
        source=source, guid=guid, title=title,
        url="https://blog.com/1", media_type="article", status="unread",
    )


def _eligible_entry(item) -> db.QueueEntry:
    now = datetime.datetime.utcnow()
    return db.QueueEntry.create(
        item=item,
        queued_at=now - datetime.timedelta(hours=3),
        eligible_at=now - datetime.timedelta(hours=1),
        status="pending",
    )


def _waiting_entry(item, hours_from_now=2) -> db.QueueEntry:
    now = datetime.datetime.utcnow()
    return db.QueueEntry.create(
        item=item,
        queued_at=now,
        eligible_at=now + datetime.timedelta(hours=hours_from_now),
        status="pending",
    )


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

class TestFmtDuration:
    def test_minutes_and_seconds(self):
        assert _fmt_duration(754) == "12:34"

    def test_with_hours(self):
        assert _fmt_duration(5025) == "1:23:45"

    def test_zero(self):
        assert _fmt_duration(0) == "0:00"

    def test_none_returns_empty(self):
        assert _fmt_duration(None) == ""


class TestFmtHumantime:
    def test_future_minutes(self):
        future = datetime.datetime.utcnow() + datetime.timedelta(minutes=47, seconds=30)
        assert _fmt_humantime(future) == "in 47m"

    def test_future_hours_exact(self):
        future = datetime.datetime.utcnow() + datetime.timedelta(hours=2, seconds=30)
        result = _fmt_humantime(future)
        assert result.startswith("in 2h") or result.startswith("in 1h")  # timing tolerance

    def test_past_or_now_returns_now(self):
        past = datetime.datetime.utcnow() - datetime.timedelta(seconds=10)
        assert _fmt_humantime(past) == "now"


class TestPluralize:
    def test_one(self):
        assert _pluralize(1) == ""

    def test_many(self):
        assert _pluralize(5) == "s"

    def test_zero(self):
        assert _pluralize(0) == "s"


# ---------------------------------------------------------------------------
# Landing page — basic rendering
# ---------------------------------------------------------------------------

class TestLandingPage:
    def test_returns_200(self, client):
        assert client.get("/").status_code == 200

    def test_any_path_returns_200(self, client):
        assert client.get("/some/deep/path?q=whatever").status_code == 200

    def test_shows_blocked_host_from_header(self, client):
        resp = client.get("/", headers={"host": "youtube.com"})
        assert "youtube.com" in resp.text
        assert "is blocked" in resp.text

    def test_no_blocked_notice_for_localhost(self, client):
        resp = client.get("/", headers={"host": "localhost"})
        assert "is blocked" not in resp.text

    def test_disable_hint_always_present(self, client):
        resp = client.get("/")
        assert "foci block remove" in resp.text

    def test_empty_state_shown_when_nothing_queued(self, client):
        resp = client.get("/")
        assert "foci feed poll" in resp.text


# ---------------------------------------------------------------------------
# Landing page — queue sections
# ---------------------------------------------------------------------------

class TestQueueSections:
    def test_shows_eligible_entry(self, client, yt_source):
        item = _video_item(yt_source)
        _eligible_entry(item)
        resp = client.get("/")
        assert "A Great Video" in resp.text
        assert "Download" in resp.text
        assert "Ready to download" in resp.text

    def test_shows_waiting_entry_with_time(self, client, yt_source):
        item = _video_item(yt_source)
        _waiting_entry(item, hours_from_now=3)
        resp = client.get("/")
        assert "A Great Video" in resp.text
        assert "not yet eligible" in resp.text
        assert "in " in resp.text  # "in Xh" or "in Xm"

    def test_eligible_and_waiting_both_shown(self, client, yt_source):
        item1 = _video_item(yt_source, guid="g1", title="Ready Video")
        item2 = _video_item(yt_source, guid="g2", title="Waiting Video")
        _eligible_entry(item1)
        _waiting_entry(item2)
        resp = client.get("/")
        assert "Ready Video" in resp.text
        assert "Waiting Video" in resp.text

    def test_duration_shown_when_present(self, client, yt_source):
        item = db.Item.create(
            source=yt_source, guid="g1", title="Video With Duration",
            url="https://yt.com/v", media_type="video", status="unread", duration=754,
        )
        _eligible_entry(item)
        resp = client.get("/")
        assert "12:34" in resp.text


# ---------------------------------------------------------------------------
# Landing page — articles section
# ---------------------------------------------------------------------------

class TestArticleSection:
    def test_shows_unread_articles(self, client, blog_source):
        _article_item(blog_source)
        resp = client.get("/")
        assert "An Interesting Article" in resp.text
        assert "Unread articles" in resp.text

    def test_shows_overflow_note_when_more_than_five(self, client, blog_source):
        for i in range(7):
            _article_item(blog_source, guid=f"a{i}", title=f"Article {i}")
        resp = client.get("/")
        assert "more" in resp.text
        assert "foci" in resp.text  # CLI hint

    def test_no_articles_section_when_feed_empty(self, client):
        resp = client.get("/")
        assert "Unread articles" not in resp.text


# ---------------------------------------------------------------------------
# POST /queue/download/{entry_id}
# ---------------------------------------------------------------------------

class TestQueueDownloadPost:
    def test_redirects_to_landing(self, client, yt_source):
        item = _video_item(yt_source)
        entry = _eligible_entry(item)
        with patch("foci.queue.downloader.download_one"):
            resp = client.post(f"/queue/download/{entry.id}", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    def test_nonexistent_entry_redirects_gracefully(self, client):
        resp = client.post("/queue/download/99999", follow_redirects=False)
        assert resp.status_code == 303

    def test_ineligible_entry_not_downloaded(self, client, yt_source):
        item = _video_item(yt_source)
        entry = _waiting_entry(item)
        with patch("foci.queue.downloader.download_one") as mock_dl:
            client.post(f"/queue/download/{entry.id}")
        mock_dl.assert_not_called()
