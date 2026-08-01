"""Execute video downloads for eligible queue entries."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from pathlib import Path

from foci import db
from foci.config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_eligible() -> list[db.QueueEntry]:
    """Return pending entries whose minimum-age window has elapsed, oldest first."""
    now = datetime.datetime.utcnow()
    return list(
        db.QueueEntry.select()
        .where(
            db.QueueEntry.status == "pending",
            db.QueueEntry.eligible_at <= now,
        )
        .order_by(db.QueueEntry.priority.desc(), db.QueueEntry.queued_at)
    )


def download_one(
    entry: db.QueueEntry,
    settings: Settings,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    """
    Download the video for a single queue entry using yt-dlp.
    Updates entry.status throughout: pending → downloading → completed | failed.
    Returns the local file path on success. Raises RuntimeError on failure.
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp is not installed. Run: pip install yt-dlp")

    downloads_dir = Path(settings.storage.downloads_dir)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    entry.status = "downloading"
    entry.started_at = datetime.datetime.utcnow()
    entry.save()

    def _progress_hook(d: dict) -> None:
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            if pct:
                _emit(on_progress, f"      {pct}  {speed}  ETA {eta}")
        elif d["status"] == "error":
            _emit(on_progress, "      yt-dlp reported an error during download.")

    ydl_opts = {
        "outtmpl": str(downloads_dir / "%(uploader)s" / "%(title)s.%(ext)s"),
        "format": settings.queue.video_format,
        "quiet": True,
        "no_warnings": True,
        "noprogress": False,
        "progress_hooks": [_progress_hook],
        # Merge video+audio into a single file
        "merge_output_format": "mp4",
        # Don't re-download if the file already exists
        "nooverwrites": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(entry.item.url, download=True)
            local_path = _resolve_output_path(info, ydl)
    except Exception as exc:
        entry.status = "failed"
        entry.save()
        raise RuntimeError(f"Download failed for '{entry.item.title}': {exc}") from exc

    entry.status = "completed"
    entry.completed_at = datetime.datetime.utcnow()
    entry.local_path = str(local_path)
    entry.save()

    return local_path


def download_all_eligible(
    settings: Settings,
    on_progress: Callable[[str], None] | None = None,
) -> dict[int, str]:
    """
    Download all eligible entries sequentially (respects max_concurrent_downloads=1).
    Returns {entry_id: "ok" | <error message>}.
    """
    eligible = get_eligible()

    if not eligible:
        _emit(on_progress, "No downloads ready yet (all items are still within the minimum age window).")
        return {}

    results: dict[int, str] = {}
    for entry in eligible:
        _emit(on_progress, f"  Downloading: {entry.item.title}")
        try:
            path = download_one(entry, settings, on_progress=on_progress)
            results[entry.id] = "ok"
            _emit(on_progress, f"    → saved: {path}")
        except Exception as exc:
            logger.error("Download failed (entry %d): %s", entry.id, exc)
            results[entry.id] = str(exc)

    return results


def cancel_entry(entry_id: int) -> bool:
    """
    Cancel a pending queue entry.
    Returns True if cancelled, False if not found or not in a cancellable state.
    """
    try:
        entry = db.QueueEntry.get_by_id(entry_id)
    except db.QueueEntry.DoesNotExist:
        return False

    if entry.status != "pending":
        return False

    entry.status = "cancelled"
    entry.save()
    return True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _resolve_output_path(info: dict, ydl) -> Path:
    """
    Get the actual output file path from yt-dlp's info dict.
    requested_downloads[0]['filepath'] is the post-merge path and is more reliable
    than prepare_filename() which reflects the pre-merge template.
    """
    req = info.get("requested_downloads")
    if req:
        fp = req[0].get("filepath") or req[0].get("filename")
        if fp:
            return Path(fp)
    return Path(ydl.prepare_filename(info))


def _emit(fn: Callable[[str], None] | None, msg: str) -> None:
    if fn:
        fn(msg)
