# SPDX-License-Identifier: GPL-3.0-or-later
"""`aiu cookie import mistral`: the admin.mistral.ai session goes from a
browser's cookie database to the keyring, and nowhere else. Every browser
database here is built by the test, the keyring is a fake `secret-tool` that
records its calls, and no network is used."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import aiu
from aiu import cli, config, cookies, keys

HOST = "admin.mistral.ai"
SESSION = "ory_session_x=S3cr3t-Sess10n-Value"
CSRF = "csrf_token=C5rf-T0ken-Value"
SAFE_STORAGE = b"Br0wser-Safe-Storage-Secret"
KEY_CMD = "secret-tool lookup service aiu provider mistral"
V2 = "chrome_libsecret_os_crypt_password_v2"
V1 = "chrome_libsecret_os_crypt_password_v1"
NEVER_SHOWN = ("S3cr3t-Sess10n-Value", "C5rf-T0ken-Value", "Br0wser-Safe-Storage-Secret")
FAR = time.time() + 30 * 86400
PAST = time.time() - 86400

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

    def add(self, attrs: list[str], secret: str) -> None:
        store = self.items()
        json.dump(store + [{"attrs": attrs, "label": "test", "secret": secret}],
                  open(self.path / "store.json", "w"))

    def items(self) -> list[dict]:
        p = self.path / "store.json"
        return json.load(open(p)) if p.exists() else []

    def mistral(self) -> str | None:
        return next((i["secret"] for i in self.items()
                     if i["attrs"] == ["service", "aiu", "provider", "mistral"]), None)

    def calls(self) -> list[dict]:
        p = self.path / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def lookups(self) -> list[list[str]]:
        return [c["argv"][1:] for c in self.calls() if c["argv"][0] == "lookup"]

    def stores(self) -> list[dict]:
        return [c for c in self.calls() if c["argv"][0] == "store"]


@pytest.fixture
def keyring(tmp_path, monkeypatch):
    d = tmp_path / "keyring"
    d.mkdir()
    tool = tmp_path / "bin" / "secret-tool"
    tool.write_text(FAKE_SECRET_TOOL.format(python=sys.executable))
    tool.chmod(0o755)
    monkeypatch.setenv("FAKE_KEYRING", str(d))
    return Keyring(d)


@pytest.fixture(autouse=True)
def private_tmp(tmp_path, monkeypatch):
    """tempfile's directory for this test: it must be empty again afterwards."""
    d = tmp_path / "tmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    yield d
    assert list(d.iterdir()) == []


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    path.parent.mkdir(mode=0o700)
    return path


def run(monkeypatch, capsys, cfg, *args):
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(cfg), "cookie", "import", "mistral", *args])
    capsys.readouterr()
    code = cli.main()
    out, err = capsys.readouterr()
    for secret in NEVER_SHOWN:
        assert secret not in out + err
    return code, out, err


# ---------------------------------------------------------------------------
# Fixture databases
# ---------------------------------------------------------------------------
def encrypt(plain: str, password: bytes, version: bytes, host: str, prefix: bool) -> bytes:
    data = (hashlib.sha256(host.encode()).digest() if prefix else b"") + plain.encode()
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)
    pad = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return version + enc.update(pad.update(data) + pad.finalize()) + enc.finalize()


def chrome_time(epoch: float) -> int:
    return int((epoch + 11644473600) * 1e6)


def chromium_db(path: Path, cookies: list[tuple], version: int = 24) -> Path:
    """cookies: (host_key, name, value, encrypted_value, path, expires epoch or 0)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE meta(key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY, value LONGVARCHAR);
        CREATE TABLE cookies(creation_utc INTEGER NOT NULL, host_key TEXT NOT NULL,
            top_frame_site_key TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
            encrypted_value BLOB NOT NULL, path TEXT NOT NULL, expires_utc INTEGER NOT NULL,
            is_secure INTEGER NOT NULL, is_httponly INTEGER NOT NULL,
            last_access_utc INTEGER NOT NULL, has_expires INTEGER NOT NULL DEFAULT 1);
    """)
    con.execute("INSERT INTO meta VALUES ('version', ?)", (str(version),))
    for i, (host, name, value, blob, cpath, expires, *partition) in enumerate(cookies):
        con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, ?)",
                    (chrome_time(time.time()) + i, host, partition[0] if partition else "",
                     name, value, blob, cpath, chrome_time(expires) if expires else 0,
                     int(bool(expires))))
    con.commit()
    con.close()
    return path


