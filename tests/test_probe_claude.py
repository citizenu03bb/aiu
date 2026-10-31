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
from aiu import probes, routing  # noqa: E402
from aiu.probes import claude  # noqa: E402

URL = claude.ANTHROPIC_USAGE_URL
TOKEN = {"read_token": True}  # the opt-in: these tests are about the token read


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
    rows = by_metric(claude.probe_claude(TOKEN))
    assert list(rows) == ["5-Hour Limit", "Weekly (all models)", "Weekly Sonnet", "Cloud Credit"]
    assert [m for m, r in rows.items() if r.sublimit] == ["Weekly Sonnet"]
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
    credit = by_metric(claude.probe_claude(TOKEN))["Cloud Credit"]
    assert (credit.kind, credit.used, credit.limit, credit.unit) == ("credit", 98.46, 100.0, "USD")
    assert credit.reset.startswith("expires in 5d") or credit.reset.startswith("expires in 6d")
    assert credit.note == ""


def test_unknown_credit_key_and_missing_remaining(creds, http):
    # Promo codenames change without notice: matched on shape, labelled by key.
    grant = {"limit_dollars": 50, "used_dollars": 20.0, "resets_at": None}
    http.on("GET", URL, (200, {"walrus_bowtie": grant}))
    [row] = claude.probe_claude(TOKEN)
    assert (row.metric, row.used, row.limit) == ("Credit (walrus_bowtie)", 30.0, 50.0)
    assert row.reset is None and row.reset_epoch is None


def test_locked_credit(creds, http):
    locked = {"limit_dollars": 100.0, "remaining_dollars": 100.0, "resets_at": iso(6 * 86400),
              "locked_reason": "verify_phone"}
    http.on("GET", URL, (200, {"iguana_necktie": locked}))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "credit" and row.note == "locked: verify_phone"
    # Routing and alerts must not offer a credit nobody can spend.
    assert routing.route_order([row]) == []


def test_expired_credit(creds, http):
    gone = {"limit_dollars": 100.0, "remaining_dollars": 40.0, "resets_at": iso(-3600)}
    http.on("GET", URL, (200, {"iguana_necktie": gone}))
    [row] = claude.probe_claude(TOKEN)
    assert row.reset == "expires now" and row.reset_epoch < time.time()
    assert routing.route_order([row]) == []


def test_extra_usage_enabled(creds, http):
    extra = {"is_enabled": True, "monthly_limit": 5000, "used_credits": 1250.0, "utilization": 25.0}
    http.on("GET", URL, (200, usage(extra_usage=extra)))
    row = by_metric(claude.probe_claude(TOKEN))["Extra Usage"]
    assert (row.kind, row.used, row.window_secs) == ("quota", 0.25, 30 * 86400)
    assert row.reset is None and row.reset_epoch is None
    # Paid overage is not subscription surplus: it never becomes a routing lot of its own.
    assert routing.route_order([row]) == []


def test_extra_usage_disabled_with_utilization(creds, http):
    extra = {"is_enabled": False, "monthly_limit": 5000, "used_credits": 0.0, "utilization": 0.0}
    http.on("GET", URL, (200, usage(extra_usage=extra)))
    assert "Extra Usage" not in by_metric(claude.probe_claude(TOKEN))


def test_monthly_limit(creds, http):
    http.on("GET", URL, (200, {"monthly_limit": {"utilization": 3.0, "resets_at": iso(20 * 86400)}}))
    [row] = claude.probe_claude(TOKEN)
    assert (row.metric, row.used, row.window_secs) == ("Monthly Limit", 0.03, 30 * 86400)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejected(creds, http, status):
    http.on("GET", URL, (status, {"type": "error", "error": {"type": "authentication_error"}}))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error"
    assert row.note == f"auth rejected ({status}) — run `claude` to refresh"


def test_server_error(creds, http):
    http.on("GET", URL, (500, {"type": "error", "error": {"type": "api_error"}}))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error" and row.note == "HTTP 500"


