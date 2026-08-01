"""Tests for foci.block — uses a temporary hosts file, no real system modification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from foci.config import BlocklistConfig
from foci.block import (
    active_blocked_domains,
    apply_block,
    has_foci_section,
    remove_block,
    _remove_section,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXISTING_HOSTS = """\
127.0.0.1 localhost
::1       localhost
# Some comment
192.168.1.1 mydevice
"""

HOSTS_WITH_FOCI = """\
127.0.0.1 localhost
::1       localhost

# --- foci begin ---
127.0.0.1 reddit.com
127.0.0.1 www.reddit.com
127.0.0.1 youtube.com
# --- foci end ---
"""

SIMPLE_BLOCKLIST = BlocklistConfig(
    blocked=["youtube.com", "www.youtube.com", "reddit.com", "www.reddit.com"],
    whitelist=[],
)

BLOCKLIST_WITH_WHITELIST = BlocklistConfig(
    blocked=["youtube.com", "www.youtube.com", "reddit.com", "www.reddit.com"],
    whitelist=["reddit.com", "www.reddit.com"],
)


def patched_hosts(tmp_path: Path, content: str = EXISTING_HOSTS):
    """Return a context manager that redirects hosts_path() to a temp file."""
    fake = tmp_path / "hosts"
    fake.write_text(content, encoding="utf-8")
    return patch("foci.block.hosts_path", return_value=fake)


def elevated():
    """Context manager that pretends we have admin privileges."""
    return patch("foci.block.is_elevated", return_value=True)


# ---------------------------------------------------------------------------
# _remove_section (pure string operation — no I/O)
# ---------------------------------------------------------------------------

class TestRemoveSection:
    def test_removes_foci_block(self):
        result = _remove_section(HOSTS_WITH_FOCI)
        assert "# --- foci begin ---" not in result
        assert "# --- foci end ---" not in result
        assert "127.0.0.1 youtube.com" not in result

    def test_preserves_other_content(self):
        result = _remove_section(HOSTS_WITH_FOCI)
        assert "127.0.0.1 localhost" in result

    def test_noop_on_hosts_without_section(self):
        result = _remove_section(EXISTING_HOSTS)
        assert result == EXISTING_HOSTS


# ---------------------------------------------------------------------------
# apply_block
# ---------------------------------------------------------------------------

class TestApplyBlock:
    def test_writes_blocked_domains(self, tmp_path):
        with patched_hosts(tmp_path), elevated():
            count = apply_block(SIMPLE_BLOCKLIST)

        content = (tmp_path / "hosts").read_text()
        assert "127.0.0.1 youtube.com" in content
        assert "127.0.0.1 reddit.com" in content
        assert count == 4

    def test_respects_whitelist(self, tmp_path):
        with patched_hosts(tmp_path), elevated():
            count = apply_block(BLOCKLIST_WITH_WHITELIST)

        content = (tmp_path / "hosts").read_text()
        assert "127.0.0.1 youtube.com" in content
        assert "127.0.0.1 reddit.com" not in content
        assert count == 2

    def test_preserves_existing_entries(self, tmp_path):
        with patched_hosts(tmp_path), elevated():
            apply_block(SIMPLE_BLOCKLIST)

        content = (tmp_path / "hosts").read_text()
        assert "192.168.1.1 mydevice" in content

    def test_replaces_previous_foci_section(self, tmp_path):
        with patched_hosts(tmp_path, HOSTS_WITH_FOCI), elevated():
            apply_block(SIMPLE_BLOCKLIST)

        content = (tmp_path / "hosts").read_text()
        # Should appear exactly once (not duplicated)
        assert content.count("# --- foci begin ---") == 1

    def test_raises_without_elevation(self, tmp_path):
        with patched_hosts(tmp_path), patch("foci.block.is_elevated", return_value=False):
            with pytest.raises(PermissionError):
                apply_block(SIMPLE_BLOCKLIST)

    def test_domains_sorted(self, tmp_path):
        with patched_hosts(tmp_path), elevated():
            apply_block(SIMPLE_BLOCKLIST)

        content = (tmp_path / "hosts").read_text()
        lines = [l for l in content.splitlines() if l.startswith("127.0.0.1 ")]
        domains = [l.split()[1] for l in lines]
        assert domains == sorted(domains)


# ---------------------------------------------------------------------------
# remove_block
# ---------------------------------------------------------------------------

class TestRemoveBlock:
    def test_removes_existing_section(self, tmp_path):
        with patched_hosts(tmp_path, HOSTS_WITH_FOCI), elevated():
            result = remove_block()

        assert result is True
        content = (tmp_path / "hosts").read_text()
        assert "# --- foci begin ---" not in content
        assert "127.0.0.1 youtube.com" not in content

    def test_returns_false_when_no_section(self, tmp_path):
        with patched_hosts(tmp_path, EXISTING_HOSTS), elevated():
            result = remove_block()

        assert result is False

    def test_preserves_other_entries(self, tmp_path):
        with patched_hosts(tmp_path, HOSTS_WITH_FOCI), elevated():
            remove_block()

        content = (tmp_path / "hosts").read_text()
        assert "127.0.0.1 localhost" in content

    def test_raises_without_elevation(self, tmp_path):
        with patched_hosts(tmp_path, HOSTS_WITH_FOCI), \
             patch("foci.block.is_elevated", return_value=False):
            with pytest.raises(PermissionError):
                remove_block()


# ---------------------------------------------------------------------------
# active_blocked_domains / has_foci_section
# ---------------------------------------------------------------------------

class TestActiveBlockedDomains:
    def test_reads_active_domains(self, tmp_path):
        with patched_hosts(tmp_path, HOSTS_WITH_FOCI):
            domains = active_blocked_domains()

        assert "youtube.com" in domains
        assert "reddit.com" in domains

    def test_empty_when_no_section(self, tmp_path):
        with patched_hosts(tmp_path, EXISTING_HOSTS):
            assert active_blocked_domains() == []

    def test_has_foci_section_true(self, tmp_path):
        with patched_hosts(tmp_path, HOSTS_WITH_FOCI):
            assert has_foci_section() is True

    def test_has_foci_section_false(self, tmp_path):
        with patched_hosts(tmp_path, EXISTING_HOSTS):
            assert has_foci_section() is False


# ---------------------------------------------------------------------------
# Round-trip: apply then remove leaves hosts file clean
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_apply_then_remove_restores_original(self, tmp_path):
        original = EXISTING_HOSTS
        with patched_hosts(tmp_path, original), elevated():
            apply_block(SIMPLE_BLOCKLIST)
            remove_block()

        result = (tmp_path / "hosts").read_text()
        # All original content should still be there
        for line in original.splitlines():
            assert line in result
        # And no Foci remnants
        assert "# --- foci" not in result