def mistral_rows(password: bytes, version: bytes, prefix: bool, expires=FAR) -> list[tuple]:
    rows = []
    # As Mistral sets them: the Ory session on the parent domain.
    for host, cookie in ((".mistral.ai", SESSION), (f".{HOST}", CSRF)):
        name, value = cookie.split("=", 1)
        rows.append((host, name, "", encrypt(value, password, version, host, prefix), "/", expires))
    return rows


def other_rows() -> list[tuple]:
    """Cookies of other sites: decrypting any of them fails, so a test that
    passes never read them."""
    junk = b"v11" + b"\x00" * 32
    return [("mistral.ai", "apex", "", junk, "/", FAR),  # host-only: not sent to admin.
            ("chat.mistral.ai", "chat", "", junk, "/", FAR),
            ("auth.mistral.ai", "__Host-ory_kratos_browser", "", junk, "/", FAR),
            ("console.mistral.ai", "csrftoken", "", junk, "/", FAR),
            ("admin.mistral.ai.example.com", "lookalike", "", junk, "/", FAR),
            ("x.admin.mistral.ai", "sub", "", junk, "/", FAR),
            ("google.com", "SID", "", junk, "/", FAR),
            (HOST, "partitioned", "", junk, "/", FAR, "https://example.com")]


BROWSER_DIRS = {"chrome": ".config/google-chrome", "chromium": ".config/chromium",
                "brave": ".config/BraveSoftware/Brave-Browser"}


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# Chromium family
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("browser", ["chrome", "chromium", "brave"])
@pytest.mark.parametrize("version, prefix", [(b"v10", False), (b"v10", True),
                                             (b"v11", False), (b"v11", True)])
def test_chromium_import(monkeypatch, capsys, tmp_path, home, cfg, keyring, browser, version, prefix):
    password = cookies.CHROMIUM_V10_PASSWORD if version == b"v10" else SAFE_STORAGE
    keyring.add(["xdg:schema", V2, "application", browser], SAFE_STORAGE.decode())
    chromium_db(home / BROWSER_DIRS[browser] / "Default" / "Network" / "Cookies",
                mistral_rows(password, version, prefix) + other_rows(), 24 if prefix else 23)
    before = snapshot(tmp_path)

    code, out, err = run(monkeypatch, capsys, cfg, "--browser", browser)

    assert code == 0, err
    header = f"{SESSION}; {CSRF}"
    assert keyring.mistral() == header
    # The value reached secret-tool on stdin, never in its arguments.
    [store] = keyring.stores()
    assert store["argv"] == ["store", "--label=aiu mistral cookie",
                             "service", "aiu", "provider", "mistral"]
    assert store["stdin"] == header
    for call in keyring.calls():
        assert not any(s in a for a in call["argv"] for s in NEVER_SHOWN)
    # Only this browser's Safe Storage entry is asked for, and only for v11.
    expected = [["xdg:schema", V2, "application", browser]] if version == b"v11" else []
    assert keyring.lookups() == expected
    assert config.load_config(cfg)["mistral"]["key_cmd"] == KEY_CMD
    assert f"mistral.key_cmd set in {cfg}" in out
    # Nothing written but the keyring and the config (with its lock file).
    after = snapshot(tmp_path)
    changed = {k for k in after.keys() | before.keys() if after.get(k) != before.get(k)}
    assert changed == {"keyring/store.json", "keyring/calls.jsonl", "cfg/config.toml",
                       "cfg/.config.toml.aiu-lock"}
    assert not any(s.encode() in after["cfg/config.toml"] for s in NEVER_SHOWN)