def test_network_error(creds, http):
    http.on("GET", URL, httpx.ConnectError("Temporary failure in name resolution"))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error" and row.note == "network: cannot connect"


def test_no_quota_reported(creds, http):
    http.on("GET", URL, (200, {"five_hour": None, "seven_day": None}))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "quota" and row.used is None
    assert row.note == "no active quotas reported"


def test_renamed_fields_are_ignored(creds, http):
    # utilization renamed: nothing usable, said so rather than inventing 0%.
    http.on("GET", URL, (200, {"five_hour": {"used_pct": 37, "resets_at": iso(3600)},
                               "fifteen_hour": {"utilization": 5.0}}))
    [row] = claude.probe_claude(TOKEN)
    assert row.used is None and row.note == "no active quotas reported"


def test_missing_reset(creds, http):
    http.on("GET", URL, (200, {"five_hour": {"utilization": 50}}))
    [row] = claude.probe_claude(TOKEN)
    assert row.used == 0.5 and row.reset is None and row.reset_epoch is None


def test_no_credentials_file(http):
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error" and "no token" in row.note
    assert http.calls == []


@pytest.mark.parametrize("content", ["{not json", json.dumps({"claudeAiOauth": None}),
                                     json.dumps({"claudeAiOauth": {"refreshToken": "x"}})])
def test_unusable_credentials_file(home, http, content):
    p = home / ".claude" / ".credentials.json"
    p.parent.mkdir()
    p.write_text(content)
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error" and "no token" in row.note
    assert http.calls == []


def test_expired_token_without_cli(home, http):
    # No `claude` on PATH to refresh it: the stale token is tried, with a warning.
    write_creds(home, token="stale", expires_in=-3600)
    http.on("GET", URL, (200, usage()))
    rows = claude.probe_claude(TOKEN)
    assert http.calls[0].headers["Authorization"] == "Bearer stale"
    assert rows[0].note == "token expired — run `claude` to refresh;"


def test_expired_token_and_rejected(home, http):
    write_creds(home, token="stale", expires_in=-3600)
    http.on("GET", URL, (401, {}))
    [row] = claude.probe_claude(TOKEN)
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
    rows = claude.probe_claude(TOKEN)
    assert http.calls[0].headers["Authorization"] == "Bearer fresh"
    assert rows[0].note == ""


def test_non_json_body(creds, http):
    http.on("GET", URL, (200, "<html>Service temporarily unavailable</html>"))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error" and row.note == "malformed response: not JSON"


@pytest.mark.parametrize("body", [[], {"five_hour": 37}, {"five_hour": {"utilization": "n/a"}}])
def test_malformed_body(creds, http, body):
    http.on("GET", URL, (200, body))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error" and row.note.startswith("malformed response")


def test_credit_without_spend_is_an_error(creds, http):
    # Neither remaining_dollars nor used_dollars: not a full grant left.
    http.on("GET", URL, (200, usage(iguana_necktie={"limit_dollars": 100.0, "resets_at": iso(86400)})))
    rows = by_metric(claude.probe_claude(TOKEN))
    assert rows["5-Hour Limit"].kind == "quota"
    credit = rows["Cloud Credit"]
    assert credit.kind == "error" and credit.used is None
    assert credit.note == "malformed response: no `remaining_dollars` or `used_dollars`"


def test_bad_header_never_quotes_the_token(home, http):
    # h11 quotes the whole header value in its message (security review, M1).
    write_creds(home, token="sk-ant-oat01-SECRET\nlogin: me@example.com")
    http.on("GET", URL, httpx.LocalProtocolError(
        "Illegal header value b'Bearer sk-ant-oat01-SECRET\\nlogin: me@example.com'"))
    [row] = claude.probe_claude(TOKEN)
    assert row.kind == "error" and "SECRET" not in row.note and "example.com" not in row.note


# The aiu-claude-usage plugin's file (docs/json-schema.md, "claude-usage.json"):
# read first, and the only source unless claude.read_token is on.

