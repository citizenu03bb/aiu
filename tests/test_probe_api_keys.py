# SPDX-License-Identifier: GPL-3.0-or-later
"""API-key probes (OpenAI, DeepSeek, OpenRouter), key resolution and the ~/.env
loader, on hand-written responses — no network, keys are fakes."""
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import cli, keys, util  # noqa: E402
from aiu.config import load_config  # noqa: E402
from aiu.probes import deepseek, gemini, openai, openrouter  # noqa: E402

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
    [row] = openai.probe_openai(openai_key)
    assert (row.metric, row.kind, row.used, row.unit) == ("Spend (MTD)", "spend", 1.26, "USD")
    [call] = http.calls
    assert call.headers["Authorization"] == "Bearer sk-admin-fake"
    assert call.params["start_time"] == openai._month_start_epoch() and "page" not in call.params


def test_openai_pagination(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page(cost_bucket(1.0), next_page="page_AAA")),
            (200, costs_page(cost_bucket(2.5))))
    [row] = openai.probe_openai(openai_key)
    assert row.used == 3.5
    assert [c.params.get("page") for c in http.calls] == [None, "page_AAA"]


def test_openai_empty_month(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page()))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "spend" and row.used == 0.0


def test_openai_null_value(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page(cost_bucket(None, 2.0))))
    [row] = openai.probe_openai(openai_key)
    assert row.used == 2.0


@pytest.mark.parametrize("status", [401, 403])
def test_openai_auth_rejected(openai_key, http, status):
    http.on("GET", OPENAI_URL, (status, {"error": {"message": "Incorrect API key provided"}}))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and row.note == f"auth rejected ({status}) — needs an ADMIN key"


def test_openai_server_error(openai_key, http):
    http.on("GET", OPENAI_URL, (500, {"error": {"message": "internal"}}))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and row.note == "HTTP 500"


def test_openai_network_error(openai_key, http):
    http.on("GET", OPENAI_URL, httpx.ConnectError("connection refused"))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and row.note == "network: cannot connect"


def test_openai_bad_header_never_quotes_the_key(openai_key, http):
    # A multi-line key_cmd output: h11 quotes the header value (security review, M1).
    http.on("GET", OPENAI_URL, httpx.LocalProtocolError(
        "Illegal header value b'Bearer sk-admin-fake\\nlogin: me@example.com'"))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and "sk-admin" not in row.note and "example.com" not in row.note


def test_openai_no_key(http):
    [row] = openai.probe_openai({"key_env": "OPENAI_ADMIN_KEY"})
    assert row.kind == "error" and "OPENAI_ADMIN_KEY" in row.note and http.calls == []


def test_openai_renamed_data(openai_key, http):
    http.on("GET", OPENAI_URL, (200, {"object": "page", "buckets": [cost_bucket(5.0)], "has_more": False}))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and row.used is None and row.note == "malformed response: no `data`"


def test_openai_renamed_results(openai_key, http):
    http.on("GET", OPENAI_URL, (200, costs_page({"object": "bucket", "items": []})))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and row.note == "malformed response: no `results`"


def test_openai_malformed_second_page(openai_key, http):
    # Half a month is not the month: a bad page fails the whole spend.
    http.on("GET", OPENAI_URL, (200, costs_page(cost_bucket(1.0), next_page="page_AAA")),
            (200, "<html>Bad gateway</html>"))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and row.used is None


@pytest.mark.parametrize("body, note", [("<html>Bad gateway</html>", "malformed response: not JSON"),
                                        (costs_page({"results": [{"amount": None}]}),
                                         "malformed response: no `amount`"),
                                        (costs_page(cost_bucket("n/a")), "malformed response (ValueError)")])
def test_openai_malformed_body(openai_key, http, body, note):
    http.on("GET", OPENAI_URL, (200, body))
    [row] = openai.probe_openai(openai_key)
    assert row.kind == "error" and row.note == note


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
    cny, usd = deepseek.probe_deepseek(deepseek_key)
    assert (cny.metric, cny.kind, cny.used, cny.unit, cny.note) == ("Balance (CNY)", "balance", 110.0, "CNY", "")
    assert (usd.metric, usd.used) == ("Balance (USD)", 4.2)
    assert http.calls[0].headers["Authorization"] == "Bearer sk-fake"


def test_deepseek_unavailable(deepseek_key, http):
    # Out of credit: DeepSeek flags the account as unavailable for API calls.
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": False, "balance_infos": [balance(total="0.00")]}))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.used == 0.0 and row.note == "account not available"


