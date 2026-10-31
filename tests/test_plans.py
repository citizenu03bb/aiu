# SPDX-License-Identifier: GPL-3.0-or-later
"""The plan each subscription's own client names, shown as a pill beside the
provider in the panel: Claude's from `claude auth status`, Mistral's from Vibe's
app-server (`account/read`), Codex's from `codex app-server` (test_probe_codex).
Fake clients in tmp/bin only (conftest); no network."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import clis, render  # noqa: E402
from aiu.probes import claude, mistral  # noqa: E402
from aiu.probes.common import _plan_name  # noqa: E402
from aiu.rows import Row  # noqa: E402

# What `claude auth status --json` printed on 2026-10-31, the email and
# organization made up: only subscriptionType may come out of aiu.
AUTH_STATUS = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
               "analyticsDisabled": False, "projectsDirectory": "/home/someone/.claude/projects",
               "configDirectory": "/home/someone/.claude", "email": "someone@example.com",
               "orgId": "00000000-0000-0000-0000-000000000001", "orgName": "Someone's Organization",
               "subscriptionType": "pro"}


@pytest.mark.parametrize("raw, plan", [
    ("pro", "pro"), ("Free", "Free"), ("  Mistral  Code Free ", "Mistral Code Free"), ("max_20x", "max_20x"),
    ("", ""), (None, ""), (3, ""), (["pro"], ""), ("[red]pro[/red]", ""), ("<b>pro</b>", ""),
    ("x" * 33, ""), ("-pro", ""), ("pro\n", "pro"), ("pro\x00", ""),
])
def test_plan_name_lets_only_a_short_plain_name_through(raw, plan):
    assert _plan_name(raw) == plan


# ---------------------------------------------------------------- Claude
def fake_claude(tmp_path, out, code=0):
    """tmp/bin/claude printing `out` and logging its arguments."""
    (tmp_path / "auth-status.out").write_text(out if isinstance(out, str) else json.dumps(out))
    path = tmp_path / "bin" / "claude"
    path.write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path / 'claude-args'}\n"
                    f"/bin/cat {tmp_path / 'auth-status.out'}\nexit {code}\n")
    path.chmod(0o755)
    return tmp_path / "claude-args"


def reading(home):
    """A fresh reading from the Claude Code plugin: rows without a token."""
    path = home / ".local" / "state" / "aiu" / claude.CLAUDE_USAGE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "source": "claude-code",
                                "written_at": datetime.now(timezone.utc).isoformat(),
                                "rate_limits": [{"kind": "five_hour", "percent_used": 12,
                                                 "resets_at": None}]}))


def test_claude_plan_from_auth_status(home, tmp_path):
    args = fake_claude(tmp_path, AUTH_STATUS)
    reading(home)
    rows = claude.probe_claude({})
    assert [(r.metric, r.plan, r.note) for r in rows] == [("5-Hour Limit", "pro", "")]
    assert args.read_text() == "auth status --json\n"
    # Only the plan is kept, and kept for hours: the next probe runs nothing.
    cache = (home / ".local" / "state" / "aiu" / "readings" / "claude-plan.json").read_text()
    assert json.loads(cache)["value"] == "pro" and json.loads(cache)["ttl"] == claude.CLAUDE_PLAN_TTL
    assert "example.com" not in cache and "Organization" not in cache and "00000000" not in cache
    assert claude.probe_claude({})[0].plan == "pro"
    assert args.read_text() == "auth status --json\n"


@pytest.mark.parametrize("out, code", [
    ({**AUTH_STATUS, "loggedIn": False}, 0),
    ({"loggedIn": True, "authMethod": "api-key", "subscriptionType": None}, 0),
    ({**AUTH_STATUS, "subscriptionType": "[bold]pro"}, 0),
    ("not json", 0),
    (["pro"], 0),
    (AUTH_STATUS, 1),
])
def test_claude_without_a_plan(home, tmp_path, out, code):
    fake_claude(tmp_path, out, code)
    reading(home)
    [row] = claude.probe_claude({})
    assert row.kind == "quota" and row.plan == ""
    cache = json.loads((home / ".local" / "state" / "aiu" / "readings" / "claude-plan.json").read_text())
    assert cache["value"] == "" and cache["ttl"] == claude.CLAUDE_PLAN_TTL_FAILED


def test_claude_error_runs_no_client(home, tmp_path):
    args = fake_claude(tmp_path, AUTH_STATUS)
    [row] = claude.probe_claude({})  # no reading, no token: nothing to put a plan on
    assert row.kind == "error" and row.plan == "" and not args.exists()


def test_claude_without_the_cli(home):
    reading(home)
    [row] = claude.probe_claude({})
    assert row.kind == "quota" and row.plan == ""


# ---------------------------------------------------------------- Mistral (Vibe)
ACCOUNT = {"status": "ready", "plan": {"kind": "chat", "name": "FREE", "title": "Free"},
           "planOffer": {"kind": "upgrade_to_pro", "url": "https://admin.mistral.ai/subscriptions"},
           "rateLimitAction": None, "teleportEligible": True, "teleportAction": None,
           "apiKey": {"preview": "AbCdEfGhIj", "scope": "vibe", "accessScope": None}}


def fake_vibe(tmp_path, account=ACCOUNT, start_error=None):
    """tmp/bin/vibe-app-server: answers initialize, session/start (an
    ephemeral session only) and account/read for that session, logging what it
    was sent and what it ran with."""
    (tmp_path / "vibe.json").write_text(json.dumps({"account": account, "start_error": start_error}))
    log = tmp_path / "vibe-log.json"
    path = tmp_path / "bin" / "vibe-app-server"
    path.write_text(f"""#!{sys.executable} -I
