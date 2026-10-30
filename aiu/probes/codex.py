# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Codex / ChatGPT subscription (used by Codex CLI and Pi — OAuth, no API key)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from aiu.probes.common import _humanize_reset_unix, _json_object, _malformed_note, _network_note
from aiu.rows import Row


CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _read_codex_token() -> tuple[str | None, str | None]:
    p = Path(os.path.expanduser("~/.codex/auth.json"))
    if not p.is_file():
        return None, None
    try:
        t = json.loads(p.read_text()).get("tokens", {})
        return t.get("access_token"), t.get("account_id")
    except Exception:
        return None, None


def probe_codex(cfg: dict) -> list[Row]:
    token, account = _read_codex_token()
    if not token:
        return [Row("codex", "—", "error",
                    note="no token in ~/.codex/auth.json (run `codex` to sign in)")]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "aiu/1.0",
    }
    if account:
        headers["X-Account-Id"] = account
        headers["chatgpt-account-id"] = account
    try:
        r = httpx.get(CODEX_USAGE_URL, headers=headers, timeout=15)
    except Exception as e:
        return [Row("codex", "—", "error", note=_network_note(e))]
    if r.status_code in (401, 403):
        return [Row("codex", "—", "error",
                    note=f"auth rejected ({r.status_code}) — run `codex` to refresh")]
    if r.status_code != 200:
        return [Row("codex", "—", "error", note=f"HTTP {r.status_code}")]
    rows: list[Row] = []
    try:
        data = _json_object(r)
        plan = data.get("plan_type", "")
        rl = data.get("rate_limit", {}) or {}
        for key, win in (("primary_window", rl.get("primary_window")),
                         ("secondary_window", rl.get("secondary_window"))):
            if not win or win.get("used_percent") is None:
                continue
            secs = win.get("limit_window_seconds") or 0
            label = "5-Hour Limit" if secs and secs <= 6 * 3600 else \
                    "Weekly Limit" if secs else key
            rows.append(Row("codex", label, "quota",
                            used=float(win["used_percent"]) / 100.0, limit=1.0, unit="%",
                            reset=_humanize_reset_unix(win.get("reset_at")),
                            note=plan if not rows else "",
                            reset_epoch=float(win["reset_at"]) if win.get("reset_at") else None,
                            window_secs=int(secs) or None))
        # The Codex CLI decodes `balance` as a string ("9.99"); take a number too.
        credits = (data.get("credits") or {}).get("balance")
    except Exception as e:
        return [Row("codex", "—", "error", note=_malformed_note(e))]
    if isinstance(credits, (int, float, str)) and not isinstance(credits, bool) and credits != "":
        try:
            rows.append(Row("codex", "Credits", "balance", used=round(float(credits), 2), unit="USD"))
        except ValueError:
            rows.append(Row("codex", "Credits", "error", note="malformed response: `balance` is not a number"))
    if not rows:
        rows.append(Row("codex", "—", "quota", note=f"plan {plan}: no active windows"))
    return rows


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "codex"
OPTIONS = {"enabled": ("bool", False)}
probe = probe_codex
