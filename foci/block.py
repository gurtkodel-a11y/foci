"""Hosts-file based browser redirect. Cross-platform: Windows and macOS/Linux."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from foci.config import BlocklistConfig

# Markers that delimit Foci's section in the hosts file.
# Chosen to be unambiguous and not collide with anything a user would write.
_FOCI_BEGIN = "# --- foci begin ---"
_FOCI_END = "# --- foci end ---"

# Regex that matches the entire Foci section, including surrounding blank lines.
_SECTION_RE = re.compile(
    r"\n*" + re.escape(_FOCI_BEGIN) + r".*?" + re.escape(_FOCI_END) + r"\n?",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def hosts_path() -> Path:
    """Return the hosts file path for the current platform."""
    if sys.platform == "win32":
        return Path(r"C:\Windows\System32\drivers\etc\hosts")
    return Path("/etc/hosts")  # macOS and Linux


def is_elevated() -> bool:
    """Return True if the current process has the privileges needed to edit the hosts file."""
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.getuid() == 0  # macOS / Linux


def elevation_hint() -> str:
    """Return a human-readable hint for how to gain elevated privileges."""
    if sys.platform == "win32":
        return (
            "Open a Command Prompt or PowerShell as Administrator "
            "(right-click the terminal icon → 'Run as administrator'), then re-run the command."
        )
    return "Re-run with: sudo foci block apply"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_block(blocklist: BlocklistConfig) -> int:
    """
    Write Foci's redirect entries into the hosts file.
    Replaces any existing Foci section. Returns the number of domains written.
    Raises PermissionError if not running with sufficient privileges.
    """
    _require_elevation()

    whitelist = {d.lower() for d in blocklist.whitelist}
    domains = [d for d in blocklist.blocked if d.lower() not in whitelist]

    block_lines = [_FOCI_BEGIN]
    for domain in sorted(domains):
        block_lines.append(f"127.0.0.1 {domain}")
    block_lines.append(_FOCI_END)

    existing = _read_hosts()
    cleaned = _remove_section(existing)
    new_content = cleaned.rstrip("\n") + "\n\n" + "\n".join(block_lines) + "\n"

    _write_hosts(new_content)
    return len(domains)


def remove_block() -> bool:
    """
    Remove Foci's section from the hosts file.
    Returns True if a section was found and removed, False if nothing was there.
    Raises PermissionError if not elevated.
    """
    _require_elevation()

    existing = _read_hosts()
    cleaned = _remove_section(existing)

    if cleaned == existing:
        return False

    _write_hosts(cleaned.rstrip("\n") + "\n")
    return True


def active_blocked_domains() -> list[str]:
    """
    Read the hosts file and return the domains currently blocked by Foci.
    Does not require elevation (read-only).
    """
    content = _read_hosts()
    in_section = False
    domains: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == _FOCI_BEGIN:
            in_section = True
            continue
        if stripped == _FOCI_END:
            break
        if in_section and stripped.startswith("127.0.0.1 "):
            domains.append(stripped[len("127.0.0.1 "):].strip())

    return domains


def has_foci_section() -> bool:
    """Return True if the hosts file currently contains a Foci block section."""
    return _FOCI_BEGIN in _read_hosts()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _require_elevation() -> None:
    if not is_elevated():
        raise PermissionError(
            f"Editing the hosts file requires elevated privileges.\n{elevation_hint()}"
        )


def _read_hosts() -> str:
    path = hosts_path()
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Windows hosts files are sometimes saved as ANSI/Latin-1
        return path.read_text(encoding="latin-1")


def _write_hosts(content: str) -> None:
    hosts_path().write_text(content, encoding="utf-8")


def _remove_section(content: str) -> str:
    """Strip the Foci section (and surrounding blank lines) from a hosts file string."""
    return _SECTION_RE.sub("", content)
