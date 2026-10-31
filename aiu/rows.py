# SPDX-License-Identifier: GPL-3.0-or-later
"""The normalized record every probe returns."""
from __future__ import annotations

from dataclasses import dataclass, asdict


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
    # A quota on one model or product inside the plan (Gemini 4 Argon, Weekly
    # Opus): full, it stops only that one, so the panel warns instead of alarming.
    sublimit: bool = False
    plan: str = ""              # the subscription's plan as its client names it ("plus", "pro")

    def to_public(self) -> dict:
        d = asdict(self)
        if self.kind == "quota" and self.used is not None:
            d["used_pct"] = round(self.used * 100, 1)
        return d
