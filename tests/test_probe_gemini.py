# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini probe — no network: the Antigravity desktop app's language server,
found through a faked /proc, is its only source."""
import builtins
import glob
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu.probes import gemini  # noqa: E402

SUMMARY = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
OPEN_ANTIGRAVITY = "Antigravity desktop app not running — open it to read the Gemini quota"


def iso(secs_from_now):
    return datetime.fromtimestamp(time.time() + secs_from_now, timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Local path: the Antigravity desktop app's language server
# ---------------------------------------------------------------------------
TCP_HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
              "   uid  timeout inode")


UID = os.getuid()
OTHER_UID = UID + 1


class FakeProc:
    """Just enough of /proc for the language-server discovery: cmdlines, owners,
    executables, socket fds, /proc/net/tcp{,6}. Every other path goes to the real
    filesystem. Processes and sockets belong to the current user unless told
    otherwise, and a process's executable is its argv[0] unless told otherwise."""

    def __init__(self):
        self.files = {"/proc/net/tcp": [TCP_HEADER], "/proc/net/tcp6": [TCP_HEADER]}
        self.fds, self.links = {}, {}

    def process(self, pid, *argv, sockets=(), uid=UID, exe=None):
        self.files[f"/proc/{pid}/cmdline"] = b"\0".join(argv) + b"\0"
        self.files[f"/proc/{pid}/status"] = [f"Name:\tproc{pid}", f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}"]
        self.links[f"/proc/{pid}/exe"] = exe if exe is not None else argv[0].decode()
        self.fds[f"/proc/{pid}/fd"] = {"0": "/dev/null",
                                       **{str(3 + i): f"socket:[{ino}]" for i, ino in enumerate(sockets)}}

    def listen(self, port, inode, ip="0100007F", state="0A", uid=UID):
        table = self.files["/proc/net/tcp6" if len(ip) == 32 else "/proc/net/tcp"]
        table.append(
            f"  {len(table) - 1}: {ip}:{port:04X} {'0' * len(ip)}:0000 {state} 00000000:00000000 "
            f"00:00000000 00000000  {uid}        0 {inode} 1 0000000000000000 100 0 0 10 0")

    def install(self, monkeypatch):
        real_open, real_listdir, real_readlink = builtins.open, os.listdir, os.readlink

        def fake_open(path, mode="r", *a, **kw):
            path = str(path)
            if path in self.files:
                v = self.files[path]
                return io.BytesIO(v) if isinstance(v, bytes) else io.StringIO("\n".join(v) + "\n")
            if path.startswith("/proc/"):
                raise FileNotFoundError(path)
            return real_open(path, mode, *a, **kw)

        def fake_listdir(path="."):
            if str(path).startswith("/proc/"):
                return list(self.fds[str(path)])
            return real_listdir(path)

        def fake_readlink(path, *a, **kw):
            if str(path) in self.links:
                return self.links[str(path)]
            d, _, fd = str(path).rpartition("/")
            if d in self.fds:
                return self.fds[d][fd]
            return real_readlink(path, *a, **kw)

        monkeypatch.setattr(glob, "glob", lambda pattern: [p for p in self.files if p.endswith("/cmdline")]
                            if pattern == "/proc/[0-9]*/cmdline" else [])
        monkeypatch.setattr(gemini, "open", fake_open, raising=False)
        monkeypatch.setattr(os, "listdir", fake_listdir)
        monkeypatch.setattr(os, "readlink", fake_readlink)
        return self


@pytest.fixture
def proc(monkeypatch):
    return FakeProc().install(monkeypatch)


LS = b"/usr/share/antigravity/resources/app/extensions/antigravity/bin/language_server_linux_x64"


def summary(*buckets, group="Gemini Models"):
    return {"response": {"groups": [
        {"displayName": "Claude Models", "buckets": [{"window": "5h", "remainingFraction": 0.1}]},
        {"displayName": group, "buckets": list(buckets)}]}}


SUMMARY_BODY = summary(
    {"bucketId": "gemini-weekly", "window": "weekly", "displayName": "Weekly",
     "remainingFraction": 0.75, "resetTime": iso(3 * 86400)},
    {"bucketId": "gemini-5h", "window": "5h", "displayName": "5 hours",
     "remainingFraction": 0.4, "resetTime": iso(2 * 3600)},
    {"bucketId": "gemini-images", "displayName": "Image generation", "remainingFraction": 1.0},
    {"bucketId": "gemini-5h-burst", "window": "5h"},  # no fraction: skipped
)


