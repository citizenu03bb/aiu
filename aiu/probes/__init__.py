# SPDX-License-Identifier: GPL-3.0-or-later
"""The probes: one module per provider, each a Probe, run by collect()."""
from __future__ import annotations

from typing import Protocol

from aiu.probes import claude, codex, deepseek, gemini, mistral, openai, openrouter
from aiu.probes.common import _redact
from aiu.rows import Row


class Probe(Protocol):
    """A provider module. NAME is its config section, OPTIONS the settings
    `aiu config` shows and sets for it (name -> (type, default when the file
    does not set it)), and probe(cfg) reads that section and returns its rows."""
    NAME: str
    OPTIONS: dict[str, tuple[str, object]]

    def probe(self, cfg: dict) -> list[Row]: ...


# Probed, listed and shown by `aiu config` in this order.
REGISTRY: tuple[Probe, ...] = (claude, codex, openai, deepseek, openrouter, gemini, mistral)
PROBES = {p.NAME: p.probe for p in REGISTRY}


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
