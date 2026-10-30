# SPDX-License-Identifier: GPL-3.0-or-later
"""Codex / ChatGPT probe on hand-written wham/usage responses — no network.
~/.codex/auth.json lives in the tmp home set up by conftest."""
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402

URL = aiu.CODEX_USAGE_URL


def window(used, secs, reset_in):
    return {"used_percent": used, "limit_window_seconds": secs,
            "reset_after_seconds": reset_in, "reset_at": int(time.time() + reset_in)}


def usage(primary=None, secondary=None, credits=None, plan="plus", limit_reached=False):
    return {"plan_type": plan,
            "rate_limit": {"allowed": not limit_reached, "limit_reached": limit_reached,
                           "primary_window": primary or window(29, 18000, 14612),
                           "secondary_window": secondary or window(54, 604800, 3 * 86400)},
            "credits": credits}


@pytest.fixture
def auth(home):
    p = home / ".codex" / "auth.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"OPENAI_API_KEY": None, "last_refresh": "2026-10-29T08:00:00Z",
                             "tokens": {"id_token": "eyJ.fake.id", "access_token": "eyJ.fake.access",
                                        "refresh_token": "rt_fake", "account_id": "acct-1234"}}))
    return p


def test_normal_response(auth, http):
    http.on("GET", URL, (200, usage()))
    five, week = aiu.probe_codex({})
    assert (five.metric, five.kind, five.used, five.window_secs) == ("5-Hour Limit", "quota", 0.29, 18000)
    assert five.note == "plus" and week.note == ""
    assert five.reset_epoch == pytest.approx(time.time() + 14612, abs=5)
    assert five.reset.startswith("in 4h")
    assert (week.metric, week.used, week.window_secs) == ("Weekly Limit", 0.54, 604800)
    [call] = http.calls
    assert call.headers["Authorization"] == "Bearer eyJ.fake.access"
    assert call.headers["chatgpt-account-id"] == "acct-1234"


def test_no_account_id(home, http):
    p = home / ".codex" / "auth.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"tokens": {"access_token": "tok"}}))
    http.on("GET", URL, (200, usage()))
    aiu.probe_codex({})
    assert "chatgpt-account-id" not in http.calls[0].headers


def test_credits_balance(auth, http):
    http.on("GET", URL, (200, usage(credits={"has_credits": True, "unlimited": False, "balance": 12.5})))
    rows = aiu.probe_codex({})
    assert (rows[-1].metric, rows[-1].kind, rows[-1].used, rows[-1].unit) == ("Credits", "balance", 12.5, "USD")


def test_credits_balance_as_string(auth, http):
    # codex-rs decodes `balance` as Option<String>, strictly (CreditStatusDetails).
    http.on("GET", URL, (200, usage(credits={"has_credits": True, "unlimited": False, "balance": "12.50"})))
    rows = aiu.probe_codex({})
    assert rows[-1].metric == "Credits" and rows[-1].used == 12.5


def test_credits_balance_not_a_number(auth, http):
    http.on("GET", URL, (200, usage(credits={"has_credits": True, "unlimited": False, "balance": "lots"})))
    five, week, credits = aiu.probe_codex({})
    assert five.kind == week.kind == "quota"
    assert credits.kind == "error" and credits.note == "malformed response: `balance` is not a number"


def test_no_credits_balance(auth, http):
    http.on("GET", URL, (200, usage(credits={"has_credits": False, "unlimited": False, "balance": None})))
    assert [r.metric for r in aiu.probe_codex({})] == ["5-Hour Limit", "Weekly Limit"]


def test_limit_reached(auth, http):
    # Locked out until reset: shown as a full window, not an error.
    http.on("GET", URL, (200, usage(primary=window(100, 18000, 1800), limit_reached=True)))
    five, _ = aiu.probe_codex({})
    assert five.used == 1.0 and five.reset_epoch > time.time()


def test_no_active_windows(auth, http):
    http.on("GET", URL, (200, {"plan_type": "free", "rate_limit": None, "credits": None}))
    [row] = aiu.probe_codex({})
    assert row.kind == "quota" and row.used is None and row.note == "plan free: no active windows"


def test_missing_window_length(auth, http):
    # limit_window_seconds dropped: the window keeps its raw key, with no length.
    w = window(10, 18000, 600)
    del w["limit_window_seconds"]
    http.on("GET", URL, (200, usage(primary=w)))
    first, _ = aiu.probe_codex({})
    assert first.metric == "primary_window" and first.window_secs is None


def test_missing_reset(auth, http):
    w = window(10, 18000, 600)
    del w["reset_at"]
    http.on("GET", URL, (200, usage(primary=w)))
    first, _ = aiu.probe_codex({})
    assert first.reset is None and first.reset_epoch is None


def test_renamed_used_percent(auth, http):
    w = window(10, 18000, 600)
    w["usedPercent"] = w.pop("used_percent")
    http.on("GET", URL, (200, usage(primary=w)))
    [week] = aiu.probe_codex({})
    assert week.metric == "Weekly Limit"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejected(auth, http, status):
    http.on("GET", URL, (status, {"detail": "Unauthorized"}))
    [row] = aiu.probe_codex({})
    assert row.kind == "error" and row.note == f"auth rejected ({status}) — run `codex` to refresh"


def test_server_error(auth, http):
    http.on("GET", URL, (500, "Internal Server Error"))
    [row] = aiu.probe_codex({})
    assert row.kind == "error" and row.note == "HTTP 500"


def test_network_error(auth, http):
    http.on("GET", URL, httpx.ReadTimeout("timed out"))
    [row] = aiu.probe_codex({})
    assert row.kind == "error" and row.note == "network: timed out"


def test_no_auth_file(http):
    [row] = aiu.probe_codex({})
    assert row.kind == "error" and "no token" in row.note and http.calls == []


@pytest.mark.parametrize("content", ["", "{}", json.dumps({"OPENAI_API_KEY": "sk-x", "tokens": None})])
def test_unusable_auth_file(home, http, content):
    # An API-key login has no OAuth tokens: nothing to read the subscription with.
    p = home / ".codex" / "auth.json"
    p.parent.mkdir()
    p.write_text(content)
    [row] = aiu.probe_codex({})
    assert row.kind == "error" and "no token" in row.note and http.calls == []


def test_non_json_body(auth, http):
    http.on("GET", URL, (200, "<!DOCTYPE html><title>Just a moment...</title>"))
    [row] = aiu.probe_codex({})
    assert row.kind == "error" and row.note == "malformed response: not JSON"


@pytest.mark.parametrize("body", [["usage"], {"rate_limit": {"primary_window": 29}}])
def test_malformed_body(auth, http, body):
    http.on("GET", URL, (200, body))
    [row] = aiu.probe_codex({})
    assert row.kind == "error" and row.note.startswith("malformed response")
