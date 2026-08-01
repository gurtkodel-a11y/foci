"""Tests for foci.queue.downloader — no network calls, temp filesystem."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from foci import db
from foci.config import Settings, StorageConfig, QueueConfig
from foci.queue.downloader import (
    cancel_entry,
    download_all_eligible,
    download_one,
    get_eligible,
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
def tmp_settings(tmp_path):
    return Settings(
        storage=StorageConfig(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "data" / "cache",
            downloads_dir=tmp_path / "data" / "downloads",
        ),
        queue=QueueConfig(minimum_age_minutes=120),
    )


@pytest.fixture
def video_item():
    source = db.Source.create(
        name="Test Channel", url="https://www.youtube.com/@test", type="youtube"
    )
    return db.Item.create(
        source=source,
        guid="youtube:abc123",
        title="A Great Video",
        url="https://www.youtube.com/watch?v=abc123",
        media_type="video",
        status="unread",
    )


def _make_entry(item, *, minutes_ago: int = 0, status: str = "pending") -> db.QueueEntry:
    """Create a queue entry that is already past its minimum age."""
    now = datetime.datetime.utcnow()
    eligible_at = now - datetime.timedelta(minutes=minutes_ago)
    return db.QueueEntry.create(
        item=item,
        queued_at=now - datetime.timedelta(minutes=minutes_ago + 5),
        eligible_at=eligible_at,
        status=status,
    )


def _make_pending_entry(item, *, future_minutes: int = 60) -> db.QueueEntry:
    """Create a queue entry that is NOT yet eligible."""
    now = datetime.datetime.utcnow()
    return db.QueueEntry.create(
        item=item,
        queued_at=now,
        eligible_at=now + datetime.timedelta(minutes=future_minutes),
        status="pending",
    )


# ---------------------------------------------------------------------------
# get_eligible
# ---------------------------------------------------------------------------

class TestGetEligible:
    def test_returns_eligible_entries(self, video_item):
        entry = _make_entry(video_item, minutes_ago=10)
        assert entry in get_eligible()

    def test_excludes_not_yet_eligible(self, video_item):
        _make_pending_entry(video_item, future_minutes=60)
        assert get_eligible() == []

    def test_excludes_non_pending_statuses(self, video_item):
        for status in ("downloading", "completed", "cancelled", "failed"):
            _make_entry(video_item, minutes_ago=10, status=status)
        assert get_eligible() == []


# ---------------------------------------------------------------------------
# cancel_entry
# ---------------------------------------------------------------------------

class TestCancelEntry:
    def test_cancels_pending_entry(self, video_item):
        entry = _make_pending_entry(video_item)
        assert cancel_entry(entry.id) is True
        assert db.QueueEntry.get_by_id(entry.id).status == "cancelled"

    def test_returns_false_for_nonexistent_id(self):
        assert cancel_entry(99999) is False

    @pytest.mark.parametrize("status", ["downloading", "completed", "cancelled", "failed"])
    def test_cannot_cancel_non_pending(self, video_item, status):
        entry = _make_entry(video_item, minutes_ago=10, status=status)
        assert cancel_entry(entry.id) is False
        assert db.QueueEntry.get_by_id(entry.id).status == status  # unchanged


# ---------------------------------------------------------------------------
# download_one
# ---------------------------------------------------------------------------

def _mock_ydl_context(tmp_path: Path):
    """Return a MagicMock that looks like a yt_dlp.YoutubeDL context manager."""
    fake_path = tmp_path / "downloads" / "Uploader" / "A Great Video.mp4"
    fake_path.parent.mkdir(parents=True, exist_ok=True)
    fake_path.touch()

    fake_info = {
        "id": "abc123",
        "title": "A Great Video",
        "ext": "mp4",
        "requested_downloads": [{"filepath": str(fake_path)}],
    }

    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info = MagicMock(return_value=fake_info)

    return mock_ydl, fake_path


class TestDownloadOne:
    def test_status_becomes_completed(self, video_item, tmp_settings, tmp_path):
        entry = _make_entry(video_item, minutes_ago=10)
        mock_ydl, _ = _mock_ydl_context(tmp_path)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            download_one(entry, tmp_settings)

        assert db.QueueEntry.get_by_id(entry.id).status == "completed"

    def test_local_path_is_stored(self, video_item, tmp_settings, tmp_path):
        entry = _make_entry(video_item, minutes_ago=10)
        mock_ydl, fake_path = _mock_ydl_context(tmp_path)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = download_one(entry, tmp_settings)

        assert result == fake_path
        assert db.QueueEntry.get_by_id(entry.id).local_path == str(fake_path)

    def test_completed_at_is_set(self, video_item, tmp_settings, tmp_path):
        entry = _make_entry(video_item, minutes_ago=10)
        mock_ydl, _ = _mock_ydl_context(tmp_path)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            download_one(entry, tmp_settings)

        assert db.QueueEntry.get_by_id(entry.id).completed_at is not None

    def test_status_becomes_failed_on_error(self, video_item, tmp_settings, tmp_path):
        entry = _make_entry(video_item, minutes_ago=10)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info = MagicMock(side_effect=Exception("network error"))

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            with pytest.raises(RuntimeError, match="Download failed"):
                download_one(entry, tmp_settings)

        assert db.QueueEntry.get_by_id(entry.id).status == "failed"


# ---------------------------------------------------------------------------
# download_all_eligible
# ---------------------------------------------------------------------------

class TestDownloadAllEligible:
    def test_downloads_eligible_entries(self, video_item, tmp_settings, tmp_path):
        entry = _make_entry(video_item, minutes_ago=10)
        mock_ydl, _ = _mock_ydl_context(tmp_path)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            results = download_all_eligible(tmp_settings)

        assert results[entry.id] == "ok"

    def test_skips_ineligible_entries(self, video_item, tmp_settings):
        _make_pending_entry(video_item, future_minutes=60)
        results = download_all_eligible(tmp_settings)
        assert results == {}

    def test_records_error_without_raising(self, video_item, tmp_settings):
        entry = _make_entry(video_item, minutes_ago=10)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info = MagicMock(side_effect=Exception("timeout"))

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            results = download_all_eligible(tmp_settings)

        assert "timeout" in results[entry.id]
