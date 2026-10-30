# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Mistral (monthly included allowances: Vibe Code + API/Studio).

Every plan, Free included, gets two € allowances reset on the 1st of the
calendar month. No API key reads them (Oct 2026: the Vibe key only opens
/api/vibe/whoami); the admin console's Usage page calls tRPC endpoints that
accept the browser session cookie only. They return token counts per billing
metric and a price list; € spent is their product, which matches the
dashboard's "Total Estimated Cost" to the cent."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from aiu.keys import KEY_SOURCES, resolve_key
from aiu.probes.common import Malformed, _humanize_reset, _json_object, _malformed_note, _network_note
from aiu.rows import Row


MISTRAL_TRPC = "https://admin.mistral.ai/api/local-trpc/"
MISTRAL_ALLOWANCE_EUR = 8.5  # each allowance, Free plan; override per config
# usage_type -> price eventType; anything unlisted is priced per token.
MISTRAL_EVENT = {
    "ocr": "api_pages", "vibe_ocr": "api_pages",
    "audio_seconds": "api_audio_seconds", "vibe_audio_seconds": "api_audio_seconds",
    "audio_characters": "api_audio_characters", "vibe_audio_characters": "api_audio_characters",
    "connectors": "api_connectors", "vibe_connectors": "api_connectors",
    "libraries_tokens": "api_libraries_tokens", "libraries_pages": "api_libraries_pages",
    "libraries_audio": "api_libraries_audio",
}


def _mistral_trpc(proc: str, payload: dict, cookie: str) -> dict:
    r = httpx.get(MISTRAL_TRPC + proc, params={"input": json.dumps(payload)},
                  headers={"Cookie": cookie, "Accept": "application/json", "User-Agent": "aiu/1.0"},
                  timeout=30, follow_redirects=False)
    if r.is_redirect or r.status_code in (401, 403):  # no session -> login redirect
        raise PermissionError(r.status_code)
    r.raise_for_status()
    try:
        data = _json_object(r)["result"]["data"]["json"]
    except (KeyError, TypeError):
        raise Malformed("not a tRPC result") from None
    if not isinstance(data, dict):
        raise Malformed("not a tRPC result")
    return data


def _mistral_spend(groups: list[dict], prices: list[dict]) -> tuple[dict[str, float], int]:
    """€ spent per allowance ("vibe" | "api"), and how many usage groups had no price."""
    table = {(p["billingMetric"], p["billingGroup"], p["apiZone"], p["serviceTier"], p["eventType"]):
             p["price"] for p in prices}
    spend = {"vibe": 0.0, "api": 0.0}
    unpriced = 0
    for g in groups:
        ut = g.get("usageType") or ""
        price = table.get((g["billingMetric"], g["billingGroup"], g.get("apiZone"),
                           g.get("serviceTier"), MISTRAL_EVENT.get(ut, "api_tokens")))
        if price is None:
            unpriced += bool(g.get("value"))
            continue
        spend["vibe" if ut.startswith("vibe") else "api"] += price * float(g.get("value") or 0)
    return spend, unpriced


def probe_mistral(cfg: dict) -> list[Row]:
    cookie = resolve_key({"key_env": "MISTRAL_ADMIN_COOKIE", **cfg}, "mistral")
    if not cookie:
        return [Row("mistral", "—", "error", note="no session cookie (set MISTRAL_ADMIN_COOKIE)")]
    now = datetime.now()
    # Naive local -> aware: the console counts months in local time, DST included.
    start = datetime(now.year, now.month, 1).astimezone()
    reset = datetime(now.year + now.month // 12, now.month % 12 + 1, 1).astimezone()
    utc = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    span = {"json": {"start": utc(start), "end": utc(now.astimezone()), "untracked": False},
            "meta": {"values": {"start": ["Date"], "end": ["Date"]}, "v": 1}}
    try:
        usage = _mistral_trpc("usage.costBreakdown", span, cookie)
        prices = _mistral_trpc("usage.prices", {"json": {"workspaceId": None}}, cookie)
    except PermissionError as e:
        return [Row("mistral", "—", "error",
                    note=f"session rejected ({e.args[0]}) — copy a fresh Cookie header from admin.mistral.ai")]
    except httpx.HTTPStatusError as e:
        return [Row("mistral", "—", "error", note=f"HTTP {e.response.status_code}")]
    except Malformed as e:
        return [Row("mistral", "—", "error", note=_malformed_note(e))]
    except Exception as e:
        return [Row("mistral", "—", "error", note=_network_note(e))]
    try:
        spend, unpriced = _mistral_spend(usage.get("groups") or [], prices.get("prices") or [])
    except Exception as e:
        return [Row("mistral", "—", "error", note=_malformed_note(e))]
    warn = (f" ; {unpriced} unpriced group(s)" if unpriced else "") + \
           (" ; partial (more pages)" if (usage.get("metadata") or {}).get("hasMore") else "")
    rows = []
    for metric, kind in (("Vibe Monthly", "vibe"), ("API Monthly", "api")):
        allowance = float(cfg.get(f"{kind}_allowance_eur", MISTRAL_ALLOWANCE_EUR))
        if allowance <= 0:
            continue
        rows.append(Row("mistral", metric, "quota",
                        used=spend[kind] / allowance, limit=1.0, unit="%",
                        reset=_humanize_reset(reset.isoformat()),
                        note=f"€{spend[kind]:.2f} / €{allowance:.2f}" + warn,
                        reset_epoch=reset.timestamp(),
                        window_secs=int((reset - start).total_seconds())))
    return rows


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "mistral"
OPTIONS = {"enabled": ("bool", False), **KEY_SOURCES,
           "key_env": ("string", "MISTRAL_ADMIN_COOKIE"),
           "vibe_allowance_eur": ("number", MISTRAL_ALLOWANCE_EUR),
           "api_allowance_eur": ("number", MISTRAL_ALLOWANCE_EUR)}
probe = probe_mistral
