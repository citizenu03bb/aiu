# SPDX-License-Identifier: GPL-3.0-or-later
"""API-key probes (OpenAI, DeepSeek, OpenRouter), key resolution and the ~/.env
loader, on hand-written responses — no network, keys are fakes."""
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402

OPENAI_URL = "https://api.openai.com/v1/organizations/costs"
DEEPSEEK_URL = "https://api.deepseek.com/user/balance"
OPENROUTER_URL = "https://openrouter.ai/api/v1/credits"


def forget(monkeypatch, *names):
    """Unset names now and after the test, even if code under test sets them."""
    for n in names:
        monkeypatch.setenv(n, "")
        monkeypatch.delenv(n)


# ---------------------------------------------------------------------------
# OpenAI: month-to-date spend from the Costs API
# ---------------------------------------------------------------------------
def cost_bucket(*values):
    return {"object": "bucket", "start_time": 1793318400, "end_time": 1793404800,
            "results": [{"object": "organization.costs.result",
                         "amount": {"value": v, "currency": "usd"},
                         "line_item": None, "project_id": None} for v in values]}


def costs_page(*buckets, next_page=None):
    return {"object": "page", "data": list(buckets),
            "has_more": next_page is not None, "next_page": next_page}


@pytest.fixture
def openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-fake")
    return {"key_env": "OPENAI_ADMIN_KEY"}


def test_openai_spend(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page(cost_bucket(0.06, 1.2), cost_bucket(), cost_bucket(0.004))))
    [row] = aiu.probe_openai(openai_key)
    assert (row.metric, row.kind, row.used, row.unit) == ("Spend (MTD)", "spend", 1.26, "USD")
    [call] = http.calls
    assert call.headers["Authorization"] == "Bearer sk-admin-fake"
    assert call.params["start_time"] == aiu._month_start_epoch() and "page" not in call.params


def test_openai_pagination(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page(cost_bucket(1.0), next_page="page_AAA")),
            (200, costs_page(cost_bucket(2.5))))
    [row] = aiu.probe_openai(openai_key)
    assert row.used == 3.5
    assert [c.params.get("page") for c in http.calls] == [None, "page_AAA"]


def test_openai_empty_month(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page()))
    [row] = aiu.probe_openai(openai_key)
    assert row.kind == "spend" and row.used == 0.0


def test_openai_null_value(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page(cost_bucket(None, 2.0))))
    [row] = aiu.probe_openai(openai_key)
    assert row.used == 2.0


@pytest.mark.parametrize("status", [401, 403])
def test_openai_auth_rejected(openai_key, http, status):
    http.on("GET", OPENAI_URL, (status, {"error": {"message": "Incorrect API key provided"}}))
    [row] = aiu.probe_openai(openai_key)
    assert row.kind == "error" and row.note == f"auth rejected ({status}) — needs an ADMIN key"


def test_openai_server_error(openai_key, http):
    http.on("GET", OPENAI_URL, (500, {"error": {"message": "internal"}}))
    [row] = aiu.probe_openai(openai_key)
    assert row.kind == "error" and row.note == "HTTP 500"


def test_openai_network_error(openai_key, http):
    http.on("GET", OPENAI_URL, httpx.ConnectError("connection refused"))
    [row] = aiu.probe_openai(openai_key)
    assert row.kind == "error" and row.note == "network: connection refused"


def test_openai_no_key(http):
    [row] = aiu.probe_openai({"key_env": "OPENAI_ADMIN_KEY"})
    assert row.kind == "error" and "OPENAI_ADMIN_KEY" in row.note and http.calls == []


@pytest.mark.xfail(strict=True, reason="probe bug: a response without `data` reads as $0.00 spent")
def test_openai_renamed_data(openai_key, http):
    http.on("GET", OPENAI_URL, (200, {"object": "page", "buckets": [cost_bucket(5.0)], "has_more": False}))
    [row] = aiu.probe_openai(openai_key)
    assert row.kind == "error"


@pytest.mark.xfail(strict=True, reason="probe bug: a malformed body is reported as a network error")
@pytest.mark.parametrize("body", ["<html>Bad gateway</html>",
                                  costs_page({"results": [{"amount": None}]})])
def test_openai_malformed_body(openai_key, http, body):
    http.on("GET", OPENAI_URL, (200, body))
    [row] = aiu.probe_openai(openai_key)
    assert row.kind == "error" and not row.note.startswith("network:")


