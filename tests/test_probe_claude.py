# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude Max probe on hand-written OAuth-usage responses — no network.
The credentials file lives in the tmp home set up by conftest."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402

URL = aiu.ANTHROPIC_USAGE_URL


def iso(secs_from_now):
    return datetime.fromtimestamp(time.time() + secs_from_now, timezone.utc).isoformat()


def write_creds(home, token="sk-ant-oat01-fake", expires_in=8 * 3600):
    p = home / ".claude" / ".credentials.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    oauth = {"accessToken": token, "refreshToken": "sk-ant-ort01-fake",
             "expiresAt": int((time.time() + expires_in) * 1000),
             "scopes": ["user:inference", "user:profile"], "subscriptionType": "max"}
    p.write_text(json.dumps({"claudeAiOauth": oauth}))
    return p


def usage(**over):
    """Usage response of a Max plan with the Nov-2026 cloud-session credit."""
    d = {
        "five_hour": {"utilization": 37.0, "resets_at": iso(2.5 * 3600)},
        "seven_day": {"utilization": 86.0, "resets_at": iso(6 * 3600)},
        "seven_day_oauth_apps": None,
        "seven_day_opus": None,
        "seven_day_sonnet": {"utilization": 12.0, "resets_at": iso(6 * 3600)},
        "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": None,
                        "utilization": None},
        "iguana_necktie": {"limit_dollars": 100.0, "used_dollars": 1.54,
                           "remaining_dollars": 98.46, "resets_at": iso(6 * 86400),
                           "locked_reason": None},
    }
    d.update(over)
    return d


@pytest.fixture
def creds(home):
    return write_creds(home)


def by_metric(rows):
    return {r.metric: r for r in rows}


def test_normal_response(creds, http):
    http.on("GET", URL, (200, usage()))
    rows = by_metric(aiu.probe_claude({}))
    assert list(rows) == ["5-Hour Limit", "Weekly (all models)", "Weekly Sonnet", "Cloud Credit"]
    five = rows["5-Hour Limit"]
    assert (five.kind, five.used, five.limit, five.unit) == ("quota", 0.37, 1.0, "%")
    assert five.window_secs == 5 * 3600 and five.note == ""
    assert five.reset_epoch == pytest.approx(time.time() + 2.5 * 3600, abs=5)
    assert five.reset.startswith("in 2h")
    assert rows["Weekly (all models)"].window_secs == 7 * 86400
    [call] = http.calls
    assert call.headers["Authorization"] == "Bearer sk-ant-oat01-fake"
    assert call.headers["anthropic-beta"] == "oauth-2025-04-20"


def test_credit_row(creds, http):
    http.on("GET", URL, (200, usage()))
    credit = by_metric(aiu.probe_claude({}))["Cloud Credit"]
    assert (credit.kind, credit.used, credit.limit, credit.unit) == ("credit", 98.46, 100.0, "USD")
    assert credit.reset.startswith("expires in 5d") or credit.reset.startswith("expires in 6d")
    assert credit.note == ""


def test_unknown_credit_key_and_missing_remaining(creds, http):
    # Promo codenames change without notice: matched on shape, labelled by key.
    grant = {"limit_dollars": 50, "used_dollars": 20.0, "resets_at": None}
    http.on("GET", URL, (200, {"walrus_bowtie": grant}))
    [row] = aiu.probe_claude({})
    assert (row.metric, row.used, row.limit) == ("Credit (walrus_bowtie)", 30.0, 50.0)
    assert row.reset is None and row.reset_epoch is None


def test_locked_credit(creds, http):
    locked = {"limit_dollars": 100.0, "remaining_dollars": 100.0, "resets_at": iso(6 * 86400),
              "locked_reason": "verify_phone"}
    http.on("GET", URL, (200, {"iguana_necktie": locked}))
    [row] = aiu.probe_claude({})
    assert row.kind == "credit" and row.note == "locked: verify_phone"
    # Routing and alerts must not offer a credit nobody can spend.
    assert aiu.route_order([row]) == []


def test_expired_credit(creds, http):
    gone = {"limit_dollars": 100.0, "remaining_dollars": 40.0, "resets_at": iso(-3600)}
    http.on("GET", URL, (200, {"iguana_necktie": gone}))
    [row] = aiu.probe_claude({})
    assert row.reset == "expires now" and row.reset_epoch < time.time()
    assert aiu.route_order([row]) == []


def test_extra_usage_enabled(creds, http):
    extra = {"is_enabled": True, "monthly_limit": 5000, "used_credits": 1250.0, "utilization": 25.0}
    http.on("GET", URL, (200, usage(extra_usage=extra)))
    row = by_metric(aiu.probe_claude({}))["Extra Usage"]
    assert (row.kind, row.used, row.window_secs) == ("quota", 0.25, 30 * 86400)
    assert row.reset is None and row.reset_epoch is None
    # Paid overage is not subscription surplus: it never becomes a routing lot of its own.
    assert aiu.route_order([row]) == []


def test_extra_usage_disabled_with_utilization(creds, http):
    extra = {"is_enabled": False, "monthly_limit": 5000, "used_credits": 0.0, "utilization": 0.0}
    http.on("GET", URL, (200, usage(extra_usage=extra)))
    assert "Extra Usage" not in by_metric(aiu.probe_claude({}))


