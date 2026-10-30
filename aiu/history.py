# SPDX-License-Identifier: GPL-3.0-or-later
"""The pace history: the last reading of each recent volume window, kept in
~/.local/state/aiu/history.json."""
from __future__ import annotations

import json
import math
import os
import stat
from pathlib import Path

from aiu.routing import RATE_WINDOW_MAX
from aiu.rows import Row
from aiu.util import state_dir


# Snapshot history: the last reading of each recent volume window, so a window
# that just reset borrows the pace of the windows before it (see _project).
HISTORY_WINDOWS = 4          # windows kept per metric: the current one and 3 before it


HISTORY_MAX_AGE = 90 * 86400  # readings older than this are dropped
HISTORY_MAX_ROWS = 256       # hard cap on the file, whatever the metrics
HISTORY_MAX_BYTES = 64 * 1024  # a bigger file is not ours: ignored, then rewritten


def _history_path() -> Path:
    return state_dir() / "history.json"


def _history_entry(e) -> dict | None:
    """A stored reading if it has the expected shape, else None."""
    if not isinstance(e, dict) or set(e) != {"p", "m", "u", "r", "w", "t"}:
        return None
    if not all(isinstance(e[k], str) and 0 < len(e[k]) <= 100 for k in ("p", "m")):
        return None
    if not all(isinstance(e[k], (int, float)) and not isinstance(e[k], bool)
               and math.isfinite(e[k]) for k in ("u", "r", "w", "t")):
        return None
    return e if 0 <= e["u"] <= 2 and e["w"] > 0 else None


def load_history() -> list[dict]:
    """Stored readings; [] when the file is missing, too big, or not ours to trust."""
    try:
        path = _history_path()
        if path.stat().st_size > HISTORY_MAX_BYTES:
            return []
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    rows = data.get("readings") if isinstance(data, dict) and data.get("v") == 1 else None
    return [e for e in map(_history_entry, rows) if e] if isinstance(rows, list) else []


def _same_window(a: dict, b: dict) -> bool:
    # Reset times jitter by seconds between reads; consecutive windows are a window apart.
    return (a["p"], a["m"]) == (b["p"], b["m"]) and abs(a["r"] - b["r"]) < a["w"] / 2


def record_history(rows: list[Row], now: float) -> list[dict]:
    """Merge this run's volume-window readings into the history file and return the
    merged history. One entry per window, overwritten by each run, so the file stays
    a few KB however often aiu runs. Only provider, metric, used fraction, reset,
    window length and time are stored: never notes. Best effort: a write that fails
    leaves the old file, and the merged history is still used for this run."""
    hist = load_history()
    fresh = [r for r in rows if r.kind == "quota" and r.used is not None and r.reset_epoch
             and (r.window_secs or 0) > RATE_WINDOW_MAX and r.reset_epoch > now]
    new = [e for r in fresh if (e := _history_entry({"p": r.provider, "m": r.metric, "u": r.used,
                                                       "r": r.reset_epoch, "w": r.window_secs,
                                                       "t": now}))]
    if not new:
        return hist
    for e in new:
        hist = [old for old in hist if not _same_window(old, e)]
        hist.append(e)
    # Prune: recent readings only, the last HISTORY_WINDOWS windows per metric.
    kept, per_metric = [], {}
    for e in sorted(hist, key=lambda e: -e["r"]):
        n = per_metric.get((e["p"], e["m"]), 0)
        if now - e["t"] <= HISTORY_MAX_AGE and n < HISTORY_WINDOWS:
            per_metric[(e["p"], e["m"])] = n + 1
            kept.append(e)
    hist = sorted(kept[:HISTORY_MAX_ROWS], key=lambda e: (e["p"], e["m"], e["r"]))
    _write_history(hist)
    return hist


def _write_history(hist: list[dict]) -> None:
    """Atomic, user-only write: a 0600 temp file in a 0700 directory, renamed over
    the old file. No lock: two runs racing lose at most one reading, which the next
    run writes again. No fsync either: a file lost in a crash is simply rebuilt."""
    import tempfile
    path = _history_path()
    tmp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = path.parent.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            return
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.chmod(path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".history.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:  # mkstemp: O_EXCL, mode 0600
            json.dump({"v": 1, "readings": hist}, f, separators=(",", ":"))
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
