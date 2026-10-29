# Foci

A personal media curation and deliberate consumption toolkit.

Foci pulls content from sources you explicitly subscribe to — RSS feeds, blogs, podcasts, YouTube
channels — and caches it locally so you never have to visit the originating site. Videos go into a
timed download queue (the gap between "I want to watch this" and "I'm watching it" is intentional).
High-temptation sites can be redirected to a local landing page that shows only what you already
have queued.

Nothing is recommended. Nothing is trending. The feed shows what you subscribed to, in chronological
order, and nothing else.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11 or newer | Required |
| ffmpeg | any recent | Required for yt-dlp to merge video+audio streams |
| git | any | Optional — useful for version-controlling your subscriptions |

### Windows

```powershell
# Install Python (if not already installed)
winget install Python.Python.3.13

# Install ffmpeg
winget install Gyan.FFmpeg
# After installing, open a new terminal so PATH is updated.
# Verify: ffmpeg -version
```

Alternatively, download Python from [python.org](https://python.org) and ffmpeg from
[ffmpeg.org](https://ffmpeg.org/download.html). If installing ffmpeg manually, add its `bin/`
folder to your system PATH.

### macOS

```bash
# Install Homebrew if you don't have it: https://brew.sh
brew install python@3.13 ffmpeg
```

---

## Installation

```bash
# 1. Get the code
git clone <your-repo-url> foci
cd foci

# 2. Create and activate a virtual environment

# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Foci and its dependencies
pip install -e .
```

> **Note:** The first `pip install` downloads all dependencies and may take a minute or two
> depending on your connection speed.

Verify the install:

```bash
foci --version
```

---

## First-run setup

### 1. Configure your subscriptions

Edit [`config/subscriptions.toml`](config/subscriptions.toml) and add your sources:

```toml
[[sources]]
name = "My Favorite Blog"
url  = "https://example.com/feed.xml"
type = "rss"
tags = ["blogs"]

[[sources]]
name = "A Podcast"
url  = "https://example.com/podcast.rss"
type = "rss"
tags = ["podcasts"]

[[sources]]
name = "A YouTube Channel"
url  = "https://www.youtube.com/@handle"
type = "youtube"
tags = ["video"]
```

RSS and Atom feeds (blogs, podcasts, newsletters, subreddit feeds) all use `type = "rss"`.
YouTube channels use `type = "youtube"`.

### 2. Fetch your first feeds

```bash
foci feed poll
```

This syncs your subscription list to the database, polls all sources, and archives any items
already past their expiry date.

### 3. Cache articles for offline reading

```bash
foci cache fetch-all
```

Downloads and cleans every unread article so you can read it without visiting the original site.

### 4. Launch the terminal UI

```bash
foci tui
```

---

## Daily usage

### Terminal UI (recommended)

```
foci tui
```

| Key | Action |
|---|---|
| `1` `2` `3` | Switch between Feed / Queue / Stats tabs |
| `↑` `↓` | Navigate the list |
| `Enter` | Read article (caches if needed) / Queue video for download |
| `d` | Dismiss the highlighted item |
| `c` | Cancel the highlighted queue entry |
| `r` | Refresh all tabs |
| `q` | Quit |

Inside the article reader, press `Escape` or `q` to return to the feed.

### CLI commands

```bash
# Feed
foci feed poll            # Fetch new items from all sources
foci feed list            # List unread items
foci feed list --status archived
foci feed read <id>       # Mark an item as read
foci feed expire          # Manually archive items past expiry date
foci feed budget          # Show today's consumption count

# Article cache
foci cache fetch-all      # Cache all unread articles
foci cache fetch <id>     # Cache a specific article
foci cache read <id>      # Read a cached article in the terminal
foci cache list           # List cached articles

# Video download queue
foci queue list           # Show pending and in-progress downloads
foci queue list --all     # Include completed and cancelled
foci queue add <id>       # Queue a video item for download
foci queue download       # Download all eligible entries
foci queue cancel <id>    # Cancel a pending entry

# Sources
foci sources list         # List all sources and their mute status
foci sources mute "Name" --days 7    # Mute a source for N days
foci sources unmute "Name"           # Lift a mute early

# Browser block
foci block status         # Show current block config and active state
foci block apply          # Write redirect entries to the hosts file (needs admin)
foci block remove         # Remove Foci entries from the hosts file (needs admin)
foci block check youtube.com         # Check if a domain would be blocked
```

---

## Browser block setup

Foci can redirect high-temptation sites (YouTube, Reddit, Twitter, etc.) to a local landing
page that shows only what you already have in your queue and feed.

This works by writing entries to the system hosts file, which requires administrator/root
privileges. The local web server must also run on port 80.

### Windows

Open **PowerShell as Administrator**:

```powershell
# Apply the hosts-file redirect
foci block apply

# Start the web server (keep this terminal open, or run it as a background service)
foci serve --port 80
```

When you navigate to a blocked site, your browser will hit `127.0.0.1:80` and see the Foci
landing page instead.

To remove the block:
```powershell
# In an Administrator terminal
foci block remove
```

### macOS

```bash
# Apply the hosts-file redirect
sudo foci block apply

# Start the web server (in a separate terminal, or add to a launchd plist)
sudo foci serve --port 80
```

To remove:
```bash
sudo foci block remove
```

### Whitelist

Sites you genuinely need (banking, utilities, etc.) are exempt from the block.
Edit [`config/blocklist.toml`](config/blocklist.toml):

```toml
[whitelist]
whitelist = [
    "mybank.com",
    "www.mybank.com",
]
```

Re-apply after any change:
```bash
# Windows (Administrator): foci block apply
# macOS: sudo foci block apply
```

> **Policy suggestion:** treat whitelist additions deliberately — each one is a decision, not a
> default. The smaller the whitelist, the more effective the block.

---

## Settings reference

Key settings in [`config/settings.toml`](config/settings.toml):

| Setting | Default | Description |
|---|---|---|
| `polling.interval_minutes` | 60 | How often to poll feeds (if using a scheduler) |
| `feed.item_expiry_days` | 14 | Unread items are auto-archived after this many days |
| `queue.minimum_age_minutes` | 120 | Videos cannot be downloaded until this long after queuing |
| `queue.video_format` | `bestvideo[height<=720]+bestaudio/…` | yt-dlp format selector; 720p cap is a preference |
| `budget.daily_article_soft_limit` | 20 | Article count that triggers a soft notice (not a hard block) |
| `server.port` | 6366 | Default web server port; use 80 for the browser block to work |

---

## Data storage

All data is local to the machine. Nothing is uploaded or synced.

| Path | Contents | Tracked in git? |
|---|---|---|
| `data/foci.db` | SQLite database | No (gitignored) |
| `data/cache/` | Cached article text | No |
| `data/downloads/` | Downloaded videos | No |
| `config/subscriptions.toml` | Your subscription list | Your choice |
| `config/settings.toml` | App settings | Your choice |
| `config/blocklist.toml` | Block/whitelist config | Your choice |

Foci is designed so that `config/` can optionally be tracked in git if you want to version-control
your subscriptions — but `data/` should never be committed.

---

## Running tests

```bash
pip install pytest
pytest
```

All tests use an in-memory SQLite database and mocked network calls — no internet access required.