def record_post_kwargs(monkeypatch, http):
    """The keyword arguments of every httpx.post call, on top of the fake."""
    seen = []

    def post(url, **kw):
        seen.append(kw)
        return http.post(url, **kw)
    monkeypatch.setattr(httpx, "post", post)
    return seen


def write_main_log(home, *tokens):
    log = home / ".config" / "Antigravity" / "logs" / "main.log"
    log.parent.mkdir(parents=True)
    log.write_text("".join(f"[info] spawn language_server --csrf_token {t} --random_port\n" for t in tokens))


def test_local_language_server(proc, http, monkeypatch):
    proc.process(4242, LS, b"--csrf_token", b"0f1e2d3c-aaaa-bbbb", b"--extension_server_port", b"40001",
                 sockets=(5555, 7777))
    proc.process(4300, b"/usr/bin/bash")                # unrelated process
    proc.listen(41394, 5555)                            # the server's listening socket
    proc.listen(40001, 9999)                            # someone else's
    proc.listen(41395, 7777, state="01")                # established, not listening
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", httpx.RemoteProtocolError("TLS expected"))
    http.on("POST", f"https://127.0.0.1:41394{SUMMARY}", (200, SUMMARY_BODY))
    posts = record_post_kwargs(monkeypatch, http)
    rows = gemini.probe_gemini({})
    assert [(r.metric, round(r.used, 2), r.window_secs) for r in rows] == [
        ("5-Hour Limit", 0.6, 5 * 3600), ("Weekly Limit", 0.25, 7 * 86400), ("Image generation", 0.0, None)]
    assert rows[0].reset_epoch == pytest.approx(time.time() + 7200, abs=5)
    assert [c.url.split(":")[0] for c in http.calls] == ["http", "https"]
    assert all(c.headers["x-codeium-csrf-token"] == "0f1e2d3c-aaaa-bbbb" for c in http.calls)
    assert all(kw.get("verify", True) is True for kw in posts)  # H1: TLS checks stay on


def test_local_main_log_without_server_sends_nothing(proc, http, home):
    # H1: with no Antigravity server in /proc, the logged token used to be tried
    # against every port listening on localhost, whoever owned it.
    write_main_log(home, "11111111-aaaa", "22222222-bbbb")
    proc.listen(631, 100)                       # another local service
    proc.listen(41394, 101, ip="00000000")      # maybe the server, maybe not
    proc.listen(41500, 102, uid=OTHER_UID)      # another user's
    assert gemini._query_local_quota_summary() is None and http.calls == []


def test_local_csrf_fallback_for_found_server(proc, http, home):
    # A6: the server is found but its cmdline has no token: the logged one is
    # used, for that server's ports only.
    write_main_log(home, "11111111-aaaa", "22222222-bbbb")
    proc.process(4242, LS, b"--random_port", sockets=(5555,))
    proc.listen(41394, 5555)
    proc.listen(631, 100)                       # not the server's
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", (200, SUMMARY_BODY))
    assert gemini._query_local_quota_summary()[0].metric == "5-Hour Limit"
    assert [(c.url, c.headers.get("x-codeium-csrf-token")) for c in http.calls] == [
        (f"http://127.0.0.1:41394{SUMMARY}", "22222222-bbbb")]


def test_local_token_goes_only_to_its_own_server(proc, http, home):
    # H1: a token is paired only with the ports of the process it was read from.
    write_main_log(home, "logged-token")
    proc.process(4242, LS, b"--csrf_token", b"token-a", sockets=(5555,))
    proc.process(4343, LS, b"--csrf_token", b"token-b", sockets=(6666,))
    proc.listen(41394, 5555)
    proc.listen(41500, 6666)
    http.on("POST", f"127.0.0.1:41394{SUMMARY}", (500, "busy"))
    http.on("POST", f"127.0.0.1:41500{SUMMARY}", (500, "busy"))
    assert gemini._query_local_quota_summary() is None
    assert {(c.url.split("/")[2], c.headers["x-codeium-csrf-token"]) for c in http.calls} == {
        ("127.0.0.1:41394", "token-a"), ("127.0.0.1:41500", "token-b")}


