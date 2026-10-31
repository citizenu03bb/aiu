# SPDX-License-Identifier: GPL-3.0-or-later
"""`aiu cookie import mistral`: copy the admin.mistral.ai session from one
browser profile into the keyring and point mistral.key_cmd at it (security
review L3). Only the user runs it: no probe, --json, --alert or the GNOME
extension does. No key, cookie or decrypted value is printed or written
anywhere but the keyring; a locked database is read from a private copy."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.parse
from pathlib import Path

from aiu.config import ConfigError, config_set
from aiu.i18n import tr
from aiu.keys import _CONTROL_RE, load_env_file
from aiu.probes.mistral import MISTRAL_TRPC


# The hosts whose cookies a browser sends to admin.mistral.ai (RFC 6265 domain
# match): its own host-only cookies, and domain cookies for it or a parent.
# Mistral's Ory session and CSRF token are set on .mistral.ai. Not other
# subdomains (auth., console., chat.), and not a host-only mistral.ai.
MISTRAL_COOKIE_HOSTS = ("admin.mistral.ai", ".admin.mistral.ai", ".mistral.ai")
MISTRAL_KEYRING = ("service", "aiu", "provider", "mistral")
MISTRAL_KEYRING_LABEL = "aiu mistral cookie"
MISTRAL_KEY_CMD = "secret-tool lookup " + " ".join(MISTRAL_KEYRING)
MISTRAL_SIGN_IN = "https://admin.mistral.ai"
# --browser -> (name shown, libsecret `application`, user-data directories)
CHROMIUM_BROWSERS = {
    "chrome": ("Chrome", "chrome", ("~/.config/google-chrome",)),
    "chromium": ("Chromium", "chromium", ("~/.config/chromium", "~/snap/chromium/common/chromium")),
    "brave": ("Brave", "brave", ("~/.config/BraveSoftware/Brave-Browser",)),
}
FIREFOX_ROOTS = ("~/snap/firefox/common/.mozilla/firefox", "~/.mozilla/firefox")
# Chromium's Linux cookie encryption: v10 uses this fixed password, v11 the
# browser's "Safe Storage" secret from the keyring.
CHROMIUM_V10_PASSWORD = b"peanuts"
CHROMIUM_SECRET_SCHEMAS = ("chrome_libsecret_os_crypt_password_v2",
                           "chrome_libsecret_os_crypt_password_v1")
CHROMIUM_EPOCH_OFFSET = 11644473600  # seconds from 1601-01-01 to 1970-01-01


class CookieError(Exception):
    """A failed import; the message says what to do and never quotes a
    cookie, a key or a decrypted value."""


def _secret_tool() -> str:
    path = shutil.which("secret-tool")
    if not path:
        raise CookieError(tr("secret-tool not found: install it "
                             "(Debian/Ubuntu: sudo apt install libsecret-tools)"))
    return path


def _keyring_lookup(*attrs: str) -> bytes | None:
    """The secret stored under these attributes, None when there is none (or
    the keyring stayed locked: secret-tool cannot tell the two apart)."""
    try:
        out = subprocess.run([_secret_tool(), "lookup", *attrs], capture_output=True,
                             stdin=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.removesuffix(b"\n") or None


def keyring_store(value: str, attrs: tuple[str, ...], label: str) -> bool:
    """Store value under attrs, replacing the previous one; False when the
    keyring did not take it. The value goes on stdin: argv can be read by every
    user through /proc. Its stderr is not shown: we don't control what it holds."""
    try:
        out = subprocess.run([_secret_tool(), "store", f"--label={label}", *attrs],
                             input=value.encode(), capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False
    return out.returncode == 0


def _keyring_store(value: str) -> None:
    if not keyring_store(value, MISTRAL_KEYRING, MISTRAL_KEYRING_LABEL):
        raise CookieError(tr("the keyring did not store the cookie (is it locked?); "
                             "nothing was changed"))


def plain_copy_where(config: dict, var) -> str | None:
    """Where key_env's variable is still set (the environment or ~/.env), else
    None. key_env is read before key_cmd: a copy left there would still be used."""
    if not var or not isinstance(var, str):
        return None
    if os.environ.get(var):
        return tr("your environment")
    if load_env_file(config).get(var):
        return os.path.expanduser(os.environ.get("AIU_ENV_FILE", "~/.env"))
    return None


def _read_cookie_db(path: Path, read):
    """read(connection) on the SQLite database at path, opened read-only in
    place. A database the browser is writing to (a -wal or -journal file next
    to it, or a lock) is copied with those files into a private temporary
    directory (0700), read there, and removed."""
    import sqlite3
    import tempfile
    sidecars = [Path(f"{path}{s}") for s in ("-wal", "-journal")]
    if not any(p.is_file() and p.stat().st_size for p in sidecars):
        uri = f"file:{urllib.parse.quote(str(path))}?mode=ro&immutable=1"
        try:
            con = sqlite3.connect(uri, uri=True)
            try:
                return read(con)
            finally:
                con.close()
        except sqlite3.DatabaseError:
            pass  # locked or busy: read a private copy
    with tempfile.TemporaryDirectory(prefix="aiu-cookies-") as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        for src, dst in [(path, copy), *((p, Path(f"{copy}{p.name[len(path.name):]}"))
                                         for p in sidecars if p.is_file())]:
            with open(src, "rb") as s, open(dst, "xb", opener=lambda p, f: os.open(p, f, 0o600)) as d:
                shutil.copyfileobj(s, d)
        try:
            con = sqlite3.connect(copy)
            try:
                return read(con)
            finally:
                con.close()
        except sqlite3.DatabaseError:
            raise CookieError(tr("cannot read the cookie database {path}", path=path)) from None


def _columns(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _chromium_profile(browser: str, profile: str | None) -> Path:
    """The profile directory: `Default` unless --profile names a directory
    (`Profile 1`) or the name the browser shows for it."""
    name, _, dirs = CHROMIUM_BROWSERS[browser]
    roots = [Path(os.path.expanduser(d)) for d in dirs]
    roots = [r for r in roots if r.is_dir()]
    if not roots:
        raise CookieError(tr("no {browser} profile directory ({dirs}): is {browser} installed, "
                             "and has it been opened once?", browser=name, dirs=", ".join(dirs)))
    wanted = profile or "Default"
    found = []
    for root in roots:
        if (root / wanted).is_dir():
            return root / wanted
        try:
            cache = json.loads((root / "Local State").read_text())["profile"]["info_cache"]
        except (OSError, ValueError, KeyError, TypeError):
            cache = {}
        for d, info in (cache.items() if isinstance(cache, dict) else ()):
            shown = info.get("name") if isinstance(info, dict) else None
            if profile and shown == profile and (root / d).is_dir():
                return root / d
            found.append(f"{d} ({shown})" if shown and shown != d else d)
    raise CookieError(tr("no {browser} profile named {profile}; profiles: {found}",
                         browser=name, profile=wanted, found=", ".join(found) or "—"))


def _chromium_decrypt(blob: bytes, host: str, secret) -> str:
    """One encrypted_value in clear. secret() gives the keyring secret, looked
    up only when a v11 value needs it. ValueError when it does not decrypt."""
    import hashlib
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    password = CHROMIUM_V10_PASSWORD if blob[:3] == b"v10" else secret()
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)
    dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    unpad = padding.PKCS7(128).unpadder()
    plain = unpad.update(dec.update(blob[3:]) + dec.finalize()) + unpad.finalize()
    # Since Chromium 130 (cookie database version 24) the value starts with
    # the SHA-256 of the cookie's host.
    if plain[:32] == hashlib.sha256(host.encode()).digest():
        plain = plain[32:]
    return plain.decode()  # UnicodeDecodeError is a ValueError


def _chromium_cookies(browser: str, profile: str | None) -> tuple[str, list[tuple]]:
    """(profile shown, [(name, value, path, expires_epoch or None)])."""
    name, application, _ = CHROMIUM_BROWSERS[browser]
    prof = _chromium_profile(browser, profile)
    db = next((p for p in (prof / "Network" / "Cookies", prof / "Cookies") if p.is_file()), None)
    if db is None:
        raise CookieError(tr("{browser} profile {profile} has no cookies yet: sign in at {url} "
                             "in it, then run this again", browser=name, profile=prof.name,
                             url=MISTRAL_SIGN_IN))

    def read(con):
        partitioned = " AND top_frame_site_key = ''" if "top_frame_site_key" in _columns(con, "cookies") else ""
        return con.execute("SELECT host_key, name, value, encrypted_value, path, expires_utc "
                           "FROM cookies WHERE host_key IN (?, ?, ?)" + partitioned,
                           MISTRAL_COOKIE_HOSTS).fetchall()

    rows = _read_cookie_db(db, read)
    secrets: list[bytes] = []

    def secret() -> bytes:
        if not secrets:  # this browser's own entry only, never every keyring secret
            for schema in CHROMIUM_SECRET_SCHEMAS:
                if v := _keyring_lookup("xdg:schema", schema, "application", application):
                    secrets.append(v)
                    break
            else:
                raise CookieError(tr("no {browser} Safe Storage secret in the keyring: unlock the "
                                     "keyring (log in to the desktop) and start {browser} once, "
                                     "then run this again", browser=name))
        return secrets[0]

    if any(bytes(r[3] or b"")[:3] in (b"v10", b"v11") for r in rows):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            raise CookieError(tr("decrypting {browser}'s cookies needs the cryptography package: "
                                 "pipx inject aiu cryptography (or pip install 'aiu[cookies]'), "
                                 "or run ./aiu.py with uv run --with cryptography",
                                 browser=name)) from None
    cookies = []
    for host, cname, value, blob, path, expires in rows:
        blob = bytes(blob or b"")
        if blob:
            if blob[:3] not in (b"v10", b"v11"):
                version = blob[:3].decode("ascii", "replace")
                raise CookieError(tr("{browser} encrypts its cookies in a way aiu does not know "
                                     "({version})", browser=name,
                                     version=version if re.fullmatch(r"v\d\d", version) else "?"))
            try:
                value = _chromium_decrypt(blob, host, secret)
            except ValueError:
                raise CookieError(tr("{browser}'s cookies do not decrypt with its Safe Storage "
                                     "secret from this keyring (a profile copied from another "
                                     "account?)", browser=name)) from None
        cookies.append((cname, value, path,
                        expires / 1e6 - CHROMIUM_EPOCH_OFFSET if expires else None))
    return prof.name, cookies


def _firefox_profile(profile: str | None) -> Path:
    """The profile --profile names (its name or its directory), else the
    default one in profiles.ini; the snap's profiles first."""
    import configparser
    found = []
    for root in (Path(os.path.expanduser(r)) for r in FIREFOX_ROOTS):
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            ini.read(root / "profiles.ini", encoding="utf-8")
        except configparser.Error:
            continue
        profiles = {s: ini[s] for s in ini.sections() if s.startswith("Profile") and "Path" in ini[s]}

        def where(sec) -> Path:
            p = Path(sec["Path"])
            return root / p if sec.get("IsRelative", "1") == "1" else p

        if profile:
            for sec in profiles.values():
                if profile in (sec.get("Name"), Path(sec["Path"]).name) and where(sec).is_dir():
                    return where(sec)
            found += [sec.get("Name") or sec["Path"] for sec in profiles.values()]
            continue
        # Firefox 67+ keeps the default per install; older ones mark a profile.
        defaults = [ini[s]["Default"] for s in ini.sections()
                    if s.startswith("Install") and ini[s].get("Default")]
        defaults += [sec["Path"] for sec in profiles.values() if sec.get("Default") == "1"]
        for path in defaults:
            sec = next((s for s in profiles.values() if s["Path"] == path), {"Path": path})
            if where(sec).is_dir():
                return where(sec)
        if len(profiles) == 1 and where(next(iter(profiles.values()))).is_dir():
            return where(next(iter(profiles.values())))
    if profile:
        raise CookieError(tr("no {browser} profile named {profile}; profiles: {found}",
                             browser="Firefox", profile=profile, found=", ".join(found) or "—"))
    raise CookieError(tr("no default Firefox profile in {dirs}: is Firefox installed, and has "
                         "it been opened once? Else name one with --profile",
                         dirs=", ".join(FIREFOX_ROOTS)))


def _firefox_cookies(profile: str | None) -> tuple[str, list[tuple]]:
    prof = _firefox_profile(profile)
    db = prof / "cookies.sqlite"
    if not db.is_file():
        raise CookieError(tr("{browser} profile {profile} has no cookies yet: sign in at {url} "
                             "in it, then run this again", browser="Firefox", profile=prof.name,
                             url=MISTRAL_SIGN_IN))

    def read(con):
        # Container tabs and partitioned cookies carry origin attributes: the
        # plain browsing context only.
        plain = " AND originAttributes = ''" if "originAttributes" in _columns(con, "moz_cookies") else ""
        return con.execute("SELECT name, value, path, expiry FROM moz_cookies "
                           "WHERE host IN (?, ?, ?)" + plain, MISTRAL_COOKIE_HOSTS).fetchall()

    # expiry is in seconds, or milliseconds in recent versions.
    return prof.name, [(n, v, p, (e / 1000 if e > 1e11 else e) if e else None)
                       for n, v, p, e in _read_cookie_db(db, read)]


def _cookie_header(cookies: list[tuple], browser: str, profile: str) -> str:
    """The Cookie header the browser would send to the tRPC endpoint: the
    unexpired cookies whose path covers it, longest path first."""
    if not cookies:
        raise CookieError(tr("no {host} cookie in {browser} profile {profile}: sign in at {url} "
                             "in it, then run this again", host=MISTRAL_COOKIE_HOSTS[0],
                             browser=browser, profile=profile, url=MISTRAL_SIGN_IN))
    trpc_path = urllib.parse.urlsplit(MISTRAL_TRPC).path
    now = time.time()
    live = [(n, v, p) for n, v, p, exp in cookies
            if (exp is None or exp > now) and trpc_path.startswith((p or "/").rstrip("/") + "/")]
    if not live:
        raise CookieError(tr("the {host} session in {browser} profile {profile} has expired: "
                             "sign in again at {url}, then run this again",
                             host=MISTRAL_COOKIE_HOSTS[0], browser=browser, profile=profile,
                             url=MISTRAL_SIGN_IN))
    live.sort(key=lambda c: -len(c[2] or "/"))
    header = "; ".join(f"{n}={v}" for n, v, _ in live)
    if _CONTROL_RE.search(header) or not all(n and "=" not in n and ";" not in n + v
                                             for n, v, _ in live):
        raise CookieError(tr("a {host} cookie holds characters a Cookie header cannot carry; "
                             "nothing was stored", host=MISTRAL_COOKIE_HOSTS[0]))
    return header


def cookie_import(path: Path, browser: str, profile: str | None) -> int:
    # Before the cookie store is read. The consumer terms (legal.mistral.ai,
    # Oct 2026: EEA and rest of the world, §3 Restrictions (f)) cover Vibe and
    # the Free plan; Studio and the API, whose console this session opens, are
    # under the commercial terms (§2.2(g), in nearly the same words).
    shown = "Firefox" if browser == "firefox" else CHROMIUM_BROWSERS[browser][0]
    print(tr("Before anything is stored: aiu keeps a whole admin.mistral.ai session, not a "
             "read-only key. Until it expires it can create API keys and change billing. To get "
             "it, aiu reads {browser}'s cookie store. Mistral's terms forbid using \"any method to "
             "extract any content from the Mistral AI Products other than as permitted through "
             "the Mistral AI Products\" (consumer terms §3, Restrictions (f), which cover the Free "
             "plan; commercial terms §2.2(g); legal.mistral.ai/terms). Mistral offers no other "
             "way to read the allowances, and aiu sends this session only to the console's own "
             "Usage endpoints.", browser=shown))
    if browser == "firefox":
        prof, cookies = _firefox_cookies(profile)
    else:
        prof, cookies = _chromium_cookies(browser, profile)
    header = _cookie_header(cookies, shown, prof)
    _keyring_store(header)
    print(tr("Stored the {host} session from {browser} ({profile}) in the keyring, "
             "labelled '{label}'.", host=MISTRAL_COOKIE_HOSTS[0], browser=shown, profile=prof,
             label=MISTRAL_KEYRING_LABEL))
    code = 0
    try:
        config_set(path, "mistral.key_cmd", MISTRAL_KEY_CMD)
        print(tr("{option} set in {path}", option="mistral.key_cmd", path=path))
    except (ConfigError, OSError, UnicodeDecodeError) as e:
        reason = e if isinstance(e, ConfigError) else getattr(e, "strerror", None) or type(e).__name__
        print(tr("aiu cookie: mistral.key_cmd not set ({error}). Once that is fixed, run:",
                 error=reason), file=sys.stderr)
        print(f"  aiu config set mistral.key_cmd \"{MISTRAL_KEY_CMD}\"", file=sys.stderr)
        code = 1
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return code
    section = config.get("mistral") if isinstance(config.get("mistral"), dict) else {}
    var = section.get("key_env")
    if where := plain_copy_where(config, var):
        print(tr("{name} is set in {where}, and aiu reads it before key_cmd: delete it there "
                 "(it is the session in plain text) so the keyring copy is used.",
                 name=var, where=where), file=sys.stderr)
    if section.get("enabled") is not True:
        print(tr("mistral is off: aiu config set mistral.enabled true"))
    print(tr("Check it with: aiu --only mistral"))
    return code


def cookie_main(args: argparse.Namespace) -> int:
    try:
        return cookie_import(Path(os.path.expanduser(args.config)), args.browser, args.profile)
    except CookieError as e:
        print(tr("aiu cookie: {error}", error=e), file=sys.stderr)
        return 1
    except OSError as e:
        where = f"{e.filename}: " if e.filename else ""
        print(tr("aiu cookie: {error}", error=where + (e.strerror or type(e).__name__)),
              file=sys.stderr)
        return 1
