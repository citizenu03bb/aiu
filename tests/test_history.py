# SPDX-License-Identifier: GPL-3.0-or-later
"""Snapshot history: the local file of past readings, and the pace it lends a
window that just reset. Synthetic rows and a throwaway home, no network."""
import json
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import cli, history, notify, probes, routing  # noqa: E402
from aiu.rows import Row  # noqa: E402

NOW = 1793350914.544  # 2026-10-30 09:01:54 UTC
H, D, W5, WK = 3600, 86400, 5 * 3600, 7 * 86400
RESET = NOW + WK - 2 * H   # the weekly window reset 2 hours ago


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)


def weekly(used, reset=RESET, provider="claude", metric="Weekly (all models)", note=""):
    return Row(provider, metric, "quota", used=used, limit=1.0, unit="%", note=note,
               reset=f"in {routing._dur(reset - NOW)}", reset_epoch=reset, window_secs=WK)


def past_week(used, weeks_back=1, at=6 * D, **kw):
    """Record a reading taken `at` into the window that ended `weeks_back` weeks ago."""
    reset = RESET - weeks_back * WK
    return history.record_history([weekly(used, reset, **kw)], reset - WK + at)


def state_file(home):
    return home / ".local" / "state" / "aiu" / "history.json"


# --- storage ---------------------------------------------------------------

def test_file_is_user_only_and_holds_only_the_reading(home):
    history.record_history([weekly(.5, note="sk-ant-secret-looking note")], NOW)
    path = state_file(home)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data == {"v": 1, "readings": [{"p": "claude", "m": "Weekly (all models)", "u": .5,
                                          "r": RESET, "w": WK, "t": NOW}]}
    assert "sk-ant" not in path.read_text()
    assert [p.name for p in path.parent.iterdir()] == ["history.json"]   # no temp file left


