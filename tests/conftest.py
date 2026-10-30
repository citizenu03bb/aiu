# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline harness shared by every test: a throwaway home, no provider keys in
the environment, no CLI on PATH, and an HTTP layer that only answers the calls
a test scripted. Any other call fails the test instead of reaching the network."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu.probes import claude  # noqa: E402

PROVIDER_ENV = ("OPENAI_ADMIN_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
                "MISTRAL_ADMIN_COOKIE", "AIU_ENV_FILE",
                "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """~ is an empty tmp dir; PATH holds only tmp/bin, and tmp/bin/claude is the
    only place aiu looks for `claude`, so no real `claude` is found unless a
    test puts a fake one there. The umask is 022 whatever the user's is,
    so permission tests see the same modes locally and in CI."""
    umask = os.umask(0o022)
    home = tmp_path / "home"
    (tmp_path / "bin").mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    # English messages whatever the developer's locale; French tests set AIU_LANG.
    for name in ("AIU_LANG", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "C.UTF-8")
    # Resolved at import time from the real home.
    monkeypatch.setattr(claude, "CLAUDE_LOCATIONS", (str(tmp_path / "bin" / "claude"),))
    yield home
    os.umask(umask)


class FakeHTTP:
    """Stands in for httpx.get / httpx.post. Routes match a method and a URL
    substring; each reply is (status, body[, headers]) or an exception to raise.
    Successive calls take successive replies, the last one repeats."""

    def __init__(self):
        self.routes, self.calls, self.unexpected = [], [], []

    def on(self, method, url_part, *replies):
        self.routes.append((method, url_part, list(replies)))
        return self

    def get(self, url, **kw):
        return self._send("GET", url, kw)

    def post(self, url, **kw):
        return self._send("POST", url, kw)

    def to(self, url_part):
        return [c for c in self.calls if url_part in c.url]

    def _send(self, method, url, kw):
        self.calls.append(SimpleNamespace(method=method, url=url, headers=kw.get("headers") or {},
                                          params=kw.get("params"), json=kw.get("json"),
                                          data=kw.get("data")))
        for m, part, replies in self.routes:
            if m != method or part not in url:
                continue
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
            if isinstance(reply, BaseException):
                raise reply
            status, body, headers = (*reply, None) if len(reply) == 2 else reply
            req = httpx.Request(method, url)
            if isinstance(body, (dict, list)):
                return httpx.Response(status, json=body, headers=headers, request=req)
            return httpx.Response(status, text=body, headers=headers, request=req)
        # Probes catch broad exceptions, so also record it for the teardown check.
        self.unexpected.append(f"{method} {url}")
        raise AssertionError(f"unscripted HTTP call: {method} {url}")


@pytest.fixture(autouse=True)
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setattr(httpx, "post", fake.post)
    yield fake
    assert not fake.unexpected, f"unscripted HTTP calls: {fake.unexpected}"

