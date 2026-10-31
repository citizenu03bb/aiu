# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini probe — no network: a fake `agy` in tmp/bin replays the real reply
of `agy -p /usage --output-format json`, captured on 2026-10-31 (its free-text
`response` cut short: aiu does not read it)."""
import copy
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import clis, config  # noqa: E402
from aiu.probes import common, gemini  # noqa: E402

REPLY = json.loads(r"""
{"conversation_id":"","status":"SUCCESS","response":"Gemini Models\tWeekly Limit Remaining\t27%\t2026-11-04T07:01:55Z\nGemini Models\tFive…","duration_seconds":0,"num_turns":0,"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":0},"command":{"name":"usage","data":{"description":"Within each group, models share a weekly limit and a 5-hour limit. Quota is consumed proportionally to the cost of the tokens. Thus, limits will last longer with shorter tasks or using more cost-effective models. The 5-hour limit smooths out aggregate demand to fairly distribute global capacity across all users, while your weekly limit is tied directly to your individual tier.","groups":[{"name":"Gemini Models","description":"Models within this group: Gemini Flash, Gemini Pro","buckets":[{"id":"gemini-weekly","name":"Weekly Limit Remaining","description":"You have used some of your weekly limit, it will fully refresh in 3 days, 23 hours.","window":"weekly","remaining_fraction":0.26864364743232727,"reset_time":"2026-11-04T07:01:55Z"},{"id":"gemini-5h","name":"Five Hour Limit Remaining","window":"5h","remaining_fraction":1,"reset_time":"2026-10-31T12:18:05Z"}]},{"name":"Claude and GPT models","description":"Models within this group: Claude Opus, Claude Sonnet, GPT-OSS","buckets":[{"id":"3p-weekly","name":"Weekly Limit Remaining","window":"weekly","remaining_fraction":1,"reset_time":"2026-11-07T07:18:05Z"},{"id":"3p-5h","name":"Five Hour Limit Remaining","window":"5h","remaining_fraction":1,"reset_time":"2026-10-31T12:18:05Z"}]},{"name":"Gemini 4 Argon","description":"For paid Pro plans, Argon quota is separate. Higher Argon quota (included as part of the larger Gemini quota bucket) is available on Ultra plans.","buckets":[{"id":"gemini-argon-wtu-weekly","name":"Gemini 4 Argon Weekly Limit","description":"You have hit your weekly limit, it refreshes in 2 days, 21 hours. If on a supported paid plan, you can use AI credits in the interim or upgrade to a higher tier.","window":"weekly","remaining_fraction":0,"reset_time":"2026-11-03T04:42:11Z"}]}]}}}
""")
NOW = 1793443200.0  # 2026-10-31 10:40:00 UTC, when the reply was captured

INSTALL = "install the Antigravity CLI (agy) to read the Gemini quota"
BACKOFF = "agy did not answer /usage and may have run the model: aiu will not run agy again for 24 hours"


@pytest.fixture
def clock(monkeypatch):
    """time.time() is NOW until a test moves it; datetime.now() too, through it."""
    now = [NOW]
    monkeypatch.setattr(time, "time", lambda: now[0])

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(now[0], tz)

    monkeypatch.setattr(common, "datetime", Frozen)
    return now


@pytest.fixture
def agy(tmp_path):
    """A fake agy at tmp/bin/agy (the only place the probe looks, see conftest):
    agy.reply(...) sets what it prints and its exit code; agy.runs() is the
    argv of each run so far."""
    return FakeAgy(tmp_path)


class FakeAgy:
    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.path = tmp_path / "bin" / "agy"
        self.log = tmp_path / "agy-runs"
        self.reply(REPLY)

    def reply(self, out, code=0):
        body = self.dir / "agy-out"
        body.write_text(out if isinstance(out, str) else json.dumps(out))
        # Only `-p /usage` answers; the plan lookup (test_plans) finds no tier.
        self.path.write_text(f'#!/bin/sh\n[ "$1" = -p ] || exit 1\n'
                             f'echo "$*" >> {self.log}\n/bin/cat {body}\nexit {code}\n')
        self.path.chmod(0o755)
        return self

    def runs(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


def quota(rows):
    return {r.metric: (round(r.used, 4), r.reset_epoch, r.window_secs) for r in rows}


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------
def test_real_reply(agy, clock):
    rows = gemini.probe_gemini({})
    assert [r.metric for r in rows] == ["5-Hour Limit", "Weekly Limit", "Claude and GPT 5-Hour",
                                        "Claude and GPT Weekly", "Gemini 4 Argon Weekly"]
    assert all(r.provider == "gemini" and r.kind == "quota" and r.limit == 1.0 and r.unit == "%"
               and r.note == "" for r in rows)
    assert quota(rows) == {
        "5-Hour Limit": (0.0, epoch("2026-10-31T12:18:05Z"), 18000),
        "Weekly Limit": (0.7314, epoch("2026-11-04T07:01:55Z"), 604800),
        "Claude and GPT 5-Hour": (0.0, epoch("2026-10-31T12:18:05Z"), 18000),
        "Claude and GPT Weekly": (0.0, epoch("2026-11-07T07:18:05Z"), 604800),
        "Gemini 4 Argon Weekly": (1.0, epoch("2026-11-03T04:42:11Z"), 604800),
    }
    assert rows[0].reset == "in 1h 38m" and rows[1].reset == "in 3d 20h"
    assert agy.runs() == ["-p /usage --output-format json --log-file /dev/null"]
    # Groups the models share are the plan's windows; Gemini 4 Argon's cap is a sublimit.
    assert [r.metric for r in rows if r.sublimit] == ["Gemini 4 Argon Weekly"]


def test_runs_with_a_clean_environment_and_no_network(agy, http, monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_API_KEY", "should-not-pass")
    env = tmp_path / "env"
    agy.path.write_text(f"#!/bin/sh\n/usr/bin/env > {env}\n/bin/cat {agy.dir / 'agy-out'}\n")
    assert len(gemini.probe_gemini({})) == 5
    assert "GOOGLE_API_KEY" not in env.read_text()
    assert http.calls == []


def test_second_probe_within_15_minutes_does_not_run_agy(agy, clock):
    first = gemini.probe_gemini({})
    clock[0] += 14 * 60
    agy.reply("never read", code=1)
    second = gemini.probe_gemini({})
    assert len(agy.runs()) == 1
    assert quota(second) == quota(first)
    assert second[0].reset == "in 1h 24m"  # resets stay relative to now
    clock[0] += 2 * 60
    [row] = gemini.probe_gemini({})
    assert len(agy.runs()) == 2 and row.kind == "error"


def test_the_cache_file_is_private(agy, home):
    gemini.probe_gemini({})
    path = home / ".local" / "state" / "aiu" / "readings" / "gemini.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["ttl"] == 15 * 60


@pytest.mark.parametrize("change", [
    lambda d: d.update(num_turns=1),
    lambda d: d["usage"].update(total_tokens=812),
    lambda d: d.update(status="ERROR"),
    lambda d: d["command"].update(name="chat"),
    lambda d: d.pop("command"),
    lambda d: d.pop("num_turns"),
    lambda d: d.update(num_turns="0"),
], ids=["num_turns 1", "tokens", "status", "command name", "no command", "no num_turns", "num_turns str"])
def test_a_reply_that_may_have_run_a_turn_backs_off_24_hours(agy, clock, change):
    reply = copy.deepcopy(REPLY)
    change(reply)
    agy.reply(reply)
    [row] = gemini.probe_gemini({})
    assert row.kind == "error" and row.note == BACKOFF
    agy.reply(REPLY)
    for later in (60, 15 * 60, 6 * 3600, 24 * 3600 - 60):
        clock[0] = NOW + later
        [row] = gemini.probe_gemini({})
        assert row.note == BACKOFF
    assert len(agy.runs()) == 1
    clock[0] = NOW + 24 * 3600 + 1
    assert len(gemini.probe_gemini({})) == 5 and len(agy.runs()) == 2


def test_a_turn_counts_even_with_a_failing_exit(agy, clock):
    agy.reply({**REPLY, "status": "ERROR", "num_turns": 1}, code=1)
    [row] = gemini.probe_gemini({})
    assert row.note == BACKOFF


def test_reply_in_french(agy, monkeypatch):
    monkeypatch.setenv("AIU_LANG", "fr")
    agy.reply({**REPLY, "num_turns": 1})
    [row] = gemini.probe_gemini({})
    assert row.note.startswith("agy n'a pas répondu à /usage")


# ---------------------------------------------------------------------------
# agy missing, untrusted or failing
# ---------------------------------------------------------------------------
def test_no_agy(http):
    [row] = gemini.probe_gemini({})
    assert (row.kind, row.metric, row.note) == ("error", "—", INSTALL)
    assert http.calls == []


def test_no_agy_in_french(monkeypatch):
    monkeypatch.setenv("AIU_LANG", "fr")
    [row] = gemini.probe_gemini({})
    assert row.note == "installe la CLI Antigravity (agy) pour lire le quota Gemini"


def test_agy_on_path_is_not_used(tmp_path, monkeypatch):
    """PATH holds tmp/bin; with the known locations elsewhere, an agy there is ignored."""
    FakeAgy(tmp_path)
    monkeypatch.setattr(gemini, "AGY_LOCATIONS", (str(tmp_path / "nowhere" / "agy"),))
    [row] = gemini.probe_gemini({})
    assert row.note == INSTALL


def test_agy_cmd(agy, tmp_path, monkeypatch):
    monkeypatch.setattr(gemini, "AGY_LOCATIONS", ())
    other = tmp_path / "opt" / "agy"
    other.parent.mkdir()
    agy.path.rename(other)
    assert len(gemini.probe_gemini({"agy_cmd": str(other)})) == 5


def test_untrusted_agy_cmd_is_not_run(agy, tmp_path, capsys):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    exe = shared / "agy"
    exe.write_text(agy.path.read_text())
    exe.chmod(0o755)
    [row] = gemini.probe_gemini({"agy_cmd": str(exe)})
    assert row.note == INSTALL and agy.runs() == []
    assert "writable by other users" in capsys.readouterr().err


def test_untrusted_agy_cmd_in_config_is_dropped(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('[gemini]\nenabled = true\nagy_cmd = "/tmp/evil"\n')
    path.chmod(0o666)
    cfg = config.load_config(path)
    assert "agy_cmd" not in cfg["gemini"]


@pytest.mark.parametrize("code, out, note", [
    (1, "", "agy /usage exited with code 1 (is agy signed in?)"),
    (2, json.dumps({"status": "ERROR", "num_turns": 0}), "agy /usage exited with code 2 (is agy signed in?)"),
    (0, "Usage: agy [options]\n", "malformed response: not JSON"),
    (0, "[1, 2]", "malformed response: not JSON"),
], ids=["exit", "exit with JSON", "text", "not an object"])
def test_ordinary_failures_are_retried_after_5_minutes(agy, clock, code, out, note):
    agy.reply(out, code=code)
    [row] = gemini.probe_gemini({})
    assert (row.kind, row.note) == ("error", note)
    clock[0] += 4 * 60
    gemini.probe_gemini({})
    assert len(agy.runs()) == 1
    agy.reply(REPLY)
    clock[0] += 2 * 60
    assert len(gemini.probe_gemini({})) == 5 and len(agy.runs()) == 2


def test_timeout(agy, monkeypatch):
    monkeypatch.setattr(gemini, "AGY_TIMEOUT", 0.3)
    agy.path.write_text(f'#!/bin/sh\necho run >> {agy.log}\n/bin/sleep 10\n')
    start = time.monotonic()
    [row] = gemini.probe_gemini({})
    assert row.note == "agy /usage timed out" and time.monotonic() - start < 5


def test_agy_that_cannot_start(agy, monkeypatch):
    monkeypatch.setattr(gemini, "run_cli", lambda argv, timeout: None)
    [row] = gemini.probe_gemini({})
    assert row.note == "agy could not be started"


# ---------------------------------------------------------------------------
# Malformed groups and buckets
# ---------------------------------------------------------------------------
def with_groups(groups):
    reply = copy.deepcopy(REPLY)
    reply["command"]["data"]["groups"] = groups
    return reply


def bucket(**kw):
    return {"id": "b", "window": "5h", "remaining_fraction": 0.5, "reset_time": "2026-10-31T12:18:05Z", **kw}


@pytest.mark.parametrize("groups", [
    None, "groups", [], [None, 3, "x"], [{"name": "Gemini Models"}],
    [{"name": "Gemini Models", "buckets": "b"}],
    [{"name": 7, "buckets": [bucket()]}],
    [{"name": "Gemini Models", "buckets": [
        bucket(remaining_fraction=True), bucket(remaining_fraction="0.5"), bucket(remaining_fraction=None),
        bucket(remaining_fraction=float("nan")), bucket(remaining_fraction=float("inf")),
        bucket(window="daily"), bucket(window=None), {"window": "5h"}, "bucket", None]}],
])
def test_no_usable_bucket(agy, groups):
    agy.reply(with_groups(groups))
    [row] = gemini.probe_gemini({})
    assert (row.kind, row.note) == ("error", "malformed response: no 5h or weekly quota")


def test_no_data(agy):
    reply = copy.deepcopy(REPLY)
    del reply["command"]["data"]
    agy.reply(reply)
    [row] = gemini.probe_gemini({})
    assert row.note == "malformed response: no 5h or weekly quota"


def test_bad_buckets_are_skipped_and_values_clamped(agy):
    agy.reply(with_groups([{"name": "Gemini Models", "buckets": [
        bucket(remaining_fraction=1.7), bucket(window="weekly", remaining_fraction=-0.2, reset_time="soon"),
        bucket(remaining_fraction="x")]}]))
    rows = gemini.probe_gemini({})
    assert [(r.metric, r.used) for r in rows] == [("5-Hour Limit", 0.0), ("Weekly Limit", 1.0)]
    assert rows[1].reset is None and rows[1].reset_epoch is None


def test_labels_are_short_unique_and_plain(agy):
    agy.reply(with_groups([
        {"name": "[bold red]Claude[/] and GPT models", "buckets": [bucket(), bucket()]},
        {"name": "A very long group name that goes on and on Models", "buckets": [bucket(window="weekly")]},
        {"name": "***", "buckets": [bucket()]},
        {"name": "Gemini Models", "buckets": [bucket(window="weekly"), bucket()]},
    ]))
    assert [r.metric for r in gemini.probe_gemini({})] == [
        "5-Hour Limit", "Weekly Limit",
        "bold redClaude and GPT 5-Hour", "bold redClaude and GPT 5-Hour 2",
        "A very long group name that goes Weekly", "Group 3 5-Hour"]


def test_unreadable_cache_is_an_error_not_a_crash(agy, home):
    path = home / ".local" / "state" / "aiu" / "readings"
    path.mkdir(parents=True, mode=0o700)
    for value in ({"groups": "x"}, {"error": "exit", "code": "1"}, ["x"], {"error": "other"}):
        (path / "gemini.json").write_text(json.dumps({"written_at": time.time(), "ttl": 900, "value": value}))
        [row] = gemini.probe_gemini({})
        assert row.kind == "error" and row.note.startswith("malformed response"), value
    assert agy.runs() == []


def test_no_local_quota_scan_left():
    """aiu reads nothing of Antigravity's desktop app: no /proc scan, no CSRF token."""
    src = Path(gemini.__file__).read_text()
    assert "/proc" not in src and "csrf" not in src.lower() and "httpx" not in src
    assert not hasattr(gemini, "_query_local_quota_summary")
    assert clis.CLEAN_ENV == ("HOME", "PATH", "USER", "LANG")
