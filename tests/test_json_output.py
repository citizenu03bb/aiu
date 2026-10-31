# SPDX-License-Identifier: GPL-3.0-or-later
"""`aiu --json` prints what it printed before the split into a package (ROADMAP
wave 7, task O), plus the fields added since (since_main): every probe answers
from recorded replies, the clock is frozen, and the output is compared with a
fixture and, where git history has it, with the last single-file aiu.py (MAIN)
run side by side. No network.

MAIN printed no schema and no `early_pace`: the fixtures hold its output with
`"schema": 1` added as the first key (task P) and `"early_pace": false` after
each lot's `pace_from_history` (wave 7, task Q: no lot here is young enough to
be true), and the side-by-side test compares the package with MAIN's output
plus those same lines (since_main), so any other difference still fails.

tests/fixtures/snapshot.json is the contract with the GNOME extension
(docs/json-schema.md): the package's output when most providers answer and two
fail. gnome-extension/tests/test_aiuClient.js reads the same file through
AiuClient, so renaming a field on either side alone fails a test.

To record the fixtures again (MAIN's output plus the schema, and snapshot.json):
  AIU_RECORD_JSON=1 uv run --with pytest pytest tests/test_json_output.py"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aiu import cli, keys, render  # noqa: E402
from aiu.probes import mistral  # noqa: E402

MAIN = "7204525"  # main just before the split: aiu.py was the whole program
FIXTURES = ROOT / "tests" / "fixtures"
NOW = 1793000000.0  # 2026-10-26 07:33:20 UTC


def iso(secs_from_now: float) -> str:
    return datetime.fromtimestamp(NOW + secs_from_now, timezone.utc).isoformat().replace("+00:00", "Z")


def trpc(payload):
    return {"result": {"data": {"json": payload}}}


PROVIDERS = ("claude", "codex", "openai", "deepseek", "openrouter", "gemini", "mistral")
# mistral: MAIN read MISTRAL_ADMIN_COOKIE by default; the package only when named.
KEY_ENV = {"openai": "OPENAI_ADMIN_KEY", "deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY",
           "mistral": "MISTRAL_ADMIN_COOKIE"}
# claude.read_token: these scenarios read Claude through the token, as MAIN
# did; MAIN ignores the option. The plugin's file is tested in test_probe_claude.
CONFIG = "".join(f"[{name}]\nenabled = true\n" + (f'key_env = "{KEY_ENV[name]}"\n' if name in KEY_ENV else "")
                 + ("read_token = true\n" if name == "claude" else "") + "\n" for name in PROVIDERS)


# Gemini's quota groups when a scenario has them, [(group name, [(window,
# remaining fraction, reset)])]: run() gives them to the package as the reply of
# a fake `agy /usage`, and to MAIN as Antigravity's local summary.
GEMINI: dict = {}


def codex_app_server(home, result):
    """The package asks `codex app-server` (tmp/bin/codex, the only place it
    looks: conftest); MAIN read ~/.codex/auth.json and called wham/usage, so
    each scenario gives both the same figures. result None: not signed in.
    The first scenario to set it wins, like the HTTP routes."""
    path = home.parent / "bin" / "codex"
    if path.exists():
        return
    msgs = [{"id": 1, "result": {}},
            {"method": "account/updated", "params": {"authMode": "chatgpt" if result else None}},
            {"id": 2, "result": result} if result else
            {"id": 2, "error": {"code": -32603, "message": "not signed in"}}]
    lines = "".join(json.dumps(m) + "\n" for m in msgs)
    path.write_text(f"#!{sys.executable} -I\nimport sys\n[sys.stdin.readline() for _ in range(3)]\n"
                    f"sys.stdout.write({lines!r})\nsys.stdout.flush()\nsys.stdin.read()\n")
    path.chmod(0o755)


def answers(home, http, monkeypatch):
    """Every provider answers: quotas, a credit, spend, balances."""
    (home / ".claude").mkdir()
    (home / ".claude" / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "sk-ant-oat01-fake", "expiresAt": (NOW + 3600) * 1000}}))
    http.on("GET", "api.anthropic.com/api/oauth/usage", (200, {
        "five_hour": {"utilization": 12.0, "resets_at": iso(2 * 3600)},
        "seven_day": {"utilization": 40.0, "resets_at": iso(3 * 86400)},
        "seven_day_sonnet": {"utilization": 5.0, "resets_at": iso(3 * 86400)},
        "extra_usage": {"utilization": None, "is_enabled": False},
        "iguana_necktie": {"limit_dollars": 50, "remaining_dollars": 31.5, "resets_at": iso(10 * 86400)},
    }))
    codex_app_server(home, {"rateLimits": {
        "limitId": "codex", "planType": "plus", "credits": {"hasCredits": True, "unlimited": False,
                                                            "balance": "9.99"},
        "primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": int(NOW + 3600)},
        "secondary": {"usedPercent": 70, "windowDurationMins": 10080, "resetsAt": int(NOW + 86400)}}})
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps(
        {"tokens": {"access_token": "codex-fake", "account_id": "acct-1"}}))
    http.on("GET", "chatgpt.com/backend-api/wham/usage", (200, {
        "plan_type": "plus",
        "rate_limit": {"primary_window": {"used_percent": 20, "limit_window_seconds": 18000,
                                          "reset_at": int(NOW + 3600)},
                       "secondary_window": {"used_percent": 70, "limit_window_seconds": 604800,
                                            "reset_at": int(NOW + 86400)}},
        "credits": {"balance": "9.99"},
    }))
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-fake")
    http.on("GET", "api.openai.com/v1/organizations/costs", (200, {
        "data": [{"results": [{"amount": {"value": 1.234}}, {"amount": {"value": 0.5}}]}],
        "has_more": False}))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-fake")
    http.on("GET", "api.deepseek.com/user/balance", (200, {
        "is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "4.20"},
                                                {"currency": "CNY", "total_balance": "12.00"}]}))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fake")
    # A key without a cap of its own: the account's credits give the balance.
    http.on("GET", "openrouter.ai/api/v1/key", (200, {"data": {
        "label": "aiu", "limit": None, "limit_remaining": None, "limit_reset": None, "usage": 0.0,
        "usage_daily": 0.0, "usage_weekly": 0.0, "usage_monthly": 0.0, "is_free_tier": False}}))
    http.on("GET", "openrouter.ai/api/v1/credits", (200, {"data": {"total_credits": 20, "total_usage": 7.5}}))
    monkeypatch.setitem(GEMINI, "groups", [("Gemini Models", [
        ("5h", 0.75, iso(2 * 3600)),
        ("weekly", 0.98, iso(6 * 86400 + 20 * 3600))])])  # a young week: its pace is unknown
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "ory_session_x=fake; csrf_token=fake")
    http.on("GET", mistral.MISTRAL_TRPC + "usage.costBreakdown", (200, trpc({
        "groups": [{"billingMetric": "mistral-medium-3-5", "billingGroup": "input", "usageType": "vibe",
                    "apiZone": "global", "serviceTier": "standard", "value": 1_000_000},
                   {"billingMetric": "mistral-large", "billingGroup": "input", "usageType": "completion",
                    "apiZone": "global", "serviceTier": "standard", "value": 2_000_000}],
        "metadata": {"hasMore": False}})))
    http.on("GET", mistral.MISTRAL_TRPC + "usage.prices", (200, trpc({"prices": [
        {"billingMetric": "mistral-medium-3-5", "billingGroup": "input", "eventType": "api_tokens",
         "apiZone": "global", "serviceTier": "standard", "price": 0.000001275},
        {"billingMetric": "mistral-large", "billingGroup": "input", "eventType": "api_tokens",
         "apiZone": "global", "serviceTier": "standard", "price": 0.000002}]})))


def failures(home, http, monkeypatch):
    """Every provider fails, each its own way."""
    codex_app_server(home, None)
    http.on("GET", "chatgpt.com/backend-api/wham/usage", (401, {"error": "expired"}))
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": "codex-fake"}}))
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-fake")
    http.on("GET", "api.openai.com/v1/organizations/costs", (500, "oops"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-fake")
    http.on("GET", "api.deepseek.com/user/balance", (200, ["not", "an", "object"]))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fake")
    http.on("GET", "openrouter.ai/api/v1/key", httpx.ConnectError("offline"))
    http.on("GET", "openrouter.ai/api/v1/credits", httpx.ConnectError("offline"))
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "ory_session_x=fake")
    http.on("GET", mistral.MISTRAL_TRPC + "usage.costBreakdown",
            (307, "", {"location": "https://auth.mistral.ai/ui/login"}))


SCENARIOS = {"answers": answers, "failures": failures}


def snapshot(home, http, monkeypatch):
    """The answers, but codex's week runs dry before its reset and openrouter and
    mistral fail: rows of every kind, errors, and advice of each status."""
    codex_app_server(home, {"rateLimits": {
        "limitId": "codex", "planType": "plus", "credits": {"hasCredits": True, "unlimited": False,
                                                            "balance": "9.99"},
        "primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": int(NOW + 3600)},
        "secondary": {"usedPercent": 90, "windowDurationMins": 10080, "resetsAt": int(NOW + 5 * 86400)}}})
    http.on("GET", "chatgpt.com/backend-api/wham/usage", (200, {
        "plan_type": "plus",
        "rate_limit": {"primary_window": {"used_percent": 20, "limit_window_seconds": 18000,
                                          "reset_at": int(NOW + 3600)},
                       "secondary_window": {"used_percent": 90, "limit_window_seconds": 604800,
                                            "reset_at": int(NOW + 5 * 86400)}},
        "credits": {"balance": "9.99"},
    }))
    http.on("GET", "openrouter.ai/api/v1/key", httpx.ConnectError("offline"))
    http.on("GET", "openrouter.ai/api/v1/credits", httpx.ConnectError("offline"))
    http.on("GET", mistral.MISTRAL_TRPC + "usage.costBreakdown",
            (307, "", {"location": "https://auth.mistral.ai/ui/login"}))
    answers(home, http, monkeypatch)  # the first route that matches answers
    # A second agy group, which MAIN did not read: the package only.
    GEMINI["groups"].append(("Claude and GPT models", [("weekly", 1.0, iso(6 * 86400 + 20 * 3600))]))


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(NOW, tz)


@pytest.fixture
def frozen(monkeypatch):
    """time.time() and datetime.now() return NOW, in UTC, in every module of aiu
    (and in the main module loaded later, see run)."""
    tz = os.environ.get("TZ")  # restored after monkeypatch's own teardown would be too late
    os.environ["TZ"] = "UTC"
    time.tzset()
    monkeypatch.setattr(time, "time", lambda: NOW)
    for name, mod in list(sys.modules.items()):
        if (name == "aiu" or name.startswith("aiu.")) and getattr(mod, "datetime", None) is datetime:
            monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    yield
    if tz is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = tz
    time.tzset()


# Gemini's note without Antigravity at MAIN, and without agy since Gemini is
# read through it (wave 9, task W).
GEMINI_NOTE_MAIN = ("Antigravity desktop not running, no ~/.gemini/oauth_creds.json fallback "
                    "\\u2014 open Antigravity to read the quota")
GEMINI_NOTE = "install the Antigravity CLI (agy) to read the Gemini quota"
# Codex's note when not signed in: MAIN's token was refused, the package's
# `codex app-server` says no one is signed in (wave 9, task V).
CODEX_NOTE_MAIN = "auth rejected (401) \\u2014 run `codex` to refresh"
CODEX_NOTE = "not signed in: run `codex` to sign in"


def since_main(out: str) -> str:
    """MAIN's output with the changes made since: `"schema": 1` as the first key
    (task P), `"early_pace": false` after each lot's `pace_from_history` (task Q),
    Gemini's note without agy (task W), Codex's when not signed in (task V),
    and each row's `sublimit`."""
    assert out.startswith("{\n") and '"schema"' not in out and '"early_pace"' not in out
    out = out.replace("{\n", f'{{\n  "schema": {render.JSON_SCHEMA},\n', 1)
    out = out.replace(GEMINI_NOTE_MAIN, GEMINI_NOTE).replace(CODEX_NOTE_MAIN, CODEX_NOTE)
    out = re.sub(r'^( +)("pace_from_history": \w+,\n)', r'\1\2\1"early_pace": false,\n', out, flags=re.M)
    # `sublimit` after each row's window_secs: true for Claude's per-model windows.
    snap = json.loads(out)
    snap["rows"] = [{k2: v2 for k, v in row.items() for k2, v2 in
                     ([(k, v), ("sublimit", row["provider"] == "claude" and row["metric"] in SUBLIMITS_MAIN)]
                      if k == "window_secs" else [(k, v)])} for row in snap["rows"]]
    return json.dumps(snap, indent=2) + "\n"


