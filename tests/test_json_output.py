# SPDX-License-Identifier: GPL-3.0-or-later
"""`aiu --json` prints the same bytes as before the split into a package
(ROADMAP wave 7, task O): every probe answers from recorded replies, the clock
is frozen, and the output is compared with a fixture recorded from the last
single-file aiu.py, and with that aiu.py itself where git history has it.
No network.

The fixtures hold the output of aiu.py at MAIN. To record them again:
  AIU_RECORD_JSON=1 uv run --with pytest pytest tests/test_json_output.py"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aiu import cli, keys  # noqa: E402
from aiu.probes import gemini, mistral  # noqa: E402

MAIN = "7204525"  # main just before the split: aiu.py was the whole program
FIXTURES = ROOT / "tests" / "fixtures"
NOW = 1793000000.0  # 2026-10-26 07:33:20 UTC


def iso(secs_from_now: float) -> str:
    return datetime.fromtimestamp(NOW + secs_from_now, timezone.utc).isoformat().replace("+00:00", "Z")


def trpc(payload):
    return {"result": {"data": {"json": payload}}}


PROVIDERS = ("claude", "codex", "openai", "deepseek", "openrouter", "gemini", "mistral")
KEY_ENV = {"openai": "OPENAI_ADMIN_KEY", "deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
CONFIG = "".join(f"[{name}]\nenabled = true\n" + (f'key_env = "{KEY_ENV[name]}"\n' if name in KEY_ENV else "")
                 + "\n" for name in PROVIDERS)


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
    http.on("GET", "openrouter.ai/api/v1/credits", (200, {"data": {"total_credits": 20, "total_usage": 7.5}}))
    (home / ".gemini").mkdir()
    (home / ".gemini" / "oauth_creds.json").write_text(json.dumps(
        {"access_token": "ya29.fake", "expiry_date": (NOW + 3600) * 1000, "refresh_token": "1//fake"}))
    http.on("POST", "v1internal:loadCodeAssist", (200, {
        "cloudaicompanionProject": "proj-1", "currentTier": {"id": "standard-tier", "name": "Gemini Code Assist"}}))
    http.on("POST", "v1internal:retrieveUserQuota", (200, {"buckets": [
        {"modelId": "gemini-2.5-pro", "remainingFraction": 0.75, "resetTime": iso(20 * 3600)},
        {"modelId": "gemini-2.5-flash", "remainingFraction": 0.9, "resetTime": iso(20 * 3600)},
        {"modelId": "gemini-2.5-flash-lite", "remainingFraction": 1.0, "resetTime": iso(20 * 3600)}]}))
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
    http.on("GET", "chatgpt.com/backend-api/wham/usage", (401, {"error": "expired"}))
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": "codex-fake"}}))
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-fake")
    http.on("GET", "api.openai.com/v1/organizations/costs", (500, "oops"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-fake")
    http.on("GET", "api.deepseek.com/user/balance", (200, ["not", "an", "object"]))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fake")
    http.on("GET", "openrouter.ai/api/v1/credits", httpx.ConnectError("offline"))
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "ory_session_x=fake")
    http.on("GET", mistral.MISTRAL_TRPC + "usage.costBreakdown",
            (307, "", {"location": "https://auth.mistral.ai/ui/login"}))


SCENARIOS = {"answers": answers, "failures": failures}


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


def run(monkeypatch, capsys, home, tmp_path, main, local_quota):
    """One `aiu --config <file> --json` with main(): (exit code, stdout, stderr).
    Antigravity's local server is never looked for: it would scan this machine."""
    monkeypatch.setattr(local_quota[0], local_quota[1], lambda: None)
    for state in (home / ".local" / "state" / "aiu").glob("*"):
        state.unlink()  # each run starts without a pace history
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
    return run(monkeypatch, capsys, home, tmp_path, cli.main, (gemini, "_query_local_quota_summary"))


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
        _, recorded, _ = run(monkeypatch, capsys, home, tmp_path, old.main,
                             (old, "_query_local_quota_summary"))
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_text(recorded)
    assert code == 0 and err == ""
    assert out == fixture.read_text()
    rows = json.loads(out)["rows"]
    assert list(dict.fromkeys(r["provider"] for r in rows)) == list(PROVIDERS)
    assert all((r["kind"] == "error") == (scenario == "failures") for r in rows)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_json_matches_main_side_by_side(monkeypatch, capsys, home, http, tmp_path, frozen, scenario):
    old = main_module(tmp_path, monkeypatch)
    if old is None:
        pytest.skip(f"{MAIN} is not in this checkout's history")
    SCENARIOS[scenario](home, http, monkeypatch)
    before = run(monkeypatch, capsys, home, tmp_path, old.main, (old, "_query_local_quota_summary"))
    after = run_package(monkeypatch, capsys, home, tmp_path)
    assert after == before
