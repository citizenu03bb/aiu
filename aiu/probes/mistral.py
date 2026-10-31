# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Mistral (monthly included allowances: Vibe Code + API/Studio).

Every plan, Free included, gets two € allowances reset on the 1st of the
calendar month. No API key reads them (Oct 2026: the Vibe key only opens
/api/vibe/whoami); the admin console's Usage page calls tRPC endpoints that
accept the browser session cookie only. They return token counts per billing
metric and a price list; € spent is their product, which matches the
dashboard's "Total Estimated Cost" to the cent.

The plan ("Free", "Pro") is asked of Mistral Vibe, as Codex's is of Codex:
`vibe-app-server` (Vibe's JSON-RPC server on stdio) answers `account/read`
for a throwaway session that Vibe neither saves nor counts, with the sign-in
Vibe keeps in the keyring. aiu keeps the plan's name and nothing else of the
answer, at most every VIBE_PLAN_TTL seconds."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone

import httpx

from aiu import __version__
from aiu.clis import cached, find_cli, spawn
from aiu.i18n import tr
from aiu.keys import KEY_SOURCES, resolve_key
from aiu.probes.common import (Malformed, _humanize_reset, _json_object, _malformed_note, _network_note,
                               _plan_name, _with_plan)
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


# Where `uv tool install mistral-vibe` (and pipx) put vibe-app-server, then
# system-wide; `vibe_cmd` overrides the list, PATH is never read.
VIBE_LOCATIONS = ("~/.local/bin/vibe-app-server", "/usr/local/bin/vibe-app-server",
                  "/usr/bin/vibe-app-server", "/opt/homebrew/bin/vibe-app-server")
VIBE_ENV = ("DBUS_SESSION_BUS_ADDRESS",)  # Vibe reads its key from the keyring
VIBE_TIMEOUT = 30       # seconds for the whole exchange (measured: 3.5 s)
VIBE_MAX_LINE = 1 << 20
VIBE_MAX_LINES = 1000   # notifications skipped before giving up on the answer
VIBE_PLAN_CACHE = "mistral-plan"
VIBE_PLAN_TTL = 6 * 3600
VIBE_PLAN_TTL_FAILED = 3600
INIT_ID, START_ID, READ_ID = 1, 2, 3


def _vibe_send(proc: subprocess.Popen, mid: int | None, method: str, params: dict) -> None:
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    if mid is not None:
        msg["id"] = mid
    proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def _vibe_plan_name(plan) -> str:
    """Vibe's own title for the plan ("Free", "[Subscription] Pro" -> "Pro"),
    else the plan's name as Mistral's account service gives it ("FREE")."""
    if not isinstance(plan, dict):
        return ""
    title = plan.get("title")
    if isinstance(title, str):
        title = _plan_name(re.sub(r"^\[[^\]]{0,32}\]\s*", "", title))
        if title:
            return title
    return _plan_name(plan.get("name"))


def _ask_vibe_plan(exe: str) -> tuple[str, float]:
    """The plan of account/read, or "" (Vibe signed out, an error, no answer),
    with its time-to-live for the cache. stdin stays open until the answer:
    the server exits on EOF."""
    plan = ""
    try:
        with tempfile.TemporaryDirectory(prefix="aiu-vibe-") as cwd, \
                spawn([exe], timeout=VIBE_TIMEOUT, keep_env=VIBE_ENV, cwd=cwd) as proc:
            _vibe_send(proc, INIT_ID, "initialize", {"clientInfo": {"name": "aiu", "version": __version__}})
            _vibe_send(proc, None, "initialized", {})
            _vibe_send(proc, START_ID, "session/start", {"kind": "ephemeral", "agentConfig": {"cwd": cwd}})
            for _ in range(VIBE_MAX_LINES):
                line = proc.stdout.readline(VIBE_MAX_LINE)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or "method" in msg or isinstance(msg.get("id"), bool):
                    continue  # a notification, or a request to the client
                result = msg.get("result")
                if "error" in msg or not isinstance(result, dict):
                    if msg.get("id") in (INIT_ID, START_ID, READ_ID):
                        break
                    continue
                if msg["id"] == START_ID:
                    state = result.get("state")
                    session = state.get("session") if isinstance(state, dict) else None
                    sid = session.get("id") if isinstance(session, dict) else None
                    if not isinstance(sid, str):
                        break
                    _vibe_send(proc, READ_ID, "account/read", {"sessionId": sid})
                elif msg["id"] == READ_ID:
                    account = result.get("account")
                    if isinstance(account, dict) and account.get("status") == "ready":
                        plan = _vibe_plan_name(account.get("plan"))
                    break
            proc.stdin.close()  # done: let it end the session and exit before the kill
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    except OSError:
        pass
    return plan, VIBE_PLAN_TTL if plan else VIBE_PLAN_TTL_FAILED


def _vibe_plan(cfg: dict) -> str:
    exe = find_cli("vibe_cmd", cfg, VIBE_LOCATIONS)
    return _plan_name(cached(VIBE_PLAN_CACHE, lambda: _ask_vibe_plan(exe))) if exe else ""


def probe_mistral(cfg: dict) -> list[Row]:
    # No default key_env: the session comes from the keyring (key_cmd, set by
    # `aiu cookie import mistral`) or from a source the user names.
    cookie = resolve_key(cfg, "mistral")
    if not cookie:
        return [Row("mistral", "—", "error", note=tr("no session: run `aiu cookie import mistral`"))]
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
    return _with_plan(rows, _vibe_plan(cfg)) if rows else rows


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "mistral"
OPTIONS = {"enabled": ("bool", False), **KEY_SOURCES,
           "vibe_allowance_eur": ("number", MISTRAL_ALLOWANCE_EUR),
           "api_allowance_eur": ("number", MISTRAL_ALLOWANCE_EUR),
           "vibe_cmd": ("string", None)}
probe = probe_mistral
