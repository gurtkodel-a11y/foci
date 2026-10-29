"""Foci CLI entry point."""

from __future__ import annotations

from pathlib import Path

import click

from foci import __version__
from foci.config import load_settings, load_sources, load_blocklist
from foci import db as database


def _init_db(settings=None):
    if settings is None:
        settings = load_settings()
    data_dir = Path(settings.storage.data_dir)
    database.init(data_dir / "foci.db")
    return settings


@click.group()
@click.version_option(__version__, prog_name="foci")
def cli():
    """Foci — deliberate media consumption toolkit."""


# ---------------------------------------------------------------------------
# foci sources
# ---------------------------------------------------------------------------

@cli.group()
def sources():
    """Manage feed subscriptions."""


@sources.command("list")
def sources_list():
    """List all sources, including mute status."""
    import datetime
    _init_db()
    srcs = load_sources()
    if not srcs:
        click.echo("No sources configured. Edit config/subscriptions.toml to add some.")
        return
    for s in srcs:
        # Check mute status from DB
        try:
            db_source = database.Source.get(database.Source.url == s.url)
            if db_source.is_muted():
                mute_note = f"  (muted until {db_source.muted_until.strftime('%Y-%m-%d')})"
            else:
                mute_note = ""
        except database.Source.DoesNotExist:
            mute_note = "  (not yet synced — run: foci feed poll)"

        tags = f"  [{', '.join(s.tags)}]" if s.tags else ""
        paused = "" if s.active else "  (paused)"
        click.echo(f"  {s.name}  ({s.type}){tags}{paused}{mute_note}")
        click.echo(f"    {s.url}")


def _find_db_source(name_or_id: str) -> database.Source:
    """Find a DB source by integer ID or by name substring. Raises ClickException if ambiguous."""
    if name_or_id.isdigit():
        try:
            return database.Source.get_by_id(int(name_or_id))
        except database.Source.DoesNotExist:
            raise click.ClickException(f"No source with ID {name_or_id}.")

    matches = list(database.Source.select().where(database.Source.name ** f"%{name_or_id}%"))
    if not matches:
        raise click.ClickException(f"No source matching '{name_or_id}'. Run: foci sources list")
    if len(matches) > 1:
        names = ", ".join(f"'{s.name}'" for s in matches)
        raise click.ClickException(f"Multiple sources match '{name_or_id}': {names}. Be more specific.")
    return matches[0]


@sources.command("mute")
@click.argument("name_or_id")
@click.option("--days", default=7, show_default=True, help="Number of days to mute.")
def sources_mute(name_or_id, days):
    """Pause a source for a set number of days without unsubscribing."""
    from foci.feeds.maintenance import mute_source

    _init_db()
    source = _find_db_source(name_or_id)
    until = mute_source(source, days)
    click.echo(f"Muted '{source.name}' until {until.strftime('%Y-%m-%d')}.")
    click.echo("Run 'foci sources unmute' to lift it early.")


@sources.command("unmute")
@click.argument("name_or_id")
def sources_unmute(name_or_id):
    """Lift the mute on a source immediately."""
    from foci.feeds.maintenance import unmute_source

    _init_db()
    source = _find_db_source(name_or_id)
    if not source.is_muted():
        click.echo(f"'{source.name}' is not muted.")
        return
    unmute_source(source)
    click.echo(f"'{source.name}' is now active again.")


# ---------------------------------------------------------------------------
# foci feed
# ---------------------------------------------------------------------------

@cli.group()
def feed():
    """Fetch and browse your feed."""


