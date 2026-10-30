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
  - claude     : Claude Max session quotas (5h / weekly / monthly), read from the
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
  - gemini     : Gemini 5h / weekly quota from the Antigravity desktop app's local
                 server while it runs; else Gemini CLI OAuth (~/.gemini/oauth_creds.json),
                 which Google now refuses for individual accounts.
  - mistral    : monthly Vibe Code + API allowances (€ spent / € included), via
                 the admin console's tRPC API; needs the browser session cookie.

Usage:
  ./aiu.py                 # pretty table
  ./aiu.py --json          # machine-readable
  ./aiu.py --html out.html # local HTML page
  ./aiu.py --only claude,deepseek
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

[claude]
enabled = true

# Codex CLI + Pi sign in via ChatGPT OAuth (no API key) -> subscription quota,
# like claude. Token read from ~/.codex/auth.json.
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

# Mistral's monthly allowances (Vibe Code + API, €8.5 each on Free) are only
# readable with the admin.mistral.ai browser session: put the whole Cookie
# request header (DevTools > Network > any /api/local-trpc/ call) in
# MISTRAL_ADMIN_COOKIE, in single quotes in ~/.env (it contains ';').
# API keys are rejected. Re-copy it when the session expires.
[mistral]
enabled = false
key_env = "MISTRAL_ADMIN_COOKIE"
# vibe_allowance_eur = 8.5   # 0 hides that row
# api_allowance_eur = 8.5

# Note: Cohere exposes NO aggregate usage/spend endpoint (dashboard only), so it
# can't be polled as a simple number without local token accounting.

# Gemini (Antigravity / Google One AI Pro) -> subscription quota, read from the
# Antigravity desktop app while it is open. No key needed.
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
# Values read from ~/.env by load_env_file: only the names the config refers to,
# and never exported to os.environ, so key_cmd and other children don't see them.
ENV_FILE: dict[str, str] = {}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def resolve_key(cfg: dict, name: str) -> str | None:
    """First usable key from key_env, key_cmd, key_file. Only the first line of
    a value counts (a `pass` entry holds more lines after the password); a value
    that still holds control characters is refused without being quoted."""
    def clean(v: str | None, source: str) -> str | None:
        v = (v or "").strip()
        if not v:
            return None
        v = v.splitlines()[0].strip()
        if _CONTROL_RE.search(v):
            print(f"[aiu] {name}: key from {source} holds control characters, ignored",
                  file=sys.stderr)
            return None
        return v

    if cfg.get("key_env"):
        env = cfg["key_env"]
        if v := clean(os.environ.get(env) or ENV_FILE.get(env), "key_env"):
            return v
    if cfg.get("key_cmd"):
        try:
            out = subprocess.run(
                cfg["key_cmd"], shell=True, capture_output=True, text=True, timeout=15
            )
            if out.returncode == 0 and (v := clean(out.stdout, "key_cmd")):
                return v
        except Exception:
            pass
    if cfg.get("key_file"):
        p = Path(os.path.expanduser(cfg["key_file"]))
        if p.is_file() and (v := clean(p.read_text(errors="replace"), "key_file")):
            return v
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


def _refresh_claude_token() -> bool:
    """Have the CLI refresh ~/.claude/.credentials.json. Any `claude` start does
    it, `auth status` without spending a request; the desktop app keeps its own
    token and never rewrites the file, so it goes stale 8h after the last CLI run.
    Clean environment: a host Claude session's variables (ANTHROPIC_BASE_URL,
    host-managed auth) must not steer the child."""
    exe = shutil.which("claude")
    if not exe:
        return False
    env = {k: os.environ[k] for k in ("HOME", "PATH", "USER", "LANG") if k in os.environ}
    try:
        return subprocess.run([exe, "auth", "status"], env=env, capture_output=True,
                              timeout=60).returncode == 0
    except Exception:
        return False


