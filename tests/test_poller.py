"""Tests for foci.feeds.poller — no network calls, in-memory DB."""

import datetime
from unittest.mock import patch, MagicMock

import feedparser
import pytest

from foci import db
from foci.config import Settings, Source as ConfigSource
from foci.feeds.poller import sync_sources, poll_source, _parse_yt_date


RSS_TWO_ARTICLES = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Blog</title>
    <link>https://example.com</link>
    <item>
      <title>Post One</title>
      <link>https://example.com/one</link>
      <guid>guid-one</guid>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Post Two</title>
      <link>https://example.com/two</link>
      <guid>guid-two</guid>
      <pubDate>Tue, 02 Jan 2024 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


@pytest.fixture(autouse=True)
def in_memory_db():
    """Use a fresh in-memory SQLite database for each test."""
    db._db.init(":memory:")
    db._db.connect(reuse_if_open=True)
    db._db.create_tables(db.MODELS, safe=True)
    yield
    db._db.drop_tables(db.MODELS, safe=True)
    db._db.close()


@pytest.fixture
def default_settings():
    return Settings()


@pytest.fixture
def rss_source():
    return db.Source.create(
        name="Test Blog",
        url="https://example.com/feed.xml",
        type="rss",
    )


# ---------------------------------------------------------------------------
# sync_sources
# ---------------------------------------------------------------------------

class TestSyncSources:
    def test_inserts_new_sources(self):
        config = [
            ConfigSource(name="Blog A", url="https://a.com/feed", type="rss"),
            ConfigSource(name="Blog B", url="https://b.com/feed", type="rss"),
        ]
        sync_sources(config)
        assert db.Source.select().count() == 2

    def test_does_not_duplicate(self):
        config = [ConfigSource(name="Blog A", url="https://a.com/feed", type="rss")]
        sync_sources(config)
        sync_sources(config)  # second call should be a no-op
        assert db.Source.select().count() == 1


# ---------------------------------------------------------------------------
# poll_source (RSS)
# ---------------------------------------------------------------------------

class TestPollSourceRss:
    def test_inserts_new_items(self, rss_source, default_settings):
        with patch("feedparser.parse", return_value=feedparser.parse(RSS_TWO_ARTICLES)):
            count = poll_source(rss_source, default_settings)

        assert count == 2
        assert db.Item.select().count() == 2

    def test_deduplicates_on_second_poll(self, rss_source, default_settings):
        parsed = feedparser.parse(RSS_TWO_ARTICLES)
        with patch("feedparser.parse", return_value=parsed):
            poll_source(rss_source, default_settings)
            count = poll_source(rss_source, default_settings)

        assert count == 0
        assert db.Item.select().count() == 2  # still just 2

    def test_sets_expiry(self, rss_source, default_settings):
        with patch("feedparser.parse", return_value=feedparser.parse(RSS_TWO_ARTICLES)):
            poll_source(rss_source, default_settings)

        for item in db.Item.select():
            assert item.expires_at is not None
            assert item.expires_at > datetime.datetime.utcnow()

    def test_marks_last_polled(self, rss_source, default_settings):
        with patch("feedparser.parse", return_value=feedparser.parse(RSS_TWO_ARTICLES)):
            poll_source(rss_source, default_settings)

        rss_source = db.Source.get_by_id(rss_source.id)
        assert rss_source.last_polled is not None

    def test_media_type_article(self, rss_source, default_settings):
        with patch("feedparser.parse", return_value=feedparser.parse(RSS_TWO_ARTICLES)):
            poll_source(rss_source, default_settings)

        for item in db.Item.select():
            assert item.media_type == "article"


# ---------------------------------------------------------------------------
# _parse_yt_date
# ---------------------------------------------------------------------------

class TestParseYtDate:
    def test_valid_date(self):
        result = _parse_yt_date("20240115")
        assert result == datetime.datetime(2024, 1, 15)

    def test_none_input(self):
        assert _parse_yt_date(None) is None

    def test_malformed_input(self):
        assert _parse_yt_date("not-a-date") is None


# ---------------------------------------------------------------------------
# poll_source (YouTube) — mocked yt-dlp
# ---------------------------------------------------------------------------

class TestPollSourceYoutube:
    def test_inserts_video_items(self, default_settings):
        yt_source = db.Source.create(
            name="Test Channel",
            url="https://www.youtube.com/@testchannel",
            type="youtube",
        )

        fake_info = {
            "entries": [
                {"id": "abc123", "title": "Video One", "upload_date": "20240101", "duration": 300},
                {"id": "def456", "title": "Video Two", "upload_date": "20240102", "duration": 600},
            ]
        }

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info = MagicMock(return_value=fake_info)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            count = poll_source(yt_source, default_settings)

        assert count == 2
        items = list(db.Item.select())
        assert all(i.media_type == "video" for i in items)
        assert {i.guid for i in items} == {"youtube:abc123", "youtube:def456"}