def write_usage(home, written_ago=60, windows=None, **over):
    """The file as the plugin writes it, `written_ago` seconds old."""
    if windows is None:
        windows = [{"kind": "five_hour", "percent_used": 19, "resets_at": iso(2.5 * 3600)},
                   {"kind": "seven_day", "percent_used": 16.5, "resets_at": iso(6 * 86400)}]
    body = {"schema": 1, "source": "claude-code", "written_at": iso(-written_ago),
            "rate_limits": windows, **over}
    p = home / ".local" / "state" / "aiu" / "claude-usage.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, indent=2) + "\n")
    return p


@pytest.fixture
def no_token(monkeypatch, home):
    """Claude Code's credentials are there, and reading them fails the test."""
    creds = write_creds(home)

    def refuse(*a, **kw):
        raise AssertionError("the token was read without claude.read_token")
    monkeypatch.setattr(claude, "_read_claude_token", refuse)
    monkeypatch.setattr(claude, "_refresh_claude_token", refuse)
    real_open = open

    def guarded_open(file, *a, **kw):
        if ".credentials" in str(file):
            refuse()
        return real_open(file, *a, **kw)
    monkeypatch.setattr("builtins.open", guarded_open)
    return creds


def test_fresh_reading(home, http, no_token):
    write_usage(home)
    rows = by_metric(claude.probe_claude({}))
    assert list(rows) == ["5-Hour Limit", "Weekly (all models)"]
    five, week = rows["5-Hour Limit"], rows["Weekly (all models)"]
    assert (five.kind, five.used, five.limit, five.unit, five.note) == ("quota", 0.19, 1.0, "%", "")
    assert five.window_secs == 5 * 3600 and five.reset.startswith("in 2h")
    assert five.reset_epoch == pytest.approx(time.time() + 2.5 * 3600, abs=5)
    assert (week.used, week.window_secs) == (0.165, 7 * 86400)
    assert http.calls == []


def test_reading_from_the_prototype(home, http, no_token, monkeypatch):
    # The file the prototype wrote on 2026-10-30, read a minute later.
    p = home / ".local" / "state" / "aiu" / "claude-usage.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"schema": 1, "source": "claude-code", "written_at": "2026-10-30T22:11:46.367Z", '
                 '"rate_limits": [{"kind": "five_hour", "percent_used": 19, "resets_at": '
                 '"2026-10-31T02:30:00.000Z"}, {"kind": "seven_day", "percent_used": 16, '
                 '"resets_at": "2026-11-06T15:00:00.000Z"}]}')
    now = datetime(2026, 10, 30, 22, 12, 46, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(time, "time", lambda: now)
    rows = claude.probe_claude({})
    assert [(r.metric, r.used, r.note) for r in rows] == [
        ("5-Hour Limit", 0.19, ""), ("Weekly (all models)", 0.16, "")]
    assert rows[0].reset_epoch == datetime(2026, 10, 31, 2, 30, tzinfo=timezone.utc).timestamp()


def test_xdg_state_home(home, http, no_token, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_usage(home)  # ~/.local/state is not read then
    p = tmp_path / "state" / "aiu" / "claude-usage.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60),
                             "rate_limits": [{"kind": "five_hour", "percent_used": 50,
                                              "resets_at": iso(3600)}]}))
    [row] = claude.probe_claude({})
    assert (row.metric, row.used) == ("5-Hour Limit", 0.5)


def test_stale_reading_says_how_old(home, http, no_token):
    write_usage(home, written_ago=2 * 3600 + 5 * 60)
    rows = claude.probe_claude({})
    assert [r.note for r in rows] == ["Claude Code reading from 2h 5m ago"] * 2
    assert rows[0].used == 0.19


def test_recent_reading_has_no_note(home, http, no_token):
    write_usage(home, written_ago=claude.CLAUDE_USAGE_STALE - 60)
    assert {r.note for r in claude.probe_claude({})} == {""}


