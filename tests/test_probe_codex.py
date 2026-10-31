# SPDX-License-Identifier: GPL-3.0-or-later
"""Codex probe: `codex app-server` replayed by a fake `codex` in tmp/bin (the
only place aiu looks, conftest). TRANSCRIPT is a real exchange with Codex 0.166,
captured on 2026-10-31 with its ids blanked; the id 3 request was a deliberately
unknown method, to show the error shape. No network, no real `codex`."""
import builtins
import copy
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402
from aiu import notify  # noqa: E402
from aiu.probes import codex  # noqa: E402

TRANSCRIPT = json.loads("""[{"id":1,"result":{"userAgent":"aiu/0.166.0 (Linux; x86_64) unknown (aiu; 0.6.0)","codexHome":"/home/user/.codex","platformFamily":"unix","platformOs":"linux"}},{"method":"remoteControl/status/changed","params":{"status":"disabled","serverName":"x","installationId":"x","environmentId":null},"emittedAtMs":1793431095378},{"error":{"code":-32600,"message":"Invalid request: unknown variant `account/doesNotExist`, expected one of `initialize`, `server/diagnostics`, `userVerifi…"},"id":3},{"method":"account/updated","params":{"authMode":"chatgpt","planType":"plus"},"emittedAtMs":1793431095902},{"id":2,"result":{"ordinaryUsageAllowed":true,"rateLimits":{"limitId":"codex","limitName":null,"normalModelSlug":null,"primary":{"usedPercent":0,"windowDurationMins":300,"resetsAt":1793449095},"secondary":{"usedPercent":81,"windowDurationMins":10080,"resetsAt":1793788638},"credits":{"hasCredits":false,"unlimited":false,"balance":"0"},"individualLimit":null,"spendControlReached":false,"planType":"plus","rateLimitReachedType":null},"rateLimitsByLimitId":{"codex":{"limitId":"codex","limitName":null,"normalModelSlug":null,"primary":{"usedPercent":0,"windowDurationMins":300,"resetsAt":1793449095},"secondary":{"usedPercent":81,"windowDurationMins":10080,"resetsAt":1793788638},"credits":{"hasCredits":false,"unlimited":false,"balance":"0"},"individualLimit":null,"spendControlReached":false,"planType":"plus","rateLimitReachedType":null}},"rateLimitResetCredits":{"availableCount":1,"credits":[{"id":"RateLimitResetCredit_0","resetType":"codexRateLimits","status":"available","grantedAt":1791408112,"expiresAt":1794000112,"title":"Full reset (Weekly + 5 hr)","description":"Thanks for using Codex! You've been granted one free rate limit reset."}]},"accountId":"00000000-0000-0000-0000-000000000000","rateLimitUpsell":null}}]""")  # noqa: E501


def answer(messages=TRANSCRIPT):
    return next(m for m in messages if m.get("id") == 2)


def result(messages=TRANSCRIPT):
    return answer(messages)["result"]


FAKE = """#!{python} -I
# Like `codex app-server`: reads the three requests, and exits on EOF without
# answering if stdin closes before it writes (as the real one does).
import select, sys, time
if sys.argv[1:] != ["app-server"]:
    sys.exit(64)
reqs = [sys.stdin.readline() for _ in range(3)]
with open({req!r}, "w") as f:
    f.write("".join(reqs))
ready, _, _ = select.select([sys.stdin], [], [], 0.1)
if ready and sys.stdin.readline() == "":
    sys.exit(0)
time.sleep({delay})
sys.stdout.write(open({out!r}).read())
sys.stdout.flush()
{tail}
"""


@pytest.fixture
def fake(tmp_path):
    """fake(messages, …): the `codex` aiu finds replays these messages, one
    JSON line each (a str goes out as it is). Returns the file the requests it
    read are written to."""
    def install(messages=TRANSCRIPT, path=None, delay=0, tail="sys.stdin.read()", mode=0o755):
        out, req = tmp_path / "codex-out", tmp_path / "codex-requests"
        out.write_text("".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages))
        path = path or tmp_path / "bin" / "codex"
        path.write_text(FAKE.format(python=sys.executable, req=str(req), out=str(out),
                                    delay=delay, tail=tail))
        path.chmod(mode)
        return req
    return install


def by_metric(rows):
    return {r.metric: r for r in rows}


