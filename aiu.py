#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "rich>=13.7",
# ]
# ///
# SPDX-License-Identifier: GPL-3.0-or-later
"""
aiu — one local snapshot of every AI subscription/quota you pay for.

Sources (each is a pluggable probe):
  - claude_max : Claude Max session quotas (5h / weekly / monthly), read from the
                 same OAuth-usage endpoint Claude Code uses. Token is read locally
                 from ~/.claude/.credentials.json. Nothing leaves your machine
                 except the authenticated call to Anthropic. Also reports promo
                 dollar credits (e.g. the cloud-session credit) found in the same
                 response.
  - codex      : ChatGPT/Codex subscription quotas (5h / weekly), OAuth token
                 read from ~/.codex/auth.json.
  - openai     : month-to-date USD spend via the Costs API (needs an Admin key).
  - deepseek   : remaining balance via /user/balance.
  - openrouter : total spend + remaining credits.
  - gemini     : Gemini Code Assist quota per model family, OAuth token read
                 from ~/.gemini/oauth_creds.json.

Usage:
  ./aiu.py                 # pretty table
  ./aiu.py --json          # machine-readable
  ./aiu.py --html out.html # local HTML page
  ./aiu.py --only claude_max,deepseek
  ./aiu.py --config ~/.config/ai-usage/config.toml

Config lives at ~/.config/ai-usage/config.toml (a template is written on first run).
No secret is ever stored in this script; keys are resolved at runtime from env,
a file, or a shell command — your choice, per provider.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

CONFIG_DIR = Path(os.path.expanduser("~/.config/ai-usage"))
CONFIG_PATH = CONFIG_DIR / "config.toml"
PRICES_PATH = CONFIG_DIR / "prices.toml"

CONFIG_TEMPLATE = """\
# aiu config — enable the providers you use, tell aiu how to find each key.
# Key resolution per provider, in priority order:
#   key_env  = "NAME"          -> read environment variable NAME
#   key_cmd  = "shell command" -> run it, use trimmed stdout (great for pulling
#                                 a key out of an existing CLI config)
#   key_file = "/path"         -> read whole file (trimmed)
# Claude Max needs no key here: the OAuth token is read from
# ~/.claude/.credentials.json automatically.

[claude_max]
enabled = true

# Codex CLI + Pi sign in via ChatGPT OAuth (no API key) -> subscription quota,
# like claude_max. Token read from ~/.codex/auth.json.
[codex]
enabled = true

[openai]
# Only enable if you actually use the OpenAI *API* (not just Codex/ChatGPT).
# Costs API requires an ADMIN key (starts with sk-admin-...), not a normal sk- key.
enabled = false
key_env = "OPENAI_ADMIN_KEY"
# key_cmd = "pass show openai/admin"

[deepseek]
enabled = false
key_env = "DEEPSEEK_API_KEY"
# key_cmd = "jq -r .api_key ~/.config/deepseek/config.json"

[openrouter]
enabled = false
key_env = "OPENROUTER_API_KEY"

# Note: Cohere and Mistral expose NO aggregate usage/spend endpoint (dashboard
# only), so they can't be polled as a simple number without local token accounting.