def test_monthly_limit(creds, http):
    http.on("GET", URL, (200, {"monthly_limit": {"utilization": 3.0, "resets_at": iso(20 * 86400)}}))
    [row] = aiu.probe_claude({})
    assert (row.metric, row.used, row.window_secs) == ("Monthly Limit", 0.03, 30 * 86400)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejected(creds, http, status):
    http.on("GET", URL, (status, {"type": "error", "error": {"type": "authentication_error"}}))
    [row] = aiu.probe_claude({})
    assert row.kind == "error"
    assert row.note == f"auth rejected ({status}) — run `claude` to refresh"


def test_server_error(creds, http):
    http.on("GET", URL, (500, {"type": "error", "error": {"type": "api_error"}}))
    [row] = aiu.probe_claude({})
    assert row.kind == "error" and row.note == "HTTP 500"


def test_network_error(creds, http):
    http.on("GET", URL, httpx.ConnectError("Temporary failure in name resolution"))
    [row] = aiu.probe_claude({})
    assert row.kind == "error" and row.note == "network: cannot connect"


def test_no_quota_reported(creds, http):
    http.on("GET", URL, (200, {"five_hour": None, "seven_day": None}))
    [row] = aiu.probe_claude({})
    assert row.kind == "quota" and row.used is None
    assert row.note == "no active quotas reported"


def test_renamed_fields_are_ignored(creds, http):
    # utilization renamed: nothing usable, said so rather than inventing 0%.
    http.on("GET", URL, (200, {"five_hour": {"used_pct": 37, "resets_at": iso(3600)},
                               "fifteen_hour": {"utilization": 5.0}}))
    [row] = aiu.probe_claude({})
    assert row.used is None and row.note == "no active quotas reported"


def test_missing_reset(creds, http):
    http.on("GET", URL, (200, {"five_hour": {"utilization": 50}}))
    [row] = aiu.probe_claude({})
    assert row.used == 0.5 and row.reset is None and row.reset_epoch is None


def test_no_credentials_file(http):
    [row] = aiu.probe_claude({})
    assert row.kind == "error" and "no token" in row.note
    assert http.calls == []


@pytest.mark.parametrize("content", ["{not json", json.dumps({"claudeAiOauth": None}),
                                     json.dumps({"claudeAiOauth": {"refreshToken": "x"}})])
def test_unusable_credentials_file(home, http, content):
    p = home / ".claude" / ".credentials.json"
    p.parent.mkdir()
    p.write_text(content)
    [row] = aiu.probe_claude({})
    assert row.kind == "error" and "no token" in row.note
    assert http.calls == []


def test_expired_token_without_cli(home, http):
    # No `claude` on PATH to refresh it: the stale token is tried, with a warning.
    write_creds(home, token="stale", expires_in=-3600)
    http.on("GET", URL, (200, usage()))
    rows = aiu.probe_claude({})
    assert http.calls[0].headers["Authorization"] == "Bearer stale"
    assert rows[0].note == "token expired — run `claude` to refresh;"


def test_expired_token_and_rejected(home, http):
    write_creds(home, token="stale", expires_in=-3600)
    http.on("GET", URL, (401, {}))
    [row] = aiu.probe_claude({})
    assert row.note.startswith("token expired") and "auth rejected (401)" in row.note


def test_expired_token_refreshed_by_cli(home, http, tmp_path):
    # A fake `claude auth status` rewrites the credentials file, like the real CLI
    # (shell builtins only: PATH holds nothing else).
    write_creds(home, token="stale", expires_in=-3600)
    fresh = json.dumps({"claudeAiOauth": {"accessToken": "fresh",
                                          "expiresAt": int((time.time() + 8 * 3600) * 1000)}})
    cli = tmp_path / "bin" / "claude"
    cli.write_text(f"#!/bin/sh\nprintf '%s' '{fresh}' > \"$HOME/.claude/.credentials.json\"\n")
    cli.chmod(0o755)
    http.on("GET", URL, (200, usage()))
    rows = aiu.probe_claude({})
    assert http.calls[0].headers["Authorization"] == "Bearer fresh"
    assert rows[0].note == ""


def test_non_json_body(creds, http):
    http.on("GET", URL, (200, "<html>Service temporarily unavailable</html>"))
    [row] = aiu.probe_claude({})
    assert row.kind == "error" and row.note == "malformed response: not JSON"


@pytest.mark.parametrize("body", [[], {"five_hour": 37}, {"five_hour": {"utilization": "n/a"}}])
def test_malformed_body(creds, http, body):
    http.on("GET", URL, (200, body))
    [row] = aiu.probe_claude({})
    assert row.kind == "error" and row.note.startswith("malformed response")


def test_credit_without_spend_is_an_error(creds, http):
    # Neither remaining_dollars nor used_dollars: not a full grant left.
    http.on("GET", URL, (200, usage(iguana_necktie={"limit_dollars": 100.0, "resets_at": iso(86400)})))
    rows = by_metric(aiu.probe_claude({}))
    assert rows["5-Hour Limit"].kind == "quota"
    credit = rows["Cloud Credit"]
    assert credit.kind == "error" and credit.used is None
    assert credit.note == "malformed response: no `remaining_dollars` or `used_dollars`"


def test_bad_header_never_quotes_the_token(home, http):
    # h11 quotes the whole header value in its message (security review, M1).
    write_creds(home, token="sk-ant-oat01-SECRET\nlogin: me@example.com")
    http.on("GET", URL, httpx.LocalProtocolError(
        "Illegal header value b'Bearer sk-ant-oat01-SECRET\\nlogin: me@example.com'"))
    [row] = aiu.probe_claude({})
    assert row.kind == "error" and "SECRET" not in row.note and "example.com" not in row.note
