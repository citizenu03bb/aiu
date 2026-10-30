# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: OpenRouter (total spend + remaining credits)."""
from __future__ import annotations

import httpx

from aiu.keys import KEY_SOURCES, resolve_key
from aiu.probes.common import _json_object, _malformed_note, _need, _network_note
from aiu.rows import Row


def probe_openrouter(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "openrouter")
    if not key:
        return [Row("openrouter", "Credits", "error", note="no key (set OPENROUTER_API_KEY)")]
    try:
        r = httpx.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"}, timeout=30,
        )
    except Exception as e:
        return [Row("openrouter", "Credits", "error", note=_network_note(e))]
    if r.status_code in (401, 403):
        return [Row("openrouter", "Credits", "error", note=f"auth rejected ({r.status_code})")]
    if r.status_code != 200:
        return [Row("openrouter", "Credits", "error", note=f"HTTP {r.status_code}")]
    try:
        d = _need(_json_object(r), "data", dict)
        total = float(_need(d, "total_credits"))
        used = float(_need(d, "total_usage"))
    except Exception as e:
        return [Row("openrouter", "Credits", "error", note=_malformed_note(e))]
    return [
        Row("openrouter", "Spend (total)", "spend", used=round(used, 2), unit="USD"),
        Row("openrouter", "Remaining", "balance", used=round(total - used, 2), unit="USD"),
    ]


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "openrouter"
OPTIONS = {"enabled": ("bool", False), **KEY_SOURCES}
probe = probe_openrouter
