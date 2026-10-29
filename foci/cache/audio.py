"""Download and locally cache podcast audio."""

from __future__ import annotations

import datetime
import logging
import mimetypes
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

from foci import db
from foci.cache.articles import is_cached
from foci.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30, read=120)
_USER_AGENT = "Mozilla/5.0 (compatible; Foci/0.1; RSS reader)"
_CHUNK_SIZE = 1024 * 256


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_audio(item: db.Item, settings: Settings) -> db.CacheEntry:
    """
    Download an item's audio enclosure to the local cache.
    Returns the CacheEntry. Raises on missing enclosure or download failure.
    """
    if not item.enclosure_url:
        raise ValueError(
            f"Item {item.id} ({item.title!r}) has no audio file URL. "
            "It was probably polled before audio caching existed."
        )

    cache_dir = _audio_cache_dir(settings)
    part = cache_dir / f"{item.id}.part"
    served_type = _download(item.enclosure_url, part)
    dest = cache_dir / f"{item.id}{_extension(item.enclosure_url, served_type)}"
    part.replace(dest)
    content_type = _audio_type(dest, served_type)

    entry, created = db.CacheEntry.get_or_create(
        item=item,
        defaults={"local_path": str(dest), "content_type": content_type},
    )
    if not created:
        entry.local_path = str(dest)
        entry.content_type = content_type
        entry.save()

    return entry


def cached_path(item: db.Item) -> Path:
    """Return the local audio file for an item. Raises if not cached."""
    try:
        entry = db.CacheEntry.get(db.CacheEntry.item == item)
    except db.CacheEntry.DoesNotExist:
        raise FileNotFoundError(f"Item {item.id} ({item.title!r}) is not cached.")

    path = Path(entry.local_path)
    if not path.exists():
        raise FileNotFoundError(f"Cache file missing on disk: {entry.local_path}")
    return path


def fetch_all_unread(
    settings: Settings,
    on_progress: Callable[[str], None] | None = None,
) -> dict[int, str]:
    """
    Cache every unread audio item that isn't already cached.
    Returns {item_id: "ok" | "skip" | "<error message>"}.
    """
    items = (
        db.Item.select()
        .where(db.Item.media_type == "audio", db.Item.status == "unread")
        .order_by(db.Item.published_at.desc())
    )

    results: dict[int, str] = {}
    for item in items:
        if is_cached(item):
            results[item.id] = "skip"
            continue

        _emit(on_progress, f"  Downloading: {item.title[:70]}")
        try:
            fetch_audio(item, settings)
            results[item.id] = "ok"
            _emit(on_progress, "    → ok")
        except Exception as exc:
            logger.warning("Failed to cache audio %d (%s): %s", item.id, item.enclosure_url, exc)
            results[item.id] = str(exc)
            _emit(on_progress, f"    → error: {exc}")

    return results


def expired_audio(settings: Settings) -> list[db.CacheEntry]:
    """Cache entries for audio cached longer ago than cache.audio_max_age_days."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=settings.cache.audio_max_age_days)
    return list(
        db.CacheEntry.select()
        .join(db.Item)
        .where(db.Item.media_type == "audio", db.CacheEntry.cached_at < cutoff)
        .order_by(db.CacheEntry.cached_at)
    )


def clean_expired_audio(settings: Settings) -> int:
    """
    Delete cached audio files past their max age and drop their cache entries.
    The feed items themselves are untouched. Returns the number of entries removed.
    """
    removed = 0
    for entry in expired_audio(settings):
        try:
            Path(entry.local_path).unlink(missing_ok=True)
        except OSError as exc:
            # Keep the entry so the file is retried next run rather than orphaned
            logger.warning("Could not delete %s: %s", entry.local_path, exc)
            continue
        entry.delete_instance()
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _audio_cache_dir(settings: Settings) -> Path:
    path = Path(settings.storage.cache_dir) / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, part_path: Path) -> str:
    """Stream url to part_path. Returns the response content type."""
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        ) as response:
            response.raise_for_status()
            with part_path.open("wb") as f:
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    f.write(chunk)
            return response.headers.get("content-type", "").split(";")[0].strip().lower()
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise


def _extension(url: str, content_type: str) -> str:
    """Prefer the extension in the URL path; fall back to the content type, then .mp3."""
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix and len(suffix) <= 5:
        return suffix
    return mimetypes.guess_extension(content_type) or ".mp3"


def _audio_type(path: Path, served_type: str) -> str:
    """Servers often send application/octet-stream for podcasts; fall back to the extension."""
    if served_type.startswith("audio/"):
        return served_type
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "audio/mpeg"


def _emit(fn: Callable[[str], None] | None, msg: str) -> None:
    if fn:
        fn(msg)