def probe_claude(cfg: dict) -> list[Row]:
    token, expires_at = _read_claude_token()
    if token and expires_at and expires_at / 1000 < time.time() + 60 and _refresh_claude_token():
        token, expires_at = _read_claude_token()
    if not token:
        return [Row("claude", "—", "error",
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


# ---------------------------------------------------------------------------
# Probe: Gemini Code Assist (Google One / AI Pro subscription quota via OAuth)
# The Gemini CLI signs in with Google OAuth — usage is a subscription quota, not
# pay-per-token API spend. Read the same token the CLI uses and hit the internal
# Code Assist quota endpoint. Since Oct 2026 Google refuses that client for
# individual accounts (UNSUPPORTED_CLIENT), so for them the Antigravity desktop
# app's local language server (+ CSRF token from its command line) is the only
# source, read first below. The `agy` CLI keeps its login in the system keyring,
# not in oauth_creds.json, and its own local server's CSRF token never leaves
# memory, so aiu can't read quota through agy.
# ---------------------------------------------------------------------------
GEMINI_CA_BASE = "https://cloudcode-pa.googleapis.com"
GEMINI_CREDS_PATH = Path(os.path.expanduser("~/.gemini/oauth_creds.json"))
GEMINI_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refreshing the token needs the Gemini CLI's public "installed app" OAuth client.
# It is not a user secret, but it is Google's to publish, not ours: take it from
# the environment, else read it out of the locally installed Gemini CLI bundle.
# The bundle holds several Google client IDs; only the named constants are the CLI's.
_GEMINI_CLIENT_ID_RE = re.compile(r'OAUTH_CLIENT_ID\s*=\s*"([^"]+\.apps\.googleusercontent\.com)"')
_GEMINI_CLIENT_SECRET_RE = re.compile(r'OAUTH_CLIENT_SECRET\s*=\s*"([^"]+)"')


def _gemini_oauth_client() -> tuple[str, str] | None:
    cid, secret = os.environ.get("GEMINI_CLIENT_ID"), os.environ.get("GEMINI_CLIENT_SECRET")
    if cid and secret:
        return cid, secret
    cli = shutil.which("gemini")
    if not cli:
        return None
    bundle = Path(os.path.realpath(cli)).parent   # .../@google/gemini-cli/bundle
    for f in sorted(bundle.glob("*.js")):
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        m_id, m_secret = _GEMINI_CLIENT_ID_RE.search(text), _GEMINI_CLIENT_SECRET_RE.search(text)
        if m_id and m_secret:
            return m_id.group(1), m_secret.group(1)
    return None


def _gemini_refresh(refresh_token: str) -> str | None:
    """Exchange the refresh token for a fresh access token and persist it back to
    oauth_creds.json (Google does not rotate refresh tokens). Returns the new token,
    which is good for this run even when it could not be saved."""
    client = _gemini_oauth_client()
    if not client:
        return None
    try:
        r = httpx.post(GEMINI_TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": client[0], "client_secret": client[1]}, timeout=20)
        if r.status_code != 200:
            return None
        j = r.json()
        at = j.get("access_token")
        if not at:
            return None
        expiry = int((time.time() + int(j.get("expires_in", 3600))) * 1000)
    except Exception:
        return None
    # persist so the next call (and the CLIs) reuse it
    reason = _gemini_persist(refresh_token, at, expiry, j.get("id_token"))
    if reason:
        print(f"[aiu] gemini: refreshed token kept for this run only, "
              f"{GEMINI_CREDS_PATH} left as is: {reason}", file=sys.stderr)
    return at


def _gemini_persist(refresh_token: str, access_token: str, expiry_ms: int,
                    id_token: str | None) -> str | None:
    """Store a refreshed token in oauth_creds.json without ever leaving it empty or
    half-written: under a lock shared by every aiu run, re-read the file, change only
    the token fields, write a 0600 temp file next to the real file (a symlink stays a
    symlink) and rename it over. Returns why nothing was written, or None."""
    import fcntl
    import tempfile
    target = Path(os.path.realpath(GEMINI_CREDS_PATH))
    tmp = None
    try:
        if target.stat().st_uid != os.getuid():
            return "not owned by you"
        lock = os.open(target.with_name(f".{target.name}.aiu-lock"),
                       os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            creds = json.loads(target.read_text())
            if not isinstance(creds, dict):
                return "not a JSON object"
            if creds.get("refresh_token") != refresh_token:
                return "signed in again meanwhile"
            creds["access_token"] = access_token
            creds["expiry_date"] = expiry_ms
            if id_token:
                creds["id_token"] = id_token
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:  # mkstemp: O_EXCL, mode 0600
                f.write(json.dumps(creds, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
            tmp = None
        finally:
            os.close(lock)  # releases the flock
    except (OSError, ValueError) as e:
        return type(e).__name__
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
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
    # A local reply is checked like any server's: a wrong shape is no summary.
    resp = data.get("response") if isinstance(data, dict) else None
    groups = resp.get("groups") if isinstance(resp, dict) else None
    rows: list[Row] = []
    for g in groups if isinstance(groups, list) else []:
        if isinstance(g, dict) and g.get("displayName") == "Gemini Models":
            buckets = g.get("buckets")
            for b in buckets if isinstance(buckets, list) else []:
                frac = b.get("remainingFraction") if isinstance(b, dict) else None
                if isinstance(frac, bool) or not isinstance(frac, (int, float)) or frac != frac:
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
                    label = str(b.get("displayName") or "")
                    secs = None
                reset = b.get("resetTime")
                if not isinstance(reset, str):
                    reset = None
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
    x-codeium-csrf-token header matching its --csrf_token command-line argument.
    Any local user can listen on loopback, so the token only goes to listening
    sockets that are ours and held by an Antigravity language_server of ours."""
    endpoint = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
    uid = os.getuid()
    # /proc/net/tcp{,6} local address -> the host a request must use to reach it.
    # Anything else (another interface, a v6-only wildcard) may be shadowed on
    # loopback by someone else's socket, so it is never queried.
    loopback = {
        "0100007F": "127.0.0.1", "00000000": "127.0.0.1",
        "00000000000000000000000001000000": "[::1]", "00000000000000000000000000000000": "[::1]",
        "0000000000000000FFFF00000100007F": "127.0.0.1",
    }

    def owned(pid: str) -> bool:
        # every uid of the process (real, effective, saved, fs) is ours
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("Uid:"):
                        return all(int(u) == uid for u in line.split()[1:])
        except Exception:
            pass
        return False

    def antigravity_server(pid: str) -> bool:
        # the executable, which unlike argv the process can't rewrite, is a
        # language_server binary inside an Antigravity install
        try:
            exe = os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")
        except Exception:
            return False
        dirs, _, name = exe.lower().rpartition("/")
        return name.startswith("language_server") and "antigravity" in dirs

    def main_log_csrf() -> str | None:
        log_path = os.path.expanduser("~/.config/Antigravity/logs/main.log")
        try:
            with open(log_path, "r", errors="ignore") as f:
                m = re.findall(r"--csrf_token\s+([0-9a-fA-F-]+)", f.read())
            return m[-1] if m else None
        except Exception:
            return None

    # 1. Discover our Antigravity language servers, their CSRF tokens and listening ports
    candidates: list[tuple[str | None, str, int]] = []
    seen: set[tuple[str, int]] = set()
    log_read, log_csrf = False, None

    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            pid = p.split("/")[2]
            if not owned(pid) or not antigravity_server(pid):
                continue
            with open(p, "rb") as f:
                parts = f.read().split(b"\0")
            csrf = None
            for i, part in enumerate(parts):
                if part == b"--csrf_token" and i + 1 < len(parts):
                    csrf = parts[i + 1].decode("utf-8", errors="ignore")
            if not csrf:
                # No token on the cmdline: fall back to the one Antigravity logged
                # when it spawned its server, for this server's ports only.
                if not log_read:
                    log_read, log_csrf = True, main_log_csrf()
                csrf = log_csrf

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
                            if (len(cols) < 10 or cols[3] != "0A" or cols[7] != str(uid)
                                    or cols[9] not in inodes):
                                continue
                            ip_hex, port_hex = cols[1].split(":")
                            host, port = loopback.get(ip_hex.upper()), int(port_hex, 16)
                            if host and (host, port) not in seen:
                                seen.add((host, port))
                                candidates.append((csrf, host, port))
                except Exception:
                    pass
        except Exception:
            continue

    # 2. Query candidates. https is verified like any other host: an Antigravity
    # server with a self-signed certificate is skipped, never trusted blindly.
    for csrf, host, port in candidates:
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        }
        if csrf:
            headers["x-codeium-csrf-token"] = csrf

        for scheme in ("http", "https"):
            url = f"{scheme}://{host}:{port}{endpoint}"
            try:
                r = httpx.post(url, json={}, headers=headers, timeout=0.5)
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
                    note="Antigravity desktop not running, no ~/.gemini/oauth_creds.json fallback "
                         "— open Antigravity to read the quota")]
    r = None
    project, plan, unsupported = None, "", None
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
                # The tier the account is on, else the one it would get (Oct 2026
                # responses carry {id, name} objects; planName is the older field).
                allowed = [t for t in j.get("allowedTiers") or [] if isinstance(t, dict)]
                tier = j.get("currentTier") or next((t for t in allowed if t.get("isDefault")),
                                                    allowed[0] if allowed else {})
                plan = j.get("planName") or (tier.get("name") if isinstance(tier, dict) else None) or ""
                # Since Oct 2026 Google refuses the Gemini CLI client for individual
                # accounts ("migrate to Antigravity"): the quota calls then 403.
                if not j.get("currentTier"):
                    unsupported = next((t.get("tierName") or t.get("tierId") for t in
                                        j.get("ineligibleTiers") or []
                                        if t.get("reasonCode") == "UNSUPPORTED_CLIENT"), None)
        except Exception:
            pass
        try:
            r = httpx.post(f"{GEMINI_CA_BASE}/v1internal:retrieveUserQuota", headers=headers,
                           json=({"project": project} if project else {}), timeout=20)
        except Exception as e:
            return [Row("gemini", "—", "error", note=f"network: {e}")]
        if unsupported and r.status_code == 403:
            return [Row("gemini", "—", "error",
                        note=f"Google no longer accepts the Gemini CLI sign-in for {unsupported} "
                             "— open the Antigravity desktop app to read the quota")]
        if r.status_code in (401, 403) and attempt == 1:  # force one refresh + retry
            token = _gemini_access_token(force=True)
            if not token:
                break
            continue
        break
    if r is not None and r.status_code == 403:
        # Google answers 401 for a bad or expired token, so a 403 the refresh and
        # retry did not clear means the account or project may not read the quota.
        return [Row("gemini", "—", "error",
                    note="permission denied (403) — this account or its Code Assist project "
                         "may not read the quota; open the Antigravity desktop app instead")]
    if r is None or r.status_code == 401:
        if not _gemini_oauth_client():
            return [Row("gemini", "—", "error",
                        note="token expired and can't refresh: install gemini-cli or set "
                             "GEMINI_CLIENT_ID / GEMINI_CLIENT_SECRET")]
        return [Row("gemini", "—", "error",
                    note="auth rejected (401) — refresh token invalid, run `agy` to re-sign in")]
    if r.status_code != 200:
        return [Row("gemini", "—", "error", note=f"HTTP {r.status_code}")]
    fam: dict[str, tuple[str, float, str | None]] = {}
    try:
        for b in r.json().get("buckets") or []:
            frac = b.get("remainingFraction")
            if frac is None:
                continue
            fid, label = _gemini_family(b.get("modelId", ""))
            used_frac = 1.0 - float(frac)
            # Select the most constrained (highest usage) bucket per family
            if fid not in fam or used_frac > fam[fid][1]:
                fam[fid] = (label, used_frac, b.get("resetTime"))
    except (ValueError, TypeError, AttributeError):
        return [Row("gemini", "—", "error", note="malformed quota response (HTTP 200)")]
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
        rows.append(Row("gemini", "—", "quota",
                        note=f"{plan}: no quota buckets returned" if plan else "no quota buckets returned"))
    return rows


# ---------------------------------------------------------------------------
# Probe: Mistral (monthly included allowances: Vibe Code + API/Studio)
# Every plan, Free included, gets two € allowances reset on the 1st of the
# calendar month. No API key reads them (Oct 2026: the Vibe key only opens
# /api/vibe/whoami); the admin console's Usage page calls tRPC endpoints that
# accept the browser session cookie only. They return token counts per billing
# metric and a price list; € spent is their product, which matches the
# dashboard's "Total Estimated Cost" to the cent.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Probe errors. A note never quotes an exception: httpx puts the offending
# header value, key included, into its message. Notes say what failed in our
# own words, and collect() redacts anything token-like that still gets in.
# ---------------------------------------------------------------------------
class Malformed(ValueError):
    """A 200 whose body is not the shape the probe reads. The message is a
    fixed text of ours, so it can go into a note."""


def _json_object(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise Malformed("not JSON") from None
    if not isinstance(data, dict):
        raise Malformed("not a JSON object")
    return data


def _need(d, key: str, kind: type | tuple[type, ...] = (int, float, str)):
    """d[key], present and of `kind`: a renamed field is an error, never a zero."""
    v = d.get(key) if isinstance(d, dict) else None
    if not isinstance(v, kind) or isinstance(v, bool):
        raise Malformed(f"no `{key}`")
    return v


def _network_note(e: Exception) -> str:
    if isinstance(e, httpx.LocalProtocolError):  # e.g. a key with a newline in it
        return "invalid request header (check the key for extra lines)"
    if isinstance(e, httpx.TimeoutException):
        return "network: timed out"
    if isinstance(e, httpx.ConnectError):
        return "network: cannot connect"
    return f"network: {type(e).__name__}"


def _malformed_note(e: Exception) -> str:
    if isinstance(e, Malformed):
        return f"malformed response: {e}"
    return f"malformed response ({type(e).__name__})"


_SECRET_RE = re.compile(
    r"(?i)\b(?:bearer|basic)\s+\S+"  # an Authorization header value
    r"|\bsk-[\w-]+"                  # API keys: sk-…, sk-ant-…, sk-or-v1-…
    r"|\b[\w.-]+=[^\s;,]{8,}"        # cookie pairs
    r"|[\w.~+/-]{32,}=*"             # long opaque strings: JWTs, OAuth tokens
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text)


PROBES = {
    "claude": probe_claude,
    "codex": probe_codex,
    "openai": probe_openai,
    "deepseek": probe_deepseek,
    "openrouter": probe_openrouter,
    "gemini": probe_gemini,
    "mistral": probe_mistral,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _month_start_epoch() -> int:
    now = datetime.now(timezone.utc)
    return int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())


_ENV_LINE_RE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
_ENV_ALWAYS = ("MISTRAL_ADMIN_COOKIE", "GEMINI_CLIENT_ID", "GEMINI_CLIENT_SECRET")


def _env_value(v: str) -> str | None:
    """A ~/.env value as a shell reads it, without expansion: '...' is literal,
    "..." honours \\ escapes, an unquoted value stops at whitespace + `#`.
    None for an unmatched quote or anything after the closing quote."""
    if v[:1].isspace():
        v = v.lstrip()
        if v[:1] == "#":
            return ""
    if v[:1] == "'":
        end = v.find("'", 1)
        if end < 0:
            return None
        out, rest = v[1:end], v[end + 1:]
    elif v[:1] == '"':
        out, i = [], 1
        while i < len(v) and v[i] != '"':
            if v[i] == "\\" and i + 1 < len(v) and v[i + 1] in '"\\$`':
                i += 1
            out.append(v[i])
            i += 1
        if i == len(v):
            return None
        out, rest = "".join(out), v[i + 1:]
    else:
        out = re.split(r"\s#", v, maxsplit=1)[0].rstrip()
        return None if "'" in out or '"' in out else out
    return out if not rest.strip() or re.match(r"\s+#", rest) else None


def load_env_file(config: dict) -> dict[str, str]:
    """Read from ~/.env (override with AIU_ENV_FILE) only the names the config
    refers to: each `key_env`, the Mistral cookie and the Gemini CLI OAuth
    client. Returns them; the caller puts them in ENV_FILE, not os.environ."""
    path = Path(os.path.expanduser(os.environ.get("AIU_ENV_FILE", "~/.env")))
    if not path.is_file():
        return {}
    wanted = set(_ENV_ALWAYS)
    wanted.update(s["key_env"] for s in config.values()
                  if isinstance(s, dict) and isinstance(s.get("key_env"), str))
    found: dict[str, str] = {}
    for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        m = _ENV_LINE_RE.fullmatch(line)
        if not m or m.group(1) not in wanted:
            continue
        v = _env_value(m.group(2))
        if v is None:
            print(f"[aiu] {path}:{n}: {m.group(1)} has unmatched quotes, ignored",
                  file=sys.stderr)
            continue
        found[m.group(1)] = v
    if found and path.stat().st_mode & 0o044:
        print(f"[aiu] {path} holds keys and other users can read it: chmod 600 {path}",
              file=sys.stderr)
    return found


def load_config(path: Path) -> dict:
    """Read the config, writing the template (0600, in a 0700 directory) on
    first run. Like OpenSSH, ignore every key_cmd when the file or its directory
    belongs to someone else or can be written by group or others."""
    if not path.is_file():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as f:
                f.write(CONFIG_TEMPLATE)
            print(f"[aiu] wrote a starter config to {path} — edit it, then re-run.",
                  file=sys.stderr)
    with open(path, "rb") as f:
        config = tomllib.load(f)
    real = Path(os.path.realpath(path))
    unsafe = []
    for p in dict.fromkeys((path, path.parent, real.parent)):
        st = p.stat()
        if st.st_uid != os.getuid():
            unsafe.append(f"{p} is not yours")
        elif st.st_mode & 0o022:
            unsafe.append(f"{p} is writable by group or others")
    cmds = [name for name, s in config.items() if isinstance(s, dict) and "key_cmd" in s]
    if unsafe and cmds:
        print(f"[aiu] key_cmd ignored for {', '.join(cmds)}: {'; '.join(unsafe)}",
              file=sys.stderr)
        for name in cmds:
            del config[name]["key_cmd"]
    return config


def collect(config: dict, only: set[str] | None) -> list[Row]:
    rows: list[Row] = []
    for name, probe in PROBES.items():
        cfg = config.get(name, {})
        if only is not None:
            if name not in only:
                continue
        elif not cfg.get("enabled"):
            continue
        try:  # one broken provider must not take the whole run down
            got = probe(cfg)
        except Exception as e:
            got = [Row(name, "—", "error", note=f"probe failed ({type(e).__name__})")]
        for r in got:
            r.note = _redact(r.note)
        rows.extend(got)
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
    # Provider text (plan names, credit keys, notes, a local server's reply) is
    # untrusted: every value is escaped, and the CSP blocks scripts regardless.
    from html import escape

    def esc(v) -> str:
        return escape(str(v), quote=True)
    lines = ["<table border=1 cellpadding=6 style='border-collapse:collapse;font-family:sans-serif'>",
             "<tr><th>Provider</th><th>Metric</th><th>Usage</th><th>Resets / note</th></tr>"]
    for r in rows:
        tail = " ".join(x for x in (r.reset or "", r.note) if x)
        lines.append(f"<tr><td>{esc(r.provider)}</td><td>{esc(r.metric)}</td>"
                     f"<td align=right>{esc(cell(r))}</td><td>{esc(tail)}</td></tr>")
    lines.append("</table>")
    path.write_text("<!doctype html><meta charset=utf-8>\n"
                    "<meta http-equiv=\"Content-Security-Policy\" "
                    "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
                    + "\n".join(lines))
    print(f"[aiu] wrote {path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Advice / alerting — turn the snapshot into "where should I route, what's wasting"
# ---------------------------------------------------------------------------
# What each flat-fee subscription is best at (free at the margin -> route here first).
PROVIDER_HINTS = {
    "claude": "raisonnement profond / agentique",
    "gemini": "volume / tâches jetables (Flash)",
    "codex": "2e voix de code / diversité",
    "mistral": "Vibe / Le Chonk (contexte 1M)",
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


# Desktop notifications go through GNOME's org.gtk.Notifications (what GNotification
# uses): one app source, one notification id that each run replaces in place, and
# that an all-clear run removes. Plain notify-send (the fallback) opens a new source
# per process, so its notifications pile up and can't be withdrawn.
APP_ID = "io.github.citizenu03bb.aiu"
NOTIFICATION_ID = "alert"
NOTIFY_RETRIES = 16  # x 0.5s, only while the .desktop file is brand new
DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name=aiu
Comment=AI subscription quotas and routing advice
Exec=sh -c "aiu --advise; read -r _"
Terminal=true
Icon=utilities-system-monitor-symbolic
NoDisplay=true
X-GNOME-UsesNotifications=true
"""


def _desktop_entry_path() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(data) / "applications" / f"{APP_ID}.desktop"


def _gvariant_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _gtk_notifications(method: str, *args: str) -> bool:
    """Call org.gtk.Notifications; False when unavailable (not GNOME, unknown app...)."""
    if not shutil.which("gdbus"):
        return False
    try:
        p = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gtk.Notifications",
                            "--object-path", "/org/gtk/Notifications",
                            "--method", f"org.gtk.Notifications.{method}", APP_ID, *args],
                           capture_output=True, text=True, timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def ensure_desktop_entry() -> None:
    """GNOME only accepts notifications from an app it knows, and takes a few seconds
    to index a new .desktop file, so main() writes it early on every run."""
    entry = _desktop_entry_path()
    if entry.is_file():
        return
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(DESKTOP_ENTRY)
    except OSError:
        pass


def _notify(title: str, body: str) -> None:
    """Show (or update in place) aiu's single desktop notification."""
    ensure_desktop_entry()
    entry = _desktop_entry_path()
    try:
        fresh = time.time() - entry.stat().st_mtime < 15
    except OSError:
        fresh = False
    payload = (f"{{'title': <{_gvariant_str(title)}>, 'body': <{_gvariant_str(body)}>, "
               f"'priority': <'normal'>}}")
    # A just-written .desktop file takes GNOME a moment to index: retry briefly.
    for attempt in range(NOTIFY_RETRIES if fresh else 1):
        if attempt:
            time.sleep(0.5)
        if _gtk_notifications("AddNotification", NOTIFICATION_ID, payload):
            return
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-a", "aiu", title, body], timeout=5)
        except Exception:
            pass


def _withdraw_notification() -> None:
    """Remove a previous alert once nothing is wrong any more (no-op if none)."""
    if _desktop_entry_path().is_file():
        _gtk_notifications("RemoveNotification", NOTIFICATION_ID)


def alert(rows: list[Row], notify: bool = True) -> int:
    """Warn about long-window quota projected to reset unused or to run dry long
    before its reset, and about expiring credits. Quiet-friendly for cron: prints only warnings (and a single OK line otherwise).
    notify=False keeps it off the desktop (tests, synthetic rows)."""
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
        # The title carries the snapshot time, so the notification can't pass for
        # current data even if it sits in the tray.
        if notify:
            taken = datetime.fromtimestamp(now).strftime("%H:%M")
            _notify(f"Quota AI · relevé {taken}", "\n".join(warns))
        return 1
    if notify:
        _withdraw_notification()
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

    config = load_config(Path(os.path.expanduser(args.config)))
    ENV_FILE.update(load_env_file(config))
    # _gemini_oauth_client reads the environment. That client is the Gemini CLI's
    # public one, not a user secret, so it is the only ~/.env value exported.
    for k in ("GEMINI_CLIENT_ID", "GEMINI_CLIENT_SECRET"):
        if k in ENV_FILE:
            os.environ.setdefault(k, ENV_FILE[k])
    if args.alert:
        ensure_desktop_entry()  # before probing, so GNOME has indexed it by notify time
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