def test_real_transcript(fake):
    req = fake()
    rows = codex.probe_codex({})
    assert [r.metric for r in rows] == ["5-Hour Limit", "Weekly Limit", "Credits",
                                        "Full reset (Weekly + 5 hr)"]
    five, week, credits, reset = rows
    assert (five.kind, five.used, five.limit, five.unit, five.note) == ("quota", 0.0, 1.0, "%", "")
    assert [r.plan for r in rows] == ["plus"] * 4  # the plan is on every row, the notes stay free
    assert (five.reset_epoch, five.window_secs) == (1793449095.0, 300 * 60)
    assert (week.kind, week.used, week.note) == ("quota", 0.81, "")
    assert (week.reset_epoch, week.window_secs) == (1793788638.0, 10080 * 60)
    assert (credits.kind, credits.used, credits.unit) == ("balance", 0.0, "USD")
    assert (reset.kind, reset.used, reset.limit, reset.unit) == ("credit", 1.0, 1.0, "reset")
    assert reset.reset_epoch == 1794000112.0 and reset.window_secs is None
    assert reset.reset.startswith("expires ")
    # What aiu sent: initialize with its name and version, initialized, the read.
    sent = [json.loads(line) for line in req.read_text().splitlines()]
    assert [m.get("method") for m in sent] == ["initialize", "initialized", "account/rateLimits/read"]
    assert [m.get("id") for m in sent] == [1, None, 2]
    assert sent[0]["params"] == {"clientInfo": {"name": "aiu", "version": aiu.__version__}}
    assert all(m["jsonrpc"] == "2.0" for m in sent)


def test_never_opens_codex_credentials(fake, home, monkeypatch):
    """~/.codex/auth.json is there, and opening it fails the test."""
    auth = home / ".codex" / "auth.json"
    auth.parent.mkdir()
    auth.write_text(json.dumps({"tokens": {"access_token": "eyJ.fake.access", "account_id": "acct-1"}}))

    def guard(real):
        def guarded(file, *a, **kw):
            if not isinstance(file, int) and "auth.json" in os.fsdecode(file):
                raise AssertionError("aiu opened ~/.codex/auth.json")
            return real(file, *a, **kw)
        return guarded
    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))
    monkeypatch.setattr(os, "open", guard(os.open))
    fake()
    assert codex.probe_codex({})[0].metric == "5-Hour Limit"
    assert not hasattr(codex, "_read_codex_token") and not hasattr(codex, "CODEX_USAGE_URL")


def test_notifications_and_noise_around_the_answer(fake):
    msgs = ["not json", "[1, 2]", {"method": "thread/started", "params": {}}, *TRANSCRIPT,
            {"method": "account/updated", "params": {"authMode": None}}]
    fake(msgs)
    assert by_metric(codex.probe_codex({}))["Weekly Limit"].used == 0.81


def test_answers_out_of_order(fake):
    # The read answered before initialize; another id in between.
    msgs = [answer(), {"id": 3, "error": {"code": -32600, "message": "x"}}, TRANSCRIPT[0]]
    fake(msgs)
    assert codex.probe_codex({})[0].metric == "5-Hour Limit"


@pytest.mark.parametrize("msgs", [
    # account/updated says no one is signed in, whatever comes next.
    [TRANSCRIPT[0], {"method": "account/updated", "params": {"authMode": None}}, answer()],
    [TRANSCRIPT[0], {"id": 2, "error": {"code": -32603, "message": "not logged in"}}],
])
def test_not_signed_in(fake, msgs):
    fake(msgs)
    [row] = codex.probe_codex({})
    assert row.kind == "error" and row.note == "not signed in: run `codex` to sign in"


def test_not_signed_in_in_french(fake, monkeypatch):
    monkeypatch.setenv("AIU_LANG", "fr")
    fake([{"method": "account/updated", "params": {"authMode": None}}, answer()])
    [row] = codex.probe_codex({})
    assert row.note == "pas connecté : lance `codex` pour te connecter"


@pytest.mark.parametrize("msgs", [
    [TRANSCRIPT[0], {"id": 2, "error": {"code": -32600, "message":
                                         "Invalid request: unknown variant `account/rateLimits/read`"}}],
    [TRANSCRIPT[0], {"id": 2, "error": {"code": -1, "message": "unknown variant `x`"}}],
    [{"id": 1, "error": {"code": -32602, "message": "missing field `clientInfo`"}}, answer()],
])
def test_protocol_changed(fake, msgs):
    fake(msgs)
    [row] = codex.probe_codex({})
    assert row.kind == "error" and row.note == "codex app-server did not understand aiu: update aiu or Codex"


