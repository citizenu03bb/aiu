# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: OpenAI (month-to-date spend via Costs API; needs Admin key)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from aiu.keys import KEY_SOURCES, resolve_key
from aiu.probes.common import _json_object, _malformed_note, _need, _network_note
from aiu.rows import Row


def _month_start_epoch() -> int:
    now = datetime.now(timezone.utc)
    return int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())


def probe_openai(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "openai")
    if not key:
        return [Row("openai", "Spend (MTD)", "error", note="no admin key (set OPENAI_ADMIN_KEY)")]
    start = _month_start_epoch()
    total = 0.0
    page = None
    for _ in range(12):  # safety bound on pagination
        params = {"start_time": start, "bucket_width": "1d", "limit": 31}
        if page:
            params["page"] = page
        try:
            r = httpx.get(
                "https://api.openai.com/v1/organizations/costs",
                headers={"Authorization": f"Bearer {key}"},
                params=params, timeout=30,
            )
        except Exception as e:
            return [Row("openai", "Spend (MTD)", "error", note=_network_note(e))]
        if r.status_code in (401, 403):
            return [Row("openai", "Spend (MTD)", "error",
                        note=f"auth rejected ({r.status_code}) — needs an ADMIN key")]
        if r.status_code != 200:
            return [Row("openai", "Spend (MTD)", "error", note=f"HTTP {r.status_code}")]
        try:
            body = _json_object(r)
            for bucket in _need(body, "data", list):
                for res in _need(bucket, "results", list):
                    total += float(_need(res, "amount", dict).get("value") or 0)
            page = body.get("next_page") if body.get("has_more") else None
        except Exception as e:
            return [Row("openai", "Spend (MTD)", "error", note=_malformed_note(e))]
        if not page:
            break
    return [Row("openai", "Spend (MTD)", "spend", used=round(total, 2), unit="USD")]


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "openai"
OPTIONS = {"enabled": ("bool", False), **KEY_SOURCES}
probe = probe_openai