def test_imported_session_is_what_the_probe_resolves(monkeypatch, capsys, home, cfg, keyring):
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies",
                mistral_rows(cookies.CHROMIUM_V10_PASSWORD, b"v10", True))
    assert run(monkeypatch, capsys, cfg, "--browser", "chrome")[0] == 0
    mistral = config.load_config(cfg)["mistral"]
    assert keys.resolve_key({"key_env": "MISTRAL_ADMIN_COOKIE", **mistral}, "mistral") \
        == f"{SESSION}; {CSRF}"


def test_falls_back_to_the_v1_schema(monkeypatch, capsys, home, cfg, keyring):
    keyring.add(["xdg:schema", V1, "application", "chromium"], SAFE_STORAGE.decode())
    keyring.add(["xdg:schema", V2, "application", "chrome"], "another browser's secret")
    chromium_db(home / BROWSER_DIRS["chromium"] / "Default" / "Network" / "Cookies",
                mistral_rows(SAFE_STORAGE, b"v11", True))
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chromium")
    assert code == 0, err
    assert keyring.lookups() == [["xdg:schema", V2, "application", "chromium"],
                                 ["xdg:schema", V1, "application", "chromium"]]


def test_no_safe_storage_entry(monkeypatch, capsys, tmp_path, home, cfg, keyring):
    keyring.add(["xdg:schema", V2, "application", "chrome"], SAFE_STORAGE.decode())
    chromium_db(home / BROWSER_DIRS["brave"] / "Default" / "Network" / "Cookies",
                mistral_rows(SAFE_STORAGE, b"v11", True))
    code, out, err = run(monkeypatch, capsys, cfg, "--browser", "brave")
    assert code == 1
    assert "no Brave Safe Storage secret in the keyring: unlock the keyring" in err
    assert keyring.stores() == [] and not cfg.exists()
    # Chrome's secret was never asked for.
    assert all(l[-1] == "brave" for l in keyring.lookups())


def test_wrong_secret_never_shows_it(monkeypatch, capsys, home, cfg, keyring):
    keyring.add(["xdg:schema", V2, "application", "chrome"], SAFE_STORAGE.decode())
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies",
                mistral_rows(b"a key from another keyring", b"v11", True))
    code, out, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1
    assert "Chrome's cookies do not decrypt with its Safe Storage secret" in err
    assert keyring.stores() == []


def test_unknown_encryption_version(monkeypatch, capsys, home, cfg, keyring):
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies",
                [(HOST, "s", "", b"v20" + b"\x01" * 32, "/", FAR)])
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1 and "in a way aiu does not know (v20)" in err


def test_not_signed_in(monkeypatch, capsys, home, cfg, keyring):
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies", other_rows())
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1
    assert "no admin.mistral.ai cookie in Chrome profile Default: sign in at" in err
    assert keyring.calls() == []


def test_expired_session(monkeypatch, capsys, home, cfg, keyring):
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies",
                mistral_rows(cookies.CHROMIUM_V10_PASSWORD, b"v10", True, expires=PAST))
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1 and "session in Chrome profile Default has expired" in err
    assert keyring.stores() == []


def test_session_cookies_and_paths(monkeypatch, capsys, home, cfg, keyring):
    """A cookie without expiry is kept; one whose path does not cover the tRPC
    endpoint is not; the longer path comes first, as a browser sends them."""
    pw = cookies.CHROMIUM_V10_PASSWORD
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies", [
        (HOST, "root", "", encrypt("1", pw, b"v10", HOST, True), "/", 0),
        (HOST, "api", "", encrypt("2", pw, b"v10", HOST, True), "/api", FAR),
        (HOST, "elsewhere", "", encrypt("3", pw, b"v10", HOST, True), "/billing", FAR),
        (HOST, "gone", "", encrypt("4", pw, b"v10", HOST, True), "/", PAST),
    ])
    assert run(monkeypatch, capsys, cfg, "--browser", "chrome")[0] == 0
    assert keyring.mistral() == "api=2; root=1"