def test_falls_back_to_rate_limits(fake):
    msgs = copy.deepcopy(TRANSCRIPT)
    result(msgs)["rateLimitsByLimitId"] = None
    result(msgs)["rateLimits"]["secondary"]["usedPercent"] = 42
    fake(msgs)
    assert by_metric(codex.probe_codex({}))["Weekly Limit"].used == 0.42


def test_several_limits_get_their_name(fake):
    msgs = copy.deepcopy(TRANSCRIPT)
    other = copy.deepcopy(result(msgs)["rateLimits"])
    other.update(limitId="codex_bengalfox", limitName="GPT-5.5-Codex-Spark", planType=None,
                 secondary=None)
    other["primary"]["usedPercent"] = 7
    result(msgs)["rateLimitsByLimitId"]["codex_bengalfox"] = other
    fake(msgs)
    rows = by_metric(codex.probe_codex({}))
    assert list(rows) == ["5-Hour Limit", "Weekly Limit", "GPT-5.5-Codex-Spark 5-Hour Limit",
                          "Credits", "Full reset (Weekly + 5 hr)"]
    assert rows["GPT-5.5-Codex-Spark 5-Hour Limit"].used == 0.07
    # A limit on one model inside the plan: used up, it stops only that model.
    assert [m for m, r in rows.items() if r.sublimit] == ["GPT-5.5-Codex-Spark 5-Hour Limit"]
    assert {r.plan for r in rows.values()} == {"plus"}


def test_reset_credits(fake):
    msgs = copy.deepcopy(TRANSCRIPT)
    soon = int(time.time() + 3 * 86400)
    result(msgs)["rateLimitResetCredits"]["credits"] += [
        {"id": "b", "resetType": "codexRateLimits", "status": "redeemed", "grantedAt": 1, "expiresAt": soon,
         "title": "Used one"},
        {"id": "c", "resetType": "codexRateLimits", "status": "available", "grantedAt": 1, "expiresAt": soon,
         "title": "Full reset (Weekly + 5 hr)"},
        {"id": "d", "resetType": "unknown", "status": "available", "grantedAt": 1, "expiresAt": None},
    ]
    fake(msgs)
    rows = by_metric(codex.probe_codex({}))
    assert "Used one" not in rows
    soon_row = rows["Full reset (Weekly + 5 hr) (2)"]
    assert soon_row.reset_epoch == soon and soon_row.reset.startswith("expires in 2d")
    untitled = rows["Rate limit reset"]
    assert untitled.kind == "credit" and untitled.reset is None and untitled.reset_epoch is None


def test_alert_flags_a_reset_about_to_expire(fake, capsys):
    msgs = copy.deepcopy(TRANSCRIPT)
    result(msgs)["rateLimitResetCredits"]["credits"][0]["expiresAt"] = int(time.time() + 3 * 86400)
    fake(msgs)
    rows = [r for r in codex.probe_codex({}) if r.kind == "credit"]
    assert notify.alert(rows, notify=False) == 1
    assert "codex · Full reset (Weekly + 5 hr): 1.00 reset unspent, expires in 2d" in capsys.readouterr().out


def test_no_windows(fake):
    msgs = copy.deepcopy(TRANSCRIPT)
    result(msgs).update(rateLimitsByLimitId=None, rateLimitResetCredits=None)
    result(msgs)["rateLimits"].update(primary=None, secondary=None, credits=None, planType="free")
    fake(msgs)
    [row] = codex.probe_codex({})
    assert row.kind == "quota" and row.used is None and row.note == "plan free: no active windows"
    assert row.plan == "free"


def test_window_without_length_or_reset(fake):
    msgs = copy.deepcopy(TRANSCRIPT)
    result(msgs)["rateLimitsByLimitId"]["codex"]["primary"] = {"usedPercent": 10}
    fake(msgs)
    first = codex.probe_codex({})[0]
    assert (first.metric, first.used, first.window_secs, first.reset, first.reset_epoch) == \
        ("primary", 0.1, None, None, None)


@pytest.mark.parametrize("balance, used", [("12.50", 12.5), (9.99, 9.99)])
def test_credits_balance(fake, balance, used):
    msgs = copy.deepcopy(TRANSCRIPT)
    result(msgs)["rateLimits"]["credits"]["balance"] = balance
    fake(msgs)
    assert by_metric(codex.probe_codex({}))["Credits"].used == used


def test_credits_balance_not_a_number(fake):
    msgs = copy.deepcopy(TRANSCRIPT)
    result(msgs)["rateLimits"]["credits"]["balance"] = "lots"
    fake(msgs)
    row = by_metric(codex.probe_codex({}))["Credits"]
    assert row.kind == "error" and row.note == "malformed response: `balance` is not a number"


