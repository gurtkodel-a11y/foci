"""Convert raw feedparser entries into normalized Item dicts."""

from __future__ import annotations

import datetime


def normalize_entry(entry, source_type: str) -> dict | None:
    """
    Convert a feedparser entry to a dict suitable for db.Item.
    Returns None if the entry lacks a usable guid or URL.
    """
    guid = _extract_guid(entry)
    url = getattr(entry, "link", "").strip()

    if not guid or not url:
        return None

    return {
        "guid": guid,
        "title": (getattr(entry, "title", "") or url).strip(),
        "url": url,
        "description": _extract_description(entry),
        "media_type": _detect_media_type(entry, source_type),
        "enclosure_url": _extract_enclosure_url(entry),
        "estimated_size": _extract_size(entry),
        "duration": _extract_duration(entry),
        "published_at": _extract_published(entry),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_guid(entry) -> str:
    return (getattr(entry, "id", None) or getattr(entry, "link", None) or "").strip()


def _extract_description(entry) -> str:
    # feedparser exposes content as a list of dicts for Atom; summary for RSS
    content = getattr(entry, "content", None)
    if isinstance(content, list) and content:
        return content[0].get("value", "")
    for attr in ("summary", "description"):
        val = getattr(entry, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


def _detect_media_type(entry, source_type: str) -> str:
    if source_type == "youtube":
        return "video"

    for enc in getattr(entry, "enclosures", []):
        mime = (getattr(enc, "type", "") or "").lower()
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"

    for media in getattr(entry, "media_content", []):
        mime = (media.get("type") or "").lower()
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"

    return "article"


def _extract_enclosure_url(entry) -> str | None:
    """URL of the first audio enclosure, if any."""
    for enc in getattr(entry, "enclosures", []):
        mime = (getattr(enc, "type", "") or "").lower()
        href = (getattr(enc, "href", "") or "").strip()
        if mime.startswith("audio/") and href:
            return href

    for media in getattr(entry, "media_content", []):
        mime = (media.get("type") or "").lower()
        href = (media.get("url") or "").strip()
        if mime.startswith("audio/") and href:
            return href

    return None


def _extract_size(entry) -> int | None:
    for enc in getattr(entry, "enclosures", []):
        length = getattr(enc, "length", None)
        if length:
            try:
                return int(length)
            except (ValueError, TypeError):
                pass
    return None


def _extract_duration(entry) -> int | None:
    # itunes:duration (podcasts)
    raw = getattr(entry, "itunes_duration", None)
    if raw:
        return _parse_duration_str(str(raw))

    # media:content duration attribute
    for media in getattr(entry, "media_content", []):
        dur = media.get("duration")
        if dur is not None:
            return _parse_duration_str(str(dur))

    return None


def _parse_duration_str(s: str) -> int | None:
    """Parse HH:MM:SS, MM:SS, or plain integer seconds."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        pass
    return None


def _extract_published(entry) -> datetime.datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.datetime(*t[:6])
            except (TypeError, ValueError):
                pass
    return None