def test_window_reset_since_the_reading(home, http, no_token):
    # The 5-hour window reset after the reading: 0 %, next reset unknown.
    write_usage(home, written_ago=3 * 3600, windows=[
        {"kind": "five_hour", "percent_used": 80, "resets_at": iso(-600)},
        {"kind": "seven_day", "percent_used": 40, "resets_at": iso(3 * 86400)}])
    rows = by_metric(claude.probe_claude({}))
    five = rows["5-Hour Limit"]
    assert (five.used, five.reset, five.reset_epoch, five.window_secs) == (0.0, None, None, 5 * 3600)
    assert five.note == "Claude Code reading from 3h 0m ago"
    assert rows["Weekly (all models)"].used == 0.4
    # An unknown reset with no pace is never a lot to route to or alert on.
    assert [lot["metric"] for lot in routing.route_order(list(rows.values()))] == ["Weekly (all models)"]


def test_fresh_reading_whose_windows_all_reset(home, http, no_token):
    write_usage(home, written_ago=120, windows=[
        {"kind": "five_hour", "percent_used": 80, "resets_at": iso(-60)}])
    [row] = claude.probe_claude({})
    assert (row.kind, row.used, row.reset_epoch, row.note) == ("quota", 0.0, None, "")


def test_old_reading_whose_windows_all_reset(home, http, no_token):
    write_usage(home, written_ago=9 * 86400, windows=[
        {"kind": "five_hour", "percent_used": 80, "resets_at": iso(-8 * 86400)},
        {"kind": "seven_day", "percent_used": 40, "resets_at": iso(-2 * 86400)}])
    [row] = claude.probe_claude({})
    assert row.kind == "error"
    assert row.note == ("last Claude Code reading is 9d 0h old: use Claude Code to take a new one "
                        "(or set claude.read_token = true)")


def test_missing_reset_time(home, http, no_token):
    write_usage(home, windows=[{"kind": "five_hour", "percent_used": 30, "resets_at": None}])
    [row] = claude.probe_claude({})
    assert (row.used, row.reset, row.reset_epoch) == (0.3, None, None)


def test_missing_reset_time_in_an_old_reading(home, http, no_token):
    # Older than the window itself: it has reset since, whatever it was.
    write_usage(home, written_ago=6 * 3600, windows=[
        {"kind": "five_hour", "percent_used": 30, "resets_at": None},
        {"kind": "seven_day", "percent_used": 20, "resets_at": iso(86400)}])
    rows = by_metric(claude.probe_claude({}))
    assert rows["5-Hour Limit"].used == 0.0 and rows["Weekly (all models)"].used == 0.2


def test_other_windows_and_unknown_kinds(home, http, no_token):
    write_usage(home, windows=[
        {"kind": "spend_limit", "percent_used": 120, "resets_at": None},  # a gateway's
        {"kind": "seven_day_opus", "percent_used": 5, "resets_at": iso(86400)}])
    [row] = claude.probe_claude({})
    assert (row.metric, row.used, row.window_secs) == ("Weekly Opus", 0.05, 7 * 86400)


NO_READING = ("no reading from Claude Code: install its aiu plugin (/plugin install "
              "aiu-claude-usage --marketplace citizenu03bb/aiu), or set claude.read_token = true")


def test_no_file_and_no_opt_in(home, http, no_token):
    [row] = claude.probe_claude({})
    assert (row.provider, row.metric, row.kind) == ("claude", "—", "error")
    assert row.note == NO_READING
    assert http.calls == []


def test_install_hint_survives_redaction(home, http, no_token):
    [row] = probes.collect({"claude": {"enabled": True}}, None)
    assert row.note == NO_READING


