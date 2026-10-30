# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Gemini Code Assist (Google One / AI Pro subscription quota via OAuth).

The Gemini CLI signs in with Google OAuth — usage is a subscription quota, not
pay-per-token API spend. Read the same token the CLI uses and hit the internal
Code Assist quota endpoint. Since Oct 2026 Google refuses that client for
individual accounts (UNSUPPORTED_CLIENT), so for them the Antigravity desktop
app's local language server (+ CSRF token from its command line) is the only
source, read first below. The `agy` CLI keeps its login in the system keyring,
not in oauth_creds.json, and its own local server's CSRF token never leaves
memory, so aiu can't read quota through agy."""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import httpx

from aiu import keys
from aiu.i18n import tr
from aiu.probes.common import _humanize_reset, _iso_epoch
from aiu.rows import Row


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
    cid, secret = (os.environ.get(k) or keys.ENV_FILE.get(k)
                   for k in ("GEMINI_CLIENT_ID", "GEMINI_CLIENT_SECRET"))
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
        print(tr("[aiu] gemini: refreshed token kept for this run only, {path} left as is: {reason}",
                 path=GEMINI_CREDS_PATH, reason=tr(reason)), file=sys.stderr)
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


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "gemini"
OPTIONS = {"enabled": ("bool", False)}
probe = probe_gemini
