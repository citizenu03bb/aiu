# SPDX-License-Identifier: GPL-3.0-or-later
"""Routing advice (SURPLUS-EDF) and alert rules on synthetic snapshots — no network."""
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import notify, routing  # noqa: E402
from aiu.rows import Row  # noqa: E402

NOW = 1793350914.544  # 2026-10-30 09:01:54 UTC, the snapshot in docs/routing-algorithms.md
H, D, W5, WK = 3600, 86400, 5 * 3600, 7 * 86400


def quota(provider, metric, used, ttr, window):
    return Row(provider, metric, "quota", used=used, limit=1.0, unit="%",
               reset=f"in {routing._dur(ttr)}", reset_epoch=NOW + ttr, window_secs=window)


def credit(left, ttr, note=""):
    return Row("claude", "Cloud Credit", "credit", used=left, limit=100, unit="USD",
               reset="expires soon", reset_epoch=NOW + ttr, note=note)


SNAPSHOT = [
    quota("claude", "5-Hour Limit", .37, 8885, W5),
    quota("claude", "Weekly (all models)", .86, 21485, WK),
    credit(98.46, 514625),
    quota("codex", "5-Hour Limit", .29, 14612, W5),
    quota("codex", "Weekly Limit", .81, 437723, WK),
    quota("gemini", "5-Hour Limit", .03, 9484, W5),
    quota("gemini", "Weekly Limit", .70, 424800, WK),
    Row("deepseek", "Balance (USD)", "balance", used=6.22, unit="USD"),
    Row("openrouter", "Spend (total)", "spend", used=41.55, unit="USD"),
]


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)


class Calls(list):
    pass


@pytest.fixture(autouse=True)
def no_desktop(monkeypatch, tmp_path):
    """Synthetic rows must never reach the real desktop: record subprocess calls
    instead of running them, and keep the .desktop file out of the real home."""
    calls = Calls()

    def fake_run(argv, **_kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, fake_run.returncode, "", "")

    fake_run.returncode = 0
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    calls.run = fake_run
    return calls



def statuses(rows):
    return [(a["provider"], a["metric"], a["status"]) for a in routing.route_order(rows, NOW)]


def test_snapshot_burns_claude_first_and_conserves_the_others():
    assert statuses(SNAPSHOT) == [
        ("claude", "Weekly (all models)", "burn"),
        ("claude", "Cloud Credit", "burn"),
        ("codex", "Weekly Limit", "conserve"),   # dry for longer than gemini -> first
        ("gemini", "Weekly Limit", "conserve"),
    ]


def test_advice_carries_leftover_and_runway():
    lots = {a["metric"] + a["provider"]: a for a in routing.route_order(SNAPSHOT, NOW)}
    claude = lots["Weekly (all models)claude"]
    assert claude["leftover"] == pytest.approx(0.11, abs=0.01)
    assert claude["runway_secs"] is None
    codex = lots["Weekly Limitcodex"]
    assert codex["leftover"] < 0 and 10 * H < codex["runway_secs"] < 12 * H
    assert lots["Cloud Creditclaude"]["leftover"] is None


def test_headroom_is_not_the_signal():
    # Same 50% used: one resets in 1h (will be wasted), one must last 6 days.
    rows = [quota("a", "Weekly Limit", .5, 1 * H, WK), quota("b", "Weekly Limit", .5, 6 * D, WK)]
    assert [s[2] for s in statuses(rows)] == ["burn", "conserve"]


def test_young_window_has_unknown_pace():
    rows = [quota("claude", "Weekly (all models)", .02, WK - H, WK)]
    assert statuses(rows)[0][2] == "?"


def test_on_track_and_rows_without_window_length():
    on_track = [quota("codex", "Weekly Limit", .5, WK / 2, WK)]
    assert statuses(on_track)[0][2] == "on_track"
    no_window = [Row("gemini", "Gemini Pro", "quota", used=.5, limit=1.0, reset_epoch=NOW + 30 * H)]
    assert statuses(no_window)[0][2] == "?"


def test_locked_or_expired_credit_is_not_routed():
    rows = [credit(50, D, note="locked: pending"), credit(50, -60)]
    assert routing.route_order(rows, NOW) == []


def test_extra_usage_is_paid_overage_not_free_quota():
    rows = [quota("claude", "Extra Usage", .1, 20 * D, 30 * D)]
    assert routing.route_order(rows, NOW) == []