@pytest.mark.parametrize("content", [
    "{not json",
    "",
    json.dumps([1, 2]),
    json.dumps({"schema": 2, "source": "claude-code", "written_at": iso(-60), "rate_limits": []}),
    json.dumps({"schema": 1, "source": "someone-else", "written_at": iso(-60), "rate_limits": []}),
    json.dumps({"schema": 1, "source": "claude-code", "rate_limits": []}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": "yesterday", "rate_limits": []}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(86400),  # from the future
                "rate_limits": [{"kind": "five_hour", "percent_used": 1, "resets_at": iso(3600)}]}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60), "rate_limits": {}}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60), "rate_limits": []}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60),
                "rate_limits": [{"kind": "five_hour", "percent_used": "19", "resets_at": None}]}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60),
                "rate_limits": [{"kind": "five_hour", "percent_used": -1, "resets_at": None}]}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60),
                "rate_limits": [{"kind": "five_hour", "percent_used": True, "resets_at": None}]}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60),
                "rate_limits": [{"kind": "five_hour", "percent_used": 5, "resets_at": "soon"}]}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60),
                "rate_limits": [{"kind": "", "percent_used": 5, "resets_at": None}]}),
    json.dumps({"schema": 1, "source": "claude-code", "written_at": iso(-60),
                "rate_limits": [{"kind": "five_hour", "percent_used": 5}] * 33}),
])
def test_unusable_file_is_absent(home, http, no_token, content):
    p = home / ".local" / "state" / "aiu" / "claude-usage.json"
    p.parent.mkdir(parents=True)
    p.write_text(content)
    [row] = claude.probe_claude({})
    assert row.kind == "error" and row.note == NO_READING


def test_oversized_file_is_absent(home, http, no_token):
    p = write_usage(home)
    p.write_text(p.read_text() + " " * claude.CLAUDE_USAGE_MAX_BYTES)
    [row] = claude.probe_claude({})
    assert row.note == NO_READING


def test_a_directory_in_its_place_is_absent(home, http, no_token):
    (home / ".local" / "state" / "aiu" / "claude-usage.json").mkdir(parents=True)
    [row] = claude.probe_claude({})
    assert row.note == NO_READING


def test_opt_in_fills_in_what_the_file_lacks(home, http):
    # The file's windows win; the endpoint adds the credit and Weekly Sonnet.
    write_creds(home)
    write_usage(home)
    http.on("GET", URL, (200, usage()))
    rows = claude.probe_claude(TOKEN)
    assert [(r.metric, r.used) for r in rows] == [
        ("5-Hour Limit", 0.19), ("Weekly (all models)", 0.165),
        ("Weekly Sonnet", 0.12), ("Cloud Credit", 98.46)]
    assert len(http.calls) == 1


def test_opt_in_without_a_file_is_the_token_read(home, http):
    write_creds(home)
    http.on("GET", URL, (200, usage()))
    assert [r.metric for r in claude.probe_claude(TOKEN)] == [
        "5-Hour Limit", "Weekly (all models)", "Weekly Sonnet", "Cloud Credit"]


def test_opt_in_with_a_file_and_no_token(home, http):
    # The reading still shows; the failed token read says why, once.
    write_usage(home)
    rows = claude.probe_claude(TOKEN)
    assert [(r.metric, r.kind) for r in rows] == [
        ("5-Hour Limit", "quota"), ("Weekly (all models)", "quota"), ("—", "error")]
    assert "no token" in rows[-1].note and http.calls == []


def test_new_weekly_windows_from_the_endpoint(creds, http):
    http.on("GET", URL, (200, {
        "seven_day_opus": {"utilization": 10.0, "resets_at": iso(86400)},
        "seven_day_oauth_apps": {"utilization": 20.0, "resets_at": iso(86400)},
        "seven_day_cowork": {"utilization": 30.0, "resets_at": iso(86400)}}))
    rows = claude.probe_claude(TOKEN)
    assert [(r.metric, r.used, r.window_secs) for r in rows] == [
        ("Weekly Opus", 0.1, 7 * 86400), ("Weekly OAuth Apps", 0.2, 7 * 86400),
        ("Weekly Cowork", 0.3, 7 * 86400)]


def test_only_unknown_kinds_is_absent(home, http, no_token):
    write_usage(home, windows=[{"kind": "spend_limit", "percent_used": 12, "resets_at": None}])
    [row] = claude.probe_claude({})
    assert row.note == NO_READING
