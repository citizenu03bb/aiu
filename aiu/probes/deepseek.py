# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: DeepSeek (remaining balance)."""
from __future__ import annotations

import httpx

from aiu.keys import KEY_SOURCES, resolve_key
from aiu.probes.common import _json_object, _malformed_note, _need, _network_note
from aiu.rows import Row


def probe_deepseek(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "deepseek")
    if not key:
        return [Row("deepseek", "Balance", "error", note="no key (set DEEPSEEK_API_KEY)")]
    try:
        r = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"}, timeout=30,
        )
    except Exception as e:
        return [Row("deepseek", "Balance", "error", note=_network_note(e))]
    if r.status_code in (401, 403):
        return [Row("deepseek", "Balance", "error", note=f"auth rejected ({r.status_code})")]
    if r.status_code != 200:
        return [Row("deepseek", "Balance", "error", note=f"HTTP {r.status_code}")]
    rows = []
    try:
        data = _json_object(r)
        for info in _need(data, "balance_infos", list):
            rows.append(Row(
                "deepseek", f"Balance ({info.get('currency','?')})", "balance",
                used=float(_need(info, "total_balance")),
                unit=info.get("currency", ""),
                # Only an explicit false: a dropped flag is no reason to alarm.
                note="account not available" if data.get("is_available") is False else "",
            ))
    except Exception as e:
        return [Row("deepseek", "Balance", "error", note=_malformed_note(e))]
    if not rows:
        rows.append(Row("deepseek", "Balance", "balance", note="no balance info returned"))
    return rows


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "deepseek"
OPTIONS = {"enabled": ("bool", False), **KEY_SOURCES}
probe = probe_deepseek
