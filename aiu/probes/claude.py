# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Claude Max."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from aiu.i18n import tr
from aiu.probes.common import _humanize_reset, _iso_epoch, _json_object, _malformed_note, _network_note
from aiu.rows import Row
from aiu.util import _writable_by_others


ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_QUOTA_LABELS = {
    "five_hour": "5-Hour Limit",
    "seven_day": "Weekly (all models)",
    "seven_day_sonnet": "Weekly Sonnet",
    "monthly_limit": "Monthly Limit",
    "extra_usage": "Extra Usage",
}
CLAUDE_WINDOW = {  # window length in seconds, for advise/alert
    "five_hour": 5 * 3600,
    "seven_day": 7 * 86400,
    "seven_day_sonnet": 7 * 86400,
    "monthly_limit": 30 * 86400,
    "extra_usage": 30 * 86400,
}


def _read_claude_token() -> tuple[str | None, int | None]:
    """Return (access_token, expires_at_ms) from the local Claude Code creds."""
    p = Path(os.path.expanduser("~/.claude/.credentials.json"))
    if not p.is_file():
        return None, None
    try:
        d = json.loads(p.read_text())
        oauth = d.get("claudeAiOauth", {})
        return oauth.get("accessToken"), oauth.get("expiresAt")
    except Exception:
        return None, None


# Where the installers put `claude`, in order: native installer, the old
# local npm install, then system-wide ones. `claude_cmd` overrides the list.
CLAUDE_LOCATIONS = ("~/.local/bin/claude", "~/.claude/local/claude",
                    "/usr/local/bin/claude", "/usr/bin/claude", "/opt/homebrew/bin/claude")
CLAUDE_REFRESH_BACKOFF = 3600  # seconds without retrying after a failed refresh


