"""FastAPI application — local landing page served when a blocked site is visited."""

from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from foci import db
from foci.config import Settings

_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

def _fmt_duration(seconds) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    if seconds is None:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _fmt_humantime(dt: datetime.datetime) -> str:
    """'in 47m', 'in 2h 15m', 'now'"""
    delta = dt - datetime.datetime.utcnow()
    total_secs = int(delta.total_seconds())
    if total_secs <= 0:
        return "now"
    mins = total_secs // 60
    if mins < 60:
        return f"in {mins}m"
    hours, leftover = divmod(mins, 60)
    return f"in {hours}h {leftover}m" if leftover else f"in {hours}h"


def _pluralize(n: int, singular: str = "", plural: str = "s") -> str:
    return singular if n == 1 else plural


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["duration"] = _fmt_duration
    templates.env.filters["humantime"] = _fmt_humantime
    templates.env.filters["pluralize"] = _pluralize

    _own_hosts = {"localhost", "127.0.0.1", settings.server.host}

    # ------------------------------------------------------------------
    # GET / (and any path — catches redirected domains on all their URLs)
    # ------------------------------------------------------------------

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def landing(request: Request, path: str = ""):
        raw_host = request.headers.get("host", "").split(":")[0]
        blocked_host = None if raw_host in _own_hosts else raw_host

        now = datetime.datetime.utcnow()

        eligible_entries = list(
            db.QueueEntry.select()
            .where(db.QueueEntry.status == "pending", db.QueueEntry.eligible_at <= now)
            .order_by(db.QueueEntry.priority.desc(), db.QueueEntry.queued_at)
        )

        waiting_entries = list(
            db.QueueEntry.select()
            .where(db.QueueEntry.status == "pending", db.QueueEntry.eligible_at > now)
            .order_by(db.QueueEntry.eligible_at)
        )

        unread_q = (
            db.Item.select()
            .where(db.Item.media_type == "article", db.Item.status == "unread")
            .order_by(db.Item.published_at.desc())
        )
        total_unread = unread_q.count()
        unread_articles = list(unread_q.limit(5))

        today = now.strftime("%Y-%m-%d")
        try:
            usage = db.DailyUsage.get(db.DailyUsage.date == today)
        except db.DailyUsage.DoesNotExist:
            usage = db.DailyUsage(
                date=today, articles_read=0, videos_watched=0, audio_played=0
            )

        return templates.TemplateResponse(
            request,
            "landing.html",
            {
                "blocked_host": blocked_host,
                "eligible_entries": eligible_entries,
                "waiting_entries": waiting_entries,
                "unread_articles": unread_articles,
                "total_unread": total_unread,
                "usage": usage,
            },
        )

    # ------------------------------------------------------------------
    # POST /queue/download/{entry_id}
    # Starts a background download for an eligible queue entry.
    # ------------------------------------------------------------------

    @app.post("/queue/download/{entry_id}")
    async def start_download(entry_id: int, background_tasks: BackgroundTasks):
        try:
            entry = db.QueueEntry.get_by_id(entry_id)
        except db.QueueEntry.DoesNotExist:
            return RedirectResponse(url="/", status_code=303)

        if entry.status == "pending" and entry.is_eligible():
            from foci.queue.downloader import download_one  # noqa: PLC0415
            background_tasks.add_task(download_one, entry, settings)

        return RedirectResponse(url="/", status_code=303)

    return app
