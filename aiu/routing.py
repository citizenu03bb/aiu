# SPDX-License-Identifier: GPL-3.0-or-later
"""Advice: turn the snapshot into "where should I route" (SURPLUS-EDF, see
docs/routing-algorithms.md): route_order for --json and consult, advise for
the terminal."""
from __future__ import annotations

import time

from aiu.i18n import tr
from aiu.rows import Row


# What each flat-fee subscription is best at (free at the margin -> route here first).
# English; advise() translates them.
PROVIDER_HINTS = {
    "claude": "deep reasoning / agentic",
    "gemini": "volume / throwaway tasks (Flash)",
    "codex": "second coding voice / diversity",
    "mistral": "Vibe / Le Chonk (1M context)",
}


# Advice ranks by earliest-expiring SURPLUS (EDF), not raw headroom: quota is
# lost at reset, so the reset is each window's deadline. Surplus = what is
# projected to be left at reset if you keep the pace of the current period.
RATE_WINDOW_MAX = 6 * 3600   # windows this short are rate limits (5h); longer = volume budgets
PACE_MIN_FRAC = 0.10         # trust a window's pace once 10% of it has elapsed
# ...but before that, with no history, a pace this many times the sustainable one
# (used >= ratio x elapsed fraction) with at least this much used means "runs dry".
EARLY_FAST_RATIO = 2.0
EARLY_FAST_MIN_USED = 0.05
ON_TRACK_BAND = 0.05         # projected leftover within ±5% of the cap counts as on track


HISTORY_DECAY = 0.5          # each older window weighs half as much in the blended pace


def _history_pace(r: Row, hist: list[dict] | None) -> float | None:
    """Average pace (fraction of the cap per second) of the windows before r's,
    newest first, each older one weighing HISTORY_DECAY times less; None without
    a usable reading. A reading counts once PACE_MIN_FRAC of its window had passed."""
    past = []
    for e in hist or ():
        # Same metric, same kind of window (a calendar month varies by a few days).
        if (e["p"], e["m"]) != (r.provider, r.metric) or abs(e["w"] - r.window_secs) > r.window_secs / 10:
            continue
        elapsed = e["w"] - (e["r"] - e["t"])
        if e["r"] <= r.reset_epoch - e["w"] / 2 and elapsed >= PACE_MIN_FRAC * e["w"]:
            past.append((e["r"], e["u"] / min(elapsed, e["w"])))
    if not past:
        return None
    weights = [HISTORY_DECAY ** i for i in range(len(past))]
    paces = [pace for _, pace in sorted(past, reverse=True)]
    return sum(w * pace for w, pace in zip(weights, paces)) / sum(weights)