def test_no_credits_balance(fake):
    msgs = copy.deepcopy(TRANSCRIPT)
    result(msgs)["rateLimits"]["credits"]["balance"] = None
    fake(msgs)
    assert "Credits" not in by_metric(codex.probe_codex({}))


def _set(path, value):
    def change(msgs):
        d = result(msgs)
        for key in path[:-1]:
            d = d[key]
        d[path[-1]] = value
    return change


@pytest.mark.parametrize("change", [
    lambda msgs: answer(msgs).update(result=None),
    lambda msgs: answer(msgs).update(result=["usage"]),
    lambda msgs: result(msgs).pop("rateLimits"),
    _set(["rateLimitsByLimitId"], ["codex"]),
    _set(["rateLimitsByLimitId", "codex"], 81),
    _set(["rateLimitsByLimitId", "codex", "primary"], 81),
    _set(["rateLimitsByLimitId", "codex", "secondary", "usedPercent"], "81"),
    _set(["rateLimitsByLimitId", "codex", "secondary", "usedPercent"], True),
    _set(["rateLimitsByLimitId", "codex", "secondary", "usedPercent"], -5),
    lambda msgs: result(msgs)["rateLimitsByLimitId"]["codex"]["secondary"].pop("usedPercent"),
    _set(["rateLimitsByLimitId", "codex", "secondary", "resetsAt"], "soon"),
    _set(["rateLimitsByLimitId", "codex", "secondary", "windowDurationMins"], 10080.5),
    _set(["rateLimitsByLimitId", "codex", "planType"], {"name": "plus"}),
    _set(["rateLimitsByLimitId", "codex", "limitName"], "x" * 500),
    _set(["rateLimits", "credits"], "0"),
    _set(["rateLimitResetCredits"], []),
    _set(["rateLimitResetCredits", "credits"], {"id": "x"}),
    _set(["rateLimitResetCredits", "credits", 0], "x"),
    _set(["rateLimitResetCredits", "credits", 0, "status"], 1),
    _set(["rateLimitResetCredits", "credits", 0, "title"], 123),
    _set(["rateLimitResetCredits", "credits", 0, "expiresAt"], "2026-11-24"),
])
def test_malformed_answer(fake, change):
    msgs = copy.deepcopy(TRANSCRIPT)
    change(msgs)
    fake(msgs)
    [row] = codex.probe_codex({})
    assert row.kind == "error" and row.note.startswith("malformed response"), row.note


def test_timeout(fake, monkeypatch):
    monkeypatch.setattr(codex, "CODEX_TIMEOUT", 0.5)
    fake(delay=30)
    start = time.monotonic()
    [row] = codex.probe_codex({})
    assert time.monotonic() - start < 10
    assert row.kind == "error" and row.note == "codex app-server timed out (0.5 s)"


def test_exits_without_answering(fake):
    fake([TRANSCRIPT[0]], tail="")
    [row] = codex.probe_codex({})
    assert row.kind == "error" and row.note == "codex app-server exited without answering"


def test_cannot_start(fake, tmp_path):
    # Executable, but nothing can run it.
    path = tmp_path / "bin" / "codex"
    path.write_text("#!/nonexistent/interpreter\n")
    path.chmod(0o755)
    [row] = codex.probe_codex({})
    assert row.kind == "error" and row.note == "codex app-server could not be started"


def test_missing_binary():
    [row] = codex.probe_codex({})
    assert row.kind == "error" and row.note == "Codex CLI not found: install it, or set codex.codex_cmd"


def test_missing_binary_in_french(monkeypatch):
    monkeypatch.setenv("AIU_LANG", "fr")
    [row] = codex.probe_codex({})
    assert row.note == "Codex CLI introuvable : installe-la, ou règle codex.codex_cmd"


def test_codex_cmd(fake, tmp_path):
    (tmp_path / "opt").mkdir()
    fake(path=tmp_path / "opt" / "codex")
    assert codex.probe_codex({"codex_cmd": str(tmp_path / "opt" / "codex")})[0].metric == "5-Hour Limit"


def test_untrusted_codex_cmd_is_not_run(fake, tmp_path, capsys):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    req = fake(path=shared / "codex")
    fake()  # a good one in tmp/bin: codex_cmd is not silently replaced by it
    [row] = codex.probe_codex({"codex_cmd": str(shared / "codex")})
    assert row.kind == "error" and row.note == "codex_cmd not run (see the warning on stderr)"
    assert "writable by other users" in capsys.readouterr().err
    assert not req.exists()
