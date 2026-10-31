# SPDX-License-Identifier: GPL-3.0-or-later
"""What the probes share: their error notes, the redaction collect() applies
to every note, reset times made readable, and the plan a client names."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from aiu.rows import Row


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


# The plan a vendor's client names ("plus", "pro", "Free"): shown as is in the
# panel's pill and the table, so only a short plain word or two gets through.
_PLAN_RE = re.compile(r"[\w][\w .+-]{0,31}")


def _plan_name(v) -> str:
    """v when it is such a name, else "" (unknown)."""
    if not isinstance(v, str):
        return ""
    v = " ".join(v.split())
    return v if _PLAN_RE.fullmatch(v) else ""


def _with_plan(rows: list[Row], plan: str) -> list[Row]:
    """rows with `plan` on each one that is not an error."""
    for r in rows:
        if r.kind != "error":
            r.plan = plan
    return rows