import json, os, sys
scenario = json.load(open({str(tmp_path / 'vibe.json')!r}))
log = {{"env": sorted(os.environ), "dbus": os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
        "cwd": os.getcwd(), "msgs": []}}
def send(m):
    sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line)
    log["msgs"].append(m)
    open({str(log)!r}, "w").write(json.dumps(log))
    if m["method"] == "initialize":
        send({{"jsonrpc": "2.0", "id": m["id"], "result": {{"serverInfo": {{"name": "vibe-app-server"}}}}}})
    elif m["method"] == "session/start":
        send({{"jsonrpc": "2.0", "method": "session/updated", "params": {{}}}})
        if scenario["start_error"] or m["params"].get("kind") != "ephemeral":
            send({{"jsonrpc": "2.0", "id": m["id"], "error": scenario["start_error"] or {{"code": "x"}}}})
        else:
            send({{"jsonrpc": "2.0", "id": m["id"], "result": {{"state": {{"session": {{"id": "s-1"}}}}}}}})
    elif m["method"] == "account/read" and m["params"] == {{"sessionId": "s-1"}}:
        send({{"jsonrpc": "2.0", "id": m["id"], "result": {{"account": scenario["account"]}}}})
""")
    path.chmod(0o755)
    return log


@pytest.fixture
def cookie(monkeypatch, http):
    """The console answers: Mistral has rows to put the plan on."""
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "ory_session_x=fake; csrf_token=fake")
    http.on("GET", mistral.MISTRAL_TRPC + "usage.costBreakdown",
            (200, {"result": {"data": {"json": {"groups": [], "metadata": {"hasMore": False}}}}}))
    http.on("GET", mistral.MISTRAL_TRPC + "usage.prices",
            (200, {"result": {"data": {"json": {"prices": []}}}}))
    return {"key_env": "MISTRAL_ADMIN_COOKIE"}


def test_mistral_plan_from_vibe(home, tmp_path, cookie, monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("MISTRAL_API_KEY", "should-not-reach-vibe")
    log = fake_vibe(tmp_path)
    rows = mistral.probe_mistral(cookie)
    assert [(r.metric, r.plan) for r in rows] == [("Vibe Monthly", "Free"), ("API Monthly", "Free")]
    assert rows[0].note.startswith("€0.00")  # the allowance stays in the note
    sent = json.loads(log.read_text())
    assert [m["method"] for m in sent["msgs"]] == ["initialize", "initialized", "session/start", "account/read"]
    assert all("params" in m for m in sent["msgs"])  # Vibe refuses a message without them
    start = sent["msgs"][2]["params"]
    # A throwaway session in an empty directory of its own, which is gone after.
    assert start["kind"] == "ephemeral" and start["agentConfig"]["cwd"] == sent["cwd"]
    assert "aiu-vibe-" in sent["cwd"] and not Path(sent["cwd"]).exists()
    # The session bus for Vibe's keyring, and nothing of aiu's own keys.
    assert sent["dbus"] == "unix:path=/run/user/1000/bus"
    assert "MISTRAL_API_KEY" not in sent["env"] and "MISTRAL_ADMIN_COOKIE" not in sent["env"]
    cache = (home / ".local" / "state" / "aiu" / "readings" / "mistral-plan.json").read_text()
    assert json.loads(cache)["value"] == "Free" and "AbCdEf" not in cache
    log.unlink()
    assert mistral.probe_mistral(cookie)[0].plan == "Free" and not log.exists()  # cached


@pytest.mark.parametrize("plan, shown", [
    ({"kind": "chat", "name": "INDIVIDUAL", "title": "[Subscription] Pro"}, "Pro"),
    ({"kind": "api", "name": "SCALE", "title": "[API] Scale plan"}, "Scale plan"),
    ({"kind": "chat", "name": "STUDENT", "title": None}, "STUDENT"),
    ({"kind": "mistral_code", "name": "E", "title": "Mistral Code Enterprise"}, "Mistral Code Enterprise"),
    ({"kind": "chat", "name": "<b>", "title": "[x]"}, ""),
    (None, ""),
    ("Free", ""),
])
def test_mistral_plan_names(home, tmp_path, cookie, plan, shown):
    fake_vibe(tmp_path, {**ACCOUNT, "plan": plan})
    assert {r.plan for r in mistral.probe_mistral(cookie)} == {shown}


@pytest.mark.parametrize("account, start_error", [
    ({"status": "missing_key"}, None),
    ({**ACCOUNT, "status": "unauthorized"}, None),
    ("ready", None),
    (ACCOUNT, {"code": "unauthorized", "message": "Missing MISTRAL_API_KEY environment variable"}),
])
def test_mistral_without_a_plan(home, tmp_path, cookie, account, start_error):
    fake_vibe(tmp_path, account, start_error)
    rows = mistral.probe_mistral(cookie)
    assert [r.kind for r in rows] == ["quota", "quota"] and {r.plan for r in rows} == {""}
    cache = json.loads((home / ".local" / "state" / "aiu" / "readings" / "mistral-plan.json").read_text())
    assert cache["value"] == "" and cache["ttl"] == mistral.VIBE_PLAN_TTL_FAILED


def test_mistral_silent_vibe_times_out(home, tmp_path, cookie, monkeypatch):
    path = tmp_path / "bin" / "vibe-app-server"
    path.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
    path.chmod(0o755)
    monkeypatch.setattr(mistral, "VIBE_TIMEOUT", 0.5)
    assert {r.plan for r in mistral.probe_mistral(cookie)} == {""}


def test_mistral_error_runs_no_client(home, tmp_path, http):
    log = fake_vibe(tmp_path)
    [row] = mistral.probe_mistral({})  # no cookie
    assert row.kind == "error" and row.plan == "" and not log.exists()


def test_vibe_cmd_names_the_program(home, tmp_path, cookie):
    fake_vibe(tmp_path)
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    (tmp_path / "bin" / "vibe-app-server").rename(moved / "vibe-app-server")
    assert {r.plan for r in mistral.probe_mistral(cookie)} == {""}  # not found: nothing ran, nothing cached
    assert {r.plan for r in mistral.probe_mistral({**cookie, "vibe_cmd": str(moved / "vibe-app-server")})} \
        == {"Free"}


# ---------------------------------------------------------------- the table
def test_table_shows_the_plan_once_per_provider(capsys):
    render.render_table([Row("codex", "5-Hour Limit", "quota", used=0.2, limit=1.0, unit="%",
                             reset="in 1h 0m", plan="plus"),
                         Row("codex", "Weekly Limit", "quota", used=0.7, limit=1.0, unit="%", plan="plus"),
                         Row("mistral", "Vibe Monthly", "quota", used=0.1, limit=1.0, unit="%",
                             note="€0.85 / €8.50", plan="Free")])
    out = capsys.readouterr().out
    assert out.count("plus") == 1 and "in 1h 0m  plus" in out and "Free  €0.85 / €8.50" in out


# ---------------------------------------------------------------- aiu.clis
def test_spawn_keeps_only_the_named_variables_and_runs_in_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/x")
    monkeypatch.setenv("SECRET_TOKEN", "nope")
    cli = tmp_path / "bin" / "env"
    cli.write_text("#!/bin/sh\necho \"$DBUS_SESSION_BUS_ADDRESS|$SECRET_TOKEN|$(/bin/pwd)\"\n")
    cli.chmod(0o755)
    with clis.spawn([str(cli)], timeout=5) as proc:
        assert proc.stdout.read() == f"||{Path.cwd()}\n"
    with clis.spawn([str(cli)], timeout=5, keep_env=("DBUS_SESSION_BUS_ADDRESS",), cwd=str(tmp_path)) as proc:
        assert proc.stdout.read() == f"unix:path=/x||{tmp_path}\n"
