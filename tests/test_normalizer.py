"""Tests for foci.feeds.normalizer — no network calls."""

import datetime

import feedparser
import pytest

from foci.feeds.normalizer import (
    normalize_entry,
    _detect_media_type,
    _extract_duration,
    _extract_size,
    _parse_duration_str,
)

# ---------------------------------------------------------------------------
# Fixtures: minimal RSS XML strings
# ---------------------------------------------------------------------------

RSS_ARTICLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Blog</title>
    <link>https://example.com</link>
    <item>
      <title>Hello World</title>
      <link>https://example.com/hello</link>
      <guid>https://example.com/hello</guid>
      <description>A test post.</description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

RSS_PODCAST = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Podcast</title>
    <link>https://example.com/podcast</link>
    <item>
      <title>Episode 1</title>
      <link>https://example.com/ep1</link>
      <guid>https://example.com/ep1</guid>
      <enclosure url="https://example.com/ep1.mp3" length="12345678" type="audio/mpeg"/>
      <itunes:duration>1:23:45</itunes:duration>
      <pubDate>Tue, 02 Jan 2024 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

RSS_NO_GUID_NO_LINK = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Bad Feed</title>
    <link>https://example.com</link>
    <item>
      <title>No link or guid here</title>
    </item>
  </channel>
</rss>
"""


def _first_entry(xml: str):
    return feedparser.parse(xml).entries[0]


# ---------------------------------------------------------------------------
# normalize_entry
# ---------------------------------------------------------------------------

class TestNormalizeEntry:
    def test_article_fields(self):
        entry = _first_entry(RSS_ARTICLE)
        result = normalize_entry(entry, "rss")

        assert result is not None
        assert result["title"] == "Hello World"
        assert result["url"] == "https://example.com/hello"
        assert result["guid"] == "https://example.com/hello"
        assert result["media_type"] == "article"
        assert result["description"] == "A test post."
        assert isinstance(result["published_at"], datetime.datetime)
        assert result["published_at"].year == 2024

    def test_podcast_media_type_and_duration(self):
        entry = _first_entry(RSS_PODCAST)
        result = normalize_entry(entry, "rss")

        assert result is not None
        assert result["media_type"] == "audio"
        assert result["duration"] == 5025          # 1h 23m 45s
        assert result["estimated_size"] == 12345678

    def test_youtube_source_type_forces_video(self):
        entry = _first_entry(RSS_ARTICLE)
        result = normalize_entry(entry, "youtube")
        assert result["media_type"] == "video"

    def test_missing_guid_and_link_returns_none(self):
        entry = _first_entry(RSS_NO_GUID_NO_LINK)
        assert normalize_entry(entry, "rss") is None


# ---------------------------------------------------------------------------
# _parse_duration_str
# ---------------------------------------------------------------------------

class TestParseDurationStr:
    @pytest.mark.parametrize("s,expected", [
        ("5025", 5025),
        ("1:23:45", 5025),
        ("83:45", 5025),
        ("0", 0),
        ("garbage", None),
        ("1:2:bad", None),
    ])
    def test_cases(self, s, expected):
        assert _parse_duration_str(s) == expected


# ---------------------------------------------------------------------------
# _extract_size
# ---------------------------------------------------------------------------

class TestExtractSize:
    def test_podcast_has_size(self):
        entry = _first_entry(RSS_PODCAST)
        assert _extract_size(entry) == 12345678

    def test_article_has_no_size(self):
        entry = _first_entry(RSS_ARTICLE)
        assert _extract_size(entry) is None
