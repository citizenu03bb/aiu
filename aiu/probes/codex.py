# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Codex / ChatGPT subscription (the Codex CLI and Pi sign in with
ChatGPT, no API key). aiu asks the Codex CLI itself: `codex app-server` (its
JSON-RPC server on stdio, marked experimental) answers
`account/rateLimits/read` with Codex's own sign-in, which Codex signs and
refreshes itself. aiu reads no Codex token and calls no endpoint for it."""
from __future__ import annotations

import json
import subprocess

from aiu import __version__
from aiu.clis import find_cli, spawn
from aiu.i18n import tr
from aiu.probes.common import Malformed, _humanize_reset_unix, _malformed_note
from aiu.rows import Row


# Where the installers put `codex`, in order: a user install (npm with a
# ~/.local or ~/.npm-global prefix, or a release binary), then system-wide
# npm prefixes and Homebrew. `codex_cmd` overrides the list; PATH is never read.
CODEX_LOCATIONS = ("~/.local/bin/codex", "~/.npm-global/bin/codex",
                   "/usr/local/bin/codex", "/usr/bin/codex", "/opt/homebrew/bin/codex",
                   "/home/linuxbrew/.linuxbrew/bin/codex")
CODEX_TIMEOUT = 20      # seconds for the whole exchange (measured: 1.4 s)
CODEX_MAX_LINE = 1 << 20
CODEX_MAX_LINES = 1000  # notifications skipped before giving up on the answer
CODEX_MAX_CREDITS = 64
INIT_ID, READ_ID = 1, 2



# Notes that tell the user what to do: in the interface language (aiu.i18n).
def _note_sign_in() -> str:
    return tr("not signed in: run `codex` to sign in")


def _note_update() -> str:
    return tr("codex app-server did not understand aiu: update aiu or Codex")


def _requests() -> str:
    msgs = [
        {"jsonrpc": "2.0", "id": INIT_ID, "method": "initialize",
         "params": {"clientInfo": {"name": "aiu", "version": __version__}}},
        {"jsonrpc": "2.0", "method": "initialized"},
        {"jsonrpc": "2.0", "id": READ_ID, "method": "account/rateLimits/read"},
    ]
    return "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in msgs)


def _protocol_changed(error) -> bool:
    """An error saying the method or its shape is unknown to this Codex."""
    if not isinstance(error, dict):
        return False
    msg = error.get("message")
    return error.get("code") in (-32600, -32601, -32602) or \
        (isinstance(msg, str) and "unknown variant" in msg)


def _ask_codex(exe: str) -> dict | str:
    """The `result` of account/rateLimits/read, or the note of the error row.
    stdin stays open until the answer: app-server exits on EOF without one."""
    signed_out = False
    try:
        with spawn([exe, "app-server"], timeout=CODEX_TIMEOUT) as proc:
            proc.stdin.write(_requests())
            proc.stdin.flush()
            for _ in range(CODEX_MAX_LINES):
                line = proc.stdout.readline(CODEX_MAX_LINE)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("method") == "account/updated" and "id" not in msg:
                    params = msg.get("params")
                    if isinstance(params, dict) and "authMode" in params:
                        signed_out = params["authMode"] is None
                    continue
                if "method" in msg:
                    continue  # another notification, or a request to the client
                mid = msg.get("id")
                if isinstance(mid, bool):
                    continue
                if mid == INIT_ID:
                    if "error" in msg:
                        return _note_update()
                    continue
                if mid != READ_ID:
                    continue
                proc.stdin.close()  # done: let it exit on EOF before the kill
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                if "error" in msg:
                    return _note_update() if _protocol_changed(msg["error"]) else _note_sign_in()
                if signed_out:
                    return _note_sign_in()
                return msg.get("result")
            else:
                return "codex app-server: no answer among its messages"
            # Its stdout closed: it exited, or the timeout killed it (a signal).
            if signed_out:
                return _note_sign_in()
            if proc.wait() < 0:
                return f"codex app-server timed out ({CODEX_TIMEOUT} s)"
            return "codex app-server exited without answering"
    except OSError:
        return "codex app-server could not be started"


# Type checks: a field of the wrong type makes the whole reply malformed.
def _opt(d: dict, key: str, kind: type | tuple[type, ...]):
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, kind) or isinstance(v, bool):
        raise Malformed(f"`{key}` is not {_kind_name(kind)}")
    return v


def _req(d: dict, key: str, kind: type | tuple[type, ...]):
    v = _opt(d, key, kind)
    if v is None:
        raise Malformed(f"no `{key}`")
    return v


def _kind_name(kind) -> str:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    return " or ".join({int: "a number", float: "a number", str: "a string", dict: "an object",
                        list: "a list"}[k] for k in dict.fromkeys(kinds))


def _text(d: dict, key: str) -> str | None:
    v = _opt(d, key, str)
    if v is not None and len(v) > 100:
        raise Malformed(f"`{key}` is too long")
    return v


NUM = (int, float)


def _unique(label: str, seen: set[str]) -> str:
    out, n = label, 2
    while out in seen:
        out, n = f"{label} ({n})", n + 1
    seen.add(out)
    return out


def _window_rows(snap: dict, prefix: str, seen: set[str]) -> list[Row]:
    """A limit's windows. Any limit but Codex's own (it has a prefix) is on
    one model inside the plan: a sublimit."""
    rows = []
    for key in ("primary", "secondary"):
        win = _opt(snap, key, dict)
        if win is None:
            continue
        pct = _req(win, "usedPercent", NUM)
        mins = _opt(win, "windowDurationMins", int)
        reset = _opt(win, "resetsAt", int)
        if not 0 <= pct <= 1000 or (mins is not None and mins <= 0):
            raise Malformed(f"`{key}` is out of range")
        label = key if mins is None else "5-Hour Limit" if mins <= 360 else "Weekly Limit"
        rows.append(Row("codex", _unique(prefix + label, seen), "quota",
                        used=float(pct) / 100.0, limit=1.0, unit="%",
                        reset=_humanize_reset_unix(reset),
                        reset_epoch=float(reset) if reset else None,
                        window_secs=mins * 60 if mins else None, sublimit=bool(prefix)))
    return rows


def _reset_credit_rows(summary: dict | None, seen: set[str]) -> list[Row]:
    """One `credit` row per available reset: one reset left of one, lost at its
    expiry, so --alert and the advice flag one about to expire unused."""
    if summary is None:
        return []
    credits = _opt(summary, "credits", list) or []
    if len(credits) > CODEX_MAX_CREDITS:
        raise Malformed("too many `credits`")
    rows = []
    for c in credits:
        if not isinstance(c, dict):
            raise Malformed("a credit is not an object")
        if _req(c, "status", str) != "available":
            continue
        expires = _opt(c, "expiresAt", int)
        title = _text(c, "title") or "Rate limit reset"
        rows.append(Row("codex", _unique(title, seen), "credit", used=1.0, limit=1.0, unit="reset",
                        reset=f"expires {_humanize_reset_unix(expires)}" if expires else None,
                        reset_epoch=float(expires) if expires else None))
    return rows


def _rows(result) -> list[Row]:
    if not isinstance(result, dict):
        raise Malformed("not a JSON object")
    default = _req(result, "rateLimits", dict)
    snaps = list((_opt(result, "rateLimitsByLimitId", dict) or {}).values()) or [default]
    rows: list[Row] = []
    seen: set[str] = set()
    plan = None
    for snap in snaps:
        if not isinstance(snap, dict):
            raise Malformed("a limit is not an object")
        name = _text(snap, "limitName") or _text(snap, "limitId")
        plan = plan or _text(snap, "planType")
        # The Codex limit keeps plain labels; any other is named, so labels stay unique.
        prefix = f"{name} " if len(snaps) > 1 and _text(snap, "limitId") != "codex" and name else ""
        rows += _window_rows(snap, prefix, seen)
    plan = plan or _text(default, "planType")
    if rows:
        rows[0].note = plan or ""
    # `balance` is a string ("9.99", "0"); take a number too.
    credits = _opt(default, "credits", dict) or {}
    balance = _opt(credits, "balance", (str, int, float))
    tail: list[Row] = []
    if balance is not None and balance != "":
        try:
            tail.append(Row("codex", "Credits", "balance", used=round(float(balance), 2), unit="USD"))
        except ValueError:
            tail.append(Row("codex", "Credits", "error",
                            note="malformed response: `balance` is not a number"))
    tail += _reset_credit_rows(_opt(result, "rateLimitResetCredits", dict), seen)
    if not rows:
        rows.append(Row("codex", "—", "quota", note=f"plan {plan or 'unknown'}: no active windows"))
    return rows + tail


def probe_codex(cfg: dict) -> list[Row]:
    exe = find_cli("codex_cmd", cfg, CODEX_LOCATIONS)
    if not exe:
        if cfg.get("codex_cmd"):  # find_cli said why on stderr
            return [Row("codex", "—", "error", note="codex_cmd not run (see the warning on stderr)")]
        return [Row("codex", "—", "error", note=tr("Codex CLI not found: install it, or set codex.codex_cmd"))]
    answer = _ask_codex(exe)
    if isinstance(answer, str):
        return [Row("codex", "—", "error", note=answer)]
    try:
        return _rows(answer)
    except Exception as e:
        return [Row("codex", "—", "error", note=_malformed_note(e))]


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "codex"
OPTIONS = {"enabled": ("bool", False), "codex_cmd": ("string", None)}
probe = probe_codex
