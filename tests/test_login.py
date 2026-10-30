# SPDX-License-Identifier: GPL-3.0-or-later
"""`aiu login openrouter`: the PKCE sign-in, from the browser's callback to the
keyring. The browser is a fake webbrowser.open that calls the listener with a
local HTTP client, OpenRouter's token endpoint is the HTTP fake, the keyring is
a fake `secret-tool` that records its calls, and no network is used."""
import base64
import hashlib
import http.client
import json
import socket
import sys
import urllib.parse
from pathlib import Path

import httpx
import pytest

from aiu import cli, config, login

CODE = "c0de-From-0penRouter"
KEY = "sk-or-v1-S3cr3tUserKey0123456789"
KEY_CMD = "secret-tool lookup service aiu provider openrouter"
TOKEN_URL = "openrouter.ai/api/v1/auth/keys"

FAKE_SECRET_TOOL = """\
#!{python}
# Fake secret-tool: a JSON keyring, and a log of every call (argv, stdin).
import json, os, sys
d = os.environ["FAKE_KEYRING"]
args = sys.argv[1:]
stdin = sys.stdin.read() if args[:1] == ["store"] else None
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps({{"argv": args, "stdin": stdin}}) + "\\n")
path = os.path.join(d, "store.json")
store = json.load(open(path)) if os.path.exists(path) else []
if args[0] == "lookup":
    for item in store:
        if item["attrs"] == args[1:]:
            sys.stdout.write(item["secret"])
            sys.exit(0)
    sys.exit(1)
if args[0] == "store":
    if os.environ.get("FAKE_KEYRING_LOCKED"):
        sys.exit(1)
    store = [i for i in store if i["attrs"] != args[2:]]
    store.append({{"attrs": args[2:], "label": args[1], "secret": stdin}})
    json.dump(store, open(path, "w"))
"""


class Keyring:
    def __init__(self, path: Path):
        self.path = path

    def items(self) -> list[dict]:
        p = self.path / "store.json"
        return json.load(open(p)) if p.exists() else []

    def calls(self) -> list[dict]:
        p = self.path / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def keyring(tmp_path, monkeypatch):
    d = tmp_path / "keyring"
    d.mkdir()
    tool = tmp_path / "bin" / "secret-tool"
    tool.write_text(FAKE_SECRET_TOOL.format(python=sys.executable))
    tool.chmod(0o755)
    monkeypatch.setenv("FAKE_KEYRING", str(d))
    return Keyring(d)


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    path.parent.mkdir(mode=0o700)
    return path


