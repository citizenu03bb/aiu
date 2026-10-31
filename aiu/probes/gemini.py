# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe: Gemini (Antigravity / Google One AI Pro subscription quota).

Usage is a subscription quota, not pay-per-token API spend. aiu asks the
Antigravity CLI for it: `agy -p /usage --output-format json` runs agy's own
`/usage` command, with no model turn, and answers with every quota group's
5-hour and weekly buckets. agy keeps its sign-in and makes the request; aiu
holds no Google credential and makes no network call for Gemini.

agy is found and run by aiu.clis (`agy_cmd`, else AGY_LOCATIONS, never PATH).
Each run starts the user's MCP servers and there is no per-run switch against
that (`--log-file /dev/null` does stop the per-run log), so a reading is
cached: 15 minutes, 5 after an ordinary failure. A reply that is not a /usage
answer may mean agy took `/usage` as a prompt and ran the model: aiu then does
not run agy again for 24 hours."""
from __future__ import annotations

import json
import math
import re

from aiu.clis import cached, find_cli, run_cli
from aiu.i18n import tr
from aiu.probes.common import Malformed, _humanize_reset, _iso_epoch, _malformed_note
from aiu.rows import Row


# Where `agy` is installed, in order; `agy_cmd` overrides the list.
AGY_LOCATIONS = ("~/.local/bin/agy", "/usr/local/bin/agy", "/usr/bin/agy", "/opt/homebrew/bin/agy")
AGY_ARGS = ("-p", "/usage", "--output-format", "json", "--log-file", "/dev/null")
AGY_TIMEOUT = 30          # seconds; /usage takes about 3
CACHE_NAME = "gemini"
TTL_READING = 15 * 60     # at most 96 runs a day, whatever the extension's interval
TTL_FAILED = 5 * 60
TTL_RAN_A_TURN = 24 * 3600

WINDOWS = {"5h": 18000, "weekly": 604800}
GEMINI_GROUP = "Gemini Models"
GEMINI_LABELS = {"5h": "5-Hour Limit", "weekly": "Weekly Limit"}
GROUP_LABELS = {"5h": "5-Hour", "weekly": "Weekly"}
GROUP_NAME_MAX = 32


def _group_label(name: str, i: int) -> str:
    """A short label from agy's group name: letters, digits and a few signs
    only (a row's metric goes into rich markup and the panel), a trailing
    "models" dropped: "Claude and GPT models" -> "Claude and GPT"."""
    name = re.sub(r"[^\w .+&-]", "", name)
    name = re.sub(r"(?i)\s+models?$", "", " ".join(name.split()))
    return name[:GROUP_NAME_MAX].strip() or f"Group {i + 1}"


def _parse_groups(groups) -> list[Row]:
    """Quota rows from `command.data.groups`: every bucket with a 5h or weekly
    window and a numeric remaining_fraction. Everything else is skipped."""
    rows: list[tuple[tuple[int, int, int], Row]] = []
    seen: set[str] = set()
    for i, g in enumerate(groups if isinstance(groups, list) else []):
        if not isinstance(g, dict):
            continue
        name = g.get("name")
        buckets = g.get("buckets")
        if not isinstance(name, str) or not isinstance(buckets, list):
            continue
        ours = name == GEMINI_GROUP
        for b in buckets:
            if not isinstance(b, dict) or b.get("window") not in WINDOWS:
                continue
            window = b["window"]
            frac = b.get("remaining_fraction")
            if isinstance(frac, bool) or not isinstance(frac, (int, float)) or not math.isfinite(frac):
                continue
            label = GEMINI_LABELS[window] if ours else f"{_group_label(name, i)} {GROUP_LABELS[window]}"
            base, n = label, 1
            while label in seen:
                n += 1
                label = f"{base} {n}"
            seen.add(label)
            reset = b.get("reset_time")
            epoch = _iso_epoch(reset) if isinstance(reset, str) and len(reset) <= 64 else None
            reset = reset if epoch is not None else None
            row = Row("gemini", label, "quota",
                      used=1.0 - max(0.0, min(1.0, float(frac))), limit=1.0, unit="%",
                      reset=_humanize_reset(reset) if reset else None, reset_epoch=epoch,
                      window_secs=WINDOWS[window])
            # "Gemini Models" first, then the groups in agy's order; 5h before weekly.
            rows.append(((0 if ours else 1, i, list(WINDOWS).index(window)), row))
    rows.sort(key=lambda kr: kr[0])
    return [r for _, r in rows]


def _ran_a_turn(d: dict) -> bool:
    """Whether a reply shows a model turn, or tokens spent."""
    usage = d.get("usage")
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return d.get("num_turns") not in (0, None) or tokens not in (0, None)


def _is_usage_answer(d: dict) -> bool:
    """The reply of agy's /usage command, which runs no model: anything else
    may be agy having taken `/usage` as a prompt."""
    command = d.get("command")
    usage = d.get("usage")
    return (d.get("status") == "SUCCESS" and isinstance(command, dict)
            and command.get("name") == "usage"
            and type(d.get("num_turns")) is int and d["num_turns"] == 0
            and (usage is None or isinstance(usage, dict) and usage.get("total_tokens", 0) == 0
                 and type(usage.get("total_tokens", 0)) is int))


def _run_usage(exe: str) -> tuple[dict, float]:
    """One `agy /usage`: (what the cache keeps, its time-to-live). The kept
    value is the reply's groups, or the failure's kind; rows are made from
    it on each probe, so reset times stay relative to now."""
    done = run_cli([exe, *AGY_ARGS], timeout=AGY_TIMEOUT)
    if done is None:
        return {"error": "start"}, TTL_FAILED
    code, out = done
    try:
        reply = json.loads(out)
    except ValueError:
        reply = None
    if isinstance(reply, dict) and _ran_a_turn(reply):
        return {"error": "turn"}, TTL_RAN_A_TURN
    if code < 0:
        return {"error": "timeout"}, TTL_FAILED
    if code != 0:
        return {"error": "exit", "code": code}, TTL_FAILED
    if not isinstance(reply, dict):
        return {"error": "json"}, TTL_FAILED
    if not _is_usage_answer(reply):
        return {"error": "turn"}, TTL_RAN_A_TURN
    data = reply["command"].get("data")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not _parse_groups(groups):
        return {"error": "nodata"}, TTL_FAILED
    return {"groups": groups}, TTL_READING


def _error(note: str) -> list[Row]:
    return [Row("gemini", "—", "error", note=note)]


def _rows(reading) -> list[Row]:
    """The rows for a reading _run_usage made (or the cache kept)."""
    if not isinstance(reading, dict):
        return _error(_malformed_note(Malformed("unreadable cached reading")))
    if "groups" in reading:
        return _parse_groups(reading["groups"]) or \
            _error(_malformed_note(Malformed("no 5h or weekly quota")))
    match reading.get("error"):
        case "turn":
            return _error(tr("agy did not answer /usage and may have run the model: "
                             "aiu will not run agy again for 24 hours"))
        case "timeout":
            return _error(tr("agy /usage timed out"))
        case "exit" if type(code := reading.get("code")) is int:
            return _error(tr("agy /usage exited with code {code} (is agy signed in?)", code=code))
        case "start":
            return _error(tr("agy could not be started"))
        case "json":
            return _error(_malformed_note(Malformed("not JSON")))
        case "nodata":
            return _error(_malformed_note(Malformed("no 5h or weekly quota")))
    return _error(_malformed_note(Malformed("unreadable cached reading")))


def probe_gemini(cfg: dict) -> list[Row]:
    exe = find_cli("agy_cmd", cfg, AGY_LOCATIONS)
    if not exe:
        return _error(tr("install the Antigravity CLI (agy) to read the Gemini quota"))
    return _rows(cached(CACHE_NAME, lambda: _run_usage(exe)))


# The Probe protocol (aiu.probes): what collect() and `aiu config` read.
NAME = "gemini"
OPTIONS = {"enabled": ("bool", False), "agy_cmd": ("string", None)}
probe = probe_gemini