# ---------------------------------------------------------------------------
# DeepSeek: remaining balance
# ---------------------------------------------------------------------------
def balance(currency="CNY", total="110.00", granted="10.00", topped_up="100.00"):
    return {"currency": currency, "total_balance": total,
            "granted_balance": granted, "topped_up_balance": topped_up}


@pytest.fixture
def deepseek_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake")
    return {"key_env": "DEEPSEEK_API_KEY"}


def test_deepseek_balance(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True,
                                        "balance_infos": [balance(), balance("USD", "4.20", "0.00", "4.20")]}))
    cny, usd = aiu.probe_deepseek(deepseek_key)
    assert (cny.metric, cny.kind, cny.used, cny.unit, cny.note) == ("Balance (CNY)", "balance", 110.0, "CNY", "")
    assert (usd.metric, usd.used) == ("Balance (USD)", 4.2)
    assert http.calls[0].headers["Authorization"] == "Bearer sk-fake"


def test_deepseek_unavailable(deepseek_key, http):
    # Out of credit: DeepSeek flags the account as unavailable for API calls.
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": False, "balance_infos": [balance(total="0.00")]}))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert row.used == 0.0 and row.note == "account not available"


def test_deepseek_no_balance_infos(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balance_infos": []}))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert row.kind == "balance" and row.used is None and row.note == "no balance info returned"


def test_deepseek_missing_fields_in_info(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balance_infos": [{"total_balance": "1.5"}]}))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert (row.metric, row.used, row.unit) == ("Balance (?)", 1.5, "")


@pytest.mark.xfail(strict=True, reason="probe bug: a missing `is_available` flags the account as unavailable")
def test_deepseek_missing_is_available(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"balance_infos": [balance()]}))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert row.note == ""


@pytest.mark.parametrize("status", [401, 403])
def test_deepseek_auth_rejected(deepseek_key, http, status):
    http.on("GET", DEEPSEEK_URL, (status, {"error": {"message": "Authentication Fails"}}))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == f"auth rejected ({status})"


def test_deepseek_server_error(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (500, ""))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == "HTTP 500"


def test_deepseek_network_error(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, httpx.ConnectTimeout("timed out"))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == "network: timed out"


def test_deepseek_no_key(http):
    [row] = aiu.probe_deepseek({"key_env": "DEEPSEEK_API_KEY"})
    assert row.kind == "error" and "DEEPSEEK_API_KEY" in row.note and http.calls == []


@pytest.mark.xfail(strict=True, raises=ValueError,
                   reason="probe bug: a 200 with a non-JSON body raises out of the probe")
def test_deepseek_non_json_body(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, "<html>502 Bad Gateway</html>"))
    [row] = aiu.probe_deepseek(deepseek_key)
    assert row.kind == "error"


# ---------------------------------------------------------------------------
# OpenRouter: total spend + remaining credits
# ---------------------------------------------------------------------------
@pytest.fixture
def openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fake")
    return {"key_env": "OPENROUTER_API_KEY"}


def test_openrouter_credits(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, (200, {"data": {"total_credits": 25.0, "total_usage": 7.4321}}))
    spend, left = aiu.probe_openrouter(openrouter_key)
    assert (spend.metric, spend.kind, spend.used, spend.unit) == ("Spend (total)", "spend", 7.43, "USD")
    assert (left.metric, left.kind, left.used) == ("Remaining", "balance", 17.57)
    assert http.calls[0].headers["Authorization"] == "Bearer sk-or-v1-fake"


def test_openrouter_overdrawn(openrouter_key, http):
    # Usage past the purchased credits (negative balance, requests refused).
    http.on("GET", OPENROUTER_URL, (200, {"data": {"total_credits": 10, "total_usage": 10.5}}))
    _, left = aiu.probe_openrouter(openrouter_key)
    assert left.used == -0.5


@pytest.mark.parametrize("status", [401, 403])
def test_openrouter_auth_rejected(openrouter_key, http, status):
    http.on("GET", OPENROUTER_URL, (status, {"error": {"code": status, "message": "No auth credentials found"}}))
    [row] = aiu.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.note == f"auth rejected ({status})"


def test_openrouter_server_error(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, (500, {"error": {"code": 500}}))
    [row] = aiu.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.note == "HTTP 500"