@feed.command("poll")
def feed_poll():
    """Poll all active sources for new items."""
    from foci.feeds.poller import sync_sources, poll_all

    settings = _init_db()
    sources_cfg = load_sources()

    if not sources_cfg:
        click.echo("No sources configured. Edit config/subscriptions.toml to add some.")
        return

    sync_sources(sources_cfg)

    active = [s for s in sources_cfg if s.active]
    click.echo(f"Polling {len(active)} active source(s)...")
    results = poll_all(settings, on_progress=click.echo)

    total_new = sum(c for c in results.values() if c >= 0)
    errors = sum(1 for c in results.values() if c < 0)
    click.echo(f"\nDone. {total_new} new item(s) added.", nl=False)
    if errors:
        click.echo(f"  {errors} source(s) had errors.")
    else:
        click.echo("")

    # Auto-expire stale items after every poll
    from foci.feeds.maintenance import expire_items
    expired = expire_items(settings)
    if expired:
        click.echo(f"Archived {expired} item(s) past their expiry date.")

    # Delete cached audio past its max age
    from foci.cache.audio import clean_expired_audio
    removed = clean_expired_audio(settings)
    if removed:
        click.echo(f"Removed {removed} cached audio file(s) past their max age.")


@feed.command("expire")
def feed_expire():
    """Archive unread items that have passed their expiry date."""
    from foci.feeds.maintenance import expire_items

    settings = _init_db()
    count = expire_items(settings)
    if count:
        click.echo(f"Archived {count} expired item(s).")
    else:
        click.echo("No expired items found.")


@feed.command("read")
@click.argument("item_id", type=int)
def feed_read(item_id):
    """Mark an item as read and record it in today's usage."""
    from foci.feeds.maintenance import record_consumption, budget_status

    settings = _init_db()
    try:
        item = database.Item.get_by_id(item_id)
    except database.Item.DoesNotExist:
        click.echo(f"No item with ID {item_id}.", err=True)
        return

    if item.status == "read":
        click.echo(f"Already marked as read: {item.title}")
        return

    usage = record_consumption(item)
    click.echo(f"Marked as read: {item.title}")

    # Soft budget notice
    _, limit, over = budget_status(settings)
    if over:
        click.echo(f"(Today: {usage.articles_read} articles read — soft limit is {limit}.)")


@feed.command("budget")
def feed_budget():
    """Show today's consumption count against the soft limit."""
    from foci.feeds.maintenance import budget_status

    settings = _init_db()
    articles, limit, over = budget_status(settings)
    usage = database.DailyUsage.get(
        database.DailyUsage.date == __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    ) if articles > 0 else None

    click.echo(f"Today's consumption:")
    click.echo(f"  Articles read:   {articles}  (soft limit: {limit}{'  ← reached' if over else ''})")
    if usage:
        click.echo(f"  Videos watched:  {usage.videos_watched}")
        click.echo(f"  Audio played:    {usage.audio_played}")


@feed.command("list")
@click.option("--status", default="unread", help="Filter by status (unread/read/archived)")
@click.option("--limit", default=20, help="Max items to show")
def feed_list(status, limit):
    """List items in your feed."""
    _init_db()
    items = (
        database.Item.select()
        .where(database.Item.status == status)
        .order_by(database.Item.published_at.desc())
        .limit(limit)
    )
    count = 0
    for item in items:
        count += 1
        pub = item.published_at.strftime("%Y-%m-%d") if item.published_at else "unknown"
        click.echo(f"  [{item.id}] {pub}  {item.title}")
        click.echo(f"         {item.source.name}  —  {item.url}")
    if count == 0:
        click.echo(f"No {status} items.")


# ---------------------------------------------------------------------------
# foci cache
# ---------------------------------------------------------------------------

@cli.group()
def cache():
    """Cache articles and podcast audio locally."""


@cache.command("fetch")
@click.argument("item_id", type=int)
def cache_fetch(item_id):
    """Download and cache a single article by item ID."""
    from foci.cache.articles import fetch_article, is_cached
    from foci.cache.audio import fetch_audio

    settings = _init_db()
    try:
        item = database.Item.get_by_id(item_id)
    except database.Item.DoesNotExist:
        click.echo(f"No item with ID {item_id}.", err=True)
        return

    if item.media_type not in ("article", "audio"):
        click.echo(f"Item {item_id} is a {item.media_type}, not an article or audio.", err=True)
        return

    if is_cached(item):
        click.echo(f"Already cached: {item.title}")
        return

    click.echo(f"Fetching: {item.title}")
    try:
        fetch = fetch_audio if item.media_type == "audio" else fetch_article
        entry = fetch(item, settings)
        click.echo(f"Cached to: {entry.local_path}")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)