# Claude's windows on one model, as MAIN labelled them: the package marks them `sublimit`.
SUBLIMITS_MAIN = {"Weekly Sonnet", "Weekly Opus", "Weekly OAuth Apps", "Weekly Cowork"}


def antigravity_summary(groups) -> dict:
    """The groups as Antigravity's local server answered MAIN."""
    return {"response": {"groups": [{"displayName": name, "buckets": [
        {"bucketId": f"b{i}", "window": w, "remainingFraction": f, "resetTime": r}
        for i, (w, f, r) in enumerate(buckets)]} for name, buckets in groups]}}


def agy_reply(groups) -> dict:
    """The groups as `agy -p /usage --output-format json` prints them."""
    return {"conversation_id": "", "status": "SUCCESS", "response": "", "duration_seconds": 0,
            "num_turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "command": {"name": "usage", "data": {"groups": [{"name": name, "buckets": [
                {"id": f"b{i}", "window": w, "remaining_fraction": f, "reset_time": r}
                for i, (w, f, r) in enumerate(buckets)]} for name, buckets in groups]}}}


def run(monkeypatch, capsys, home, tmp_path, main, old=None):
    """One `aiu --config <file> --json` with main(): (exit code, stdout, stderr).
    The scenario's GEMINI groups reach the package through a fake agy in
    tmp/bin (none when the scenario has none), and MAIN (old) through its own
    parser: its scan for Antigravity's local server would read this machine."""
    groups = GEMINI.get("groups")
    agy = tmp_path / "bin" / "agy"
    agy.unlink(missing_ok=True)
    if old is not None:
        monkeypatch.setattr(old, "_query_local_quota_summary", lambda: old._parse_summary_response(
            antigravity_summary(groups)) if groups else None)
    elif groups:
        (tmp_path / "agy-reply.json").write_text(json.dumps(agy_reply(groups)))
        agy.write_text(f"#!/bin/sh\n/bin/cat {tmp_path / 'agy-reply.json'}\n")
        agy.chmod(0o755)
    # each run starts without a pace history or a cached reading
    shutil.rmtree(home / ".local" / "state" / "aiu", ignore_errors=True)
    cfg = tmp_path / "config.toml"
    cfg.write_text(CONFIG)
    cfg.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(cfg), "--json"])
    capsys.readouterr()
    code = main()
    out, err = capsys.readouterr()
    return code, out, err