def get(port: int, target: str, host: str | None = None) -> int:
    """One GET to the listener, as a browser sends it; the status."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", target, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        conn.endheaders()
        r = conn.getresponse()
        r.read()
        return r.status
    finally:
        conn.close()


class Browser:
    """Stands in for webbrowser.open: records the address, then plays the
    requests `script` gives it (target, Host or None) against the callback."""

    def __init__(self, script=(("/callback?code=" + CODE, None),)):
        self.script, self.urls, self.statuses = list(script), [], []

    def __call__(self, url):
        self.urls.append(url)
        port = self.port
        self.statuses = [get(port, target, host) for target, host in self.script]
        return True

    @property
    def params(self) -> dict:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.urls[-1]).query, strict_parsing=True)
        return {k: v[0] for k, v in q.items()}

    @property
    def port(self) -> int:
        return urllib.parse.urlsplit(self.params["callback_url"]).port


@pytest.fixture
def browser(monkeypatch):
    for name in ("WAYLAND_DISPLAY", "BROWSER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    b = Browser()
    monkeypatch.setattr(login.webbrowser, "open", b)
    return b


def run(monkeypatch, capsys, cfg, *args):
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(cfg), "login", "openrouter", *args])
    capsys.readouterr()
    code = cli.main()
    out, err = capsys.readouterr()
    assert KEY not in out + err
    return code, out, err


def closed(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    return False


def s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_pkce_pair():
    verifier, challenge = login.pkce_pair()
    assert len(verifier) == 64 and set(verifier) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert challenge == s256(verifier) and "=" not in challenge
    assert login.pkce_pair()[0] != verifier


def test_login_stores_the_key_and_sets_key_cmd(monkeypatch, capsys, http, cfg, keyring, browser):
    http.on("POST", TOKEN_URL, (200, {"key": KEY, "user_id": "user_1"}))
    code, out, err = run(monkeypatch, capsys, cfg)
    assert code == 0 and "error" not in err and "not set" not in err, err
    # The address the browser got, printed too for a machine without one.
    p = browser.params
    assert browser.urls[0].startswith("https://openrouter.ai/auth?") and browser.urls[0] in out
    assert p["callback_url"] == f"http://127.0.0.1:{browser.port}/callback"
    assert (p["code_challenge_method"], p["key_label"]) == ("S256", "aiu")
    assert browser.statuses == [200]
    # The code and the verifier that matches the challenge buy the key.
    [call] = http.calls
    assert call.url == "https://openrouter.ai/api/v1/auth/keys"
    assert set(call.json) == {"code", "code_verifier", "code_challenge_method"}
    assert (call.json["code"], call.json["code_challenge_method"]) == (CODE, "S256")
    assert s256(call.json["code_verifier"]) == p["code_challenge"]
    assert call.json["code_verifier"] not in out + err and CODE not in out + err
    # The key went to the keyring on stdin, never in argv.
    [store] = keyring.calls()
    assert store == {"argv": ["store", "--label=aiu openrouter key", "service", "aiu",
                              "provider", "openrouter"], "stdin": KEY}
    assert [i["secret"] for i in keyring.items()] == [KEY]
    assert f'key_cmd = "{KEY_CMD}"' in cfg.read_text()
    assert "openrouter is off: aiu config set openrouter.enabled true" in out
    assert "Check it with: aiu --only openrouter" in out
    assert closed(browser.port)


def test_the_probe_then_reads_the_key_from_the_keyring(monkeypatch, capsys, http, cfg, keyring,
                                                      browser):
    http.on("POST", TOKEN_URL, (200, {"key": KEY}))
    assert run(monkeypatch, capsys, cfg)[0] == 0
    config.config_set(cfg, "openrouter.enabled", "true")
    http.on("GET", "openrouter.ai/api/v1/key", (200, {"data": {
        "limit": 10, "limit_remaining": 9.5, "limit_reset": None, "usage": 0.5,
        "usage_daily": 0.5, "is_free_tier": False}}))
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(cfg), "--only", "openrouter", "--json"])
    capsys.readouterr()
    assert cli.main() == 0
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert [(r["metric"], r["used"]) for r in rows] == [("Spend (total)", 0.5), ("Remaining", 9.5)]
    assert http.to("api/v1/key")[0].headers["Authorization"] == f"Bearer {KEY}"


def test_other_requests_are_turned_away(monkeypatch, capsys, http, cfg, keyring, browser):
    browser.script = [
        ("/", None),                                   # wrong path
        ("/callback/x?code=nope", None),
        ("/callback", None),                           # no code
        ("/callback?code=", None),
        ("/callback?code=a&code=b", None),
        ("/callback?code=has%20space", None),
        ("/callback?code=" + "x" * 3000, None),
        ("/callback?code=evil", "evil.example:80"),    # a foreign Host (DNS rebinding)
        ("/callback?code=evil", "localhost"),
        ("/callback?code=" + CODE, None),
        ("/callback?code=late", None),                 # after the first: no new code
    ]
    http.on("POST", TOKEN_URL, (200, {"key": KEY}))
    code, _, err = run(monkeypatch, capsys, cfg)
    assert code == 0, err
    assert browser.statuses[:-2] == [404, 404, 400, 400, 400, 400, 400, 400, 400]
    assert http.calls[0].json["code"] == CODE
    assert closed(browser.port)


def test_timeout_closes_the_listener(monkeypatch, capsys, http, cfg, keyring, browser):
    browser.script = []
    monkeypatch.setattr(login, "LOGIN_TIMEOUT", 0.1)
    code, _, err = run(monkeypatch, capsys, cfg)
    assert code == 1 and "no sign-in within 0 minutes; nothing was changed" in err
    assert http.calls == [] and keyring.calls() == [] and not cfg.exists()
    assert closed(browser.port)


def test_ctrl_c_closes_the_listener(monkeypatch, capsys, http, cfg, keyring, browser):
    ports = []

    def interrupt(url):
        browser.urls.append(url)
        ports.append(browser.port)
        raise KeyboardInterrupt

    monkeypatch.setattr(login.webbrowser, "open", interrupt)
    code, _, err = run(monkeypatch, capsys, cfg)
    assert code == 130 and "aiu login: cancelled; nothing was changed" in err
    assert http.calls == [] and keyring.calls() == []
    assert closed(ports[0])


@pytest.mark.parametrize("args, display", [(["--no-browser"], ":0"), ([], None)])
def test_no_browser_only_prints_the_address(monkeypatch, capsys, http, cfg, keyring, browser,
                                            args, display):
    if display is None:
        monkeypatch.delenv("DISPLAY")
    monkeypatch.setattr(login, "LOGIN_TIMEOUT", 0.1)
    code, out, _ = run(monkeypatch, capsys, cfg, *args)
    assert code == 1 and browser.urls == [] and "https://openrouter.ai/auth?callback_url=" in out


@pytest.mark.parametrize("reply, message", [
    ((403, {"error": {"message": "Invalid code"}}),
     "OpenRouter refused the sign-in code (HTTP 403); nothing was changed, run this again"),
    ((400, {"error": {"message": "Invalid code_challenge_method"}}),
     "OpenRouter refused the sign-in code (HTTP 400)"),
    ((200, {"user_id": "u"}), "OpenRouter's answer holds no usable key"),
    ((200, {"key": "two\nlines"}), "OpenRouter's answer holds no usable key"),
    ((200, "<html>"), "OpenRouter's answer holds no usable key"),
    (httpx.ConnectError(f"offline {KEY}"),
     "cannot reach OpenRouter to finish the sign-in (network: cannot connect)"),
])
def test_token_endpoint_failures_change_nothing(monkeypatch, capsys, http, cfg, keyring, browser,
                                                reply, message):
    http.on("POST", TOKEN_URL, reply)
    code, _, err = run(monkeypatch, capsys, cfg)
    assert code == 1 and message in err
    assert keyring.calls() == [] and not cfg.exists()


def test_locked_keyring(monkeypatch, capsys, http, cfg, keyring, browser):
    monkeypatch.setenv("FAKE_KEYRING_LOCKED", "1")
    http.on("POST", TOKEN_URL, (200, {"key": KEY}))
    code, _, err = run(monkeypatch, capsys, cfg)
    assert code == 1 and "the keyring did not store the key (is it locked?)" in err
    assert "https://openrouter.ai/settings/keys" in err and not cfg.exists()


def test_no_secret_tool_stops_before_the_sign_in(monkeypatch, capsys, http, cfg, browser):
    code, _, err = run(monkeypatch, capsys, cfg)
    assert code == 1 and "secret-tool not found" in err and browser.urls == []


def test_untrusted_config_keeps_the_key_and_prints_the_command(monkeypatch, capsys, http, cfg,
                                                               keyring, browser):
    config.ensure_config(cfg)
    cfg.parent.chmod(0o777)
    http.on("POST", TOKEN_URL, (200, {"key": KEY}))
    code, _, err = run(monkeypatch, capsys, cfg)
    cfg.parent.chmod(0o700)
    assert code == 1 and [i["secret"] for i in keyring.items()] == [KEY]
    assert "openrouter.key_cmd not set (openrouter.key_cmd refused:" in err
    assert f'aiu config set openrouter.key_cmd "{KEY_CMD}"' in err
    assert "key_cmd" not in config.load_config(cfg)["openrouter"]


def test_warns_when_the_environment_still_wins(monkeypatch, capsys, home, http, cfg, keyring,
                                               browser):
    http.on("POST", TOKEN_URL, (200, {"key": KEY}))
    code, _, err = run(monkeypatch, capsys, cfg)
    assert code == 0 and "before key_cmd" not in err

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-old")
    _, _, err = run(monkeypatch, capsys, cfg)
    assert "OPENROUTER_API_KEY is set in your environment, and aiu reads it before key_cmd" in err

    monkeypatch.delenv("OPENROUTER_API_KEY")
    env = home / ".env"
    env.write_text("OPENROUTER_API_KEY=sk-or-v1-old\n")
    env.chmod(0o600)
    _, _, err = run(monkeypatch, capsys, cfg)
    assert f"OPENROUTER_API_KEY is set in {env}, and aiu reads it before key_cmd" in err

    config.config_set(cfg, "openrouter.key_env", "")
    _, _, err = run(monkeypatch, capsys, cfg)
    assert "before key_cmd" not in err
    assert cfg.read_text().count(f'key_cmd = "{KEY_CMD}"') == 1


def test_messages_in_french(monkeypatch, capsys, http, cfg, keyring, browser):
    monkeypatch.setenv("AIU_LANG", "fr")
    http.on("POST", TOKEN_URL, (200, {"key": KEY}))
    code, out, _ = run(monkeypatch, capsys, cfg)
    assert code == 0
    assert "Connecte-toi à OpenRouter et autorise aiu" in out
    assert "Nouvelle clé OpenRouter 'aiu' enregistrée dans le trousseau" in out
