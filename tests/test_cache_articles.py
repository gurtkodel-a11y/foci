"""Tests for foci.cache.articles — no network calls, temp filesystem."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from foci import db
from foci.config import Settings, StorageConfig
from foci.cache.articles import (
    fetch_article,
    fetch_all_unread,
    is_cached,
    read_cached,
)

FAKE_HTML = "<html><body><article><p>Hello, world.</p></article></body></html>"
FAKE_MARKDOWN = "Hello, world."


@pytest.fixture(autouse=True)
def in_memory_db():
    db._db.init(":memory:")
    db._db.connect(reuse_if_open=True)
    db._db.create_tables(db.MODELS, safe=True)
    yield
    db._db.drop_tables(db.MODELS, safe=True)
    db._db.close()


@pytest.fixture
def tmp_settings(tmp_path):
    return Settings(
        storage=StorageConfig(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "data" / "cache",
            downloads_dir=tmp_path / "data" / "downloads",
        )
    )


@pytest.fixture
def article_item():
    source = db.Source.create(name="Test Blog", url="https://example.com/feed", type="rss")
    return db.Item.create(
        source=source,
        guid="guid-article-1",
        title="Hello World",
        url="https://example.com/hello",
        media_type="article",
        status="unread",
    )


@pytest.fixture
def video_item():
    source = db.Source.create(name="Test Channel", url="https://yt.com/@test", type="youtube")
    return db.Item.create(
        source=source,
        guid="youtube:abc123",
        title="My Video",
        url="https://youtube.com/watch?v=abc123",
        media_type="video",
        status="unread",
    )


# ---------------------------------------------------------------------------
# is_cached
# ---------------------------------------------------------------------------

class TestIsCached:
    def test_not_cached_initially(self, article_item):
        assert not is_cached(article_item)

    def test_cached_after_fetch(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            fetch_article(article_item, tmp_settings)

        assert is_cached(article_item)

    def test_false_when_db_entry_exists_but_file_missing(self, article_item, tmp_settings):
        db.CacheEntry.create(
            item=article_item,
            local_path="/nonexistent/path/to/file.md",
            content_type="text/markdown",
        )
        assert not is_cached(article_item)


# ---------------------------------------------------------------------------
# fetch_article
# ---------------------------------------------------------------------------

class TestFetchArticle:
    def test_creates_cache_entry(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            entry = fetch_article(article_item, tmp_settings)

        assert entry is not None
        assert db.CacheEntry.select().count() == 1

    def test_file_written_to_disk(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            entry = fetch_article(article_item, tmp_settings)

        path = Path(entry.local_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Hello World" in content      # title
        assert FAKE_MARKDOWN in content

    def test_file_is_markdown(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            entry = fetch_article(article_item, tmp_settings)

        assert entry.local_path.endswith(".md")
        assert entry.content_type == "text/markdown"

    def test_raises_when_extraction_fails(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=None):
            with pytest.raises(ValueError, match="No readable content"):
                fetch_article(article_item, tmp_settings)

    def test_idempotent_on_second_call(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            fetch_article(article_item, tmp_settings)
            fetch_article(article_item, tmp_settings)  # should not create a duplicate

        assert db.CacheEntry.select().count() == 1


# ---------------------------------------------------------------------------
# read_cached
# ---------------------------------------------------------------------------

class TestReadCached:
    def test_returns_content(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            fetch_article(article_item, tmp_settings)

        text = read_cached(article_item)
        assert FAKE_MARKDOWN in text

    def test_raises_if_not_cached(self, article_item):
        with pytest.raises(FileNotFoundError):
            read_cached(article_item)

    def test_raises_if_file_deleted(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            entry = fetch_article(article_item, tmp_settings)

        Path(entry.local_path).unlink()
        with pytest.raises(FileNotFoundError):
            read_cached(article_item)


# ---------------------------------------------------------------------------
# fetch_all_unread
# ---------------------------------------------------------------------------

class TestFetchAllUnread:
    def test_caches_unread_articles(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            results = fetch_all_unread(tmp_settings)

        assert results[article_item.id] == "ok"

    def test_skips_already_cached(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            fetch_all_unread(tmp_settings)
            results = fetch_all_unread(tmp_settings)

        assert results[article_item.id] == "skip"

    def test_skips_video_items(self, video_item, tmp_settings):
        with patch("foci.cache.articles._download", return_value=FAKE_HTML), \
             patch("trafilatura.extract", return_value=FAKE_MARKDOWN):
            results = fetch_all_unread(tmp_settings)

        assert video_item.id not in results

    def test_records_error_on_failure(self, article_item, tmp_settings):
        with patch("foci.cache.articles._download", side_effect=Exception("connection refused")):
            results = fetch_all_unread(tmp_settings)

        assert "connection refused" in results[article_item.id]
