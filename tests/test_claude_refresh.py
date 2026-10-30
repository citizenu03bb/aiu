# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude probe refreshes a stale CLI token before calling the usage API — no network.
Which `claude` runs, and how often (L4 in docs/security-review.md)."""
import grp
import os
import pwd
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import util  # noqa: E402
from aiu.probes import claude  # noqa: E402

NOW = 1793350914.544
USAGE = {"five_hour": {"utilization": 12.0, "resets_at": "2026-10-30T12:00:00+00:00"}}


class Resp:
    status_code = 200

    def json(self):
        return USAGE


def setup(monkeypatch, tokens, refresh_ok=True):
    """tokens: successive (token, expires_at_ms) values the credentials file holds."""
    reads, refreshes, sent = list(tokens), [], []
    monkeypatch.setattr(time, "time", lambda: NOW)
    monkeypatch.setattr(claude, "_read_claude_token", lambda: reads.pop(0) if len(reads) > 1 else reads[0])
    monkeypatch.setattr(claude, "_refresh_claude_token", lambda cfg: refreshes.append(cfg) or refresh_ok)
    monkeypatch.setattr(httpx, "get",
                        lambda url, headers, timeout: sent.append(headers["Authorization"]) or Resp())
    return refreshes, sent


def test_expired_token_is_refreshed_first(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("old", (NOW - 3600) * 1000), ("new", (NOW + 8 * 3600) * 1000)])
    rows = claude.probe_claude({"read_token": True, "claude_cmd": "/opt/claude"})
    assert refreshes == [{"read_token": True, "claude_cmd": "/opt/claude"}] and sent == ["Bearer new"]
    assert rows[0].metric == "5-Hour Limit" and "expired" not in rows[0].note


def test_valid_token_is_left_alone(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("tok", (NOW + 3600) * 1000)])
    claude.probe_claude({"read_token": True})
    assert refreshes == [] and sent == ["Bearer tok"]


def test_failed_refresh_still_warns(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("old", (NOW - 3600) * 1000)], refresh_ok=False)
    rows = claude.probe_claude({"read_token": True})
    assert refreshes == [{"read_token": True}] and sent == ["Bearer old"]
    assert "token expired" in rows[0].note


def fake_claude(path: Path, rc=0) -> Path:
    """A `claude` that appends its arguments to <its dir>/runs.log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "$0 $*" >> "{path.parent}/runs.log"\nexit {rc}\n')
    path.chmod(0o755)
    return path


def runs(path: Path) -> list[str]:
    log = path.parent / "runs.log"
    return log.read_text().splitlines() if log.exists() else []


def test_runs_known_location_not_path(home, tmp_path, monkeypatch):
    known = fake_claude(home / ".local" / "bin" / "claude")
    on_path = fake_claude(tmp_path / "bin" / "claude")
    monkeypatch.setattr(claude, "CLAUDE_LOCATIONS", ("~/.local/bin/claude",))
    assert claude._refresh_claude_token({}) is True
    assert runs(known) == [f"{known} auth status"] and runs(on_path) == []


def test_symlink_runs_its_resolved_target(home, tmp_path, monkeypatch):
    target = fake_claude(home / ".local" / "share" / "claude" / "versions" / "2.1.0")
    link = home / ".local" / "bin" / "claude"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setattr(claude, "CLAUDE_LOCATIONS", ("~/.local/bin/claude",))
    assert claude._refresh_claude_token({}) is True
    assert runs(target) == [f"{target} auth status"]


def test_claude_cmd_overrides_known_locations(tmp_path):
    known = fake_claude(tmp_path / "bin" / "claude")
    chosen = fake_claude(tmp_path / "opt" / "claude")
    assert claude._refresh_claude_token({"claude_cmd": str(chosen)}) is True
    assert runs(chosen) == [f"{chosen} auth status"] and runs(known) == []


def test_missing_claude_cmd_falls_back_to_nothing(tmp_path, capsys):
    known = fake_claude(tmp_path / "bin" / "claude")
    assert claude._refresh_claude_token({"claude_cmd": str(tmp_path / "nope")}) is False
    assert runs(known) == [] and "not an executable file" in capsys.readouterr().err