def _dur(secs: float) -> str:
    secs = int(max(0, secs))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d{h}h" if d else f"{h}h{rem // 60:02d}m" if h else f"{rem // 60}m"


def _project(r: Row, now: float, hist: list[dict] | None = None) -> dict | None:
    """Extrapolate a quota window to its reset at the pace of its current period.
    Under PACE_MIN_FRAC elapsed that pace is noise, so the history fills in: the
    first PACE_MIN_FRAC of the window is assumed to hold what is used so far plus
    the past windows' pace for the rest of it. That blend slides from the history
    alone at the reset to the window's own pace at PACE_MIN_FRAC.
    With no history, a young window gets no pace (None) unless it is clearly
    running dry: used >= EARLY_FAST_RATIO x the elapsed fraction and >=
    EARLY_FAST_MIN_USED. It is then projected at its own pace, flagged "early";
    at that pace it runs at least a whole cap short, so it always conserves."""
    if r.used is None or not r.reset_epoch or not r.window_secs:
        return None
    ttr = r.reset_epoch - now
    elapsed = r.window_secs - ttr
    if ttr <= 0:
        return None
    young = elapsed < PACE_MIN_FRAC * r.window_secs
    past = _history_pace(r, hist) if young else None
    if past is not None:
        head = PACE_MIN_FRAC * r.window_secs
        pace = (r.used + past * (head - max(elapsed, 0))) / head
    elif young and not (elapsed > 0 and r.used >= EARLY_FAST_MIN_USED
                        and r.used >= EARLY_FAST_RATIO * elapsed / r.window_secs):
        return None                            # slow early use proves nothing
    else:
        pace = r.used / elapsed                # fraction of the cap per second
    left = 1.0 - r.used
    return {"r": r, "ttr": ttr, "pace": pace, "left": left,
            "history": past is not None, "early": young and past is None,
            "leftover": left - pace * ttr,     # > 0: expires unused ; < 0: runs dry first
            "runway": left / pace if pace else float("inf")}


def _lots(rows: list[Row], now: float, hist: list[dict] | None = None) -> list[dict]:
    """One routing lot per subscription (its volume windows) and per promo credit.
    status: burn (surplus expires unused) | ? (pace unknown) | on_track | conserve (runs dry).
    hist: past readings (load_history), for windows too young to have their own pace;
    without it a young window used fast already is conserve ("early", see _project)."""
    lots, quotas = [], {}
    for r in rows:
        if r.kind == "quota" and r.used is not None and r.metric != "Extra Usage":
            quotas.setdefault(r.provider, []).append(r)   # Extra Usage = paid overage
        elif (r.kind == "credit" and r.used and r.reset_epoch and r.reset_epoch > now
              and "locked" not in r.note):
            # Spent only by sessions you choose to run: no committed load, all surplus.
            ttr = r.reset_epoch - now
            lots.append({"prov": r.provider, "status": "burn", "ttr": ttr, "metric": r.metric,
                         "what": tr("{metric} {used}/{limit} {unit}, expires in {ttr} "
                                    "(≈{per_day} {unit}/day to use it all)",
                                    metric=r.metric, used=f"{r.used:,.2f}", limit=f"{r.limit:,.0f}",
                                    unit=r.unit, ttr=_dur(ttr), per_day=f"{r.used / ttr * 86400:,.2f}")})
    for prov, rs in quotas.items():
        vol = [r for r in rs if (r.window_secs or 0) > RATE_WINDOW_MAX] or rs
        projs = [_project(r, now, hist) for r in vol]
        known = [p for p in projs if p]
        dry = [p for p in known if p["leftover"] < -ON_TRACK_BAND]
        if dry:
            p = min(dry, key=lambda p: p["runway"])
            lot = {"status": "conserve", "ttr": p["ttr"], "dry": p["ttr"] - p["runway"],
                   "metric": p["r"].metric, "leftover": p["leftover"], "runway": p["runway"],
                   "what": tr("{metric} {left}% free: runs dry in ~{runway}, reset in {ttr} "
                              "→ ~{gap} without {provider}",
                              metric=p["r"].metric, left=f"{p['left'] * 100:.0f}",
                              runway=_dur(p["runway"]), ttr=_dur(p["ttr"]),
                              gap=_dur(p["ttr"] - p["runway"]), provider=prov)}
        elif len(known) < len(projs):
            # No usable pace: fall back to plain EDF and say so.
            r = min((r for r in vol if r.reset_epoch), key=lambda r: r.reset_epoch, default=vol[0])
            ttr = r.reset_epoch - now if r.reset_epoch else float("inf")
            free = f"{100 * (1 - r.used):.0f}"
            lot = {"status": "?", "ttr": ttr, "metric": r.metric,
                   "what": tr("{metric} {left}% free, reset in {ttr} (unknown pace)",
                              metric=r.metric, left=free, ttr=_dur(ttr)) if r.reset_epoch else
                           tr("{metric} {left}% free, reset unknown (unknown pace)",
                              metric=r.metric, left=free)}
        else:
            p = min(known, key=lambda p: p["ttr"])
            if all(q["leftover"] > ON_TRACK_BAND for q in known):
                need = p["left"] / p["ttr"]
                lot = {"status": "burn", "ttr": p["ttr"], "metric": p["r"].metric,
                       "leftover": p["leftover"],
                       "what": tr("{metric}: reset in {ttr}, ~{leftover}% of the cap lost at your pace "
                                  "({pace}%/h; using it all = {need}%/h, ×{ratio})",
                                  metric=p["r"].metric, ttr=_dur(p["ttr"]),
                                  leftover=f"{p['leftover'] * 100:.0f}", pace=f"{p['pace'] * 360000:.2f}",
                                  need=f"{need * 360000:.2f}",
                                  ratio=f"{need / p['pace']:.1f}" if p["pace"] else "∞")}
            else:
                lot = {"status": "on_track", "ttr": p["ttr"], "metric": p["r"].metric,
                       "leftover": p["leftover"],
                       "what": tr("{metric} {left}% free, reset in {ttr} — on track",
                                  metric=p["r"].metric, left=f"{p['left'] * 100:.0f}",
                                  ttr=_dur(p["ttr"]))}
        lot["history"] = lot["status"] != "?" and any(q["history"] for q in known)
        if lot["history"]:
            lot["what"] += tr(" (pace from past windows)")
        lot["early"] = lot["status"] != "?" and any(q["early"] for q in known)
        if lot["early"]:
            lot["what"] += tr(" (early, fast pace)")
        # Short windows only gate availability; flag them when they'd run dry first.
        for r in rs:
            if r in vol:
                continue
            p = _project(r, now)
            if p and p["leftover"] < 0:
                lot["what"] += tr("; {metric} runs out in ~{runway}",
                                  metric=r.metric, runway=_dur(p["runway"]))
        lot["prov"] = prov
        lots.append(lot)
    return lots


def route_order(rows: list[Row], now: float | None = None,
                hist: list[dict] | None = None) -> list[dict]:
    """Public advice for --json / consult: lots in routing order (burn & unknown by
    deadline, then on-track, then conserve by how long they'd stay dry)."""
    now = time.time() if now is None else now
    lots = _lots(rows, now, hist)
    rank = {"burn": 0, "?": 0, "on_track": 1, "conserve": 2}
    lots.sort(key=lambda l: (rank[l["status"]],
                             -l["dry"] if l["status"] == "conserve" else l["ttr"]))
    return [{"provider": l["prov"], "metric": l["metric"], "status": l["status"],
             "ttr_secs": None if l["ttr"] == float("inf") else round(l["ttr"]),
             # projected fraction of the cap left at reset (<0: short by that much)
             "leftover": round(l["leftover"], 3) if "leftover" in l else None,
             "runway_secs": round(l["runway"]) if "runway" in l else None,
             # the pace of a window that just reset, borrowed from the ones before it
             "pace_from_history": l.get("history", False),
             # a young window with no history, already used fast enough to run dry
             "early_pace": l.get("early", False),
             "detail": l["what"]} for l in lots]


def advise(rows: list[Row], now: float | None = None, hist: list[dict] | None = None) -> int:
    from rich.console import Console
    console = Console()
    now = time.time() if now is None else now
    lots = _lots(rows, now, hist)
    order = sorted((l for l in lots if l["status"] in ("burn", "?")), key=lambda l: l["ttr"])
    steady = sorted((l for l in lots if l["status"] == "on_track"), key=lambda l: l["ttr"])
    scarce = sorted((l for l in lots if l["status"] == "conserve"), key=lambda l: -l["dry"])
    console.print(tr("\n[bold]Routing advice[/bold] — earliest-expiring capacity first "
                     "(surplus projected at your current pace):\n"))
    for i, l in enumerate(order, 1):
        color = "green" if l["status"] == "burn" else "yellow"
        hint = tr(PROVIDER_HINTS[l["prov"]]) if l["prov"] in PROVIDER_HINTS else ""
        console.print(f"  {i}. [{color}]{l['prov']:<12}[/{color}] {l['what']}   [dim]{hint}[/dim]")
    if steady:
        console.print(tr("\n  [bold]On track (no surplus to give):[/bold]"))
        for l in steady:
            console.print(f"     {l['prov']:<12} {l['what']}")
    if scarce:
        console.print(tr("\n  [bold red]Conserve[/bold red] (runs dry before the reset — keep it for "
                         "what only it can do):"))
        for l in scarce:
            console.print(f"     [red]{l['prov']:<12}[/red] {l['what']}")
    # Lifetime spend is sunk, not capacity: only list what can still absorb overflow.
    paid = [r for r in rows if r.kind == "balance" and r.used is not None
            or r.kind == "spend" and r.used is not None and "total" not in r.metric.lower()]
    if paid:
        console.print(tr("\n  [dim]Paid (overflow only; currencies not added up):[/dim]"))
        for r in paid:
            console.print(f"     [dim]{r.provider:<12} {r.used:,.2f} {r.unit}  ({r.metric})[/dim]")
    for r in rows:
        if r.kind == "error":
            console.print(f"  [red]{r.provider}: {r.note}[/red]")
    keep = list(dict.fromkeys(l["prov"] for l in scarce))
    names = ", ".join(keep)
    if order:
        first = order[0]
        if first["status"] == "?":
            msg = tr("\n  [bold green]→ Burn {provider} first[/bold green] (expires in {ttr}, unknown pace): "
                     "send it everything it can do", provider=first["prov"], ttr=_dur(first["ttr"]))
        else:
            msg = tr("\n  [bold green]→ Burn {provider} first[/bold green] (expires in {ttr}): "
                     "send it everything it can do", provider=first["prov"], ttr=_dur(first["ttr"]))
        msg += tr(", including what you would give {names}.", names=names) if names else "."
        if names:
            msg += (tr(" Conserve {names} for what only it can do.", names=names) if len(keep) == 1
                    else tr(" Conserve {names} for what only they can do.", names=names))
        console.print(msg + "\n")
    elif steady:
        console.print(tr("\n  [bold]→ No expiring surplus[/bold]: route by task fit to {providers} (on track)",
                         providers=", ".join(dict.fromkeys(l["prov"] for l in steady)))
                      + (tr(", conserve {names}.", names=names) if names else ".") + "\n")
    else:
        console.print(tr("\n  [bold yellow]→ No subscription has surplus[/bold yellow]: keep each for what "
                         "only it can do; overflow the rest to the cheapest paid API.\n"))
    return 0
