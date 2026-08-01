"""Foci terminal UI — built with Textual."""

from __future__ import annotations

import datetime
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    MarkdownViewer,
    Static,
    TabbedContent,
    TabPane,
)

from foci import db
from foci.config import Settings


# ---------------------------------------------------------------------------
# Reader screen (pushed on top of the main app)
# ---------------------------------------------------------------------------

class ReaderScreen(Screen[None]):
    """Full-screen article reader. Press Escape or q to go back."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.pop_screen", "Back"),
    ]

    def __init__(self, item: db.Item, content: str, settings: Settings) -> None:
        super().__init__()
        self._item = item
        self._content = content
        self._settings = settings

    def compose(self) -> ComposeResult:
        yield Header()
        yield MarkdownViewer(self._content, show_table_of_contents=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._item.title
        self._record()

    def _record(self) -> None:
        from foci.feeds.maintenance import record_consumption, budget_status
        usage = record_consumption(self._item)
        _, limit, over = budget_status(self._settings)
        if over:
            self.notify(
                f"Today: {usage.articles_read} articles read — soft limit is {limit}.",
                timeout=5,
            )


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class FociApp(App[None]):
    CSS_PATH = Path(__file__).with_suffix(".tcss")
    TITLE = "Foci"
    SUB_TITLE = "deliberate media consumption"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Refresh"),
        Binding("d", "dismiss_item", "Dismiss", show=True),
        Binding("c", "cancel_entry", "Cancel", show=True),
        Binding("1", "switch_tab('feed')", "Feed", show=False),
        Binding("2", "switch_tab('queue')", "Queue", show=False),
        Binding("3", "switch_tab('stats')", "Stats", show=False),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._feed_row_key: str | None = None
        self._queue_row_key: str | None = None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="feed", id="tabs"):
            with TabPane("Feed  [1]", id="feed"):
                yield DataTable(
                    id="feed-table", cursor_type="row", zebra_stripes=True
                )
            with TabPane("Queue  [2]", id="queue"):
                yield DataTable(
                    id="queue-table", cursor_type="row", zebra_stripes=True
                )
            with TabPane("Stats  [3]", id="stats"):
                yield Static(id="stats-content", expand=True)
        yield Footer()

    def on_mount(self) -> None:
        self._reload_feed()
        self._reload_queue()
        self._reload_stats()

    # ------------------------------------------------------------------
    # Feed tab
    # ------------------------------------------------------------------

    def _reload_feed(self) -> None:
        table = self.query_one("#feed-table", DataTable)
        table.clear(columns=True)
        table.add_column("Date",   key="date",   width=6)
        table.add_column("Type",   key="type",   width=5)
        table.add_column("Source", key="source", width=22)
        table.add_column("Title",  key="title")

        for item in (
            db.Item.select()
            .where(db.Item.status == "unread")
            .order_by(db.Item.published_at.desc())
            .limit(200)
        ):
            pub   = item.published_at.strftime("%m-%d") if item.published_at else "  --"
            mtype = {"article": "art", "video": "vid", "audio": "aud"}.get(
                item.media_type, "---"
            )
            table.add_row(pub, mtype, item.source.name, item.title, key=str(item.id))

    @on(DataTable.RowHighlighted, "#feed-table")
    def _on_feed_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._feed_row_key = str(event.row_key.value) if event.row_key else None

    @on(DataTable.RowSelected, "#feed-table")
    def _on_feed_selected(self, event: DataTable.RowSelected) -> None:
        if not event.row_key:
            return
        try:
            item = db.Item.get_by_id(int(str(event.row_key.value)))
        except db.Item.DoesNotExist:
            return

        if item.media_type == "article":
            self._open_article(item)
        elif item.media_type == "video":
            self._enqueue_video(item)
        else:
            self.notify(
                f"Audio — run: foci cache fetch {item.id} to download the file.",
                timeout=5,
            )

    def _open_article(self, item: db.Item) -> None:
        from foci.cache.articles import fetch_article, is_cached, read_cached

        if not is_cached(item):
            self.notify(f"Fetching '{item.title[:40]}'…", timeout=6)
            try:
                fetch_article(item, self._settings)
            except Exception as exc:
                self.notify(f"Fetch failed: {exc}", severity="error", timeout=8)
                return

        try:
            content = read_cached(item)
        except FileNotFoundError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return

        self.push_screen(ReaderScreen(item, content, self._settings))

    def _enqueue_video(self, item: db.Item) -> None:
        now   = datetime.datetime.utcnow()
        delay = datetime.timedelta(minutes=self._settings.queue.minimum_age_minutes)
        _, created = db.QueueEntry.get_or_create(
            item=item,
            defaults={"queued_at": now, "eligible_at": now + delay},
        )
        if created:
            mins = self._settings.queue.minimum_age_minutes
            self.notify(f"Queued — eligible to download in {mins}m.", timeout=4)
            self._reload_queue()
            self._reload_stats()
        else:
            self.notify("Already in the download queue.", timeout=3)

    def action_dismiss_item(self) -> None:
        """Dismiss (archive) the currently highlighted feed item."""
        if not self._feed_row_key:
            return
        try:
            item = db.Item.get_by_id(int(self._feed_row_key))
        except db.Item.DoesNotExist:
            return
        item.status = "dismissed"
        item.save()
        self._reload_feed()
        self._reload_stats()
        self.notify(f"Dismissed: {item.title[:50]}", timeout=2)

    # ------------------------------------------------------------------
    # Queue tab
    # ------------------------------------------------------------------

    def _reload_queue(self) -> None:
        now   = datetime.datetime.utcnow()
        table = self.query_one("#queue-table", DataTable)
        table.clear(columns=True)
        table.add_column("Status",   key="status",   width=12)
        table.add_column("Eligible", key="eligible", width=10)
        table.add_column("Source",   key="source",   width=22)
        table.add_column("Title",    key="title")

        for entry in (
            db.QueueEntry.select()
            .where(db.QueueEntry.status.in_(["pending", "downloading", "failed"]))
            .order_by(db.QueueEntry.eligible_at)
        ):
            if entry.status == "downloading":
                state, when = "downloading…", "—"
            elif entry.status == "failed":
                state, when = "failed", "—"
            elif entry.eligible_at <= now:
                state, when = "ready  ↵", "now"
            else:
                delta = int((entry.eligible_at - now).total_seconds() / 60)
                state = "waiting"
                when  = f"in {delta}m" if delta < 60 else f"in {delta // 60}h"

            table.add_row(
                state, when,
                entry.item.source.name,
                entry.item.title,
                key=str(entry.id),
            )

    @on(DataTable.RowHighlighted, "#queue-table")
    def _on_queue_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._queue_row_key = str(event.row_key.value) if event.row_key else None

    @on(DataTable.RowSelected, "#queue-table")
    def _on_queue_selected(self, event: DataTable.RowSelected) -> None:
        if not event.row_key:
            return
        try:
            entry = db.QueueEntry.get_by_id(int(str(event.row_key.value)))
        except db.QueueEntry.DoesNotExist:
            return

        if not entry.is_eligible():
            self.notify("Not yet eligible — minimum age window hasn't elapsed.", timeout=4)
            return
        if entry.status != "pending":
            self.notify(f"Entry is {entry.status}, not pending.", timeout=3)
            return

        self._run_download(entry)

    @work(thread=True)
    def _run_download(self, entry: db.QueueEntry) -> None:
        from foci.queue.downloader import download_one
        self.call_from_thread(
            self.notify, f"Downloading '{entry.item.title[:40]}'…", timeout=8
        )
        try:
            path = download_one(entry, self._settings)
            self.call_from_thread(
                self.notify, f"Saved: {path.name}", timeout=6
            )
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Download failed: {exc}", severity="error", timeout=10
            )
        self.call_from_thread(self._reload_queue)

    def action_cancel_entry(self) -> None:
        """Cancel the currently highlighted queue entry."""
        if not self._queue_row_key:
            return
        from foci.queue.downloader import cancel_entry
        cancelled = cancel_entry(int(self._queue_row_key))
        if cancelled:
            self._reload_queue()
            self.notify("Queue entry cancelled.", timeout=2)
        else:
            self.notify("Cannot cancel — entry is not in a pending state.", timeout=3)

    # ------------------------------------------------------------------
    # Stats tab
    # ------------------------------------------------------------------

    def _reload_stats(self) -> None:
        from foci.feeds.maintenance import budget_status, get_daily_usage

        usage                    = get_daily_usage()
        articles, limit, over    = budget_status(self._settings)
        budget_line              = f"{articles} / {limit}" + ("  ← soft limit reached" if over else "")

        source_rows = []
        for src in db.Source.select().order_by(db.Source.name):
            total  = db.Item.select().where(db.Item.source == src).count()
            unread = db.Item.select().where(
                db.Item.source == src, db.Item.status == "unread"
            ).count()
            muted  = f"  (muted until {src.muted_until.strftime('%Y-%m-%d')})" if src.is_muted() else ""
            source_rows.append(f"  {src.name:<28}  {unread:>4} unread / {total:>5} total{muted}")

        sources_block = "\n".join(source_rows) if source_rows else "  (no sources synced yet — run: foci feed poll)"

        content = (
            f"Today's usage\n"
            f"{'─' * 48}\n"
            f"  Articles read:   {budget_line}\n"
            f"  Videos watched:  {usage.videos_watched}\n"
            f"  Audio played:    {usage.audio_played}\n"
            f"\n"
            f"Sources\n"
            f"{'─' * 48}\n"
            f"{sources_block}\n"
        )
        self.query_one("#stats-content", Static).update(content)

    # ------------------------------------------------------------------
    # Global actions
    # ------------------------------------------------------------------

    def action_reload(self) -> None:
        self._reload_feed()
        self._reload_queue()
        self._reload_stats()
        self.notify("Refreshed.", timeout=1)

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id