def test_alert_on_snapshot(capsys, no_desktop):
    assert notify.alert(SNAPSHOT, notify=False) == 1
    assert no_desktop == []
    out = capsys.readouterr().out
    assert "claude · Weekly (all models): ~11% of the cap lost at reset" in out
    assert "Cloud Credit: 98.46 USD unspent" in out
    assert "codex · Weekly Limit: runs dry in" in out
    assert "gemini · Weekly Limit: runs dry in" in out


def test_alert_reports_probe_errors_and_skips_locked_credit(capsys):
    rows = [Row("codex", "—", "error", note="HTTP 500"), credit(50, D, note="locked: x")]
    assert notify.alert(rows, notify=False) == 1
    out = capsys.readouterr().out
    assert "codex: data unavailable (HTTP 500)" in out
    assert "Cloud Credit" not in out


def test_alert_all_clear(capsys):
    assert notify.alert([quota("codex", "Weekly Limit", .5, WK / 2, WK)], notify=False) == 0


TAKEN = datetime.fromtimestamp(NOW).strftime("%H:%M")


def test_notification_replaces_one_gtk_notification(monkeypatch, no_desktop, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    notify.alert(SNAPSHOT)
    (argv,) = no_desktop
    assert argv[:9] == ["gdbus", "call", "--session", "--dest", "org.gtk.Notifications",
                        "--object-path", "/org/gtk/Notifications", "--method",
                        "org.gtk.Notifications.AddNotification"]
    assert argv[9:11] == [notify.APP_ID, notify.NOTIFICATION_ID]   # same id every run = replace
    assert f"'title': <'AI quota · snapshot {TAKEN}'>" in argv[11]
    # GNOME only accepts notifications from an app it knows
    assert (tmp_path / "applications" / f"{notify.APP_ID}.desktop").is_file()


def test_falls_back_to_notify_send_outside_gnome(monkeypatch, no_desktop):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    no_desktop.run.returncode = 1          # e.g. no org.gtk.Notifications service
    notify.alert(SNAPSHOT)
    assert no_desktop[-1][:4] == ["notify-send", "-a", "aiu", f"AI quota · snapshot {TAKEN}"]


def test_all_clear_withdraws_the_previous_alert(monkeypatch, no_desktop):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    notify.alert(SNAPSHOT)                    # creates the .desktop entry
    no_desktop.clear()
    assert notify.alert([quota("codex", "Weekly Limit", .5, WK / 2, WK)]) == 0
    (argv,) = no_desktop
    assert argv[8:] == ["org.gtk.Notifications.RemoveNotification", notify.APP_ID, notify.NOTIFICATION_ID]


def test_gvariant_string_escaping():
    assert notify._gvariant_str("l'été\\x\nok") == "'l\\'été\\\\x\\nok'"


def test_advise_renders_without_error(capsys):
    assert routing.advise(SNAPSHOT, NOW) == 0
    out = " ".join(capsys.readouterr().out.split())   # undo rich's line wrapping
    assert "Burn claude first" in out
    assert "Conserve codex, gemini for what only they can do." in out
    assert "Spend (total)" not in out   # lifetime spend is sunk, not overflow capacity


def test_advise_and_alert_in_french(monkeypatch, capsys):
    monkeypatch.setenv("AIU_LANG", "fr")
    routing.advise(SNAPSHOT, NOW)
    out = " ".join(capsys.readouterr().out.split())
    assert "Conseil de routage" in out and "Brûle d'abord claude" in out
    assert "Ménage codex, gemini pour ce qu'eux seuls savent faire." in out
    assert "Weekly Limit 19% libre : à sec dans" in out
    assert "raisonnement profond / agentique" in out
    assert notify.alert(SNAPSHOT, notify=False) == 1
    out = capsys.readouterr().out
    assert "claude · Weekly (all models): ~11% du plafond perdu au reset" in out
    assert "Cloud Credit: 98.46 USD non dépensés" in out
    assert "Routing advice" not in out and "runs dry" not in out


def test_french_advice_detail_keeps_the_routing():
    """Only the wording changes with the language, never the order or statuses."""
    english = routing.route_order(SNAPSHOT, NOW)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AIU_LANG", "fr")
        french = routing.route_order(SNAPSHOT, NOW)
    strip = lambda lots: [{k: v for k, v in l.items() if k != "detail"} for l in lots]
    assert strip(french) == strip(english)
    assert "libre" in french[2]["detail"] and "free" in english[2]["detail"]