def test_runs_binary_its_private_group_can_write(monkeypatch, tmp_path):
    """A umask 002 install (~/.local/bin is 0775, group = the user's own) still runs."""
    monkeypatch.setattr(util, "_private_group", lambda gid: True)
    cli = fake_claude(tmp_path / "bin" / "claude")
    os.chmod(cli.parent, 0o775)
    cli.chmod(0o775)
    assert claude._refresh_claude_token({}) is True and runs(cli) == [f"{cli} auth status"]


def test_skips_binary_a_shared_group_can_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(util, "_private_group", lambda gid: False)
    cli = fake_claude(tmp_path / "bin" / "claude")
    cli.chmod(0o775)
    assert claude._refresh_claude_token({}) is False
    assert runs(cli) == [] and "writable by other users" in capsys.readouterr().err


def test_skips_binary_others_can_write(tmp_path, capsys):
    cli = fake_claude(tmp_path / "bin" / "claude")
    cli.chmod(0o777)
    assert claude._refresh_claude_token({}) is False
    assert runs(cli) == [] and "writable by other users" in capsys.readouterr().err


def test_skips_directory_others_can_write(tmp_path, capsys):
    cli = fake_claude(tmp_path / "shared" / "claude")
    os.chmod(cli.parent, 0o777)
    assert claude._refresh_claude_token({"claude_cmd": str(cli)}) is False
    assert runs(cli) == [] and "writable by other users" in capsys.readouterr().err


def test_failed_refresh_backs_off(tmp_path, monkeypatch):
    cli = fake_claude(tmp_path / "bin" / "claude", rc=1)
    now = [NOW]
    monkeypatch.setattr(time, "time", lambda: now[0])
    assert claude._refresh_claude_token({}) is False
    now[0] += 300  # the extension's next fetch
    assert claude._refresh_claude_token({}) is False
    assert len(runs(cli)) == 1
    now[0] += claude.CLAUDE_REFRESH_BACKOFF
    assert claude._refresh_claude_token({}) is False
    assert len(runs(cli)) == 2


def test_success_clears_the_backoff(home, tmp_path, monkeypatch):
    cli = fake_claude(tmp_path / "bin" / "claude", rc=1)
    now = [NOW]
    monkeypatch.setattr(time, "time", lambda: now[0])
    claude._refresh_claude_token({})
    fake_claude(cli)  # now succeeds
    now[0] += claude.CLAUDE_REFRESH_BACKOFF
    assert claude._refresh_claude_token({}) is True
    assert not (home / ".cache" / "aiu" / "claude-refresh-failed").exists()
    assert claude._refresh_claude_token({}) is True
    assert len(runs(cli)) == 3


def fake_accounts(monkeypatch, groups, users):
    """groups: {gid: (name, members)}, users: {name: primary gid}; we are "me"."""
    def getgrgid(gid):
        if gid not in groups:
            raise KeyError(gid)
        return SimpleNamespace(gr_name=groups[gid][0], gr_mem=groups[gid][1])
    pw = [SimpleNamespace(pw_name=n, pw_gid=g) for n, g in users.items()]
    monkeypatch.setattr(grp, "getgrgid", getgrgid)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pw[0])
    monkeypatch.setattr(pwd, "getpwall", lambda: pw)


@pytest.mark.parametrize("groups, users, private", [
    ({1000: ("me", [])}, {"me": 1000}, True),
    ({1000: ("me", ["me"])}, {"me": 1000}, True),
    ({1000: ("me", ["bob"])}, {"me": 1000}, False),          # another member
    ({1000: ("me", [])}, {"me": 1000, "bob": 1000}, False),  # bob's primary group too
    ({1000: ("staff", [])}, {"me": 1000}, False),            # not named after us
    ({}, {"me": 1000}, False),                               # unknown gid
])
def test_private_group(monkeypatch, groups, users, private):
    fake_accounts(monkeypatch, groups, users)
    assert util._private_group(1000) is private
