# Foci

A personal media curation and deliberate consumption toolkit. The goal is to limit internet
exposure to intentionally chosen, subscribed content — pulling it locally where possible to
avoid algorithmic recommendations, ads, and the ambient temptation to browse aimlessly.

## Project Philosophy

- **Pull, don't browse.** Foci fetches content from sources the user has explicitly subscribed to.
  Nothing else enters the feed.
- **Distance creates deliberateness.** Larger media (videos) goes into a download queue rather
  than playing immediately. The gap between "I want to watch this" and "I am watching this" is
  intentional — it gives time to reconsider.
- **Cache locally, consume offline.** Small media (articles, short audio, images) is cached
  locally so the user never needs to visit the originating site (and its recommendations).
- **No algorithmic surface.** Foci presents only what the user subscribed to, in chronological
  order. No "up next," no trending, no related content.

## Implementation Status

All core features are implemented. The following is what exists and is tested:

| Module | File(s) | Status |
|---|---|---|
| Config loading | `foci/config.py` | Done |
| Database schema | `foci/db.py` | Done |
| Feed polling (RSS + YouTube) | `foci/feeds/poller.py`, `normalizer.py` | Done |
| Feed maintenance (expiry, mute, budget) | `foci/feeds/maintenance.py` | Done |
| Article caching | `foci/cache/articles.py` | Done |
| Video download queue | `foci/queue/downloader.py` | Done |
| Hosts-file blocker | `foci/block.py` | Done — cross-platform (Windows + macOS) |
| Web landing page | `foci/ui/web/app.py`, `templates/landing.html` | Done |
| Terminal UI | `foci/ui/tui/app.py` | Done |
| CLI entry point | `foci/cli.py` | Done |

Podcast audio download (`foci/cache/audio.py`) is the one planned module not yet implemented.

## Architecture Notes

### Cross-platform considerations
- All path handling uses `pathlib.Path` — no hardcoded separators.
- `foci/block.py` detects `sys.platform` at runtime:
  - Windows: hosts at `C:\Windows\System32\drivers\etc\hosts`, elevation via `ctypes.windll`
  - macOS/Linux: hosts at `/etc/hosts`, elevation via `os.getuid() == 0`
- The web server needs port 80 for the hosts-file redirect to work. Port 80 requires admin on
  both platforms. The default dev port is 6366; production block use requires `--port 80`.
- ffmpeg must be installed separately (yt-dlp uses it to merge video+audio streams).

### Data flow
```
subscriptions.toml → foci feed poll → db.Item (unread)
                                    ↓
                    foci cache fetch-all → data/cache/articles/{id}.md
                                    ↓
                    foci tui / foci cache read → user reads article
                                    ↓
                    record_consumption() → db.DailyUsage

db.Item (video, unread) → foci queue add → db.QueueEntry (pending, eligible_at = now + 2h)
                                         ↓
                          foci queue download → yt-dlp → data/downloads/
```

### Key design decisions
- **Item expiry**: `expires_at` is set at ingest time (`now + item_expiry_days`). The `expire_items()`
  function archives by comparing against `now`. Queue-protected items are never auto-archived.
- **Minimum queue age**: enforced by `eligible_at` timestamp, not by a runtime check at download
  time. The web page and TUI both display remaining time from this field.
- **Daily budget**: a soft notice only — never a hard block. Counter lives in `db.DailyUsage`.
- **Mute**: stored in `db.Source.muted_until` (DB state, not config). Config controls subscription
  list; DB controls operational state.
- **Browser block**: uses `# --- foci begin ---` / `# --- foci end ---` markers so Foci's entries
  can be found and replaced/removed without touching anything else in the hosts file.

## Directory Layout

```
foci/
  CLAUDE.md
  README.md
  pyproject.toml
  .gitignore
  foci/
    __init__.py
    config.py           # Load/validate TOML config
    db.py               # Database schema (peewee + SQLite)
    block.py            # Hosts-file redirect management (cross-platform)
    cli.py              # `foci` CLI entry point (click)
    feeds/
      poller.py         # Fetch and ingest RSS/Atom/YouTube feeds
      normalizer.py     # Normalize feedparser entries to Item dicts
      maintenance.py    # Expiry, consumption tracking, mute helpers
    cache/
      articles.py       # Article fetch + trafilatura extraction
    queue/
      downloader.py     # yt-dlp wrapper + queue state management
    ui/
      tui/
        app.py          # Textual TUI (Feed / Queue / Stats tabs + ReaderScreen)
        app.tcss        # TUI stylesheet
      web/
        app.py          # FastAPI landing page app
        templates/
          landing.html  # Server-rendered landing page (no JS, no external resources)
  data/                 # gitignored — machine-local
    foci.db
    cache/
    downloads/
  config/
    subscriptions.toml  # User's subscription list
    settings.toml       # App settings
    blocklist.toml      # Blocked + whitelisted domains
  tests/
    test_normalizer.py
    test_poller.py
    test_cache_articles.py
    test_downloader.py
    test_block.py
    test_web.py
    test_maintenance.py
```

## Development Commands

```bash
# Run all tests (no network, no disk I/O beyond tmp)
pytest

# Run a specific test file
pytest tests/test_maintenance.py -v

# Syntax-check all Python files
python -c "
import ast, pathlib, sys
errors = []
for f in pathlib.Path('.').rglob('*.py'):
    if '.venv' in f.parts: continue
    try: ast.parse(f.read_text(encoding='utf-8'))
    except SyntaxError as e: errors.append(f'{f}: {e}')
print('OK' if not errors else '\n'.join(errors)); sys.exit(bool(errors))
"

# Run the TUI (normal dev use)
foci tui

# Run the web server on the dev port (no admin needed)
foci serve

# Run the web server on port 80 (admin needed, for block to work)
# Windows (Administrator terminal):
foci serve --port 80
# macOS:
sudo foci serve --port 80
```

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | yt-dlp, feedparser, and most relevant libs are Python-native |
| Database | SQLite via `peewee` | Local, zero-config, sufficient for personal scale |
| Feed parsing | `feedparser` | Battle-tested RSS/Atom/RDF parsing |
| Video download | `yt-dlp` | Best-in-class for YouTube and many other platforms |
| Article extraction | `trafilatura` | Clean reader-mode Markdown extraction |
| TUI | `textual` | Full-featured terminal UI with tabs, DataTable, MarkdownViewer |
| Web UI | `FastAPI` + `Jinja2` | Minimal server-rendered landing page; no JavaScript |
| Config | `tomllib` (stdlib) | Human-readable TOML; built into Python 3.11+ |

## Key Constraints

- Everything runs locally. No cloud services, no accounts, no external APIs beyond the feeds.
- No tracking, no analytics, no phoning home.
- Designed for slow internet — video format capped at 720p by default; article caching avoids
  re-visiting sites.
- Prefer correctness and simplicity over cleverness. This is a personal tool.

## Planned / Future Features

- `foci/cache/audio.py` — podcast/audio download (the one unimplemented planned module)
- Periodic scheduling (`apscheduler`) so `foci feed poll` runs automatically
- Weekly digest view — a summary of what arrived this week
- Stats per source surfaced in the TUI (items received vs. consumed over time)
- `foci tui` seek-to-source filtering — show only items from one source