def test_deepseek_no_balance_infos(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balance_infos": []}))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.kind == "balance" and row.used is None and row.note == "no balance info returned"


def test_deepseek_missing_fields_in_info(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balance_infos": [{"total_balance": "1.5"}]}))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert (row.metric, row.used, row.unit) == ("Balance (?)", 1.5, "")


def test_deepseek_missing_total_balance(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balance_infos": [{"currency": "CNY"}]}))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.used is None and row.note == "malformed response: no `total_balance`"


def test_deepseek_renamed_balance_infos(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balances": [balance()]}))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == "malformed response: no `balance_infos`"


def test_deepseek_missing_is_available(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, {"balance_infos": [balance()]}))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.note == ""


@pytest.mark.parametrize("status", [401, 403])
def test_deepseek_auth_rejected(deepseek_key, http, status):
    http.on("GET", DEEPSEEK_URL, (status, {"error": {"message": "Authentication Fails"}}))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == f"auth rejected ({status})"


def test_deepseek_server_error(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (500, ""))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == "HTTP 500"


def test_deepseek_network_error(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, httpx.ConnectTimeout("timed out"))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == "network: timed out"


def test_deepseek_no_key(http):
    [row] = deepseek.probe_deepseek({"key_env": "DEEPSEEK_API_KEY"})
    assert row.kind == "error" and "DEEPSEEK_API_KEY" in row.note and http.calls == []


def test_deepseek_non_json_body(deepseek_key, http):
    http.on("GET", DEEPSEEK_URL, (200, "<html>502 Bad Gateway</html>"))
    [row] = deepseek.probe_deepseek(deepseek_key)
    assert row.kind == "error" and row.note == "malformed response: not JSON"


# ---------------------------------------------------------------------------
# OpenRouter: total spend + remaining credits
# ---------------------------------------------------------------------------
@pytest.fixture
def openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fake")
    return {"key_env": "OPENROUTER_API_KEY"}


def test_openrouter_credits(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, (200, {"data": {"total_credits": 25.0, "total_usage": 7.4321}}))
    spend, left = openrouter.probe_openrouter(openrouter_key)
    assert (spend.metric, spend.kind, spend.used, spend.unit) == ("Spend (total)", "spend", 7.43, "USD")
    assert (left.metric, left.kind, left.used) == ("Remaining", "balance", 17.57)
    assert http.calls[0].headers["Authorization"] == "Bearer sk-or-v1-fake"


def test_openrouter_overdrawn(openrouter_key, http):
    # Usage past the purchased credits (negative balance, requests refused).
    http.on("GET", OPENROUTER_URL, (200, {"data": {"total_credits": 10, "total_usage": 10.5}}))
    _, left = openrouter.probe_openrouter(openrouter_key)
    assert left.used == -0.5


@pytest.mark.parametrize("status", [401, 403])
def test_openrouter_auth_rejected(openrouter_key, http, status):
    http.on("GET", OPENROUTER_URL, (status, {"error": {"code": status, "message": "No auth credentials found"}}))
    [row] = openrouter.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.note == f"auth rejected ({status})"


def test_openrouter_server_error(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, (500, {"error": {"code": 500}}))
    [row] = openrouter.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.note == "HTTP 500"


def test_openrouter_network_error(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, httpx.ConnectError("connection reset"))
    [row] = openrouter.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.note == "network: cannot connect"


def test_openrouter_no_key(http):
    [row] = openrouter.probe_openrouter({"key_env": "OPENROUTER_API_KEY"})
    assert row.kind == "error" and "OPENROUTER_API_KEY" in row.note and http.calls == []


def test_openrouter_renamed_fields(openrouter_key, http):
    http.on("GET", OPENROUTER_URL, (200, {"data": {"credits": 25.0, "usage": 7.4}}))
    [row] = openrouter.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.used is None and row.note == "malformed response: no `total_credits`"


@pytest.mark.parametrize("body, note", [("<html>Cloudflare</html>", "malformed response: not JSON"),
                                        ({"data": None}, "malformed response: no `data`"),
                                        ({"data": {"total_credits": 25.0, "total_usage": None}},
                                         "malformed response: no `total_usage`")])
def test_openrouter_malformed_body(openrouter_key, http, body, note):
    http.on("GET", OPENROUTER_URL, (200, body))
    [row] = openrouter.probe_openrouter(openrouter_key)
    assert row.kind == "error" and row.note == note


# ---------------------------------------------------------------------------
# Key resolution and the ~/.env loader
# ---------------------------------------------------------------------------
def test_resolve_key_priority(monkeypatch, home):
    (home / "key").write_text("  from-file\n")
    cfg = {"key_env": "AIU_TEST_KEY", "key_cmd": "printf ' from-cmd \\n'", "key_file": "~/key"}
    forget(monkeypatch, "AIU_TEST_KEY")
    assert keys.resolve_key(cfg, "x") == "from-cmd"
    monkeypatch.setenv("AIU_TEST_KEY", " from-env ")
    assert keys.resolve_key(cfg, "x") == "from-env"
    assert keys.resolve_key({"key_file": "~/key"}, "x") == "from-file"


@pytest.mark.parametrize("cmd", ["exit 3", "printf ''", "no-such-command-anywhere"])
def test_resolve_key_failed_cmd_falls_back(home, cmd):
    (home / "key").write_text("from-file")
    assert keys.resolve_key({"key_cmd": cmd, "key_file": "~/key"}, "x") == "from-file"


def test_resolve_key_nothing(home):
    assert keys.resolve_key({"key_env": "AIU_TEST_UNSET", "key_file": "~/missing"}, "x") is None
    assert keys.resolve_key({}, "x") is None


def test_resolve_key_keeps_first_line(monkeypatch, home, http):
    """M1: a `pass` entry holds more lines after the password; only the first is the key."""
    (home / "key").write_text("sk-from-file\r\n# my admin key\r\n")
    assert keys.resolve_key({"key_file": "~/key"}, "x") == "sk-from-file"
    monkeypatch.setenv("AIU_TEST_KEY", "sk-from-env\nlogin: me@example.com")
    assert keys.resolve_key({"key_env": "AIU_TEST_KEY"}, "x") == "sk-from-env"
    cfg = {"key_cmd": "printf 'sk-admin-fake\\nlogin: me@example.com\\nurl: x\\n'"}
    http.on("GET", OPENAI_URL, (200, costs_page(cost_bucket(1.0))))
    openai.probe_openai(cfg)
    assert http.calls[0].headers["Authorization"] == "Bearer sk-admin-fake"


@pytest.mark.parametrize("value", ["sk-SECRET\tpart", "sk-SECRET\x1b[0m", "sk-SECRET\x7f"])
def test_resolve_key_refuses_control_chars(monkeypatch, home, capsys, value):
    """M1: a key that still holds control characters is refused, and never quoted."""
    (home / "key").write_text("from-file")
    monkeypatch.setenv("AIU_TEST_KEY", value)
    assert keys.resolve_key({"key_env": "AIU_TEST_KEY", "key_file": "~/key"}, "openai") == "from-file"
    err = capsys.readouterr().err
    assert "openai: key from key_env holds control characters" in err
    assert "SECRET" not in err


@pytest.fixture
def env_file(monkeypatch, home):
    """Write ~/.env (0600) and return a loader for it; ENV_FILE starts empty."""
    monkeypatch.setattr(keys, "ENV_FILE", {})
    path = home / ".env"

    def write(text, config=None, mode=0o600):
        path.write_text(text)
        path.chmod(mode)
        return keys.load_env_file(config or {})
    return write


def test_env_file_feeds_probes(monkeypatch, env_file, http):
    """L2: only referenced names are read, into ENV_FILE, never os.environ."""
    forget(monkeypatch, "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_ADMIN_COOKIE",
           "AIU_PLAIN", "LD_PRELOAD", "HTTPS_PROXY", "SSL_CERT_FILE")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-already-set")
    config = {"openai": {"key_env": "OPENAI_ADMIN_KEY"}, "deepseek": {"key_env": "DEEPSEEK_API_KEY"},
              "openrouter": {"key_env": "OPENROUTER_API_KEY"}, "mistral": {"enabled": True}}
    before = dict(os.environ)
    found = env_file(
        "# provider keys\n"
        "export DEEPSEEK_API_KEY=\"sk-from-env-file\"\n"
        "OPENROUTER_API_KEY = 'sk-or-v1-from-env-file'\n"
        "MISTRAL_ADMIN_COOKIE='ory_session=abc; csrf=\"x=y\"'\n"
        "OPENAI_ADMIN_KEY=sk-admin-from-file\n"
        "not a pair\n"
        "\n"
        "AIU_PLAIN=plain\n"
        "LD_PRELOAD=/tmp/evil.so\n"
        "HTTPS_PROXY=http://proxy.example:3128\n"
        "SSL_CERT_FILE=/tmp/ca.pem\n", config)
    assert found == {"DEEPSEEK_API_KEY": "sk-from-env-file",
                     "OPENROUTER_API_KEY": "sk-or-v1-from-env-file",
                     "MISTRAL_ADMIN_COOKIE": 'ory_session=abc; csrf="x=y"',
                     "OPENAI_ADMIN_KEY": "sk-admin-from-file"}
    assert dict(os.environ) == before
    keys.ENV_FILE.update(found)
    assert keys.resolve_key(config["openai"], "openai") == "sk-admin-already-set"  # env wins
    assert keys.resolve_key({"key_env": "MISTRAL_ADMIN_COOKIE"}, "mistral") == 'ory_session=abc; csrf="x=y"'
    http.on("GET", DEEPSEEK_URL, (200, {"is_available": True, "balance_infos": [balance()]}))
    deepseek.probe_deepseek({"key_env": "DEEPSEEK_API_KEY"})
    assert http.calls[0].headers["Authorization"] == "Bearer sk-from-env-file"


@pytest.mark.parametrize("line, value", [
    ("K=sk-abc", "sk-abc"),
    ("K=sk-abc   # a comment", "sk-abc"),
    ("K=sk-abc\t# a comment", "sk-abc"),
    ("K=sk#abc", "sk#abc"),                      # `#` inside a word is not a comment
    ("K= # nothing", ""),
    ("K='a b # c'  # comment", "a b # c"),
    ("K='it''s'", None),                         # text after the closing quote
    ('K="a \\"b\\" \\\\ $HOME"', 'a "b" \\ $HOME'),  # no expansion
    ('K="sk-abc" # comment', "sk-abc"),
    ("K='sk-abc", None),
    ('K="sk-abc', None),
    ("K=sk-abc'", None),
    ("K=sk-abc\"", None),
    ("  export   K = sk-abc", "sk-abc"),
])
def test_env_file_parses_like_a_shell(env_file, line, value):
    assert env_file(line + "\n", {"p": {"key_env": "K"}}).get("K") == value


def test_env_file_unmatched_quote_warns_without_value(env_file, capsys):
    assert env_file("K='sk-SECRET\n", {"p": {"key_env": "K"}}) == {}
    err = capsys.readouterr().err
    assert ".env:1: K has unmatched quotes, ignored (.env values must fit on one line)" in err
    assert "SECRET" not in err


def test_env_file_multiline_value_says_one_line(env_file, capsys):
    """A quoted value continued on the next line is refused with the reason."""
    assert env_file("MISTRAL_ADMIN_COOKIE='a=1;\nb=2'\n", {}) == {}
    assert "values must fit on one line" in capsys.readouterr().err


def test_env_file_readme_example(env_file):
    """The README's own example, inline comments included, yields bare values."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    block = readme.split("### Secrets", 1)[1].split("```sh\n", 1)[1].split("```", 1)[0]
    names = [line.split("=", 1)[0] for line in block.splitlines() if "=" in line]
    assert "OPENAI_ADMIN_KEY" in names and "MISTRAL_ADMIN_COOKIE" in names
    found = env_file(block, {n: {"key_env": n} for n in names})
    assert found["OPENAI_ADMIN_KEY"] == "sk-admin-..."
    assert found["MISTRAL_ADMIN_COOKIE"] == "..."
    assert all("#" not in v and " " not in v for v in found.values())


def test_env_file_warns_when_others_can_read(env_file, capsys):
    env_file("K=sk-abc\n", {"p": {"key_env": "K"}}, mode=0o644)
    assert "other users can read it: chmod 600" in capsys.readouterr().err
    env_file("K=sk-abc\n", {"p": {"key_env": "K"}}, mode=0o600)
    env_file("OTHER=1\n", {"p": {"key_env": "K"}}, mode=0o644)  # none of ours: not our business
    assert capsys.readouterr().err == ""


def test_env_file_override_and_missing(monkeypatch, home, tmp_path):
    other = tmp_path / "other.env"
    other.write_text("AIU_FROM_OTHER=1\n")
    monkeypatch.setenv("AIU_ENV_FILE", str(other))
    assert keys.load_env_file({"p": {"key_env": "AIU_FROM_OTHER"}}) == {"AIU_FROM_OTHER": "1"}
    monkeypatch.setenv("AIU_ENV_FILE", str(tmp_path / "absent.env"))
    assert keys.load_env_file({"p": {"key_env": "AIU_FROM_OTHER"}}) == {}  # no file: silently nothing


def test_main_exports_nothing_from_env_file(monkeypatch, home, tmp_path, capsys):
    """GEMINI_CLIENT_ID / _SECRET keep working from ~/.env, and no ~/.env value,
    provider key or Gemini client, reaches os.environ."""
    forget(monkeypatch, "GEMINI_CLIENT_ID", "GEMINI_CLIENT_SECRET", "DEEPSEEK_API_KEY")
    monkeypatch.setattr(keys, "ENV_FILE", {})
    config = tmp_path / "cfg" / "config.toml"
    config.parent.mkdir(mode=0o700)
    config.write_text('[deepseek]\nkey_env = "DEEPSEEK_API_KEY"\n')
    config.chmod(0o600)
    (home / ".env").write_text("GEMINI_CLIENT_ID=id.apps.googleusercontent.com\n"
                               "GEMINI_CLIENT_SECRET='s3cret' # public CLI client\n"
                               "DEEPSEEK_API_KEY=sk-ds\n")
    (home / ".env").chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(config), "--only", "none", "--json"])
    assert cli.main() == 0
    for k in ("GEMINI_CLIENT_ID", "GEMINI_CLIENT_SECRET", "DEEPSEEK_API_KEY"):
        assert k not in os.environ
    assert gemini._gemini_oauth_client() == ("id.apps.googleusercontent.com", "s3cret")
    assert keys.ENV_FILE["DEEPSEEK_API_KEY"] == "sk-ds"


# ---------------------------------------------------------------------------
# Config file: creation and the key_cmd trust check
# ---------------------------------------------------------------------------
KEY_CMD_CONFIG = '[openai]\nkey_env = "OPENAI_ADMIN_KEY"\nkey_cmd = "echo sk-x"\n'


def test_load_config_creates_private_template(tmp_path, capsys):
    """L1: a --config path whose parent is missing gets a 0700 dir and a 0600 file."""
    path = tmp_path / "a" / "b" / "config.toml"
    config = load_config(path)
    assert config["claude"]["enabled"] is True
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert "wrote a starter config" in capsys.readouterr().err


def write_config(path, mode=0o600, dir_mode=0o700):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(dir_mode)
    path.write_text(KEY_CMD_CONFIG)
    path.chmod(mode)
    return path


def test_load_config_keeps_key_cmd_when_private(tmp_path, capsys):
    for mode, dir_mode in ((0o600, 0o700), (0o644, 0o755)):
        path = write_config(tmp_path / "cfg" / "config.toml", mode, dir_mode)
        assert load_config(path)["openai"]["key_cmd"] == "echo sk-x"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("mode, dir_mode, why", [
    (0o620, 0o700, "config.toml is writable by group or others"),
    (0o602, 0o700, "config.toml is writable by group or others"),
    (0o600, 0o770, "cfg is writable by group or others"),
    (0o600, 0o1777, "cfg is writable by group or others"),
])
def test_load_config_refuses_key_cmd_when_writable(monkeypatch, tmp_path, capsys,
                                                   mode, dir_mode, why):
    monkeypatch.setattr(util, "_private_group", lambda gid: False)
    path = write_config(tmp_path / "cfg" / "config.toml", mode, dir_mode)
    config = load_config(path)
    assert "key_cmd" not in config["openai"]
    assert config["openai"]["key_env"] == "OPENAI_ADMIN_KEY"  # the rest still applies
    err = capsys.readouterr().err
    assert "key_cmd ignored for openai" in err and why in err


def test_load_config_keeps_key_cmd_for_private_group(monkeypatch, tmp_path, capsys):
    """Group write by the user's own private group (umask 002) is not "others"."""
    monkeypatch.setattr(util, "_private_group", lambda gid: True)
    path = write_config(tmp_path / "cfg" / "config.toml", 0o664, 0o775)
    assert load_config(path)["openai"]["key_cmd"] == "echo sk-x"
    assert capsys.readouterr().err == ""
    path.chmod(0o666)
    assert "key_cmd" not in load_config(path)["openai"]


def test_load_config_refuses_key_cmd_through_symlink(tmp_path, capsys):
    """A config symlinked into a shared folder is judged by where it really lives."""
    target = write_config(tmp_path / "shared" / "config.toml", 0o600, 0o777)
    link = tmp_path / "cfg" / "config.toml"
    link.parent.mkdir(mode=0o700)
    link.symlink_to(target)
    assert "key_cmd" not in load_config(link)["openai"]
    assert "shared is writable by group or others" in capsys.readouterr().err


def test_load_config_refuses_key_cmd_not_owned(monkeypatch, tmp_path, capsys):
    path = write_config(tmp_path / "cfg" / "config.toml")
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    assert "key_cmd" not in load_config(path)["openai"]
    assert "config.toml is not yours" in capsys.readouterr().err