def test_openrouter_network_error(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, httpx.ConnectError("connection reset"))
    [row] = aiu.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.note == "network: connection reset"


def test_openrouter_no_key(http):
    [row] = aiu.probe_openrouter({"key_env": "OPENROUTER_API_KEY"})
    assert row.kind == "error" and "OPENROUTER_API_KEY" in row.note and http.calls == []


@pytest.mark.xfail(strict=True, reason="probe bug: renamed fields read as 0 spent, 0 left")
def test_openrouter_renamed_fields(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, (200, {"data": {"credits": 25.0, "usage": 7.4}}))
    rows = aiu.probe_openrouter(openrouter_key)
    assert all(r.kind == "error" for r in rows)


@pytest.mark.xfail(strict=True, raises=(ValueError, AttributeError),
                   reason="probe bug: a non-JSON body, or `data: null`, raises out of the probe")
@pytest.mark.parametrize("body", ["<html>Cloudflare</html>", {"data": None}])
def test_openrouter_malformed_body(openrouter_key, http, body):
    http.on("GET", OPENROUTER_URL, (200, body))
    rows = aiu.probe_openrouter(openrouter_key)
    assert rows[0].kind == "error"


# ---------------------------------------------------------------------------
# Key resolution and the ~/.env loader
# ---------------------------------------------------------------------------
def test_resolve_key_priority(monkeypatch, home):
    (home / "key").write_text("  from-file\n")
    cfg = {"key_env": "AIU_TEST_KEY", "key_cmd": "printf ' from-cmd \\n'", "key_file": "~/key"}
    forget(monkeypatch, "AIU_TEST_KEY")
    assert aiu.resolve_key(cfg, "x") == "from-cmd"
    monkeypatch.setenv("AIU_TEST_KEY", " from-env ")
    assert aiu.resolve_key(cfg, "x") == "from-env"
    assert aiu.resolve_key({"key_file": "~/key"}, "x") == "from-file"


@pytest.mark.parametrize("cmd", ["exit 3", "printf ''", "no-such-command-anywhere"])
def test_resolve_key_failed_cmd_falls_back(home, cmd):
    (home / "key").write_text("from-file")
    assert aiu.resolve_key({"key_cmd": cmd, "key_file": "~/key"}, "x") == "from-file"


def test_resolve_key_nothing(home):
    assert aiu.resolve_key({"key_env": "AIU_TEST_UNSET", "key_file": "~/missing"}, "x") is None
    assert aiu.resolve_key({}, "x") is None


def test_env_file_feeds_probes(monkeypatch, home, http):
    forget(monkeypatch, "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_ADMIN_COOKIE", "AIU_PLAIN")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-already-set")
    (home / ".env").write_text(
        "# provider keys\n"
        "export DEEPSEEK_API_KEY=\"sk-from-env-file\"\n"
        "OPENROUTER_API_KEY = 'sk-or-v1-from-env-file'\n"
        "MISTRAL_ADMIN_COOKIE='ory_session=abc; csrf=\"x=y\"'\n"
        "OPENAI_ADMIN_KEY=sk-admin-from-file\n"
        "not a pair\n"
        "\n"
        "AIU_PLAIN=plain\n")
    aiu.load_env_file()
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-env-file"
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-v1-from-env-file"
    assert os.environ["MISTRAL_ADMIN_COOKIE"] == 'ory_session=abc; csrf="x=y"'
    assert os.environ["OPENAI_ADMIN_KEY"] == "sk-admin-already-set"  # not clobbered
    assert os.environ["AIU_PLAIN"] == "plain"
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balance_infos": [balance()]}))
    aiu.probe_deepseek({"key_env": "DEEPSEEK_API_KEY"})
    assert http.calls[0].headers["Authorization"] == "Bearer sk-from-env-file"


def test_env_file_override_and_missing(monkeypatch, home, tmp_path):
    forget(monkeypatch, "AIU_FROM_OTHER")
    other = tmp_path / "other.env"
    other.write_text("AIU_FROM_OTHER=1\n")
    monkeypatch.setenv("AIU_ENV_FILE", str(other))
    aiu.load_env_file()
    assert os.environ["AIU_FROM_OTHER"] == "1"
    monkeypatch.setenv("AIU_ENV_FILE", str(tmp_path / "absent.env"))
    aiu.load_env_file()  # no file: silently nothing
