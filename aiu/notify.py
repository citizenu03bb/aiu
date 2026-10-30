# SPDX-License-Identifier: GPL-3.0-or-later
"""--alert: warn about paid-for capacity about to vanish unused, in the
terminal and as one desktop notification."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from aiu.i18n import tr
from aiu.routing import _dur, _project
from aiu.rows import Row


# Alert thresholds for "paid-for capacity about to vanish unused".
ALERT_USED_MAX = 40.0        # only warn if a window is under this % used
ALERT_TTR_MAX = 48 * 3600    # ...and resets within this many seconds
ALERT_MIN_WINDOW = 6 * 3600  # ...and it's a long window (not a 5h burst bucket)
# One-shot credits vanish entirely at expiry, so warn earlier.
CREDIT_LEFT_MIN = 0.25       # warn if at least this fraction of the grant is unspent
CREDIT_TTR_MAX = 14 * 86400  # ...and it expires within this many seconds
ALERT_LEFTOVER_MIN = 0.05    # warn if a window is projected to lose this much of its cap at reset
ALERT_DRY_MIN = 24 * 3600    # warn if a window is projected to sit empty this long before reset


# Desktop notifications go through GNOME's org.gtk.Notifications (what GNotification
# uses): one app source, one notification id that each run replaces in place, and
# that an all-clear run removes. Plain notify-send (the fallback) opens a new source
# per process, so its notifications pile up and can't be withdrawn.
APP_ID = "io.github.citizenu03bb.aiu"
NOTIFICATION_ID = "alert"
NOTIFY_RETRIES = 16  # x 0.5s, only while the .desktop file is brand new
DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name=aiu
Comment=AI subscription quotas and routing advice
Exec=sh -c "aiu --advise; read -r _"
Terminal=true
Icon=utilities-system-monitor-symbolic
NoDisplay=true
X-GNOME-UsesNotifications=true
"""


def _desktop_entry_path() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(data) / "applications" / f"{APP_ID}.desktop"


def _gvariant_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _gtk_notifications(method: str, *args: str) -> bool:
    """Call org.gtk.Notifications; False when unavailable (not GNOME, unknown app...)."""
    if not shutil.which("gdbus"):
        return False
    try:
        p = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gtk.Notifications",
                            "--object-path", "/org/gtk/Notifications",
                            "--method", f"org.gtk.Notifications.{method}", APP_ID, *args],
                           capture_output=True, text=True, timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def ensure_desktop_entry() -> None:
    """GNOME only accepts notifications from an app it knows, and takes a few seconds
    to index a new .desktop file, so main() writes it early on every run."""
    entry = _desktop_entry_path()
    if entry.is_file():
        return
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(DESKTOP_ENTRY)
    except OSError:
        pass


def _notify(title: str, body: str) -> None:
    """Show (or update in place) aiu's single desktop notification."""
    ensure_desktop_entry()
    entry = _desktop_entry_path()
    try:
        fresh = time.time() - entry.stat().st_mtime < 15
    except OSError:
        fresh = False
    payload = (f"{{'title': <{_gvariant_str(title)}>, 'body': <{_gvariant_str(body)}>, "
               f"'priority': <'normal'>}}")
    # A just-written .desktop file takes GNOME a moment to index: retry briefly.
    for attempt in range(NOTIFY_RETRIES if fresh else 1):
        if attempt:
            time.sleep(0.5)
        if _gtk_notifications("AddNotification", NOTIFICATION_ID, payload):
            return
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-a", "aiu", title, body], timeout=5)
        except Exception:
            pass


def _withdraw_notification() -> None:
    """Remove a previous alert once nothing is wrong any more (no-op if none)."""
    if _desktop_entry_path().is_file():
        _gtk_notifications("RemoveNotification", NOTIFICATION_ID)


def alert(rows: list[Row], notify: bool = True, hist: list[dict] | None = None) -> int:
    """Warn about long-window quota projected to reset unused or to run dry long
    before its reset, and about expiring credits. Quiet-friendly for cron: prints only warnings (and a single OK line otherwise).
    notify=False keeps it off the desktop (tests, synthetic rows)."""
    now = time.time()
    warns = []
    for r in rows:
        if r.kind == "credit" and r.used is not None and r.limit and r.reset_epoch:
            ttr = r.reset_epoch - now
            if "locked" not in r.note and r.used / r.limit >= CREDIT_LEFT_MIN and 0 < ttr <= CREDIT_TTR_MAX:
                warns.append(tr("⚠ {provider} · {metric}: {left} {unit} unspent, {reset}",
                                provider=r.provider, metric=r.metric, left=f"{r.used:,.2f}",
                                unit=r.unit, reset=r.reset))
            continue
        if r.kind != "quota" or r.used is None or not r.reset_epoch:
            continue
        ttr = r.reset_epoch - now
        used_pct = r.used * 100
        long_window = (r.window_secs or 0) >= ALERT_MIN_WINDOW or \
                      (r.window_secs is None and ttr >= ALERT_MIN_WINDOW)
        if not long_window or ttr <= 0:
            continue
        p = _project(r, now, hist)  # None while the pace is unknown: keep the level rule
        waste = p["leftover"] if p else (1 - r.used) * (used_pct < ALERT_USED_MAX)
        if waste >= ALERT_LEFTOVER_MIN and ttr <= ALERT_TTR_MAX:
            warns.append(tr("⚠ {provider} · {metric}: ~{waste}% of the cap lost at reset, reset {reset}",
                            provider=r.provider, metric=r.metric, waste=f"{waste * 100:.0f}",
                            reset=r.reset))
        elif p and p["leftover"] < 0 and ttr - p["runway"] >= ALERT_DRY_MIN:
            warns.append(tr("⚠ {provider} · {metric}: runs dry in ~{runway} at your pace, reset {reset} "
                            "→ ~{gap} without {provider}",
                            provider=r.provider, metric=r.metric, runway=_dur(p["runway"]),
                            reset=r.reset, gap=_dur(ttr - p["runway"])))
    # Missing data is not an all-clear.
    warns += [tr("⚠ {provider}: data unavailable ({note})", provider=r.provider, note=r.note)
              for r in rows if r.kind == "error"]
    if warns:
        for w in warns:
            print(w)
        # The title carries the snapshot time, so the notification can't pass for
        # current data even if it sits in the tray.
        if notify:
            taken = datetime.fromtimestamp(now).strftime("%H:%M")
            _notify(tr("AI quota · snapshot {time}", time=taken), "\n".join(warns))
        return 1
    if notify:
        _withdraw_notification()
    print(tr("✓ nothing will be lost at reset or run dry for long"))
    return 0