# Gemini CLI signs in with Google OAuth (Code Assist / Google One AI Pro) ->
# subscription quota, token read from ~/.gemini/oauth_creds.json. No key needed.
[gemini]
enabled = false
"""


# ---------------------------------------------------------------------------
# Normalized record every probe returns
# ---------------------------------------------------------------------------
@dataclass
class Row:
    provider: str
    metric: str                 # human label, e.g. "5-Hour Limit", "Spend (MTD)"
    kind: str                   # "quota" | "spend" | "balance" | "credit" | "error"
    used: float | None = None   # quota: 0..1 ; spend/balance: amount ; credit: amount left
    limit: float | None = None  # quota: 1.0 ; spend: optional budget ; credit: grant size
    unit: str = ""              # "%", "USD", "CNY", ...
    reset: str | None = None    # human reset string
    note: str = ""
    reset_epoch: float | None = None  # machine reset time (for advise/alert)
    window_secs: int | None = None    # quota window length, when known

    def to_public(self) -> dict:
        d = asdict(self)
        if self.kind == "quota" and self.used is not None:
            d["used_pct"] = round(self.used * 100, 1)
        return d


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------
def resolve_key(cfg: dict, name: str) -> str | None:
    if cfg.get("key_env"):
        v = os.environ.get(cfg["key_env"])
        if v:
            return v.strip()
    if cfg.get("key_cmd"):
        try:
            out = subprocess.run(
                cfg["key_cmd"], shell=True, capture_output=True, text=True, timeout=15
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    if cfg.get("key_file"):
        p = Path(os.path.expanduser(cfg["key_file"]))
        if p.is_file():
            return p.read_text().strip()
    return None


# ---------------------------------------------------------------------------
# Probe: Claude Max
# ---------------------------------------------------------------------------
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


def probe_claude_max(cfg: dict) -> list[Row]:
    token, expires_at = _read_claude_token()
    if not token:
        return [Row("claude_max", "—", "error",
                    note="no token in ~/.claude/.credentials.json (run `claude` once)")]
    warn = ""
    if expires_at and (expires_at / 1000) < datetime.now(timezone.utc).timestamp():
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
        return [Row("claude_max", "—", "error", note=f"{warn}network: {e}")]
    if r.status_code in (401, 403):
        return [Row("claude_max", "—", "error",
                    note=f"{warn}auth rejected ({r.status_code}) — run `claude` to refresh")]
    if r.status_code != 200:
        return [Row("claude_max", "—", "error", note=f"{warn}HTTP {r.status_code}")]
    data = r.json()
    rows: list[Row] = []
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
        rows.append(Row("claude_max", label, "quota",
                        used=float(entry["utilization"]) / 100.0, limit=1.0, unit="%",
                        reset=reset_h, note=warn.strip(),
                        reset_epoch=_iso_epoch(reset) if reset else None,
                        window_secs=CLAUDE_WINDOW.get(key)))
    rows.extend(_claude_credits(data, warn))
    if not rows:
        rows.append(Row("claude_max", "—", "quota", note=f"{warn}no active quotas reported"))
    return rows


# Promo dollar grants sit under codenamed keys that change without notice
# (the Nov-2026 cloud-session credit is "iguana_necktie"), so match on shape.
CLAUDE_CREDIT_LABELS = {"iguana_necktie": "Cloud Credit"}


def _claude_credits(data: dict, warn: str) -> list[Row]:
    rows = []
    for key, entry in data.items():
        if not isinstance(entry, dict) or entry.get("limit_dollars") is None:
            continue
        limit = float(entry["limit_dollars"])
        left = entry.get("remaining_dollars")
        if left is None:
            left = limit - float(entry.get("used_dollars") or 0)
        reset = entry.get("resets_at")
        note = warn + (f"locked: {entry['locked_reason']}" if entry.get("locked_reason") else "")
        rows.append(Row("claude_max", CLAUDE_CREDIT_LABELS.get(key, f"Credit ({key})"), "credit",
                        used=round(float(left), 2), limit=limit, unit="USD",
                        reset=f"expires {_humanize_reset(reset)}" if reset else None,
                        note=note.strip(),
                        reset_epoch=_iso_epoch(reset) if reset else None))
    return rows


# ---------------------------------------------------------------------------
# Probe: Codex / ChatGPT subscription (used by Codex CLI and Pi — OAuth, no API key)
# ---------------------------------------------------------------------------
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
        return [Row("codex", "—", "error", note=f"network: {e}")]
    if r.status_code in (401, 403):
        return [Row("codex", "—", "error",
                    note=f"auth rejected ({r.status_code}) — run `codex` to refresh")]
    if r.status_code != 200:
        return [Row("codex", "—", "error", note=f"HTTP {r.status_code}")]
    data = r.json()
    plan = data.get("plan_type", "")
    rl = data.get("rate_limit", {}) or {}
    rows: list[Row] = []
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
    credits = (data.get("credits") or {}).get("balance")
    if isinstance(credits, (int, float)):
        rows.append(Row("codex", "Credits", "balance", used=round(float(credits), 2), unit="USD"))
    if not rows:
        rows.append(Row("codex", "—", "quota", note=f"plan {plan}: no active windows"))
    return rows


def _humanize_reset_unix(ts: int | None) -> str | None:
    if not ts:
        return None
    try:
        return _humanize_reset(datetime.fromtimestamp(int(ts), timezone.utc).isoformat())
    except Exception:
        return None


def _iso_epoch(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _humanize_reset(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = t - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "now"
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        if d:
            return f"in {d}d {h}h"
        if h:
            return f"in {h}h {m}m"
        return f"in {m}m"
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# Probe: OpenAI (month-to-date spend via Costs API; needs Admin key)
# ---------------------------------------------------------------------------
def probe_openai(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "openai")
    if not key:
        return [Row("openai", "Spend (MTD)", "error", note="no admin key (set OPENAI_ADMIN_KEY)")]
    start = _month_start_epoch()
    try:
        total = 0.0
        page = None
        for _ in range(12):  # safety bound on pagination
            params = {"start_time": start, "bucket_width": "1d", "limit": 31}
            if page:
                params["page"] = page
            r = httpx.get(
                "https://api.openai.com/v1/organizations/costs",
                headers={"Authorization": f"Bearer {key}"},
                params=params, timeout=30,
            )
            if r.status_code in (401, 403):
                return [Row("openai", "Spend (MTD)", "error",
                            note=f"auth rejected ({r.status_code}) — needs an ADMIN key")]
            if r.status_code != 200:
                return [Row("openai", "Spend (MTD)", "error", note=f"HTTP {r.status_code}")]
            body = r.json()
            for bucket in body.get("data", []):
                for res in bucket.get("results", []):
                    amt = res.get("amount", {})
                    total += float(amt.get("value", 0) or 0)
            if body.get("has_more") and body.get("next_page"):
                page = body["next_page"]
            else:
                break
    except Exception as e:
        return [Row("openai", "Spend (MTD)", "error", note=f"network: {e}")]
    return [Row("openai", "Spend (MTD)", "spend", used=round(total, 2), unit="USD")]


# ---------------------------------------------------------------------------
# Probe: DeepSeek (remaining balance)
# ---------------------------------------------------------------------------
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
        return [Row("deepseek", "Balance", "error", note=f"network: {e}")]
    if r.status_code in (401, 403):
        return [Row("deepseek", "Balance", "error", note=f"auth rejected ({r.status_code})")]
    if r.status_code != 200:
        return [Row("deepseek", "Balance", "error", note=f"HTTP {r.status_code}")]
    data = r.json()
    rows = []
    for info in data.get("balance_infos", []):
        rows.append(Row(
            "deepseek", f"Balance ({info.get('currency','?')})", "balance",
            used=float(info.get("total_balance", 0) or 0),
            unit=info.get("currency", ""),
            note="" if data.get("is_available") else "account not available",
        ))
    if not rows:
        rows.append(Row("deepseek", "Balance", "balance", note="no balance info returned"))
    return rows


# ---------------------------------------------------------------------------
# Probe: OpenRouter (total spend + remaining credits)
# ---------------------------------------------------------------------------
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
        return [Row("openrouter", "Credits", "error", note=f"network: {e}")]
    if r.status_code in (401, 403):
        return [Row("openrouter", "Credits", "error", note=f"auth rejected ({r.status_code})")]
    if r.status_code != 200:
        return [Row("openrouter", "Credits", "error", note=f"HTTP {r.status_code}")]
    d = r.json().get("data", {})
    total = float(d.get("total_credits", 0) or 0)
    used = float(d.get("total_usage", 0) or 0)
    return [
        Row("openrouter", "Spend (total)", "spend", used=round(used, 2), unit="USD"),
        Row("openrouter", "Remaining", "balance", used=round(total - used, 2), unit="USD"),
    ]


# ---------------------------------------------------------------------------
# Probe: Gemini Code Assist (Google One / AI Pro subscription quota via OAuth)
# The Gemini CLI signs in with Google OAuth — usage is a subscription quota, not
# pay-per-token API spend. Read the same token the CLI uses and hit the internal
# Code Assist quota endpoint. The Antigravity CLI (`agy`, the Gemini CLI successor)
# installs under ~/.gemini/antigravity-cli/ and SHARES this same oauth_creds.json +
# account-level Code Assist quota pool, so this one probe covers both CLIs.
# (The Antigravity *IDE/desktop* app is different: local language server + CSRF.)
# ---------------------------------------------------------------------------
GEMINI_CA_BASE = "https://cloudcode-pa.googleapis.com"
GEMINI_CREDS_PATH = Path(os.path.expanduser("~/.gemini/oauth_creds.json"))
GEMINI_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Public OAuth client of the Gemini CLI (baked into the open-source CLI, not a
# user secret). Override via env if Google ever rotates it.
GEMINI_CLIENT_ID = (os.environ.get("GEMINI_CLIENT_ID")
                    or "GEMINI_CLI_OAUTH_CLIENT_ID_REMOVED")
GEMINI_CLIENT_SECRET = (os.environ.get("GEMINI_CLIENT_SECRET")
                        or "GEMINI_CLI_OAUTH_CLIENT_SECRET_REMOVED")


def _gemini_refresh(refresh_token: str) -> str | None:
    """Exchange the refresh token for a fresh access token and persist it back to
    oauth_creds.json (Google does not rotate refresh tokens). Returns the new token."""
    try:
        r = httpx.post(GEMINI_TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": GEMINI_CLIENT_ID, "client_secret": GEMINI_CLIENT_SECRET}, timeout=20)
        if r.status_code != 200:
            return None
        j = r.json()
        at = j.get("access_token")
        if not at:
            return None
        try:  # persist so the next call (and the CLIs) reuse it
            creds = json.loads(GEMINI_CREDS_PATH.read_text())
            creds["access_token"] = at
            creds["expiry_date"] = int((time.time() + int(j.get("expires_in", 3600))) * 1000)
            if j.get("id_token"):
                creds["id_token"] = j["id_token"]
            GEMINI_CREDS_PATH.write_text(json.dumps(creds, indent=2))
        except Exception:
            pass
        return at
    except Exception:
        return None


def _gemini_access_token(force: bool = False) -> str | None:
    """Return a valid access token, refreshing via the refresh token if the stored
    one is expired/near-expiry (60s buffer) or `force` is set."""
    if not GEMINI_CREDS_PATH.is_file():
        return None
    try:
        creds = json.loads(GEMINI_CREDS_PATH.read_text())
    except Exception:
        return None
    tok = creds.get("access_token")
    exp = creds.get("expiry_date", 0)  # epoch ms
    stale = force or (not tok) or (exp and exp / 1000 < time.time() + 60)
    if stale and creds.get("refresh_token"):
        return _gemini_refresh(creds["refresh_token"]) or tok
    return tok


def _gemini_family(model_id: str) -> tuple[str, str]:
    m = model_id.lower()
    if "flash" in m and "lite" in m:
        return "flash_lite", "Gemini Flash Lite"
    if "pro" in m:
        return "pro", "Gemini Pro"
    if "flash" in m:
        return "flash", "Gemini Flash"
    return model_id, model_id


def _parse_summary_response(data: dict) -> list[Row] | None:
    groups = data.get("response", {}).get("groups", [])
    rows: list[Row] = []
    for g in groups:
        if g.get("displayName") == "Gemini Models":
            buckets = g.get("buckets", [])
            for b in buckets:
                frac = b.get("remainingFraction")
                if frac is None:
                    continue
                win = b.get("window", "")
                bid = b.get("bucketId", "")
                if win == "5h" or bid == "gemini-5h":
                    label = "5-Hour Limit"
                    secs = 18000
                elif win == "weekly" or bid == "gemini-weekly":
                    label = "Weekly Limit"
                    secs = 604800
                else:
                    label = b.get("displayName", "")
                    secs = None
                reset = b.get("resetTime")
                reset_h = _humanize_reset(reset) if reset else None
                rows.append(Row("gemini", label, "quota",
                                used=max(0.0, min(1.0, 1.0 - float(frac))),
                                limit=1.0, unit="%",
                                reset=reset_h,
                                reset_epoch=_iso_epoch(reset) if reset else None,
                                window_secs=secs))
    if not rows:
        return None
    order = {"5-Hour Limit": 0, "Weekly Limit": 1}
    rows.sort(key=lambda r: order.get(r.metric, 99))
    return rows


def _query_local_quota_summary() -> list[Row] | None:
    """Query Antigravity's local language server for quota summary.
    Antigravity IDE runs a local language server on localhost and requires the
    x-codeium-csrf-token header matching its --csrf_token command-line argument."""
    endpoint = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"

    # 1. Discover running language_server instances, their CSRF tokens and listening ports
    candidates: list[tuple[str | None, int]] = []
    seen: set[tuple[str | None, int]] = set()

    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(p, "rb") as f:
                raw = f.read()
            parts = raw.split(b"\0")
            if not parts or not any(b"language_server" in part for part in parts):
                continue
            pid = int(p.split("/")[2])
            csrf = None
            for i, part in enumerate(parts):
                if part == b"--csrf_token" and i + 1 < len(parts):
                    csrf = parts[i + 1].decode("utf-8", errors="ignore")

            inodes = set()
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(os.path.join(fd_dir, fd))
                        if target.startswith("socket:["):
                            inodes.add(target[8:-1])
                    except Exception:
                        pass
            except Exception:
                pass

            for netpath in ("/proc/net/tcp", "/proc/net/tcp6"):
                try:
                    with open(netpath, "r") as f:
                        for line in f.readlines()[1:]:
                            cols = line.strip().split()
                            if len(cols) >= 10 and cols[3] == "0A" and cols[9] in inodes:
                                port = int(cols[1].split(":")[1], 16)
                                if (csrf, port) not in seen:
                                    seen.add((csrf, port))
                                    candidates.append((csrf, port))
                except Exception:
                    pass
        except Exception:
            continue

    # Fallback CSRF token from Antigravity main.log if cmdline inspection was incomplete
    fallback_csrf = None
    log_path = os.path.expanduser("~/.config/Antigravity/logs/main.log")
    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", errors="ignore") as f:
                txt = f.read()
            m = re.findall(r"--csrf_token\s+([0-9a-fA-F-]+)", txt)
            if m:
                fallback_csrf = m[-1]
        except Exception:
            pass

    # Fallback listening ports on 127.0.0.1 if none were resolved to a PID
    if not candidates and fallback_csrf:
        for netpath in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(netpath, "r") as f:
                    for line in f.readlines()[1:]:
                        cols = line.strip().split()
                        if len(cols) >= 4 and cols[3] == "0A":
                            ip_hex, port_hex = cols[1].split(":")
                            if ip_hex in ("0100007F", "00000000"):
                                port = int(port_hex, 16)
                                if (fallback_csrf, port) not in seen:
                                    seen.add((fallback_csrf, port))
                                    candidates.append((fallback_csrf, port))
            except Exception:
                pass

    # 2. Query candidates
    for csrf, port in candidates:
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        }
        if csrf:
            headers["x-codeium-csrf-token"] = csrf

        for scheme in ("http", "https"):
            url = f"{scheme}://127.0.0.1:{port}{endpoint}"
            try:
                r = httpx.post(url, json={}, headers=headers, verify=False, timeout=0.5)
                if r.status_code == 200:
                    rows = _parse_summary_response(r.json())
                    if rows:
                        return rows
            except Exception:
                pass

    return None


def probe_gemini(cfg: dict) -> list[Row]:
    local_rows = _query_local_quota_summary()
    if local_rows is not None:
        return local_rows

    # access tokens expire ~hourly; _gemini_access_token refreshes via the refresh
    # token (and persists) so aiu doesn't depend on a CLI having run recently.
    token = _gemini_access_token()
    if not token:
        return [Row("gemini", "—", "error",
                    note="no token in ~/.gemini/oauth_creds.json (open Antigravity to view quota)")]
    r = None
    project, plan = None, ""
    for attempt in (1, 2):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "aiu/1.0",
        }
        try:
            tr = httpx.post(f"{GEMINI_CA_BASE}/v1internal:loadCodeAssist", headers=headers,
                            json={"metadata": {"ideType": "IDE_UNSPECIFIED",
                                               "platform": "PLATFORM_UNSPECIFIED",
                                               "pluginType": "GEMINI"}}, timeout=20)
            if tr.status_code == 200:
                j = tr.json()
                project = j.get("cloudaicompanionProject")
                plan = j.get("planName") or j.get("tier") or ""
        except Exception:
            pass
        try:
            r = httpx.post(f"{GEMINI_CA_BASE}/v1internal:retrieveUserQuota", headers=headers,
                           json=({"project": project} if project else {}), timeout=20)
        except Exception as e:
            return [Row("gemini", "—", "error", note=f"network: {e}")]
        if r.status_code in (401, 403) and attempt == 1:  # force one refresh + retry
            token = _gemini_access_token(force=True)
            if not token:
                break
            continue
        break
    if r is None or r.status_code in (401, 403):
        return [Row("gemini", "—", "error",
                    note="auth rejected (401) — refresh token invalid, run `agy` to re-sign in")]
    if r.status_code != 200:
        return [Row("gemini", "—", "error", note=f"HTTP {r.status_code}")]
    buckets = r.json().get("buckets", [])
    fam: dict[str, tuple[str, float, str | None]] = {}
    for b in buckets:
        frac = b.get("remainingFraction")
        if frac is None:
            continue
        fid, label = _gemini_family(b.get("modelId", ""))
        used_frac = 1.0 - float(frac)
        # Select the most constrained (highest usage) bucket per family
        if fid not in fam or used_frac > fam[fid][1]:
            fam[fid] = (label, used_frac, b.get("resetTime"))
    order = {"pro": 0, "flash": 1, "flash_lite": 2}
    rows: list[Row] = []
    for fid in sorted(fam, key=lambda x: order.get(x, 9)):
        label, used, reset = fam[fid]
        rows.append(Row("gemini", label, "quota",
                        used=max(0.0, min(1.0, used)), limit=1.0, unit="%",
                        reset=_humanize_reset(reset) if reset else None,
                        note=plan if not rows else "",
                        reset_epoch=_iso_epoch(reset) if reset else None))
    if not rows:
        rows.append(Row("gemini", "—", "quota", note=f"{plan}: no quota buckets returned".strip()))
    return rows


PROBES = {
    "claude_max": probe_claude_max,
    "codex": probe_codex,
    "openai": probe_openai,
    "deepseek": probe_deepseek,
    "openrouter": probe_openrouter,
    "gemini": probe_gemini,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _month_start_epoch() -> int:
    now = datetime.now(timezone.utc)
    return int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())


def load_env_file() -> None:
    """Load KEY=VALUE lines from ~/.env (override with AIU_ENV_FILE) into the
    environment, without clobbering vars already set. Makes `./aiu.py` behave
    like the `aiu` wrapper regardless of how it's launched."""
    path = Path(os.path.expanduser(os.environ.get("AIU_ENV_FILE", "~/.env")))
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def load_config(path: Path) -> dict:
    if not path.is_file():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_TEMPLATE)
        print(f"[aiu] wrote a starter config to {path} — edit it, then re-run.",
              file=sys.stderr)
    with open(path, "rb") as f:
        return tomllib.load(f)


