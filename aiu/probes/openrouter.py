# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: OpenRouter (spend + remaining credits).

GET /api/v1/key describes the key itself (openrouter.ai/docs/api/reference/limits):
its spending cap (`limit`, null when it has none), what is left of it
(`limit_remaining`), its spend (`usage`, `usage_daily`) and whether the
account ever bought credits (`is_free_tier`). A key with a cap can spend no
more than `limit_remaining`. A key without one spends the account's credits,
which only GET /api/v1/credits reports (documented for management keys); that
endpoint is also the fallback when /key turns the key away."""
from __future__ import annotations

import httpx

from aiu.keys import KEY_SOURCES, resolve_key
from aiu.probes.common import _json_object, _malformed_note, _need, _network_note
from aiu.rows import Row

OPENROUTER_KEY = "https://openrouter.ai/api/v1/key"
OPENROUTER_CREDITS = "https://openrouter.ai/api/v1/credits"
NUMBER = (int, float)


def _error(note: str) -> list[Row]:
    return [Row("openrouter", "Credits", "error", note=note)]


def _get(url: str, key: str) -> httpx.Response:
    return httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=30)


def _status_note(r: httpx.Response) -> str:
    if r.status_code in (401, 403):
        return f"auth rejected ({r.status_code})"
    return f"HTTP {r.status_code}"


def _credits(key: str) -> tuple[float, float] | str:
    """(total_credits, total_usage) of the account, else an error note."""
    try:
        r = _get(OPENROUTER_CREDITS, key)
    except Exception as e:
        return _network_note(e)
    if r.status_code != 200:
        return _status_note(r)
    try:
        d = _need(_json_object(r), "data", dict)
        return float(_need(d, "total_credits")), float(_need(d, "total_usage"))
    except Exception as e:
        return _malformed_note(e)


def _account_rows(total: float, used: float, note: str = "") -> list[Row]:
    return [
        Row("openrouter", "Spend (total)", "spend", used=round(used, 2), unit="USD"),
        Row("openrouter", "Remaining", "balance", used=round(total - used, 2), unit="USD", note=note),
    ]


def probe_openrouter(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "openrouter")
    if not key:
        return _error("no key (aiu login openrouter, or set OPENROUTER_API_KEY)")
    try:
        r = _get(OPENROUTER_KEY, key)
    except Exception as e:
        return _error(_network_note(e))
    if r.status_code in (401, 403, 404):
        # A key /key does not describe (a management key): the account totals.
        got = _credits(key)
        return _error(got) if isinstance(got, str) else _account_rows(*got)
    if r.status_code != 200:
        return _error(_status_note(r))
    try:
        d = _need(_json_object(r), "data", dict)
        usage = float(_need(d, "usage", NUMBER))
        limit = d.get("limit")
        if limit is not None:
            limit = float(_need(d, "limit", NUMBER))
            remaining = float(_need(d, "limit_remaining", NUMBER))
        daily = d.get("usage_daily")
        daily = float(daily) if isinstance(daily, NUMBER) and not isinstance(daily, bool) else None
    except Exception as e:
        return _error(_malformed_note(e))
    free = " (free tier)" if d.get("is_free_tier") is True else ""
    if limit is None:
        got = _credits(key)
        if not isinstance(got, str):
            return _account_rows(*got, note="free tier" if free else "")
        # Neither a cap nor the account's balance: this key's spend only.
        note = "this key; no limit, account balance not readable" + free
        if daily is not None:
            note += f"; today {daily:.2f} USD"
        return [Row("openrouter", "Spend (total)", "spend", used=round(usage, 2), unit="USD",
                    note=note)]
    reset = d.get("limit_reset")
    cap = f"key limit {limit:.2f} USD" + (f", resets {reset}" if isinstance(reset, str) and reset else "")
    spend_note = "this key" + (f"; today {daily:.2f} USD" if daily is not None else "")
    return [
        Row("openrouter", "Spend (total)", "spend", used=round(usage, 2), unit="USD", note=spend_note),
        Row("openrouter", "Remaining", "balance", used=round(remaining, 2), unit="USD",
            note=cap + free),
    ]


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "openrouter"
OPTIONS = {"enabled": ("bool", False), **KEY_SOURCES}
probe = probe_openrouter