def test_legacy_cookie_location_and_named_profile(monkeypatch, capsys, home, cfg, keyring):
    root = home / BROWSER_DIRS["chrome"]
    chromium_db(root / "Profile 2" / "Cookies", mistral_rows(cookies.CHROMIUM_V10_PASSWORD, b"v10", False))
    (root / "Default").mkdir()
    (root / "Local State").write_text(json.dumps(
        {"profile": {"info_cache": {"Default": {"name": "Personal"}, "Profile 2": {"name": "Work"}}}}))
    code, out, _ = run(monkeypatch, capsys, cfg, "--browser", "chrome", "--profile", "Work")
    assert code == 0 and "from Chrome (Profile 2)" in out
    assert run(monkeypatch, capsys, cfg, "--browser", "chrome", "--profile", "Profile 2")[0] == 0
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome", "--profile", "Nope")
    assert code == 1
    assert "no Chrome profile named Nope; profiles: Default (Personal), Profile 2 (Work)" in err
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1 and "Chrome profile Default has no cookies yet" in err


def test_browser_not_installed(monkeypatch, capsys, cfg, keyring):
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "brave")
    assert code == 1 and "no Brave profile directory (~/.config/BraveSoftware/Brave-Browser)" in err


def test_without_cryptography(monkeypatch, capsys, home, cfg, keyring):
    monkeypatch.setitem(sys.modules, "cryptography", None)  # import fails
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies",
                mistral_rows(cookies.CHROMIUM_V10_PASSWORD, b"v10", True))
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1 and "needs the cryptography package: pipx inject aiu cryptography" in err


def test_without_secret_tool(monkeypatch, capsys, home, cfg):
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies",
                mistral_rows(cookies.CHROMIUM_V10_PASSWORD, b"v10", True))
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1 and "secret-tool not found" in err
    assert not cfg.exists()


def test_locked_keyring_changes_nothing(monkeypatch, capsys, home, cfg, keyring):
    monkeypatch.setenv("FAKE_KEYRING_LOCKED", "1")
    chromium_db(home / BROWSER_DIRS["chrome"] / "Default" / "Network" / "Cookies",
                mistral_rows(cookies.CHROMIUM_V10_PASSWORD, b"v10", True))
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "chrome")
    assert code == 1 and "the keyring did not store the cookie (is it locked?)" in err
    assert keyring.mistral() is None and not cfg.exists()


# ---------------------------------------------------------------------------
# Firefox
# ---------------------------------------------------------------------------
def firefox_profile(root: Path, name: str, dirname: str, install_default: bool = True) -> Path:
    prof = root / dirname
    prof.mkdir(parents=True)
    ini = root / "profiles.ini"
    text = ini.read_text() if ini.exists() else "[General]\nStartWithLastProfile=1\nVersion=2\n"
    n = text.count("[Profile")
    text += f"\n[Profile{n}]\nName={name}\nIsRelative=1\nPath={dirname}\n"
    if install_default:
        text += f"\n[Install4F96D1932A9F858E]\nDefault={dirname}\nLocked=1\n"
    ini.write_text(text)
    return prof