def _refresh_claude_token(cfg: dict | None = None) -> bool:
    """Have the CLI refresh ~/.claude/.credentials.json. Any `claude` start does
    it, `auth status` without spending a request; the desktop app keeps its own
    token and never rewrites the file, so it goes stale 8h after the last CLI run.
    Clean environment: a host Claude session's variables (ANTHROPIC_BASE_URL,
    host-managed auth) must not steer the child.

    The binary is never taken from PATH (gnome-shell's, when run from the
    extension): only `claude_cmd` from the [claude] config, or else the first
    known install location. It is resolved once to its real path, skipped when
    someone else can write it or its directory, and that exact file is run.
    A failed run is not retried for CLAUDE_REFRESH_BACKOFF seconds.
    It can race an interactive `claude` over the same refresh token; the
    backoff keeps that to one attempt per stale period."""
    marker = Path(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")) \
        / "aiu" / "claude-refresh-failed"
    try:
        if time.time() - marker.stat().st_mtime < CLAUDE_REFRESH_BACKOFF:
            return False
    except OSError:
        pass
    configured = (cfg or {}).get("claude_cmd")
    candidates = [configured] if configured else CLAUDE_LOCATIONS
    exe = None
    for c in candidates:
        path = Path(os.path.expanduser(str(c)))
        real = Path(os.path.realpath(path))
        if not (real.is_file() and os.access(real, os.X_OK)):
            if configured:
                print(tr("aiu: claude_cmd {path} is not an executable file", path=path),
                      file=sys.stderr)
            continue
        # The link, its target and both directories: none writable by others.
        try:
            safe = all((st.st_uid in (0, os.getuid()) and not _writable_by_others(st))
                       for st in (os.stat(p) for p in {path.parent, real, real.parent}))
        except OSError:
            safe = False
        if safe:
            exe = str(real)
        else:
            print(tr("aiu: not running {path}: writable by other users", path=path),
                  file=sys.stderr)
        break
    if not exe:
        return False
    env = {k: os.environ[k] for k in ("HOME", "PATH", "USER", "LANG") if k in os.environ}
    try:
        ok = subprocess.run([exe, "auth", "status"], env=env, capture_output=True,
                            timeout=60).returncode == 0
    except Exception:
        ok = False
    try:
        if ok:
            marker.unlink(missing_ok=True)
        else:
            marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            marker.touch(mode=0o600)
            os.utime(marker, (time.time(),) * 2)
    except OSError:
        pass
    return ok


def probe_claude(cfg: dict) -> list[Row]:
    token, expires_at = _read_claude_token()
    if token and expires_at and expires_at / 1000 < time.time() + 60 and _refresh_claude_token(cfg):
        token, expires_at = _read_claude_token()
    if not token:
        return [Row("claude", "—", "error",
                    note="no token in ~/.claude/.credentials.json (run `claude` once)")]
    warn = ""
    if expires_at and expires_at / 1000 < time.time():
        warn = "token expired — run `claude` to refresh; "
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.1.69",
    }
    try:
        r = httpx.get(ANTHROPIC_USAGE_URL, headers=headers, timeout=30)
    except Exception as e:
        return [Row("claude", "—", "error", note=warn + _network_note(e))]
    if r.status_code in (401, 403):
        return [Row("claude", "—", "error",
                    note=f"{warn}auth rejected ({r.status_code}) — run `claude` to refresh")]
    if r.status_code != 200:
        return [Row("claude", "—", "error", note=f"{warn}HTTP {r.status_code}")]
    rows: list[Row] = []
    try:
        data = _json_object(r)
        for key, label in CLAUDE_QUOTA_LABELS.items():
            entry = data.get(key)
            if not entry or entry.get("utilization") is None:
                continue
            if entry.get("is_enabled") is False:
                continue
            reset = entry.get("resets_at")
            reset_h = _humanize_reset(reset) if reset else None
            # API reports utilization already as a 0-100 percentage (Claude console
            # "1% used" == API value 1.0). Store as a 0-1 fraction internally.
            rows.append(Row("claude", label, "quota",
                            used=float(entry["utilization"]) / 100.0, limit=1.0, unit="%",
                            reset=reset_h, note=warn.strip(),
                            reset_epoch=_iso_epoch(reset) if reset else None,
                            window_secs=CLAUDE_WINDOW.get(key)))
        rows.extend(_claude_credits(data, warn))
    except Exception as e:
        return [Row("claude", "—", "error", note=warn + _malformed_note(e))]
    if not rows:
        rows.append(Row("claude", "—", "quota", note=f"{warn}no active quotas reported"))
    return rows


# Promo dollar grants sit under codenamed keys that change without notice
# (the Nov-2026 cloud-session credit is "iguana_necktie"), so match on shape.
CLAUDE_CREDIT_LABELS = {"iguana_necktie": "Cloud Credit"}


def _claude_credits(data: dict, warn: str) -> list[Row]:
    rows = []
    for key, entry in data.items():
        if not isinstance(entry, dict) or entry.get("limit_dollars") is None:
            continue
        label = CLAUDE_CREDIT_LABELS.get(key, f"Credit ({key})")
        limit = float(entry["limit_dollars"])
        left = entry.get("remaining_dollars")
        if left is None and entry.get("used_dollars") is not None:
            left = limit - float(entry["used_dollars"])
        if left is None:  # unknown spend: never assume the grant is untouched
            rows.append(Row("claude", label, "error",
                            note=warn + "malformed response: no `remaining_dollars` or `used_dollars`"))
            continue
        reset = entry.get("resets_at")
        note = warn + (f"locked: {entry['locked_reason']}" if entry.get("locked_reason") else "")
        rows.append(Row("claude", label, "credit",
                        used=round(float(left), 2), limit=limit, unit="USD",
                        reset=f"expires {_humanize_reset(reset)}" if reset else None,
                        note=note.strip(),
                        reset_epoch=_iso_epoch(reset) if reset else None))
    return rows


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "claude"
OPTIONS = {"enabled": ("bool", False), "claude_cmd": ("string", None)}
probe = probe_claude