def collect(config: dict, only: set[str] | None) -> list[Row]:
    rows: list[Row] = []
    for name, probe in PROBES.items():
        cfg = config.get(name, {})
        if only is not None:
            if name not in only:
                continue
        elif not cfg.get("enabled"):
            continue
        rows.extend(probe(cfg))
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_table(rows: list[Row]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    t = Table(box=box.SIMPLE_HEAVY, title="AI usage snapshot", title_style="bold")
    t.add_column("Provider", style="cyan")
    t.add_column("Metric")
    t.add_column("Usage", justify="right")
    t.add_column("Resets / note", style="dim")

    for r in rows:
        if r.kind == "error":
            usage = "[red]err[/red]"
        elif r.kind == "quota" and r.used is not None:
            pct = r.used * 100
            color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
            bar = _bar(r.used)
            usage = f"[{color}]{bar} {pct:5.1f}%[/{color}]"
        elif r.kind in ("spend", "balance") and r.used is not None:
            usage = f"{r.used:,.2f} {r.unit}"
        elif r.kind == "credit" and r.used is not None:
            usage = f"{r.used:,.2f}/{r.limit:,.0f} {r.unit} left"
        else:
            usage = "—"
        tail = r.reset or ""
        if r.note:
            tail = f"{tail}  {r.note}".strip()
        t.add_row(r.provider, r.metric, usage, tail)
    console.print(t)


def _bar(frac: float, width: int = 12) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def render_html(rows: list[Row], path: Path) -> None:
    def cell(r: Row) -> str:
        if r.kind == "quota" and r.used is not None:
            return f"{r.used*100:.1f}%"
        if r.kind in ("spend", "balance") and r.used is not None:
            return f"{r.used:,.2f} {r.unit}"
        if r.kind == "credit" and r.used is not None:
            return f"{r.used:,.2f}/{r.limit:,.0f} {r.unit} left"
        return "—" if r.kind != "error" else "err"
    lines = ["<table border=1 cellpadding=6 style='border-collapse:collapse;font-family:sans-serif'>",
             "<tr><th>Provider</th><th>Metric</th><th>Usage</th><th>Resets / note</th></tr>"]
    for r in rows:
        tail = " ".join(x for x in (r.reset or "", r.note) if x)
        lines.append(f"<tr><td>{r.provider}</td><td>{r.metric}</td>"
                     f"<td align=right>{cell(r)}</td><td>{tail}</td></tr>")
    lines.append("</table>")
    path.write_text("<!doctype html><meta charset=utf-8>\n" + "\n".join(lines))
    print(f"[aiu] wrote {path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Advice / alerting — turn the snapshot into "where should I route, what's wasting"
# ---------------------------------------------------------------------------
# What each flat-fee subscription is best at (free at the margin -> route here first).
PROVIDER_HINTS = {
    "claude_max": "raisonnement profond / agentique",
    "gemini": "volume / tâches jetables (Flash)",
    "codex": "2e voix de code / diversité",
}
# Alert thresholds for "paid-for capacity about to vanish unused".
ALERT_USED_MAX = 40.0        # only warn if a window is under this % used
ALERT_TTR_MAX = 48 * 3600    # ...and resets within this many seconds
ALERT_MIN_WINDOW = 6 * 3600  # ...and it's a long window (not a 5h burst bucket)
# One-shot credits vanish entirely at expiry, so warn earlier.
CREDIT_LEFT_MIN = 0.25       # warn if at least this fraction of the grant is unspent
CREDIT_TTR_MAX = 14 * 86400  # ...and it expires within this many seconds
ALERT_LEFTOVER_MIN = 0.05    # warn if a window is projected to lose this much of its cap at reset
ALERT_DRY_MIN = 24 * 3600    # warn if a window is projected to sit empty this long before reset


# Advice ranks by earliest-expiring SURPLUS (EDF), not raw headroom: quota is
# lost at reset, so the reset is each window's deadline. Surplus = what is
# projected to be left at reset if you keep the pace of the current period.
RATE_WINDOW_MAX = 6 * 3600   # windows this short are rate limits (5h); longer = volume budgets
PACE_MIN_FRAC = 0.10         # trust a window's pace once 10% of it has elapsed
ON_TRACK_BAND = 0.05         # projected leftover within ±5% of the cap counts as on track


def _dur(secs: float) -> str:
    secs = int(max(0, secs))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d{h}h" if d else f"{h}h{rem // 60:02d}m" if h else f"{rem // 60}m"


def _project(r: Row, now: float) -> dict | None:
    """Extrapolate a quota window to its reset at the pace of its current period."""
    if r.used is None or not r.reset_epoch or not r.window_secs:
        return None
    ttr = r.reset_epoch - now
    elapsed = r.window_secs - ttr
    if ttr <= 0 or elapsed < PACE_MIN_FRAC * r.window_secs:
        return None
    pace = r.used / elapsed                    # fraction of the cap per second
    left = 1.0 - r.used
    return {"r": r, "ttr": ttr, "pace": pace, "left": left,
            "leftover": left - pace * ttr,     # > 0: expires unused ; < 0: runs dry first
            "runway": left / pace if pace else float("inf")}


def _lots(rows: list[Row], now: float) -> list[dict]:
    """One routing lot per subscription (its volume windows) and per promo credit.
    status: burn (surplus expires unused) | ? (pace unknown) | on_track | conserve (runs dry)."""
    lots, quotas = [], {}
    for r in rows:
        if r.kind == "quota" and r.used is not None and r.metric != "Extra Usage":
            quotas.setdefault(r.provider, []).append(r)   # Extra Usage = paid overage
        elif (r.kind == "credit" and r.used and r.reset_epoch and r.reset_epoch > now
              and "locked" not in r.note):
            # Spent only by sessions you choose to run: no committed load, all surplus.
            ttr = r.reset_epoch - now
            lots.append({"prov": r.provider, "status": "burn", "ttr": ttr, "metric": r.metric,
                         "what": f"{r.metric} {r.used:,.2f}/{r.limit:,.0f} {r.unit}, expire dans "
                                 f"{_dur(ttr)} (≈{r.used / ttr * 86400:,.2f} {r.unit}/jour pour tout utiliser)"})
    for prov, rs in quotas.items():
        vol = [r for r in rs if (r.window_secs or 0) > RATE_WINDOW_MAX] or rs
        projs = [_project(r, now) for r in vol]
        known = [p for p in projs if p]
        dry = [p for p in known if p["leftover"] < -ON_TRACK_BAND]
        if dry:
            p = min(dry, key=lambda p: p["runway"])
            lot = {"status": "conserve", "ttr": p["ttr"], "dry": p["ttr"] - p["runway"],
                   "metric": p["r"].metric, "leftover": p["leftover"], "runway": p["runway"],
                   "what": f"{p['r'].metric} {p['left'] * 100:.0f}% libre : à sec dans ~{_dur(p['runway'])}, "
                           f"reset dans {_dur(p['ttr'])} → ~{_dur(p['ttr'] - p['runway'])} sans {prov}"}
        elif len(known) < len(projs):
            # No usable pace: fall back to plain EDF and say so.
            r = min((r for r in vol if r.reset_epoch), key=lambda r: r.reset_epoch, default=vol[0])
            ttr = r.reset_epoch - now if r.reset_epoch else float("inf")
            lot = {"status": "?", "ttr": ttr, "metric": r.metric,
                   "what": f"{r.metric} {100 * (1 - r.used):.0f}% libre, "
                           + (f"reset dans {_dur(ttr)}" if r.reset_epoch else "reset inconnu")
                           + " (allure inconnue)"}
        else:
            p = min(known, key=lambda p: p["ttr"])
            if all(q["leftover"] > ON_TRACK_BAND for q in known):
                need = p["left"] / p["ttr"]
                lot = {"status": "burn", "ttr": p["ttr"], "metric": p["r"].metric,
                       "leftover": p["leftover"],
                       "what": f"{p['r'].metric} : reset dans {_dur(p['ttr'])}, ~{p['leftover'] * 100:.0f}% "
                               f"du plafond perdu à ton allure ({p['pace'] * 360000:.2f}%/h ; "
                               f"tout utiliser = {need * 360000:.2f}%/h, ×{need / p['pace']:.1f})"}
            else:
                lot = {"status": "on_track", "ttr": p["ttr"], "metric": p["r"].metric,
                       "leftover": p["leftover"],
                       "what": f"{p['r'].metric} {p['left'] * 100:.0f}% libre, reset dans "
                               f"{_dur(p['ttr'])} — au rythme"}
        # Short windows only gate availability; flag them when they'd run dry first.
        for r in rs:
            if r in vol:
                continue
            p = _project(r, now)
            if p and p["leftover"] < 0:
                lot["what"] += f" ; {r.metric} épuisé dans ~{_dur(p['runway'])}"
        lot["prov"] = prov
        lots.append(lot)
    return lots


def route_order(rows: list[Row], now: float | None = None) -> list[dict]:
    """Public advice for --json / consult: lots in routing order (burn & unknown by
    deadline, then on-track, then conserve by how long they'd stay dry)."""
    now = time.time() if now is None else now
    lots = _lots(rows, now)
    rank = {"burn": 0, "?": 0, "on_track": 1, "conserve": 2}
    lots.sort(key=lambda l: (rank[l["status"]],
                             -l["dry"] if l["status"] == "conserve" else l["ttr"]))
    return [{"provider": l["prov"], "metric": l["metric"], "status": l["status"],
             "ttr_secs": None if l["ttr"] == float("inf") else round(l["ttr"]),
             # projected fraction of the cap left at reset (<0: short by that much)
             "leftover": round(l["leftover"], 3) if "leftover" in l else None,
             "runway_secs": round(l["runway"]) if "runway" in l else None,
             "detail": l["what"]} for l in lots]


def advise(rows: list[Row], now: float | None = None) -> int:
    from rich.console import Console
    console = Console()
    now = time.time() if now is None else now
    lots = _lots(rows, now)
    order = sorted((l for l in lots if l["status"] in ("burn", "?")), key=lambda l: l["ttr"])
    steady = sorted((l for l in lots if l["status"] == "on_track"), key=lambda l: l["ttr"])
    scarce = sorted((l for l in lots if l["status"] == "conserve"), key=lambda l: -l["dry"])
    console.print("\n[bold]Conseil de routage[/bold] — la capacité qui expire le plus tôt d'abord "
                  "(surplus projeté à ton allure actuelle) :\n")
    for i, l in enumerate(order, 1):
        color = "green" if l["status"] == "burn" else "yellow"
        hint = PROVIDER_HINTS.get(l["prov"], "")
        console.print(f"  {i}. [{color}]{l['prov']:<12}[/{color}] {l['what']}   [dim]{hint}[/dim]")
    if steady:
        console.print("\n  [bold]Au rythme (pas de surplus à offrir) :[/bold]")
        for l in steady:
            console.print(f"     {l['prov']:<12} {l['what']}")
    if scarce:
        console.print("\n  [bold red]À ménager[/bold red] (s'épuise avant le reset — garde-le pour "
                      "ce que lui seul sait faire) :")
        for l in scarce:
            console.print(f"     [red]{l['prov']:<12}[/red] {l['what']}")
    # Lifetime spend is sunk, not capacity: only list what can still absorb overflow.
    paid = [r for r in rows if r.kind == "balance" and r.used is not None
            or r.kind == "spend" and r.used is not None and "total" not in r.metric.lower()]
    if paid:
        console.print("\n  [dim]Payant (débordement seulement ; devises non additionnées) :[/dim]")
        for r in paid:
            console.print(f"     [dim]{r.provider:<12} {r.used:,.2f} {r.unit}  ({r.metric})[/dim]")
    for r in rows:
        if r.kind == "error":
            console.print(f"  [red]{r.provider}: {r.note}[/red]")
    keep = list(dict.fromkeys(l["prov"] for l in scarce))
    names = ", ".join(keep)
    if order:
        first = order[0]
        msg = (f"\n  [bold green]→ Brûle d'abord {first['prov']}[/bold green] "
               f"(expire dans {_dur(first['ttr'])}"
               + (", allure inconnue" if first["status"] == "?" else "")
               + ") : envoie-lui tout ce qu'il sait faire")
        msg += f", y compris ce que tu donnerais à {names}." if names else "."
        if names:
            who = "ce que lui seul sait" if len(keep) == 1 else "ce qu'eux seuls savent"
            msg += f" Ménage {names} pour {who} faire."
        console.print(msg + "\n")
    elif steady:
        console.print("\n  [bold]→ Aucun surplus qui expire[/bold] : route par fit de tâche vers "
                      f"{', '.join(dict.fromkeys(l['prov'] for l in steady))} (au rythme)"
                      + (f", ménage {names}." if names else ".") + "\n")
    else:
        console.print("\n  [bold yellow]→ Aucun forfait n'a de surplus[/bold yellow] : réserve chacun à ce "
                      "que lui seul sait faire ; déborde le reste sur l'API payante la moins chère.\n")
    return 0


def alert(rows: list[Row]) -> int:
    """Warn about long-window quota projected to reset unused or to run dry long
    before its reset, and about expiring credits. Quiet-friendly for cron: prints only warnings (and a single OK line otherwise)."""
    now = time.time()
    warns = []
    for r in rows:
        if r.kind == "credit" and r.used is not None and r.limit and r.reset_epoch:
            ttr = r.reset_epoch - now
            if "locked" not in r.note and r.used / r.limit >= CREDIT_LEFT_MIN and 0 < ttr <= CREDIT_TTR_MAX:
                warns.append(f"⚠ {r.provider} · {r.metric}: {r.used:,.2f} {r.unit} "
                             f"non dépensés, {r.reset}")
            continue
        if r.kind != "quota" or r.used is None or not r.reset_epoch:
            continue
        ttr = r.reset_epoch - now
        used_pct = r.used * 100
        long_window = (r.window_secs or 0) >= ALERT_MIN_WINDOW or \
                      (r.window_secs is None and ttr >= ALERT_MIN_WINDOW)
        if not long_window or ttr <= 0:
            continue
        p = _project(r, now)  # None while the pace is unknown: keep the level rule
        waste = p["leftover"] if p else (1 - r.used) * (used_pct < ALERT_USED_MAX)
        if waste >= ALERT_LEFTOVER_MIN and ttr <= ALERT_TTR_MAX:
            warns.append(f"⚠ {r.provider} · {r.metric}: ~{waste * 100:.0f}% du plafond "
                         f"perdu au reset, reset {r.reset}")
        elif p and p["leftover"] < 0 and ttr - p["runway"] >= ALERT_DRY_MIN:
            warns.append(f"⚠ {r.provider} · {r.metric}: à sec dans ~{_dur(p['runway'])} à ton allure, "
                         f"reset {r.reset} → ~{_dur(ttr - p['runway'])} sans {r.provider}")
    # Missing data is not an all-clear.
    warns += [f"⚠ {r.provider}: données indisponibles ({r.note})" for r in rows if r.kind == "error"]
    if warns:
        for w in warns:
            print(w)
        # best-effort desktop notification so it lands without watching the terminal
        if shutil.which("notify-send"):
            try:
                subprocess.run(["notify-send", "-a", "aiu", "Quota AI",
                                "\n".join(warns)], timeout=5)
            except Exception:
                pass
        return 1
    print("✓ rien ne sera perdu au reset ni à sec longtemps")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="One snapshot of every AI subscription you pay for.")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--only", help="comma-separated provider subset (ignores enabled flags)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--html", metavar="PATH", help="write an HTML page")
    ap.add_argument("--advise", action="store_true",
                    help="recommend where to route: earliest-expiring surplus first")
    ap.add_argument("--alert", action="store_true",
                    help="warn about quota lost at reset / running dry, expiring credits (cron-friendly)")
    args = ap.parse_args()

    load_env_file()
    config = load_config(Path(os.path.expanduser(args.config)))
    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    rows = collect(config, only)

    if args.json:
        print(json.dumps({"captured_at": datetime.now(timezone.utc).isoformat(),
                          "rows": [r.to_public() for r in rows],
                          "advice": route_order(rows)}, indent=2))
    if args.html:
        render_html(rows, Path(os.path.expanduser(args.html)))
    if args.advise:
        return advise(rows)
    if args.alert:
        return alert(rows)
    if not args.json and not args.html:
        render_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