def firefox_db(path: Path, cookies: list[tuple], wal: bool = False):
    """cookies: (host, name, value, path, expiry, originAttributes). With wal,
    the rows stay in the -wal file and the connection is returned open."""
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL "
                "DEFAULT '', name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, "
                "lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER)")
    con.commit()
    if wal:  # the table reaches the main file; the cookies stay in the WAL
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for host, name, value, cpath, expiry, attrs in cookies:
        con.execute("INSERT INTO moz_cookies (originAttributes, name, value, host, path, expiry) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (attrs, name, value, host, cpath, expiry))
    con.commit()
    if wal:
        return con
    con.close()


def firefox_rows(expiry=int(FAR)) -> list[tuple]:
    rows = [(host, *cookie.split("=", 1), "/", expiry, "")
            for host, cookie in ((".mistral.ai", SESSION), (f".{HOST}", CSRF))]
    return rows + [("mistral.ai", "apex", "a", "/", expiry, ""),
                   ("chat.mistral.ai", "chat", "c", "/", expiry, ""),
                   (HOST, "container", "k", "/", expiry, "^userContextId=2")]


@pytest.mark.parametrize("snap", [True, False])
def test_firefox_import(monkeypatch, capsys, tmp_path, home, cfg, keyring, snap):
    root = home / ("snap/firefox/common/.mozilla/firefox" if snap else ".mozilla/firefox")
    firefox_profile(root, "old", "aaa.old", install_default=False)
    prof = firefox_profile(root, "default-release", "bbb.default-release")
    firefox_db(prof / "cookies.sqlite", firefox_rows())
    before = snapshot(tmp_path)

    code, out, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")

    assert code == 0, err
    assert "from Firefox (bbb.default-release)" in out
    assert keyring.mistral() == f"{SESSION}; {CSRF}"
    assert keyring.lookups() == []
    [store] = keyring.stores()
    assert store["stdin"] == f"{SESSION}; {CSRF}"
    assert not any(s in a for a in store["argv"] for s in NEVER_SHOWN)
    assert config.load_config(cfg)["mistral"]["key_cmd"] == KEY_CMD
    after = snapshot(tmp_path)
    assert {k for k in after if after[k] != before.get(k)} == {
        "keyring/store.json", "keyring/calls.jsonl", "cfg/config.toml", "cfg/.config.toml.aiu-lock"}


def test_firefox_needs_no_cryptography(monkeypatch, capsys, home, cfg, keyring):
    monkeypatch.setitem(sys.modules, "cryptography", None)
    prof = firefox_profile(home / ".mozilla/firefox", "default", "x.default")
    firefox_db(prof / "cookies.sqlite", firefox_rows(expiry=int(FAR * 1000)))  # milliseconds
    assert run(monkeypatch, capsys, cfg, "--browser", "firefox")[0] == 0
    assert keyring.mistral() == f"{SESSION}; {CSRF}"


def test_firefox_named_profile_and_errors(monkeypatch, capsys, home, cfg, keyring):
    root = home / ".mozilla/firefox"
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    assert code == 1 and "no default Firefox profile in" in err
    firefox_profile(root, "default-release", "bbb.default-release")
    work = firefox_profile(root, "work", "ccc.work", install_default=False)
    firefox_db(work / "cookies.sqlite", firefox_rows(expiry=int(PAST)))
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    assert code == 1 and "Firefox profile bbb.default-release has no cookies yet" in err
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox", "--profile", "nope")
    assert code == 1 and "no Firefox profile named nope; profiles: default-release, work" in err
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox", "--profile", "work")
    assert code == 1 and "session in Firefox profile ccc.work has expired" in err
    assert keyring.calls() == []


def test_busy_database_is_read_from_a_private_copy(monkeypatch, capsys, tmp_path, home, cfg,
                                                   keyring, private_tmp):
    """The cookies only exist in the -wal file, which the in-place immutable
    read ignores: finding them proves the copy was read, and the private_tmp
    fixture checks the copy is gone."""
    prof = firefox_profile(home / ".mozilla/firefox", "default", "x.default")
    con = firefox_db(prof / "cookies.sqlite", firefox_rows(), wal=True)
    made = []
    real = tempfile.TemporaryDirectory

    def spy(*a, **kw):
        t = real(*a, **kw)
        made.append(Path(t.name))
        assert Path(t.name).stat().st_mode & 0o777 == 0o700
        assert Path(t.name).parent == private_tmp
        return t

    monkeypatch.setattr(tempfile, "TemporaryDirectory", spy)
    try:
        assert Path(f"{prof / 'cookies.sqlite'}-wal").stat().st_size
        code, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    finally:
        con.close()
    assert code == 0, err
    assert keyring.mistral() == f"{SESSION}; {CSRF}"
    assert len(made) == 1 and not made[0].exists()


def test_locked_database_is_read_from_a_private_copy(monkeypatch, capsys, home, cfg, keyring,
                                                     private_tmp):
    prof = firefox_profile(home / ".mozilla/firefox", "default", "x.default")
    firefox_db(prof / "cookies.sqlite", firefox_rows())
    real, opened = sqlite3.connect, []

    def connect(target, *a, **kw):
        opened.append(str(target))
        if kw.get("uri"):
            raise sqlite3.OperationalError("database is locked")
        assert Path(target).parent.parent == private_tmp
        return real(target, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert run(monkeypatch, capsys, cfg, "--browser", "firefox")[0] == 0
    assert keyring.mistral() == f"{SESSION}; {CSRF}"
    assert opened[0].startswith("file:") and opened[0].endswith("?mode=ro&immutable=1")
    assert len(opened) == 2


def test_quiet_database_is_read_in_place(monkeypatch, capsys, home, cfg, keyring):
    prof = firefox_profile(home / ".mozilla/firefox", "default", "x.default")
    firefox_db(prof / "cookies.sqlite", firefox_rows())
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda *a, **kw: pytest.fail("copied"))
    assert run(monkeypatch, capsys, cfg, "--browser", "firefox")[0] == 0


# ---------------------------------------------------------------------------
# Config, precedence, re-runs
# ---------------------------------------------------------------------------
@pytest.fixture
def firefox(home):
    prof = firefox_profile(home / ".mozilla/firefox", "default", "x.default")
    firefox_db(prof / "cookies.sqlite", firefox_rows())
    return prof


def test_rerun_replaces_the_session(monkeypatch, capsys, cfg, keyring, firefox):
    assert run(monkeypatch, capsys, cfg, "--browser", "firefox")[0] == 0
    con = sqlite3.connect(firefox / "cookies.sqlite")
    con.execute("UPDATE moz_cookies SET value = 'Fresh' WHERE name = 'ory_session_x'")
    con.commit()
    con.close()
    assert run(monkeypatch, capsys, cfg, "--browser", "firefox")[0] == 0
    assert [i["secret"] for i in keyring.items()] == [f"ory_session_x=Fresh; {CSRF}"]
    assert cfg.read_text().count(f'key_cmd = "{KEY_CMD}"') == 1


def test_untrusted_config_keeps_the_keyring_and_prints_the_command(monkeypatch, capsys, cfg,
                                                                   keyring, firefox):
    config.ensure_config(cfg)
    cfg.parent.chmod(0o777)
    code, out, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    cfg.parent.chmod(0o700)
    assert code == 1
    assert keyring.mistral() == f"{SESSION}; {CSRF}"
    assert "mistral.key_cmd not set (mistral.key_cmd refused:" in err
    assert f'aiu config set mistral.key_cmd "{KEY_CMD}"' in err
    assert "key_cmd" not in config.load_config(cfg)["mistral"]


def test_warns_when_the_environment_still_wins(monkeypatch, capsys, home, cfg, keyring, firefox):
    code, out, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    assert code == 0 and "aiu reads it before key_cmd" not in err
    assert "mistral is off: aiu config set mistral.enabled true" in out
    assert "Check it with: aiu --only mistral" in out

    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "old")
    _, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    assert "MISTRAL_ADMIN_COOKIE is set in your environment, and aiu reads it before key_cmd" in err

    monkeypatch.delenv("MISTRAL_ADMIN_COOKIE")
    env = home / ".env"
    env.write_text("MISTRAL_ADMIN_COOKIE='old=1; other=2'\n")
    env.chmod(0o600)
    _, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    assert f"MISTRAL_ADMIN_COOKIE is set in {env}, and aiu reads it before key_cmd" in err

    # Once key_env is off, the copy in ~/.env is no longer read.
    assert config.config_set(cfg, "mistral.key_env", "") is None
    config.config_set(cfg, "mistral.enabled", "true")
    code, out, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    assert code == 0 and "before key_cmd" not in err and "mistral is off" not in out


def test_messages_in_french(monkeypatch, capsys, cfg, keyring):
    monkeypatch.setenv("AIU_LANG", "fr")
    code, _, err = run(monkeypatch, capsys, cfg, "--browser", "firefox")
    assert code == 1 and "aucun profil Firefox par défaut" in err


def test_only_the_user_runs_it():
    """Neither the snapshot path nor the GNOME extension ever imports a cookie."""
    root = Path(aiu.__file__).resolve().parents[1]
    for js in (root / "gnome-extension").glob("*.js"):
        assert "cookie" not in js.read_text().lower(), js.name
    src = "".join(p.read_text() for p in (root / "aiu").rglob("*.py"))
    assert src.count("cookie_main(") == 2  # its definition and the `cookie` dispatch in main
    assert src.count("cookie_import(") == 2