@cache.command("fetch-all")
def cache_fetch_all():
    """Cache all unread articles that aren't yet cached."""
    from foci.cache.articles import fetch_all_unread

    settings = _init_db()
    results = fetch_all_unread(settings, on_progress=click.echo)

    ok = sum(1 for v in results.values() if v == "ok")
    skipped = sum(1 for v in results.values() if v == "skip")
    errors = sum(1 for v in results.values() if v not in ("ok", "skip"))
    click.echo(f"\nDone. {ok} fetched, {skipped} already cached, {errors} error(s).")


@cache.command("fetch-audio")
def cache_fetch_audio():
    """Download all unread podcast episodes that aren't yet cached."""
    from foci.cache.audio import fetch_all_unread

    settings = _init_db()
    results = fetch_all_unread(settings, on_progress=click.echo)

    ok = sum(1 for v in results.values() if v == "ok")
    skipped = sum(1 for v in results.values() if v == "skip")
    errors = sum(1 for v in results.values() if v not in ("ok", "skip"))
    click.echo(f"\nDone. {ok} downloaded, {skipped} already cached, {errors} error(s).")


@cache.command("clean")
@click.option("--dry-run", is_flag=True, help="List what would be deleted without deleting.")
def cache_clean(dry_run):
    """Delete cached audio older than cache.audio_max_age_days."""
    from foci.cache.audio import clean_expired_audio, expired_audio

    settings = _init_db()
    days = settings.cache.audio_max_age_days

    if dry_run:
        entries = expired_audio(settings)
        for entry in entries:
            click.echo(f"  [{entry.item.id}] {entry.cached_at:%Y-%m-%d}  {entry.item.title}")
        click.echo(f"{len(entries)} cached audio file(s) older than {days} days.")
        return

    count = clean_expired_audio(settings)
    click.echo(f"Removed {count} cached audio file(s) older than {days} days.")


@cache.command("path")
@click.argument("item_id", type=int)
def cache_path(item_id):
    """Print the local file path of a cached audio episode (e.g. to pass to a player)."""
    from foci.cache.audio import cached_path

    _init_db()
    try:
        item = database.Item.get_by_id(item_id)
    except database.Item.DoesNotExist:
        click.echo(f"No item with ID {item_id}.", err=True)
        return

    try:
        click.echo(cached_path(item))
    except FileNotFoundError:
        click.echo(f"Not cached yet. Run: foci cache fetch {item_id}", err=True)


@cache.command("read")
@click.argument("item_id", type=int)
@click.option("--plain", is_flag=True, help="Print plain text instead of rendered markdown.")
def cache_read(item_id, plain):
    """Read a cached article in the terminal."""
    from foci.cache.articles import read_cached

    _init_db()
    try:
        item = database.Item.get_by_id(item_id)
    except database.Item.DoesNotExist:
        click.echo(f"No item with ID {item_id}.", err=True)
        return

    try:
        text = read_cached(item)
    except FileNotFoundError as exc:
        click.echo(f"Not cached yet. Run: foci cache fetch {item_id}", err=True)
        return

    if plain:
        click.echo(text)
    else:
        try:
            from rich.console import Console
            from rich.markdown import Markdown
            Console().print(Markdown(text))
        except ImportError:
            click.echo(text)

    # Mark as read and update daily budget
    from foci.feeds.maintenance import record_consumption, budget_status
    settings = load_settings()
    usage = record_consumption(item)
    _, limit, over = budget_status(settings)
    if over:
        click.echo(f"\n(Today: {usage.articles_read} articles read — soft limit is {limit}.)")


