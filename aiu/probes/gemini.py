# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Gemini (Antigravity / Google One AI Pro subscription quota).

Usage is a subscription quota, not pay-per-token API spend. The only source is
the Antigravity desktop app's local language server (+ CSRF token from its
command line), while the app is open. aiu no longer uses the Gemini CLI's OAuth
token and client against Code Assist: the Gemini CLI FAQ calls third-party
software that piggybacks on that sign-in a violation that may get the account
suspended. The `agy` CLI keeps its login in the system keyring and its own
local server's CSRF token never leaves memory, so aiu can't read quota through
agy."""
from __future__ import annotations

import glob
import os
import re

import httpx

from aiu.i18n import tr
from aiu.probes.common import _humanize_reset, _iso_epoch
from aiu.rows import Row


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
    rows = _query_local_quota_summary()
    if rows is not None:
        return rows
    return [Row("gemini", "—", "error",
                note=tr("Antigravity desktop app not running — open it to read the Gemini quota"))]


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "gemini"
OPTIONS = {"enabled": ("bool", False)}
probe = probe_gemini
