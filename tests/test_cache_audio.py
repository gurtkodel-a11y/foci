"""Tests for foci.cache.audio — network access is mocked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import datetime

import pytest

from foci import db
from foci.cache.audio import (
    _extension,
    cached_path,
    clean_expired_audio,
    expired_audio,
    fetch_all_unread,
    fetch_audio,
)
from foci.config import Settings, StorageConfig


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
def source():
    return db.Source.create(name="Pod", url="https://example.com/podcast.xml", type="rss")


def _episode(source, n=1, enclosure="https://cdn.example.com/ep1.mp3"):
    return db.Item.create(
        source=source, guid=f"ep-{n}", title=f"Episode {n}",
        url=f"https://example.com/ep{n}", media_type="audio",
        status="unread", enclosure_url=enclosure,
    )


def _fake_download(content_type="audio/mpeg", data=b"ID3fake"):
    def _dl(url, part_path):
        part_path.write_bytes(data)
        return content_type
    return _dl


class TestFetchAudio:
    def test_writes_file_and_cache_entry(self, source, tmp_settings):
        item = _episode(source)
        with patch("foci.cache.audio._download", side_effect=_fake_download()):
            entry = fetch_audio(item, tmp_settings)

        path = Path(entry.local_path)
        assert path.read_bytes() == b"ID3fake"
        assert path.name == f"{item.id}.mp3"
        assert entry.content_type == "audio/mpeg"
        assert not list(path.parent.glob("*.part"))

    def test_octet_stream_falls_back_to_extension_type(self, source, tmp_settings):
        item = _episode(source, enclosure="https://cdn.example.com/ep1.m4a")
        with patch("foci.cache.audio._download",
                   side_effect=_fake_download("application/octet-stream")):
            entry = fetch_audio(item, tmp_settings)
        assert entry.content_type.startswith("audio/")
        assert entry.local_path.endswith(".m4a")

    def test_raises_without_enclosure(self, source, tmp_settings):
        item = _episode(source, enclosure=None)
        with pytest.raises(ValueError, match="no audio file URL"):
            fetch_audio(item, tmp_settings)

    def test_idempotent(self, source, tmp_settings):
        item = _episode(source)
        with patch("foci.cache.audio._download", side_effect=_fake_download()):
            fetch_audio(item, tmp_settings)
            fetch_audio(item, tmp_settings)
        assert db.CacheEntry.select().count() == 1

    def test_download_failure_leaves_no_entry(self, source, tmp_settings):
        item = _episode(source)
        with patch("foci.cache.audio._download", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                fetch_audio(item, tmp_settings)
        assert db.CacheEntry.select().count() == 0


class TestCachedPath:
    def test_returns_path(self, source, tmp_settings):
        item = _episode(source)
        with patch("foci.cache.audio._download", side_effect=_fake_download()):
            entry = fetch_audio(item, tmp_settings)
        assert cached_path(item) == Path(entry.local_path)

    def test_raises_when_not_cached(self, source):
        with pytest.raises(FileNotFoundError):
            cached_path(_episode(source))


class TestFetchAllUnread:
    def test_downloads_skips_and_reports_errors(self, source, tmp_settings):
        new = _episode(source, 1)
        done = _episode(source, 2)
        bad = _episode(source, 3, enclosure=None)
        read = _episode(source, 4)
        read.status = "read"
        read.save()

        with patch("foci.cache.audio._download", side_effect=_fake_download()):
            fetch_audio(done, tmp_settings)
            results = fetch_all_unread(tmp_settings)

        assert results[new.id] == "ok"
        assert results[done.id] == "skip"
        assert "no audio file URL" in results[bad.id]
        assert read.id not in results

    def test_ignores_articles(self, source, tmp_settings):
        db.Item.create(source=source, guid="a", title="A", url="https://x/a",
                       media_type="article", status="unread")
        assert fetch_all_unread(tmp_settings) == {}


class TestExtension:
    def test_from_url(self):
        assert _extension("https://x.com/a/ep.OGG?token=1", "audio/ogg") == ".ogg"

    def test_from_content_type(self):
        assert _extension("https://x.com/download/123", "audio/mpeg") in (".mp3", ".mpga", ".mp2")

    def test_default(self):
        assert _extension("https://x.com/download/123", "") == ".mp3"


class TestMigration:
    def test_adds_enclosure_url_to_old_database(self, tmp_path):
        import sqlite3

        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title VARCHAR(255))")
        con.commit()
        con.close()

        db._db.close()
        db.init(path)
        assert "enclosure_url" in {c.name for c in db._db.get_columns("items")}
        db.init(path)  # second run is a no-op


class TestCleanExpiredAudio:
    def _cache(self, source, tmp_settings, n, age_days):
        item = _episode(source, n)
        with patch("foci.cache.audio._download", side_effect=_fake_download()):
            entry = fetch_audio(item, tmp_settings)
        entry.cached_at = datetime.datetime.utcnow() - datetime.timedelta(days=age_days)
        entry.save()
        return item, entry

    def test_removes_only_old_audio(self, source, tmp_settings):
        _, old = self._cache(source, tmp_settings, 1, tmp_settings.cache.audio_max_age_days + 1)
        new_item, new = self._cache(source, tmp_settings, 2, 1)

        assert clean_expired_audio(tmp_settings) == 1

        assert not Path(old.local_path).exists()
        assert Path(new.local_path).exists()
        assert db.CacheEntry.select().count() == 1
        assert cached_path(new_item) == Path(new.local_path)

    def test_keeps_feed_item(self, source, tmp_settings):
        item, _ = self._cache(source, tmp_settings, 1, 1000)
        clean_expired_audio(tmp_settings)
        assert db.Item.get_by_id(item.id).status == "unread"

    def test_missing_file_still_removes_entry(self, source, tmp_settings):
        _, entry = self._cache(source, tmp_settings, 1, 1000)
        Path(entry.local_path).unlink()
        assert clean_expired_audio(tmp_settings) == 1
        assert db.CacheEntry.select().count() == 0

    def test_ignores_articles(self, source, tmp_settings):
        article = db.Item.create(source=source, guid="a", title="A", url="https://x/a",
                                 media_type="article", status="unread")
        db.CacheEntry.create(
            item=article, local_path="x.md", content_type="text/markdown",
            cached_at=datetime.datetime.utcnow() - datetime.timedelta(days=1000),
        )
        assert expired_audio(tmp_settings) == []
        assert clean_expired_audio(tmp_settings) == 0
        assert db.CacheEntry.select().count() == 1
