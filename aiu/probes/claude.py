# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Claude Max. Read from what Claude Code itself reports, written by the
aiu-claude-usage plugin (claude-code-plugin/) to a file in aiu's state
directory. Reading Claude Code's own OAuth token and calling the usage
endpoint with it is an opt-in (claude.read_token): Anthropic's terms say
developers may not collect, store, or intermediate Claude.ai credentials."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from aiu.clis import find_cli, run_cli
from aiu.probes.common import (_humanize_reset, _humanize_reset_unix, _iso_epoch, _json_object,
                               _malformed_note, _network_note)
from aiu.rows import Row
from aiu.util import state_dir


ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_QUOTA_LABELS = {
    "five_hour": "5-Hour Limit",
    "seven_day": "Weekly (all models)",
    "seven_day_sonnet": "Weekly Sonnet",
    "seven_day_opus": "Weekly Opus",
    "seven_day_oauth_apps": "Weekly OAuth Apps",
    "seven_day_cowork": "Weekly Cowork",
    "monthly_limit": "Monthly Limit",
    "extra_usage": "Extra Usage",
}
CLAUDE_WINDOW = {  # window length in seconds, for advise/alert
    "five_hour": 5 * 3600,
    "seven_day": 7 * 86400,
    "seven_day_sonnet": 7 * 86400,
    "seven_day_opus": 7 * 86400,
    "seven_day_oauth_apps": 7 * 86400,
    "seven_day_cowork": 7 * 86400,
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

    Which binary runs, and how, is aiu.clis: `claude_cmd` from the [claude]
    config, or else the first of CLAUDE_LOCATIONS, never PATH. A failed run
    is not retried for CLAUDE_REFRESH_BACKOFF seconds. It can race an interactive `claude` over the same refresh token; the
    backoff keeps that to one attempt per stale period."""
    marker = Path(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")) \
        / "aiu" / "claude-refresh-failed"
    try:
        if time.time() - marker.stat().st_mtime < CLAUDE_REFRESH_BACKOFF:
            return False
    except OSError:
        pass
    exe = find_cli("claude_cmd", cfg, CLAUDE_LOCATIONS)
    if not exe:
        return False
    done = run_cli([exe, "auth", "status"], timeout=60)
    ok = done is not None and done[0] == 0
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


# The plugin's file: docs/json-schema.md, "claude-usage.json".
CLAUDE_USAGE_FILE = "claude-usage.json"
CLAUDE_USAGE_MAX_BYTES = 64 * 1024  # a bigger file is not the plugin's
CLAUDE_USAGE_MAX_WINDOWS = 32
# Claude Code writes after a turn that moved a window by a whole point, so a
# reading only ages while Claude Code is idle; usage from claude.ai or another
# machine meanwhile is not in it. Older than this, a row says how old it is,
# and the reading is used only while one of its windows has yet to reset.
CLAUDE_USAGE_STALE = 3600
CLAUDE_PLUGIN_INSTALL = "/plugin install aiu-claude-usage --marketplace citizenu03bb/aiu"


def _iso_time(v) -> float | None:
    return _iso_epoch(v) if isinstance(v, str) and len(v) <= 64 else None


def _read_claude_usage() -> dict | None:
    """The plugin's last reading, {"written_at": epoch, "windows": [(kind,
    percent, reset epoch or None)]} for the windows aiu has a label for, or None
    when the file is missing, too big, not JSON, not schema 1 from claude-code
    in every field, or holds no such window."""
    path = state_dir() / CLAUDE_USAGE_FILE
    try:
        if not path.is_file():  # nor a FIFO left there to hang the probe
            return None
        with open(path, "rb") as f:
            raw = f.read(CLAUDE_USAGE_MAX_BYTES + 1)
        if len(raw) > CLAUDE_USAGE_MAX_BYTES:
            return None
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("source") != "claude-code":
        return None
    written = _iso_time(data.get("written_at"))
    limits = data.get("rate_limits")
    if written is None or written > time.time() + 300 or not isinstance(limits, list) \
            or len(limits) > CLAUDE_USAGE_MAX_WINDOWS:
        return None
    windows = []
    for w in limits:
        if not isinstance(w, dict):
            return None
        kind, pct, reset = w.get("kind"), w.get("percent_used"), w.get("resets_at")
        if not isinstance(kind, str) or not 0 < len(kind) <= 100:
            return None
        if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not 0 <= pct <= 1000:
            return None
        if reset is not None and _iso_time(reset) is None:
            return None
        if kind in CLAUDE_QUOTA_LABELS:  # not, e.g., a gateway's spend_limit
            windows.append((kind, float(pct), _iso_time(reset) if reset is not None else None))
    return {"written_at": written, "windows": windows} if windows else None


def _ago(secs: float) -> str:
    m = int(secs) // 60
    d, h = divmod(m // 60, 24)
    return f"{d}d {h}h" if d else f"{h}h {m % 60}m" if h else f"{m}m"


def _claude_usage_rows(reading: dict) -> list[Row] | None:
    """Rows from the plugin's reading, or None when it is too old to use: past
    CLAUDE_USAGE_STALE with every window reset since. A window that reset since
    the reading is at 0 % with its next reset unknown."""
    now = time.time()
    age = max(0.0, now - reading["written_at"])
    stale = age > CLAUDE_USAGE_STALE
    note = f"Claude Code reading from {_ago(age)} ago" if stale else ""
    rows, ahead = [], False
    for kind, pct, reset in reading["windows"]:
        window = CLAUDE_WINDOW.get(kind)
        passed = reset <= now if reset is not None else bool(window and age >= window)
        ahead |= reset is not None and not passed
        rows.append(Row("claude", CLAUDE_QUOTA_LABELS[kind], "quota",
                        used=0.0 if passed else pct / 100.0, limit=1.0, unit="%",
                        reset=None if passed else _humanize_reset_unix(reset),
                        note=note, reset_epoch=None if passed else reset, window_secs=window))
    return None if stale and not ahead else rows


def probe_claude(cfg: dict) -> list[Row]:
    """The plugin's reading first; the token read only with claude.read_token,
    and then only for what the reading lacks (credits, other windows)."""
    reading = _read_claude_usage()
    rows = _claude_usage_rows(reading) if reading else None
    if not cfg.get("read_token"):
        if rows:
            return rows
        if reading:
            age = _ago(max(0.0, time.time() - reading["written_at"]))
            return [Row("claude", "—", "error",
                        note=f"last Claude Code reading is {age} old: use Claude Code to take "
                             "a new one (or set claude.read_token = true)")]
        return [Row("claude", "—", "error",
                    note=f"no reading from Claude Code: install its aiu plugin "
                         f"({CLAUDE_PLUGIN_INSTALL}), or set claude.read_token = true")]
    token_rows = _probe_claude_token(cfg)
    if not rows:
        return token_rows
    have = {r.metric for r in rows}
    return rows + [r for r in token_rows if r.metric not in have]


def _probe_claude_token(cfg: dict) -> list[Row]:
    """Claude Code's stored OAuth token, refreshed by `claude auth status` when
    stale, sent to the usage endpoint the Claude apps read. Opt-in only."""
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
# NOTICES: what `aiu config set` prints when an option is turned on.
NAME = "claude"
OPTIONS = {"enabled": ("bool", False), "read_token": ("bool", False),
           "claude_cmd": ("string", None)}
probe = probe_claude
NOTICES = {"read_token": (
    "aiu now reads Claude Code's sign-in token (~/.claude/.credentials.json) and sends it to "
    "Anthropic's usage endpoint, as the Claude apps do. Anthropic's terms say: \"developers may "
    "not collect, store, or intermediate Claude.ai credentials or session tokens\" "
    "(code.claude.com/docs/en/legal-and-compliance). The aiu-claude-usage plugin for Claude Code "
    "needs no token: /plugin install aiu-claude-usage --marketplace citizenu03bb/aiu. "
    "To turn this off: aiu config set claude.read_token false")}
