# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendering the snapshot: the terminal table, the HTML page and --json."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from aiu.i18n import tr
from aiu.routing import route_order
from aiu.rows import Row

# The version of the JSON that `aiu --json` and `aiu config show --json` print
# (docs/json-schema.md). Adding a field keeps it; renaming or removing one, or
# changing its type, bumps it.
JSON_SCHEMA = 1


def render_table(rows: list[Row]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    t = Table(box=box.SIMPLE_HEAVY, title=tr("AI usage snapshot"), title_style="bold")
    t.add_column(tr("Provider"), style="cyan")
    t.add_column(tr("Metric"))
    t.add_column(tr("Usage"), justify="right")
    t.add_column(tr("Resets / note"), style="dim")

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
            usage = _credit_left(r)
        else:
            usage = "—"
        tail = r.reset or ""
        if r.note:
            tail = f"{tail}  {r.note}".strip()
        t.add_row(r.provider, r.metric, usage, tail)
    console.print(t)


def _credit_left(r: Row) -> str:
    return tr("{used}/{limit} {unit} left", used=f"{r.used:,.2f}", limit=f"{r.limit:,.0f}", unit=r.unit)


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
            return _credit_left(r)
        return "—" if r.kind != "error" else "err"
    # Provider text (plan names, credit keys, notes, a local server's reply) is
    # untrusted: every value is escaped, and the CSP blocks scripts regardless.
    from html import escape

    def esc(v) -> str:
        return escape(str(v), quote=True)
    lines = ["<table border=1 cellpadding=6 style='border-collapse:collapse;font-family:sans-serif'>",
             "<tr>" + "".join(f"<th>{esc(tr(h))}</th>"
                              for h in ("Provider", "Metric", "Usage", "Resets / note")) + "</tr>"]
    for r in rows:
        tail = " ".join(x for x in (r.reset or "", r.note) if x)
        lines.append(f"<tr><td>{esc(r.provider)}</td><td>{esc(r.metric)}</td>"
                     f"<td align=right>{esc(cell(r))}</td><td>{esc(tail)}</td></tr>")
    lines.append("</table>")
    path.write_text("<!doctype html><meta charset=utf-8>\n"
                    "<meta http-equiv=\"Content-Security-Policy\" "
                    "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
                    + "\n".join(lines))
    print(tr("[aiu] wrote {path}", path=path), file=sys.stderr)


def render_json(rows: list[Row], hist: list[dict] | None) -> None:
    """The snapshot as `aiu --json` prints it, read by the GNOME extension and consult."""
    print(json.dumps({"schema": JSON_SCHEMA,
                      "captured_at": datetime.now(timezone.utc).isoformat(),
                      "rows": [r.to_public() for r in rows],
                      "advice": route_order(rows, hist=hist)}, indent=2))