def run_package(monkeypatch, capsys, home, tmp_path):
    monkeypatch.setattr(keys, "ENV_FILE", {})
    return run(monkeypatch, capsys, home, tmp_path, cli.main)


def main_module(tmp_path, monkeypatch):
    """aiu.py as it was at MAIN, imported from a temporary copy under its own
    name; None when this checkout has no such history (a shallow clone)."""
    try:
        src = subprocess.run(["git", "-C", str(ROOT), "show", f"{MAIN}:aiu.py"], capture_output=True,
                             timeout=30, env={**os.environ, "PATH": os.defpath}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if b"def main()" not in src:
        return None
    path = tmp_path / "aiu_main.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("aiu_main", path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "aiu_main", mod)
    spec.loader.exec_module(mod)  # after the home fixture: its paths resolve in the test home
    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    return mod


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_json_matches_the_recorded_output(monkeypatch, capsys, home, http, tmp_path, frozen, scenario):
    SCENARIOS[scenario](home, http, monkeypatch)
    code, out, err = run_package(monkeypatch, capsys, home, tmp_path)
    fixture = FIXTURES / f"json-{MAIN}-{scenario}.json"
    if os.environ.get("AIU_RECORD_JSON"):
        old = main_module(tmp_path, monkeypatch)
        assert old, f"recording needs {MAIN} in the git history"
        _, recorded, _ = run(monkeypatch, capsys, home, tmp_path, old.main, old)
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_text(since_main(recorded))
    assert code == 0 and err == ""
    assert out == fixture.read_text()
    snap = json.loads(out)
    assert list(snap)[0] == "schema" and snap["schema"] == 1
    rows = snap["rows"]
    assert list(dict.fromkeys(r["provider"] for r in rows)) == list(PROVIDERS)
    assert all((r["kind"] == "error") == (scenario == "failures") for r in rows)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_json_matches_main_side_by_side(monkeypatch, capsys, home, http, tmp_path, frozen, scenario):
    old = main_module(tmp_path, monkeypatch)
    if old is None:
        pytest.skip(f"{MAIN} is not in this checkout's history")
    SCENARIOS[scenario](home, http, monkeypatch)
    before = run(monkeypatch, capsys, home, tmp_path, old.main, old)
    after = run_package(monkeypatch, capsys, home, tmp_path)
    code, out, err = before
    assert after == (code, since_main(out), err)


def test_snapshot_fixture_is_current(monkeypatch, capsys, home, http, tmp_path, frozen):
    """tests/fixtures/snapshot.json is what aiu prints today: change a field
    and this fails until the fixture, and so the extension's test, follow."""
    snapshot(home, http, monkeypatch)
    code, out, err = run_package(monkeypatch, capsys, home, tmp_path)
    fixture = FIXTURES / "snapshot.json"
    if os.environ.get("AIU_RECORD_JSON"):
        fixture.write_text(out)
    assert code == 0 and err == ""
    assert out == fixture.read_text(), "aiu --json changed: see docs/json-schema.md"
    snap = json.loads(out)
    assert snap["schema"] == render.JSON_SCHEMA == 1
    assert {r["kind"] for r in snap["rows"]} == {"quota", "credit", "balance", "spend", "error"}
    assert {r["provider"] for r in snap["rows"] if r["kind"] == "error"} == {"openrouter", "mistral"}
    assert {lot["status"] for lot in snap["advice"]} == {"burn", "?", "conserve"}