@cache.command("list")
def cache_list():
    """List all cached articles and audio."""
    _init_db()
    entries = (
        database.CacheEntry.select()
        .join(database.Item)
        .where(database.Item.media_type.in_(["article", "audio"]))
        .order_by(database.CacheEntry.cached_at.desc())
    )
    count = 0
    for entry in entries:
        count += 1
        status = entry.item.status
        cached = entry.cached_at.strftime("%Y-%m-%d")
        kind = "aud" if entry.item.media_type == "audio" else "art"
        click.echo(f"  [{entry.item.id}] {cached}  {kind}  [{status}]  {entry.item.title}")
    if count == 0:
        click.echo("Nothing cached. Run: foci cache fetch-all (articles) or fetch-audio")


# ---------------------------------------------------------------------------
# foci queue
# ---------------------------------------------------------------------------

@cli.group()
def queue():
    """Manage the video download queue."""


@queue.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include completed and cancelled entries.")
def queue_list(show_all):
    """List queued downloads."""
    _init_db()

    if show_all:
        statuses = ["pending", "downloading", "completed", "cancelled", "failed"]
    else:
        statuses = ["pending", "downloading", "failed"]

    entries = (
        database.QueueEntry.select()
        .where(database.QueueEntry.status.in_(statuses))
        .order_by(database.QueueEntry.priority.desc(), database.QueueEntry.queued_at)
    )

    count = 0
    for entry in entries:
        count += 1
        if entry.status == "pending":
            if entry.is_eligible():
                state = "ready to download"
            else:
                mins = int((entry.eligible_at - __import__("datetime").datetime.utcnow()).total_seconds() / 60)
                state = f"eligible in {mins}m"
        elif entry.status == "completed":
            state = f"done  {entry.local_path}"
        else:
            state = entry.status
        click.echo(f"  [{entry.id}] {entry.item.title}")
        click.echo(f"         {state}")

    if count == 0:
        click.echo("Queue is empty." if show_all else "Nothing pending. Use --all to see completed entries.")


@queue.command("add")
@click.argument("item_id", type=int)
def queue_add(item_id):
    """Add a feed item to the download queue."""
    import datetime
    settings = _init_db()
    try:
        item = database.Item.get_by_id(item_id)
    except database.Item.DoesNotExist:
        click.echo(f"No item with ID {item_id}.", err=True)
        return
    if item.media_type != "video":
        click.echo(f"Item {item_id} is not a video ({item.media_type}).", err=True)
        return
    delay = datetime.timedelta(minutes=settings.queue.minimum_age_minutes)
    now = datetime.datetime.utcnow()
    entry, created = database.QueueEntry.get_or_create(
        item=item,
        defaults={"queued_at": now, "eligible_at": now + delay},
    )
    if created:
        click.echo(f"Queued: {item.title}")
        click.echo(f"Eligible to download after: {entry.eligible_at.strftime('%Y-%m-%d %H:%M')} UTC")
    else:
        click.echo(f"Already in queue (status: {entry.status}).")


@queue.command("download")
def queue_download():
    """Download all eligible queue entries."""
    from foci.queue.downloader import download_all_eligible

    settings = _init_db()
    results = download_all_eligible(settings, on_progress=click.echo)

    if results:
        ok = sum(1 for v in results.values() if v == "ok")
        failed = sum(1 for v in results.values() if v != "ok")
        click.echo(f"\nDone. {ok} downloaded, {failed} failed.")


@queue.command("cancel")
@click.argument("entry_id", type=int)
def queue_cancel(entry_id):
    """Cancel a pending queue entry."""
    from foci.queue.downloader import cancel_entry

    _init_db()
    if cancel_entry(entry_id):
        click.echo(f"Cancelled entry {entry_id}.")
    else:
        click.echo(
            f"Could not cancel entry {entry_id} — not found or not in a pending state.",
            err=True,
        )


# ---------------------------------------------------------------------------
# foci block
# ---------------------------------------------------------------------------

@cli.group()
def block():
    """Manage the browser hosts-file blocklist. Requires admin privileges."""


