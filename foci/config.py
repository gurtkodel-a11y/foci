"""Load and validate configuration from TOML files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"


@dataclass
class PollingConfig:
    interval_minutes: int = 60


@dataclass
class FeedConfig:
    item_expiry_days: int = 14


@dataclass
class CacheConfig:
    article_max_age_days: int = 30
    audio_max_age_days: int = 90


@dataclass
class QueueConfig:
    minimum_age_minutes: int = 120
    max_concurrent_downloads: int = 1
    video_format: str = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"


@dataclass
class BudgetConfig:
    daily_article_soft_limit: int = 20


@dataclass
class StorageConfig:
    data_dir: Path = Path("data")
    cache_dir: Path = Path("data/cache")
    downloads_dir: Path = Path("data/downloads")


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 6366


@dataclass
class Settings:
    polling: PollingConfig = field(default_factory=PollingConfig)
    feed: FeedConfig = field(default_factory=FeedConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


@dataclass
class Source:
    name: str
    url: str
    type: str  # "rss" | "youtube"
    tags: list[str] = field(default_factory=list)
    active: bool = True


@dataclass
class BlocklistConfig:
    blocked: list[str] = field(default_factory=list)
    whitelist: list[str] = field(default_factory=list)


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings(config_dir: Path = CONFIG_DIR) -> Settings:
    raw = _load_toml(config_dir / "settings.toml")

    def _sub(key, cls, **overrides):
        section = {**raw.get(key, {}), **overrides}
        # Convert path strings to Path objects for StorageConfig
        if cls is StorageConfig:
            return cls(**{k: Path(v) for k, v in section.items()})
        return cls(**section)

    return Settings(
        polling=_sub("polling", PollingConfig),
        feed=_sub("feed", FeedConfig),
        cache=_sub("cache", CacheConfig),
        queue=_sub("queue", QueueConfig),
        budget=_sub("budget", BudgetConfig),
        storage=_sub("storage", StorageConfig),
        server=_sub("server", ServerConfig),
    )


def load_sources(config_dir: Path = CONFIG_DIR) -> list[Source]:
    raw = _load_toml(config_dir / "subscriptions.toml")
    return [
        Source(
            name=s["name"],
            url=s["url"],
            type=s["type"],
            tags=s.get("tags", []),
            active=s.get("active", True),
        )
        for s in raw.get("sources", [])
    ]


def load_blocklist(config_dir: Path = CONFIG_DIR) -> BlocklistConfig:
    raw = _load_toml(config_dir / "blocklist.toml")
    return BlocklistConfig(
        blocked=raw.get("blocked", []),
        whitelist=raw.get("whitelist", []),
    )