def test_xdg_state_home_is_honoured_and_a_relative_one_ignored(home, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert history._history_path() == tmp_path / "state" / "aiu" / "history.json"
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    assert history._history_path() == state_file(home)


def test_loose_directory_is_tightened(home):
    state_file(home).parent.mkdir(parents=True, mode=0o755)
    history.record_history([weekly(.5)], NOW)
    assert stat.S_IMODE(state_file(home).parent.stat().st_mode) == 0o700


def test_one_entry_per_window_however_often_aiu_runs():
    for i in range(50):   # the GNOME extension: every 5 minutes
        hist = history.record_history([weekly(.01 + i / 1000)], NOW + i * 300)
    assert len(hist) == 1 and hist[0]["u"] == pytest.approx(.059) and hist[0]["t"] == NOW + 49 * 300
    assert history.load_history() == hist


def test_bounded_to_a_few_windows_per_metric():
    for k in range(10, 0, -1):
        past_week(.5, weeks_back=k)
    past_week(.4, weeks_back=1, provider="codex", metric="Weekly Limit")
    hist = history.record_history([weekly(.02)], NOW)
    claude = [e for e in hist if e["p"] == "claude"]
    # The current window and the three before it; codex kept beside them.
    assert [round((RESET - e["r"]) / WK) for e in claude] == [3, 2, 1, 0]
    assert [e["p"] for e in hist].count("codex") == 1


def test_old_readings_age_out():
    past_week(.5, weeks_back=20)
    hist = history.record_history([weekly(.02)], NOW)
    assert [e["r"] for e in hist] == [RESET]


def test_only_volume_windows_with_a_future_reset_are_recorded():
    rows = [Row("claude", "5-Hour Limit", "quota", used=.3, reset_epoch=NOW + H, window_secs=W5),
            Row("gemini", "Gemini Pro", "quota", used=.3, reset_epoch=NOW + H),   # no window length
            weekly(.4, reset=NOW - 60),
            Row("claude", "—", "error", note="token expired"),
            Row("deepseek", "Balance (USD)", "balance", used=6.22, unit="USD")]
    assert history.record_history(rows, NOW) == []
    assert history.load_history() == []


@pytest.mark.parametrize("content", [
    "", "not json", "[]", '{"v": 2, "readings": []}', '{"v": 1, "readings": {}}',
    '{"v": 1, "readings": [1, {"p": "x"}, {"p": "claude", "m": "W", "u": "0.5", "r": 1, "w": 1, "t": 1}]}',
    '{"v": 1, "readings": [{"p": "claude", "m": "W", "u": NaN, "r": 1, "w": 1, "t": 1}]}',
])
def test_corrupt_file_is_ignored_then_rewritten(home, content):
    path = state_file(home)
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(content)
    assert history.load_history() == []
    hist = history.record_history([weekly(.5)], NOW)
    assert len(hist) == 1 and history.load_history() == hist


def test_valid_entries_survive_beside_corrupt_ones(home):
    good = {"p": "claude", "m": "Weekly (all models)", "u": .5, "r": RESET, "w": WK, "t": NOW}
    path = state_file(home)
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(json.dumps({"v": 1, "readings": [good, {**good, "note": "x"}, {**good, "u": True}]}))
    assert history.load_history() == [good]


def test_oversized_file_is_ignored(home):
    path = state_file(home)
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(" " * (history.HISTORY_MAX_BYTES + 1))
    assert history.load_history() == []


def test_unwritable_state_is_not_an_error(home):
    (home / ".local" / "state").mkdir(parents=True)
    (home / ".local" / "state" / "aiu").write_text("a file, not a directory")
    hist = history.record_history([weekly(.5)], NOW)
    assert len(hist) == 1   # still used for this run


# --- pace ------------------------------------------------------------------

def test_young_window_without_history_stays_unknown():
    lots = routing.route_order([weekly(.03)], NOW, hist=history.load_history())
    assert lots[0]["status"] == "?" and lots[0]["pace_from_history"] is False
    assert "unknown pace" in lots[0]["detail"]


def test_young_window_borrows_the_previous_window_pace():
    hist = past_week(.6, at=6 * D)                      # 10%/day last week
    p = routing._project(weekly(0.0, reset=NOW + WK), NOW, hist)   # reset this very second
    assert p["history"] and p["pace"] == pytest.approx(.1 / D)
    assert p["leftover"] == pytest.approx(1 - .7)


def test_blend_slides_to_the_window_own_pace():
    hist = past_week(.6, at=6 * D)
    head = routing.PACE_MIN_FRAC * WK
    r = weekly(.05, reset=NOW + WK - head / 2)          # halfway through the young part
    p = routing._project(r, NOW, hist)
    assert p["pace"] == pytest.approx((.05 + .1 / D * head / 2) / head)
    # At the threshold the history weighs nothing: no jump when it hands over.
    edge = weekly(.05, reset=NOW + WK - head + 1)
    assert routing._project(edge, NOW, hist)["pace"] == pytest.approx(.05 / head, rel=1e-4)
    past = weekly(.05, reset=NOW + WK - head)
    assert routing._project(past, NOW, hist)["history"] is False


def test_older_windows_weigh_less():
    past_week(.7, weeks_back=2, at=7 * D - H)          # 10%/day two weeks ago
    hist = past_week(.35, weeks_back=1, at=7 * D - H)  # 5%/day last week
    pace = routing._history_pace(weekly(.0), hist)
    w = routing.HISTORY_DECAY
    assert pace == pytest.approx((.35 + w * .7) / (1 + w) / (7 * D - H))


def test_early_readings_and_other_metrics_lend_no_pace():
    hist = past_week(.05, at=12 * H)                       # under 10% of last week
    hist = past_week(.5, provider="codex", metric="Weekly Limit")
    assert routing._history_pace(weekly(.0), hist) is None


def test_zero_pace_does_not_crash():
    hist = past_week(0.0)
    [lot] = routing.route_order([weekly(0.0)], NOW, hist=hist)
    assert lot["status"] == "burn" and "×∞" in lot["detail"]


def test_history_lets_a_young_window_conserve():
    hist = past_week(1.0, at=4 * D)                    # ran dry after 4 days last week
    [lot] = routing.route_order([weekly(.05)], NOW, hist=hist)
    assert lot["status"] == "conserve" and lot["pace_from_history"] is True
    assert lot["detail"].endswith("(pace from past windows)")


def test_alert_uses_the_history_too(capsys):
    hist = past_week(1.0, at=3 * D)
    # 3% after 2 hours: under EARLY_FAST_MIN_USED, so only the history gives a pace.
    assert notify.alert([weekly(.03)], notify=False) == 0
    assert notify.alert([weekly(.03)], notify=False, hist=hist) == 1
    assert "runs dry" in capsys.readouterr().out


# --- end to end ------------------------------------------------------------

@pytest.fixture
def fake_probe(monkeypatch):
    """A provider whose weekly window reset 2 hours ago, 3% used since."""
    probe = lambda _cfg: [weekly(.03), Row("claude", "5-Hour Limit", "quota", used=.03, limit=1.0,
                                           reset_epoch=NOW + 3 * H, window_secs=W5)]
    monkeypatch.setattr(probes, "PROBES", {"claude": probe})


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(tmp_path / "config.toml"),
                                      "--only", "claude", *argv])
    return cli.main()


def test_advise_projects_a_pace_right_after_a_reset(fake_probe, monkeypatch, tmp_path, capsys):
    run(monkeypatch, tmp_path, "--advise")
    out = " ".join(capsys.readouterr().out.split())
    assert "(unknown pace)" in out

    past_week(.6, at=6 * D)                            # the history holds last week
    run(monkeypatch, tmp_path, "--advise")
    out = " ".join(capsys.readouterr().out.split())
    assert "unknown pace" not in out
    assert "Weekly (all models): reset in 6d22h, ~" in out and "%/h" in out
    assert "(pace from past windows)" in out
    assert "Burn claude first (expires in 6d22h): send it everything it can do." in out


def test_json_run_records_and_flags_the_history(fake_probe, monkeypatch, tmp_path, capsys, home):
    past_week(.6, at=6 * D)
    run(monkeypatch, tmp_path, "--json")
    [lot] = json.loads(capsys.readouterr().out)["advice"]
    assert lot["status"] == "burn" and lot["pace_from_history"] is True
    assert [e["r"] for e in history.load_history()] == [RESET - WK, RESET]


def test_history_marker_in_french(monkeypatch):
    monkeypatch.setenv("AIU_LANG", "fr")
    [lot] = routing.route_order([weekly(.03)], NOW, hist=past_week(.6, at=6 * D))
    assert lot["detail"].endswith("(allure tirée des fenêtres passées)")