@block.command("status")
def block_status():
    """Show configured domains and what is currently active in the hosts file."""
    from foci.block import active_blocked_domains, has_foci_section, hosts_path

    bl = load_blocklist()

    click.echo(f"Hosts file: {hosts_path()}")
    active = active_blocked_domains()
    if active:
        click.echo(f"Foci block is ACTIVE ({len(active)} domain(s) redirected)\n")
    else:
        click.echo("Foci block is NOT active (run: foci block apply)\n")

    click.echo(f"Configured blocked domains ({len(bl.blocked)}):")
    whitelist_set = {d.lower() for d in bl.whitelist}
    for d in bl.blocked:
        note = "  [whitelisted — exempt]" if d.lower() in whitelist_set else ""
        click.echo(f"  {d}{note}")

    if bl.whitelist:
        click.echo(f"\nWhitelisted domains ({len(bl.whitelist)}):")
        for d in bl.whitelist:
            click.echo(f"  {d}")


@block.command("apply")
def block_apply():
    """Write Foci block entries to the hosts file. Requires admin/sudo."""
    from foci.block import apply_block

    bl = load_blocklist()
    try:
        count = apply_block(bl)
        click.echo(f"Block applied. {count} domain(s) now redirect to 127.0.0.1.")
        click.echo("")
        click.echo("Start the landing page server (in a separate terminal, with admin):")
        click.echo("  foci serve --port 80")
        click.echo("")
        click.echo("To undo: foci block remove")
    except PermissionError as exc:
        click.echo(f"Error: {exc}", err=True)


@block.command("remove")
def block_remove():
    """Remove Foci block entries from the hosts file. Requires admin/sudo."""
    from foci.block import remove_block

    try:
        if remove_block():
            click.echo("Foci block entries removed from hosts file.")
        else:
            click.echo("No Foci block entries found in hosts file.")
    except PermissionError as exc:
        click.echo(f"Error: {exc}", err=True)


@block.command("check")
@click.argument("domain")
def block_check(domain):
    """Check whether a domain would be blocked or whitelisted."""
    bl = load_blocklist()
    whitelist_set = {d.lower() for d in bl.whitelist}
    blocked_set = {d.lower() for d in bl.blocked}
    d = domain.lower().lstrip("www.")

    if d in whitelist_set or f"www.{d}" in whitelist_set:
        click.echo(f"  {domain}  →  WHITELISTED (always allowed)")
    elif d in blocked_set or f"www.{d}" in blocked_set:
        click.echo(f"  {domain}  →  BLOCKED (redirects to localhost when block is active)")
    else:
        click.echo(f"  {domain}  →  not in blocklist or whitelist")


# ---------------------------------------------------------------------------
# foci tui
# ---------------------------------------------------------------------------

@cli.command("tui")
def tui():
    """Launch the Foci terminal UI."""
    from foci.ui.tui.app import FociApp
    settings = _init_db()
    FociApp(settings).run()


# ---------------------------------------------------------------------------
# foci serve
# ---------------------------------------------------------------------------

@cli.command("serve")
@click.option("--port", default=None, type=int, help="Port to listen on (default: from settings).")
@click.option("--host", default=None, help="Host to bind to (default: from settings).")
def serve(port, host):
    """Start the local Foci web server.

    For the browser block to work, the server must run on port 80, which
    requires admin privileges. Use: sudo foci serve --port 80 (macOS/Linux)
    or run as Administrator on Windows.
    """
    import uvicorn
    from foci.block import is_elevated
    from foci.ui.web.app import create_app

    settings = _init_db()
    actual_port = port if port is not None else settings.server.port
    actual_host = host if host is not None else settings.server.host

    if actual_port < 1024 and not is_elevated():
        click.echo(
            f"Warning: port {actual_port} requires elevated privileges.\n"
            "  Windows: run this terminal as Administrator.\n"
            "  macOS/Linux: prefix with sudo.",
            err=True,
        )

    app = create_app(settings)
    click.echo(f"Foci server listening on http://{actual_host}:{actual_port}/")
    click.echo("Press Ctrl-C to stop.")
    uvicorn.run(app, host=actual_host, port=actual_port, log_level="warning")


if __name__ == "__main__":
    cli()
