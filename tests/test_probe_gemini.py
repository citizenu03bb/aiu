# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini probe, both sources — no network:
- local: the Antigravity desktop app's language server, found through a faked /proc;
- OAuth: ~/.gemini/oauth_creds.json (in the tmp home), the token refresh and its
  client lookup (environment, installed Gemini CLI bundle, neither), and the
  Code Assist quota endpoints."""
import builtins
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402

LOAD_URL = aiu.GEMINI_CA_BASE + "/v1internal:loadCodeAssist"
QUOTA_URL = aiu.GEMINI_CA_BASE + "/v1internal:retrieveUserQuota"
TOKEN_URL = aiu.GEMINI_TOKEN_URL
SUMMARY = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
CLIENT = ("123456-fake.apps.googleusercontent.com", "GOCSPX-not-a-real-secret")


def iso(secs_from_now):
    return datetime.fromtimestamp(time.time() + secs_from_now, timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# OAuth client lookup
# ---------------------------------------------------------------------------
def install_cli(tmp_path, chunks):
    """Lay out an npm-style install: bin/gemini -> .../gemini-cli/bundle/gemini.js."""
    bundle = tmp_path / "lib" / "node_modules" / "@google" / "gemini-cli" / "bundle"
    bundle.mkdir(parents=True)
    entry = bundle / "gemini.js"
    entry.write_text("#!/usr/bin/env node\nimport './chunk-main.js';\n")
    entry.chmod(0o755)
    for name, text in chunks.items():
        (bundle / name).write_text(text)
    (tmp_path / "bin" / "gemini").symlink_to(entry)
    return bundle


CLI_CHUNK = (f'var OAUTH_CLIENT_ID = "{CLIENT[0]}";\n'
             f'var OAUTH_CLIENT_SECRET = "{CLIENT[1]}";\n')
# Other Google client IDs ship in the same bundle, outside the named constants.
DECOY_CHUNK = ('const clientId = "999999-decoy.apps.googleusercontent.com";\n'
               'const client_secret = "GOCSPX-decoy";\n')


@pytest.fixture
def env_client(monkeypatch):
    monkeypatch.setenv("GEMINI_CLIENT_ID", CLIENT[0])
    monkeypatch.setenv("GEMINI_CLIENT_SECRET", CLIENT[1])


def test_client_from_environment(env_client, tmp_path):
    install_cli(tmp_path, {"chunk-AAA.js": 'OAUTH_CLIENT_ID = "1-cli.apps.googleusercontent.com"\n'
                                           'OAUTH_CLIENT_SECRET = "cli"\n'})
    assert aiu._gemini_oauth_client() == CLIENT  # the environment wins over the CLI


def test_client_from_installed_cli(tmp_path):
    install_cli(tmp_path, {"chunk-AAA.js": DECOY_CHUNK, "chunk-BBB.js": CLI_CHUNK})
    assert aiu._gemini_oauth_client() == CLIENT


def test_client_half_set_environment_uses_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_CLIENT_ID", "only-the-id.apps.googleusercontent.com")
    install_cli(tmp_path, {"chunk-BBB.js": CLI_CHUNK})
    assert aiu._gemini_oauth_client() == CLIENT


def test_client_cli_without_constants(tmp_path):
    install_cli(tmp_path, {"chunk-AAA.js": DECOY_CHUNK})
    assert aiu._gemini_oauth_client() is None


def test_client_neither(monkeypatch):
    monkeypatch.setenv("GEMINI_CLIENT_SECRET", "secret-without-id")
    assert aiu._gemini_oauth_client() is None


# ---------------------------------------------------------------------------
# Access token and refresh
# ---------------------------------------------------------------------------
def write_creds(token="ya29.fake-access", expires_in=3000, refresh="1//fake-refresh", **extra):
    creds = {"access_token": token, "scope": "https://www.googleapis.com/auth/cloud-platform",
             "token_type": "Bearer", "id_token": "eyJ.old-id",
             "expiry_date": int((time.time() + expires_in) * 1000), **extra}
    if refresh:
        creds["refresh_token"] = refresh
    aiu.GEMINI_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    aiu.GEMINI_CREDS_PATH.write_text(json.dumps(creds))
    return creds


def read_creds():
    return json.loads(aiu.GEMINI_CREDS_PATH.read_text())


REFRESHED = {"access_token": "ya29.refreshed", "expires_in": 3599, "token_type": "Bearer",
             "scope": "https://www.googleapis.com/auth/cloud-platform", "id_token": "eyJ.new-id"}


def test_fresh_token_is_used_as_is(http):
    write_creds()
    assert aiu._gemini_access_token() == "ya29.fake-access"
    assert http.calls == []


def test_expired_token_is_refreshed_and_persisted(env_client, http):
    write_creds(expires_in=-60)
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    assert aiu._gemini_access_token() == "ya29.refreshed"
    [call] = http.calls
    assert call.data == {"grant_type": "refresh_token", "refresh_token": "1//fake-refresh",
                         "client_id": CLIENT[0], "client_secret": CLIENT[1]}
    creds = read_creds()
    assert creds["access_token"] == "ya29.refreshed" and creds["id_token"] == "eyJ.new-id"
    assert creds["refresh_token"] == "1//fake-refresh"  # Google does not rotate it
    assert creds["expiry_date"] / 1000 == pytest.approx(time.time() + 3599, abs=5)


def test_near_expiry_is_refreshed(env_client, http):
    write_creds(expires_in=30)  # inside the 60s buffer
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    assert aiu._gemini_access_token() == "ya29.refreshed"


def test_refresh_with_cli_bundle_client(tmp_path, http):
    install_cli(tmp_path, {"chunk-BBB.js": CLI_CHUNK})
    write_creds(expires_in=-60)
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    assert aiu._gemini_access_token() == "ya29.refreshed"
    assert http.calls[0].data["client_id"] == CLIENT[0]


def test_expired_without_client_keeps_old_token(http):
    write_creds(expires_in=-60)
    assert aiu._gemini_access_token() == "ya29.fake-access"
    assert http.calls == [] and read_creds()["access_token"] == "ya29.fake-access"


@pytest.mark.parametrize("reply", [(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}),
                                   (500, "backend error"),
                                   (200, {"token_type": "Bearer"}),
                                   httpx.ConnectError("offline")])
def test_failed_refresh_keeps_old_token(env_client, http, reply):
    before = write_creds(expires_in=-60)
    http.on("POST", TOKEN_URL, reply)
    assert aiu._gemini_access_token() == "ya29.fake-access"
    assert read_creds() == before


def test_expired_without_refresh_token(env_client, http):
    write_creds(expires_in=-60, refresh=None)
    assert aiu._gemini_access_token() == "ya29.fake-access" and http.calls == []


def test_forced_refresh(env_client, http):
    write_creds()
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    assert aiu._gemini_access_token(force=True) == "ya29.refreshed"


@pytest.mark.parametrize("content", [None, "", "{oops"])
def test_no_usable_creds_file(http, content):
    if content is not None:
        aiu.GEMINI_CREDS_PATH.parent.mkdir()
        aiu.GEMINI_CREDS_PATH.write_text(content)
    assert aiu._gemini_access_token() is None and http.calls == []


def test_refresh_writes_a_0600_file_and_keeps_other_fields(env_client, http):
    write_creds(expires_in=-60, added_by_cli="keep me")
    aiu.GEMINI_CREDS_PATH.chmod(0o644)
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    assert aiu._gemini_access_token() == "ya29.refreshed"
    assert read_creds()["added_by_cli"] == "keep me"
    assert aiu.GEMINI_CREDS_PATH.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in aiu.GEMINI_CREDS_PATH.parent.iterdir()) == [
        ".oauth_creds.json.aiu-lock", "oauth_creds.json"]  # no temp file left


def test_refresh_through_a_symlink_keeps_the_symlink(env_client, http, home):
    # Dotfile managers link ~/.gemini/oauth_creds.json into their own tree.
    write_creds(expires_in=-60)
    real = home / "dotfiles" / "oauth_creds.json"
    real.parent.mkdir()
    aiu.GEMINI_CREDS_PATH.rename(real)
    aiu.GEMINI_CREDS_PATH.symlink_to(real)
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    assert aiu._gemini_access_token() == "ya29.refreshed"
    assert aiu.GEMINI_CREDS_PATH.is_symlink()
    assert json.loads(real.read_text())["access_token"] == "ya29.refreshed"


@pytest.mark.parametrize("crash", [OSError(28, "No space left on device"), KeyboardInterrupt()])
def test_interrupted_write_leaves_the_old_file_intact(env_client, http, monkeypatch, capsys, crash):
    write_creds(expires_in=-60)
    before = aiu.GEMINI_CREDS_PATH.read_bytes()
    http.on("POST", TOKEN_URL, (200, REFRESHED))

    def fsync(fd):  # the temp file is written, the rename has not happened yet
        raise crash
    monkeypatch.setattr(aiu.os, "fsync", fsync)
    if isinstance(crash, OSError):  # logged, and the token still serves this run
        assert aiu._gemini_access_token() == "ya29.refreshed"
        err = capsys.readouterr().err
        assert "kept for this run only" in err and "OSError" in err and "ya29" not in err
    else:  # the run is killed mid-write
        with pytest.raises(KeyboardInterrupt):
            aiu._gemini_access_token()
    assert aiu.GEMINI_CREDS_PATH.read_bytes() == before
    assert [p.name for p in aiu.GEMINI_CREDS_PATH.parent.glob("*.tmp")] == []


def test_refresh_does_not_write_a_file_owned_by_someone_else(env_client, http, monkeypatch, capsys):
    before = write_creds(expires_in=-60)
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    uid = aiu.GEMINI_CREDS_PATH.stat().st_uid
    monkeypatch.setattr(aiu.os, "getuid", lambda: uid + 1)
    assert aiu._gemini_access_token() == "ya29.refreshed"
    assert read_creds() == before and "not owned by you" in capsys.readouterr().err


def test_persist_rereads_the_file_under_the_lock(env_client):
    # The user signed in again between aiu's read and its write: keep their new login.
    before = write_creds(refresh="1//signed-in-again")
    assert aiu._gemini_persist("1//fake-refresh", "ya29.refreshed", 0, None) == \
        "signed in again meanwhile"
    assert read_creds() == before


def test_persist_waits_for_the_lock(env_client):
    import fcntl
    import os
    import threading
    write_creds()
    lock = os.open(aiu.GEMINI_CREDS_PATH.with_name(".oauth_creds.json.aiu-lock"),
                   os.O_CREAT | os.O_WRONLY, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)  # another aiu run is writing
    t = threading.Thread(target=aiu._gemini_persist,
                         args=("1//fake-refresh", "ya29.refreshed", 0, None))
    t.start()
    t.join(0.3)
    assert t.is_alive() and read_creds()["access_token"] == "ya29.fake-access"
    os.close(lock)
    t.join(5)
    assert read_creds()["access_token"] == "ya29.refreshed"


# ---------------------------------------------------------------------------
# OAuth probe (the desktop app is not running)
# ---------------------------------------------------------------------------
@pytest.fixture
def no_desktop(monkeypatch):
    monkeypatch.setattr(aiu, "_query_local_quota_summary", lambda: None)


# loadCodeAssist for an account the CLI client still serves (trimmed).
LOAD = {"currentTier": {"id": "standard-tier", "name": "Gemini Code Assist",
                        "userDefinedCloudaicompanionProject": False},
        "allowedTiers": [{"id": "standard-tier", "name": "Gemini Code Assist", "isDefault": True}],
        "cloudaicompanionProject": "fluent-cursor-abc12"}


def quota(*buckets):
    return {"buckets": list(buckets)}


def bucket(model, remaining, reset_in=None):
    b = {"modelId": model, "tokenType": "REQUESTS", "remainingFraction": remaining}
    if reset_in is not None:
        b["resetTime"] = iso(reset_in)
    return b


def test_oauth_normal_response(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, LOAD))
    http.on("POST", QUOTA_URL, (200, quota(
        bucket("gemini-2.5-flash", 1.0, 86400),
        bucket("gemini-2.5-pro", 0.8, 86400),
        bucket("gemini-2.5-pro_vertex", 0.6, 7200),   # tighter: wins the family
        bucket("gemini-2.5-flash-lite", 0.95),
        bucket("gemini-embedding-001", 0.5),
        {"modelId": "gemini-2.0-flash", "tokenType": "REQUESTS"},  # no fraction: skipped
    )))
    rows = aiu.probe_gemini({})
    assert [(r.metric, round(r.used, 2)) for r in rows] == [
        ("Gemini Pro", 0.4), ("Gemini Flash", 0.0), ("Gemini Flash Lite", 0.05),
        ("gemini-embedding-001", 0.5)]
    pro = rows[0]
    assert pro.kind == "quota" and pro.reset_epoch == pytest.approx(time.time() + 7200, abs=5)
    assert rows[2].reset is None and rows[2].reset_epoch is None
    load, q = http.calls
    assert load.headers["Authorization"] == "Bearer ya29.fake-access"
    assert q.json == {"project": "fluent-cursor-abc12"}


def test_oauth_plan_name_on_first_row(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, {**LOAD, "planName": "Google AI Pro"}))
    http.on("POST", QUOTA_URL, (200, quota(bucket("gemini-2.5-pro", 0.5), bucket("gemini-2.5-flash", 0.5))))
    pro, flash = aiu.probe_gemini({})
    assert pro.note == "Google AI Pro" and flash.note == ""


def test_oauth_plan_from_current_tier(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, LOAD))
    http.on("POST", QUOTA_URL, (200, quota(bucket("gemini-2.5-pro", 0.5), bucket("gemini-2.5-flash", 0.5))))
    pro, flash = aiu.probe_gemini({})
    assert pro.note == "Gemini Code Assist" and flash.note == ""


@pytest.mark.parametrize("load_reply", [(500, {"error": {"code": 500}}), httpx.ConnectError("offline")])
def test_oauth_load_failure_still_reads_quota(no_desktop, http, load_reply):
    write_creds()
    http.on("POST", LOAD_URL, load_reply)
    http.on("POST", QUOTA_URL, (200, quota(bucket("gemini-2.5-pro", 0.25))))
    [row] = aiu.probe_gemini({})
    assert row.used == 0.75
    assert http.to(QUOTA_URL)[0].json == {}  # no project known


def test_oauth_rejected_then_refreshed(no_desktop, env_client, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, LOAD))
    http.on("POST", QUOTA_URL, (401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}),
            (200, quota(bucket("gemini-2.5-pro", 0.5))))
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    [row] = aiu.probe_gemini({})
    assert row.metric == "Gemini Pro" and row.used == 0.5
    assert [c.headers["Authorization"] for c in http.to(QUOTA_URL)] == [
        "Bearer ya29.fake-access", "Bearer ya29.refreshed"]
    assert read_creds()["access_token"] == "ya29.refreshed"


def test_oauth_rejected_twice(no_desktop, env_client, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, LOAD))
    http.on("POST", QUOTA_URL, (401, {"error": {"code": 401}}))
    http.on("POST", TOKEN_URL, (400, {"error": "invalid_grant"}))
    [row] = aiu.probe_gemini({})
    assert row.kind == "error" and "refresh token invalid" in row.note
    assert len(http.to(QUOTA_URL)) == 2


def test_oauth_rejected_and_no_client(no_desktop, http):
    # Neither the environment nor an installed CLI provides the OAuth client.
    write_creds(expires_in=-60)
    http.on("POST", LOAD_URL, (401, {"error": {"code": 401}}))
    http.on("POST", QUOTA_URL, (401, {"error": {"code": 401}}))
    [row] = aiu.probe_gemini({})
    assert row.kind == "error"
    assert row.note == ("token expired and can't refresh: install gemini-cli or set "
                        "GEMINI_CLIENT_ID / GEMINI_CLIENT_SECRET")
    assert http.to(TOKEN_URL) == []


def test_oauth_forbidden_twice(no_desktop, env_client, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, LOAD))
    http.on("POST", QUOTA_URL, (403, {"error": {"code": 403, "status": "PERMISSION_DENIED"}}))
    http.on("POST", TOKEN_URL, (200, REFRESHED))
    [row] = aiu.probe_gemini({})
    assert row.kind == "error" and "permission denied (403)" in row.note
    assert "refresh token" not in row.note and "(401)" not in row.note
    assert len(http.to(QUOTA_URL)) == 2 and len(http.to(TOKEN_URL)) == 1


def test_oauth_unsupported_client(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, {"allowedTiers": [{"id": "standard-tier"}],
                                     "ineligibleTiers": [{"reasonCode": "UNSUPPORTED_CLIENT",
                                                          "tierId": "free-tier"}]}))
    http.on("POST", QUOTA_URL, (403, {"error": {"code": 403, "status": "PERMISSION_DENIED"}}))
    [row] = aiu.probe_gemini({})
    assert "for free-tier" in row.note and "Antigravity" in row.note


def test_oauth_server_error(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, LOAD))
    http.on("POST", QUOTA_URL, (500, {"error": {"code": 500, "status": "INTERNAL"}}))
    [row] = aiu.probe_gemini({})
    assert row.kind == "error" and row.note == "HTTP 500"


def test_oauth_network_error(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, httpx.ConnectError("offline"))
    http.on("POST", QUOTA_URL, httpx.ConnectError("offline"))
    [row] = aiu.probe_gemini({})
    assert row.kind == "error" and row.note == "network: offline"


def test_oauth_no_buckets_with_plan(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, {**LOAD, "planName": "Google AI Pro"}))
    http.on("POST", QUOTA_URL, (200, {}))
    [row] = aiu.probe_gemini({})
    assert row.kind == "quota" and row.used is None
    assert row.note == "Google AI Pro: no quota buckets returned"


def test_oauth_no_buckets_without_plan(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (200, LOAD))
    http.on("POST", QUOTA_URL, (200, quota({"modelId": "gemini-2.5-pro", "remaining": 0.5})))
    [row] = aiu.probe_gemini({})
    assert row.note == "Gemini Code Assist: no quota buckets returned"


def test_oauth_no_buckets_and_no_plan(no_desktop, http):
    write_creds()
    http.on("POST", LOAD_URL, (500, {"error": {"code": 500}}))
    http.on("POST", QUOTA_URL, (200, {}))
    [row] = aiu.probe_gemini({})
    assert row.note == "no quota buckets returned"


@pytest.mark.parametrize("body", ["<html>Google</html>", [], {"buckets": "pro"},
                                  {"buckets": [{"modelId": "gemini-2.5-pro", "remainingFraction": "n/a"}]}])
def test_oauth_non_json_body(no_desktop, http, body):
    write_creds()
    http.on("POST", LOAD_URL, (200, "<html>Google</html>"))
    http.on("POST", QUOTA_URL, (200, body))
    [row] = aiu.probe_gemini({})
    assert row.kind == "error" and row.note == "malformed quota response (HTTP 200)"


def test_oauth_no_creds(no_desktop, http):
    [row] = aiu.probe_gemini({})
    assert row.kind == "error" and "open Antigravity" in row.note and http.calls == []


# ---------------------------------------------------------------------------
# Local path: the Antigravity desktop app's language server
# ---------------------------------------------------------------------------
TCP_HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
              "   uid  timeout inode")


class FakeProc:
    """Just enough of /proc for the language-server discovery: cmdlines, socket fds,
    /proc/net/tcp{,6}. Every other path goes to the real filesystem."""

    def __init__(self):
        self.files = {"/proc/net/tcp": [TCP_HEADER], "/proc/net/tcp6": [TCP_HEADER]}
        self.fds = {}

    def process(self, pid, *argv, sockets=()):
        self.files[f"/proc/{pid}/cmdline"] = b"\0".join(argv) + b"\0"
        self.fds[f"/proc/{pid}/fd"] = {"0": "/dev/null",
                                       **{str(3 + i): f"socket:[{ino}]" for i, ino in enumerate(sockets)}}

    def listen(self, port, inode, ip="0100007F", state="0A"):
        n = len(self.files["/proc/net/tcp"]) - 1
        self.files["/proc/net/tcp"].append(
            f"  {n}: {ip}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 "
            f"00000000  1000        0 {inode} 1 0000000000000000 100 0 0 10 0")

    def install(self, monkeypatch):
        real_open, real_listdir, real_readlink = builtins.open, aiu.os.listdir, aiu.os.readlink

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
            d, _, fd = str(path).rpartition("/")
            if d in self.fds:
                return self.fds[d][fd]
            return real_readlink(path, *a, **kw)

        monkeypatch.setattr(aiu.glob, "glob", lambda pattern: [p for p in self.files if p.endswith("/cmdline")]
                            if pattern == "/proc/[0-9]*/cmdline" else [])
        monkeypatch.setattr(aiu, "open", fake_open, raising=False)
        monkeypatch.setattr(aiu.os, "listdir", fake_listdir)
        monkeypatch.setattr(aiu.os, "readlink", fake_readlink)
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


def test_local_language_server(proc, http):
    proc.process(4242, LS, b"--csrf_token", b"0f1e2d3c-aaaa-bbbb", b"--extension_server_port", b"40001",
                 sockets=(5555, 7777))
    proc.process(4300, b"/usr/bin/bash")                # unrelated process
    proc.listen(41394, 5555)                            # the server's listening socket
    proc.listen(40001, 9999)                            # someone else's
    proc.listen(41395, 7777, state="01")                # established, not listening
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", httpx.RemoteProtocolError("TLS expected"))
    http.on("POST", f"https://127.0.0.1:41394{SUMMARY}", (200, SUMMARY_BODY))
    rows = aiu.probe_gemini({})  # no OAuth call: no creds file, none scripted
    assert [(r.metric, round(r.used, 2), r.window_secs) for r in rows] == [
        ("5-Hour Limit", 0.6, 5 * 3600), ("Weekly Limit", 0.25, 7 * 86400), ("Image generation", 0.0, None)]
    assert rows[0].reset_epoch == pytest.approx(time.time() + 7200, abs=5)
    assert [c.url.split(":")[0] for c in http.calls] == ["http", "https"]
    assert all(c.headers["x-codeium-csrf-token"] == "0f1e2d3c-aaaa-bbbb" for c in http.calls)


def test_local_csrf_from_main_log(proc, http, home):
    # No language_server visible in /proc: the token comes from the app's log,
    # tried against every port listening on localhost.
    log = home / ".config" / "Antigravity" / "logs" / "main.log"
    log.parent.mkdir(parents=True)
    log.write_text("[info] spawn language_server --csrf_token 11111111-aaaa --random_port\n"
                   "[info] restart language_server --csrf_token 22222222-bbbb --random_port\n")
    proc.listen(631, 100)                       # another local service
    proc.listen(41394, 101, ip="00000000")      # the language server, on all interfaces
    proc.listen(8080, 102, ip="0A00020F")       # not loopback: ignored
    http.on("POST", f"127.0.0.1:631{SUMMARY}", (404, "not found"))
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", (200, SUMMARY_BODY))
    rows = aiu.probe_gemini({})
    assert rows[0].metric == "5-Hour Limit"
    assert {c.headers["x-codeium-csrf-token"] for c in http.calls} == {"22222222-bbbb"}
    assert not any(":8080" in c.url for c in http.calls)


def test_local_without_gemini_group_falls_back_to_oauth(proc, http):
    proc.process(4242, LS, b"--csrf_token", b"tok", sockets=(5555,))
    proc.listen(41394, 5555)
    http.on("POST", f"127.0.0.1:41394{SUMMARY}", (200, summary(group="Other Models")))
    [row] = aiu.probe_gemini({})
    assert row.kind == "error" and "open Antigravity" in row.note


def test_local_server_errors_fall_back(proc, http):
    proc.process(4242, LS, b"--csrf_token", b"tok", sockets=(5555,))
    proc.listen(41394, 5555)
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", (401, {"code": "unauthenticated"}))
    http.on("POST", f"https://127.0.0.1:41394{SUMMARY}", (200, "not json"))
    assert aiu._query_local_quota_summary() is None


def test_local_nothing_running(proc, http):
    assert aiu._query_local_quota_summary() is None and http.calls == []


@pytest.mark.xfail(strict=True, reason="probe bug: the main.log CSRF fallback is skipped when the "
                                       "server is found but its cmdline carries no token")
def test_local_csrf_fallback_for_found_server(proc, http, home):
    log = home / ".config" / "Antigravity" / "logs" / "main.log"
    log.parent.mkdir(parents=True)
    log.write_text("[info] spawn language_server --csrf_token 22222222-bbbb\n")
    proc.process(4242, LS, b"--random_port", sockets=(5555,))
    proc.listen(41394, 5555)
    http.on("POST", f"http://127.0.0.1:41394{SUMMARY}", (200, SUMMARY_BODY))
    aiu._query_local_quota_summary()
    assert http.calls[0].headers.get("x-codeium-csrf-token") == "22222222-bbbb"
