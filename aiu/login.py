# SPDX-License-Identifier: GPL-3.0-or-later
"""`aiu login openrouter`: OpenRouter's OAuth PKCE sign-in
(openrouter.ai/docs/guides/overview/auth/oauth). The browser signs in at
openrouter.ai/auth and comes back to a one-shot listener on 127.0.0.1 with a
code; the code and the PKCE verifier buy a user API key at /api/v1/auth/keys.
The key goes to the keyring and openrouter.key_cmd points at it, as `aiu
cookie import` does. Only the user runs it: no probe, --json, --alert or the
GNOME extension does. The key is never printed, logged or put in argv."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.server
import os
import re
import secrets
import sys
import threading
import tomllib
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

from aiu.config import ConfigError, config_set
from aiu.cookies import CookieError, _secret_tool, keyring_store, plain_copy_where
from aiu.i18n import tr
from aiu.keys import _CONTROL_RE
from aiu.probes.common import _json_object, _need, _network_note

OPENROUTER_AUTH = "https://openrouter.ai/auth"
OPENROUTER_KEYS = "https://openrouter.ai/api/v1/auth/keys"
OPENROUTER_KEY_LABEL = "aiu"  # the key's name in openrouter.ai/settings/keys
OPENROUTER_KEYRING = ("service", "aiu", "provider", "openrouter")
OPENROUTER_KEYRING_LABEL = "aiu openrouter key"
OPENROUTER_KEY_CMD = "secret-tool lookup " + " ".join(OPENROUTER_KEYRING)
LOGIN_TIMEOUT = 300  # seconds the listener waits for the browser
# What a code may look like: printable ASCII, no space, of a sane length.
_CODE_RE = re.compile(r"[!-~]{1,2048}")


class LoginError(Exception):
    """A failed sign-in; the message says what to do and never quotes the
    code, the verifier or the key."""


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge), RFC 7636: 64 unreserved
    characters, and the unpadded base64url of their SHA-256."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class _CallbackServer(http.server.HTTPServer):
    """Loopback only, a port the kernel picks. Takes the first well-formed
    GET /callback?code=… whose Host is this listener; answers anything else
    with an error and keeps waiting."""
    allow_reuse_address = False

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _CallbackHandler)
        self.host = f"127.0.0.1:{self.server_address[1]}"
        self.code: str | None = None
        self.done = threading.Event()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer
    timeout = 10  # a client that sends nothing does not hold the listener

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        # The Host check turns away a page on another site that resolves its
        # own name to 127.0.0.1 (DNS rebinding).
        if self.headers.get("Host") != self.server.host:
            return self._answer(400, tr("Wrong host."))
        if url.path != "/callback":
            return self._answer(404, tr("Not found."))
        codes = urllib.parse.parse_qs(url.query, keep_blank_values=True).get("code", [])
        if len(codes) != 1 or not _CODE_RE.fullmatch(codes[0]):
            return self._answer(400, tr("No sign-in code in this request."))
        if self.server.code is None:
            self.server.code = codes[0]
            self.server.done.set()
        self._answer(200, tr("aiu got its OpenRouter sign-in. You can close this tab."))

    def _answer(self, status: int, text: str) -> None:
        body = (f"<!doctype html><meta charset=utf-8><title>aiu</title>"
                f"<p>{html.escape(text)}</p>").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # the request line holds the code


def _has_display() -> bool:
    # Without one, webbrowser would start a text browser in this terminal.
    return any(os.environ.get(v) for v in ("DISPLAY", "WAYLAND_DISPLAY", "BROWSER"))


def _wait_for_code(open_browser: bool, timeout: float) -> tuple[str, str]:
    """(code, verifier) once the browser has come back to the listener."""
    verifier, challenge = pkce_pair()
    server = _CallbackServer()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    try:
        thread.start()
        url = OPENROUTER_AUTH + "?" + urllib.parse.urlencode({
            "callback_url": f"http://{server.host}/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "key_label": OPENROUTER_KEY_LABEL,
        })
        print(tr("Sign in to OpenRouter and authorize aiu. If no browser opens, open this "
                 "address on this computer:"))
        print(f"  {url}", flush=True)
        if open_browser and _has_display():
            try:
                webbrowser.open(url)
            except Exception:
                pass  # the address is printed
        if not server.done.wait(timeout):
            raise LoginError(tr("no sign-in within {minutes} minutes; nothing was changed",
                                minutes=round(timeout / 60)))
        return server.code, verifier
    finally:
        if thread.is_alive():
            server.shutdown()
        server.server_close()


def _exchange(code: str, verifier: str) -> str:
    try:
        r = httpx.post(OPENROUTER_KEYS, json={"code": code, "code_verifier": verifier,
                                              "code_challenge_method": "S256"}, timeout=30)
    except Exception as e:
        raise LoginError(tr("cannot reach OpenRouter to finish the sign-in ({error}); "
                            "nothing was changed", error=_network_note(e))) from None
    if r.status_code != 200:
        raise LoginError(tr("OpenRouter refused the sign-in code (HTTP {status}); "
                            "nothing was changed, run this again", status=r.status_code))
    try:
        key = _need(_json_object(r), "key", str).strip()
    except ValueError:
        key = ""
    if not key or _CONTROL_RE.search(key) or any(c.isspace() for c in key):
        raise LoginError(tr("OpenRouter's answer holds no usable key; nothing was changed"))
    return key


def login_openrouter(path: Path, open_browser: bool = True) -> int:
    _secret_tool()  # before the user signs in for nothing
    code, verifier = _wait_for_code(open_browser, LOGIN_TIMEOUT)
    key = _exchange(code, verifier)
    if not keyring_store(key, OPENROUTER_KEYRING, OPENROUTER_KEYRING_LABEL):
        raise LoginError(tr("the keyring did not store the key (is it locked?). Delete the "
                            "unused key '{name}' at https://openrouter.ai/settings/keys, then "
                            "run this again", name=OPENROUTER_KEY_LABEL))
    print(tr("Stored a new OpenRouter key '{name}' in the keyring, labelled '{label}'.",
             name=OPENROUTER_KEY_LABEL, label=OPENROUTER_KEYRING_LABEL))
    status = 0
    try:
        config_set(path, "openrouter.key_cmd", OPENROUTER_KEY_CMD)
        print(tr("{option} set in {path}", option="openrouter.key_cmd", path=path))
    except (ConfigError, OSError, UnicodeDecodeError) as e:
        reason = e if isinstance(e, ConfigError) else getattr(e, "strerror", None) or type(e).__name__
        print(tr("aiu login: openrouter.key_cmd not set ({error}). Once that is fixed, run:",
                 error=reason), file=sys.stderr)
        print(f"  aiu config set openrouter.key_cmd \"{OPENROUTER_KEY_CMD}\"", file=sys.stderr)
        status = 1
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return status
    section = config.get("openrouter") if isinstance(config.get("openrouter"), dict) else {}
    var = section.get("key_env")
    if where := plain_copy_where(config, var):
        print(tr("{name} is set in {where}, and aiu reads it before key_cmd: delete it there "
                 "(it is a key in plain text) so the keyring copy is used.",
                 name=var, where=where), file=sys.stderr)
    if section.get("enabled") is not True:
        print(tr("openrouter is off: aiu config set openrouter.enabled true"))
    print(tr("Check it with: aiu --only openrouter"))
    return status


# provider -> sign-in; ChatGPT could join here (see ROADMAP, Later).
PROVIDERS = {"openrouter": login_openrouter}


def login_main(args: argparse.Namespace) -> int:
    try:
        return PROVIDERS[args.provider](Path(os.path.expanduser(args.config)),
                                        open_browser=not args.no_browser)
    except (LoginError, CookieError) as e:
        print(tr("aiu login: {error}", error=e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(tr("aiu login: cancelled; nothing was changed"), file=sys.stderr)
        return 130
    except OSError as e:
        where = f"{e.filename}: " if e.filename else ""
        print(tr("aiu login: {error}", error=where + (e.strerror or type(e).__name__)),
              file=sys.stderr)
        return 1
