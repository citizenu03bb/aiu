# SPDX-License-Identifier: GPL-3.0-or-later
"""Routing advice (SURPLUS-EDF) and alert rules on synthetic snapshots — no network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402
from aiu import Row  # noqa: E402

NOW = 1793350914.544  # 2026-10-30 09:01:54 UTC, the snapshot in docs/routing-algorithms.md
H, D, W5, WK = 3600, 86400, 5 * 3600, 7 * 86400


def quota(provider, metric, used, ttr, window):
    return Row(provider, metric, "quota", used=used, limit=1.0, unit="%",
               reset=f"in {aiu._dur(ttr)}", reset_epoch=NOW + ttr, window_secs=window)


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
    monkeypatch.setattr(aiu.time, "time", lambda: NOW)


def statuses(rows):
    return [(a["provider"], a["metric"], a["status"]) for a in aiu.route_order(rows, NOW)]


def test_snapshot_burns_claude_first_and_conserves_the_others():
    assert statuses(SNAPSHOT) == [
        ("claude", "Weekly (all models)", "burn"),
        ("claude", "Cloud Credit", "burn"),
        ("codex", "Weekly Limit", "conserve"),   # dry for longer than gemini -> first
        ("gemini", "Weekly Limit", "conserve"),
    ]


def test_advice_carries_leftover_and_runway():
    lots = {a["metric"] + a["provider"]: a for a in aiu.route_order(SNAPSHOT, NOW)}
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
    assert aiu.route_order(rows, NOW) == []


def test_extra_usage_is_paid_overage_not_free_quota():
    rows = [quota("claude", "Extra Usage", .1, 20 * D, 30 * D)]
    assert aiu.route_order(rows, NOW) == []


def test_alert_on_snapshot(capsys):
    assert aiu.alert(SNAPSHOT) == 1
    out = capsys.readouterr().out
    assert "claude · Weekly (all models): ~11% du plafond perdu au reset" in out
    assert "Cloud Credit: 98.46 USD non dépensés" in out
    assert "codex · Weekly Limit: à sec dans" in out
    assert "gemini · Weekly Limit: à sec dans" in out


def test_alert_reports_probe_errors_and_skips_locked_credit(capsys):
    rows = [Row("codex", "—", "error", note="HTTP 500"), credit(50, D, note="locked: x")]
    assert aiu.alert(rows) == 1
    out = capsys.readouterr().out
    assert "codex: données indisponibles (HTTP 500)" in out
    assert "Cloud Credit" not in out


def test_alert_all_clear(capsys):
    assert aiu.alert([quota("codex", "Weekly Limit", .5, WK / 2, WK)]) == 0


def test_advise_renders_without_error(capsys):
    assert aiu.advise(SNAPSHOT, NOW) == 0
    out = capsys.readouterr().out
    assert "Brûle d'abord claude" in out
    assert "Spend (total)" not in out   # lifetime spend is sunk, not overflow capacity