@pytest.mark.parametrize("argv, owner", [
    ((LS,), dict(uid=OTHER_UID)),                                   # another user's Antigravity
    ((LS,), dict(exe="/usr/bin/python3")),                          # argv[0] says LS, the binary doesn't
    ((b"/usr/bin/vim", b"/usr/share/antigravity/language_server.py"), {}),  # old substring match
    ((b"/opt/windsurf/bin/language_server_linux_x64",), {}),        # not Antigravity's
    ((b"/usr/share/antigravity/bin/node",), {}),                    # Antigravity's, not its server
], ids=["other-user", "spoofed-argv", "editor", "other-ide", "other-binary"])
def test_local_only_our_antigravity_server(proc, http, home, argv, owner):
    # H1: the token and the request go only to a language_server binary of an
    # Antigravity install, run by us.
    write_main_log(home, "22222222-bbbb")
    proc.process(4242, *argv, b"--csrf_token", b"tok", sockets=(5555,), **owner)
    proc.listen(41394, 5555)
    assert gemini._query_local_quota_summary() is None and http.calls == []


@pytest.mark.parametrize("sock", [
    dict(uid=OTHER_UID),                                    # socket owned by someone else
    dict(ip="0F02000A"),                                    # 10.0.2.15: 127.0.0.1 may be another's
    dict(ip="000080FE000000000000000001000000"),            # fe80::1
], ids=["other-user", "other-interface", "ipv6-link-local"])
def test_local_only_our_loopback_sockets(proc, http, sock):
    proc.process(4242, LS, b"--csrf_token", b"tok", sockets=(5555,))
    proc.listen(41394, 5555, **sock)
    assert gemini._query_local_quota_summary() is None and http.calls == []


def test_local_ipv6_loopback(proc, http):
    proc.process(4242, LS, b"--csrf_token", b"tok", sockets=(5555,))
    proc.listen(41394, 5555, ip="00000000000000000000000001000000")  # [::1]
    http.on("POST", f"http://[::1]:41394{SUMMARY}", (200, SUMMARY_BODY))
    assert gemini._query_local_quota_summary()[0].metric == "5-Hour Limit"


def test_local_server_updated_while_running(proc, http):
    # An update replaces the binary on disk: the link then ends in " (deleted)".
    proc.process(4242, LS, b"--csrf_token", b"tok", sockets=(5555,), exe=LS.decode() + " (deleted)")
    proc.listen(41394, 5555)
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", (200, SUMMARY_BODY))
    assert gemini._query_local_quota_summary()[0].metric == "5-Hour Limit"


def test_local_without_gemini_group_says_open_antigravity(proc, http):
    proc.process(4242, LS, b"--csrf_token", b"tok", sockets=(5555,))
    proc.listen(41394, 5555)
    http.on("POST", f"127.0.0.1:41394{SUMMARY}", (200, summary(group="Other Models")))
    [row] = gemini.probe_gemini({})
    assert row.kind == "error" and row.note == OPEN_ANTIGRAVITY


def test_local_server_errors_fall_back(proc, http):
    proc.process(4242, LS, b"--csrf_token", b"tok", sockets=(5555,))
    proc.listen(41394, 5555)
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", (401, {"code": "unauthenticated"}))
    http.on("POST", f"https://127.0.0.1:41394{SUMMARY}", (200, "not json"))
    assert gemini._query_local_quota_summary() is None


@pytest.mark.parametrize("body", [
    [], {"response": None}, {"response": {"groups": {"displayName": "Gemini Models"}}},
    summary("gemini-5h"), summary({"window": "5h", "remainingFraction": "0.4"}),
    summary({"window": "5h", "remainingFraction": True}),
    summary({"window": "5h", "remainingFraction": float("nan")}),
])
def test_local_malformed_summary_is_no_summary(body):
    assert gemini._parse_summary_response(body) is None


def test_local_nothing_running(proc, http):
    assert gemini._query_local_quota_summary() is None and http.calls == []


def test_no_local_server_makes_no_network_call(proc, http, home):
    # Task S: without Antigravity there is no other source. Even with a Gemini
    # CLI sign-in on disk, nothing is read from it or sent anywhere: the fake
    # HTTP layer fails the test on any call, and none is scripted.
    (home / ".gemini").mkdir()
    (home / ".gemini" / "oauth_creds.json").write_text(json.dumps(
        {"access_token": "ya29.fake", "expiry_date": 0, "refresh_token": "1//fake"}))
    [row] = gemini.probe_gemini({})
    assert (row.provider, row.metric, row.kind) == ("gemini", "—", "error")
    assert row.note == OPEN_ANTIGRAVITY
    assert http.calls == []


def test_open_antigravity_row_in_french(proc, http, monkeypatch):
    monkeypatch.setenv("AIU_LANG", "fr")
    [row] = gemini.probe_gemini({})
    assert row.note == "application de bureau Antigravity fermée — ouvre-la pour lire le quota Gemini"
    assert http.calls == []
