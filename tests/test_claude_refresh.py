# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude probe refreshes a stale CLI token before calling the usage API — no network.
Which `claude` runs, and how often (L4 in docs/security-review.md)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402

NOW = 1793350914.544
USAGE = {"five_hour": {"utilization": 12.0, "resets_at": "2026-10-30T12:00:00+00:00"}}


class Resp:
    status_code = 200

    def json(self):
        return USAGE


def setup(monkeypatch, tokens, refresh_ok=True):
    """tokens: successive (token, expires_at_ms) values the credentials file holds."""
    reads, refreshes, sent = list(tokens), [], []
    monkeypatch.setattr(aiu.time, "time", lambda: NOW)
    monkeypatch.setattr(aiu, "_read_claude_token", lambda: reads.pop(0) if len(reads) > 1 else reads[0])
    monkeypatch.setattr(aiu, "_refresh_claude_token", lambda cfg: refreshes.append(cfg) or refresh_ok)
    monkeypatch.setattr(aiu.httpx, "get",
                        lambda url, headers, timeout: sent.append(headers["Authorization"]) or Resp())
    return refreshes, sent


def test_expired_token_is_refreshed_first(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("old", (NOW - 3600) * 1000), ("new", (NOW + 8 * 3600) * 1000)])
    rows = aiu.probe_claude({"claude_cmd": "/opt/claude"})
    assert refreshes == [{"claude_cmd": "/opt/claude"}] and sent == ["Bearer new"]
    assert rows[0].metric == "5-Hour Limit" and "expired" not in rows[0].note


def test_valid_token_is_left_alone(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("tok", (NOW + 3600) * 1000)])
    aiu.probe_claude({})
    assert refreshes == [] and sent == ["Bearer tok"]


def test_failed_refresh_still_warns(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("old", (NOW - 3600) * 1000)], refresh_ok=False)
    rows = aiu.probe_claude({})
    assert refreshes == [{}] and sent == ["Bearer old"]
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
    monkeypatch.setattr(aiu, "CLAUDE_LOCATIONS", ("~/.local/bin/claude",))
    assert aiu._refresh_claude_token({}) is True
    assert runs(known) == [f"{known} auth status"] and runs(on_path) == []


def test_symlink_runs_its_resolved_target(home, tmp_path, monkeypatch):
    target = fake_claude(home / ".local" / "share" / "claude" / "versions" / "2.1.0")
    link = home / ".local" / "bin" / "claude"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setattr(aiu, "CLAUDE_LOCATIONS", ("~/.local/bin/claude",))
    assert aiu._refresh_claude_token({}) is True
    assert runs(target) == [f"{target} auth status"]


def test_claude_cmd_overrides_known_locations(tmp_path):
    known = fake_claude(tmp_path / "bin" / "claude")
    chosen = fake_claude(tmp_path / "opt" / "claude")
    assert aiu._refresh_claude_token({"claude_cmd": str(chosen)}) is True
    assert runs(chosen) == [f"{chosen} auth status"] and runs(known) == []


def test_missing_claude_cmd_falls_back_to_nothing(tmp_path, capsys):
    known = fake_claude(tmp_path / "bin" / "claude")
    assert aiu._refresh_claude_token({"claude_cmd": str(tmp_path / "nope")}) is False
    assert runs(known) == [] and "not an executable file" in capsys.readouterr().err


def test_skips_binary_others_can_write(tmp_path, capsys):
    cli = fake_claude(tmp_path / "bin" / "claude")
    cli.chmod(0o777)
    assert aiu._refresh_claude_token({}) is False
    assert runs(cli) == [] and "writable by other users" in capsys.readouterr().err


def test_skips_directory_others_can_write(tmp_path, capsys):
    cli = fake_claude(tmp_path / "shared" / "claude")
    os.chmod(cli.parent, 0o777)
    assert aiu._refresh_claude_token({"claude_cmd": str(cli)}) is False
    assert runs(cli) == [] and "writable by other users" in capsys.readouterr().err


def test_failed_refresh_backs_off(tmp_path, monkeypatch):
    cli = fake_claude(tmp_path / "bin" / "claude", rc=1)
    now = [NOW]
    monkeypatch.setattr(aiu.time, "time", lambda: now[0])
    assert aiu._refresh_claude_token({}) is False
    now[0] += 300  # the extension's next fetch
    assert aiu._refresh_claude_token({}) is False
    assert len(runs(cli)) == 1
    now[0] += aiu.CLAUDE_REFRESH_BACKOFF
    assert aiu._refresh_claude_token({}) is False
    assert len(runs(cli)) == 2


def test_success_clears_the_backoff(home, tmp_path, monkeypatch):
    cli = fake_claude(tmp_path / "bin" / "claude", rc=1)
    now = [NOW]
    monkeypatch.setattr(aiu.time, "time", lambda: now[0])
    aiu._refresh_claude_token({})
    fake_claude(cli)  # now succeeds
    now[0] += aiu.CLAUDE_REFRESH_BACKOFF
    assert aiu._refresh_claude_token({}) is True
    assert not (home / ".cache" / "aiu" / "claude-refresh-failed").exists()
    assert aiu._refresh_claude_token({}) is True
    assert len(runs(cli)) == 3
