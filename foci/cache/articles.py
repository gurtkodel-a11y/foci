"""Fetch, clean, and locally cache article content."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import trafilatura

from foci import db
from foci.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_USER_AGENT = "Mozilla/5.0 (compatible; Foci/0.1; RSS reader)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_cached(item: db.Item) -> bool:
    """True if the item has a cache entry pointing to an existing file."""
    try:
        entry = db.CacheEntry.get(db.CacheEntry.item == item)
        return Path(entry.local_path).exists()
    except db.CacheEntry.DoesNotExist:
        return False


def fetch_article(item: db.Item, settings: Settings) -> db.CacheEntry:
    """
    Download an article, extract clean content, store as Markdown.
    Returns the CacheEntry. Raises on download or extraction failure.
    """
    dest = _article_cache_dir(settings) / f"{item.id}.md"

    html = _download(item.url)
    content = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        no_fallback=False,
        favor_recall=True,   # extract more rather than less for personal reading
    )

    if not content:
        raise ValueError(f"No readable content found at {item.url}")

    dest.write_text(
        f"# {item.title}\n\n*Source: {item.url}*\n\n---\n\n{content}",
        encoding="utf-8",
    )

    entry, created = db.CacheEntry.get_or_create(
        item=item,
        defaults={"local_path": str(dest), "content_type": "text/markdown"},
    )
    if not created and entry.local_path != str(dest):
        entry.local_path = str(dest)
        entry.save()

    return entry


def read_cached(item: db.Item) -> str:
    """Return the cached Markdown text for an item. Raises if not cached."""
    try:
        entry = db.CacheEntry.get(db.CacheEntry.item == item)
    except db.CacheEntry.DoesNotExist:
        raise FileNotFoundError(f"Item {item.id} ({item.title!r}) is not cached.")

    path = Path(entry.local_path)
    if not path.exists():
        raise FileNotFoundError(f"Cache file missing on disk: {entry.local_path}")

    return path.read_text(encoding="utf-8")


def fetch_all_unread(
    settings: Settings,
    on_progress: Callable[[str], None] | None = None,
) -> dict[int, str]:
    """
    Cache every unread article item that isn't already cached.
    Returns {item_id: "ok" | "skip" | "<error message>"}.
    """
    items = (
        db.Item.select()
        .where(db.Item.media_type == "article", db.Item.status == "unread")
        .order_by(db.Item.published_at.desc())
    )

    results: dict[int, str] = {}
    for item in items:
        if is_cached(item):
            results[item.id] = "skip"
            continue

        _emit(on_progress, f"  Caching: {item.title[:70]}")
        try:
            fetch_article(item, settings)
            results[item.id] = "ok"
            _emit(on_progress, "    → ok")
        except Exception as exc:
            logger.warning("Failed to cache item %d (%s): %s", item.id, item.url, exc)
            results[item.id] = str(exc)
            _emit(on_progress, f"    → error: {exc}")

    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _article_cache_dir(settings: Settings) -> Path:
    path = Path(settings.storage.cache_dir) / "articles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str) -> str:
    response = httpx.get(
        url,
        follow_redirects=True,
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def _emit(fn: Callable[[str], None] | None, msg: str) -> None:
    if fn:
        fn(msg)
