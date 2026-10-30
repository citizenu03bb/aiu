# SPDX-License-Identifier: GPL-3.0-or-later
"""Early fast pace (ROADMAP wave 7, task Q): under PACE_MIN_FRAC of a window
and with no history, a window already used fast is conserve instead of
"unknown pace". Synthetic rows, a frozen clock, no network."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import notify, routing  # noqa: E402
from aiu.rows import Row  # noqa: E402

NOW = 1_800_000_000.0   # a whole second, so elapsed fractions below are exact
H, D, WK = 3600, 86400, 7 * 86400


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)


def weekly(used, elapsed, metric="Weekly (all models)"):
    """A Claude weekly window `elapsed` seconds after its reset."""
    reset = NOW + WK - elapsed
    return Row("claude", metric, "quota", used=used, limit=1.0, unit="%",
               reset=f"in {routing._dur(reset - NOW)}", reset_epoch=reset, window_secs=WK)


def past_week(used, at):
    """History: one reading `at` seconds into last week's window."""
    reset = NOW + WK - 2 * H - WK
    return [{"p": "claude", "m": "Weekly (all models)", "u": used, "r": reset, "w": WK,
             "t": reset - WK + at}]


def lot(*rows, hist=None):
    [l] = routing.route_order(list(rows), NOW, hist=hist)
    return l


def test_the_case_from_the_roadmap_conserves():
    # 2026-10-31: Claude weekly at 12% used after 3.3% of the window (~3.6x the
    # sustainable pace), shown as "unknown pace" before this rule.
    l = lot(weekly(.12, .033 * WK))
    assert l["status"] == "conserve" and l["early_pace"] is True and l["pace_from_history"] is False
    assert l["leftover"] == pytest.approx(1 - .12 / .033, abs=1e-3)   # at its own pace
    assert l["runway_secs"] == round(.88 / (.12 / (.033 * WK)))
    assert l["detail"] == ("Weekly (all models) 88% free: runs dry in ~1d16h, reset in 6d18h "
                           "→ ~5d1h without claude (early, fast pace)")


def test_advise_lists_it_under_conserve(capsys):
    routing.advise([weekly(.12, .033 * WK)], NOW)
    out = " ".join(capsys.readouterr().out.split())
    assert "unknown pace" not in out
    assert "Conserve (runs dry" in out and "(early, fast pace)" in out
    assert "No subscription has surplus" in out


@pytest.mark.parametrize("used, frac", [
    (.04, .01),    # 4x the pace, but under EARLY_FAST_MIN_USED
    (.08, .05),    # over 5%, but only 1.6x the pace
    (.0, .001),    # nothing used yet
])
def test_slow_or_small_early_use_stays_unknown(used, frac):
    l = lot(weekly(used, frac * WK))
    assert l["status"] == "?" and l["early_pace"] is False
    assert "unknown pace" in l["detail"] and "early" not in l["detail"]


@pytest.mark.parametrize("used, frac, status", [
    (.05, .025, "conserve"),    # exactly 5% used, exactly 2x the pace: both bounds count
    (.10, .05, "conserve"),     # exactly 2x the pace, well over 5%
    (.0499, .025, "?"),         # just under 5%
    (.05, .026, "?"),           # 5%, but just under 2x
    (.0999, .05, "?"),          # just under 2x
])
def test_both_thresholds_are_inclusive(used, frac, status):
    assert routing.EARLY_FAST_RATIO == 2.0 and routing.EARLY_FAST_MIN_USED == .05
    l = lot(weekly(used, frac * WK))
    assert (l["status"], l["early_pace"]) == (status, status == "conserve")


def test_history_keeps_priority():
    r = weekly(.06, .01 * WK)                         # 6% after 1%: early, fast
    assert lot(r)["status"] == "conserve"
    hist = past_week(.06, at=6 * D)                   # but last week went slowly
    l = lot(r, hist=hist)
    assert l["status"] == "burn" and l["pace_from_history"] is True and l["early_pace"] is False
    assert "(pace from past windows)" in l["detail"] and "early" not in l["detail"]
    p = routing._project(r, NOW, hist)
    assert p["history"] and not p["early"]


@pytest.mark.parametrize("used, status", [(.2, "conserve"), (.1, "on_track"), (.05, "burn")])
def test_from_ten_percent_on_the_own_pace_is_unchanged(used, status):
    r = weekly(used, routing.PACE_MIN_FRAC * WK)
    p = routing._project(r, NOW)
    assert p["pace"] == used / (routing.PACE_MIN_FRAC * WK) and not p["early"] and not p["history"]
    l = lot(r)
    assert (l["status"], l["early_pace"]) == (status, False) and "early" not in l["detail"]


def test_one_early_fast_window_conserves_the_subscription():
    l = lot(weekly(.12, .033 * WK), weekly(.01, .033 * WK, metric="Weekly Sonnet"))
    assert (l["status"], l["metric"], l["early_pace"]) == ("conserve", "Weekly (all models)", True)


def test_a_reading_from_before_the_window_gets_no_pace():
    assert routing._project(weekly(.5, 0), NOW) is None
    assert routing._project(weekly(.5, -H), NOW) is None


def test_alert_warns_it_runs_dry(capsys):
    assert notify.alert([weekly(.12, .033 * WK)], notify=False) == 1
    out = capsys.readouterr().out
    assert "runs dry in ~1d16h" in out and out.rstrip().endswith("(early, fast pace)")
    assert notify.alert([weekly(.03, .033 * WK)], notify=False) == 0   # slow: nothing to say


def test_early_marker_in_french(monkeypatch):
    monkeypatch.setenv("AIU_LANG", "fr")
    assert lot(weekly(.12, .033 * WK))["detail"].endswith("(début de fenêtre, allure rapide)")
