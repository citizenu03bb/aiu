# SPDX-License-Identifier: GPL-3.0-or-later
"""Key resolution: key_env, key_cmd and key_file, and the names read from ~/.env."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from aiu.i18n import tr


# Values read from ~/.env by load_env_file: only the names the config refers to,
# and never exported to os.environ, so key_cmd and other children don't see them.
ENV_FILE: dict[str, str] = {}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def resolve_key(cfg: dict, name: str) -> str | None:
    """First usable key from key_env, key_cmd, key_file. Only the first line of
    a value counts (a `pass` entry holds more lines after the password); a value
    that still holds control characters is refused without being quoted."""
    def clean(v: str | None, source: str) -> str | None:
        v = (v or "").strip()
        if not v:
            return None
        v = v.splitlines()[0].strip()
        if _CONTROL_RE.search(v):
            print(tr("[aiu] {name}: key from {source} holds control characters, ignored",
                     name=name, source=source), file=sys.stderr)
            return None
        return v

    if cfg.get("key_env"):
        env = cfg["key_env"]
        if v := clean(os.environ.get(env) or ENV_FILE.get(env), "key_env"):
            return v
    if cfg.get("key_cmd"):
        try:
            out = subprocess.run(
                cfg["key_cmd"], shell=True, capture_output=True, text=True, timeout=15
            )
            if out.returncode == 0 and (v := clean(out.stdout, "key_cmd")):
                return v
        except Exception:
            pass
    if cfg.get("key_file"):
        p = Path(os.path.expanduser(cfg["key_file"]))
        if p.is_file() and (v := clean(p.read_text(errors="replace"), "key_file")):
            return v
    return None


_ENV_LINE_RE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
_ENV_ALWAYS = ("MISTRAL_ADMIN_COOKIE", "GEMINI_CLIENT_ID", "GEMINI_CLIENT_SECRET")


def _env_value(v: str) -> str | None:
    """A ~/.env value as a shell reads it, without expansion: '...' is literal,
    "..." honours \\ escapes, an unquoted value stops at whitespace + `#`.
    None for an unmatched quote or anything after the closing quote."""
    if v[:1].isspace():
        v = v.lstrip()
        if v[:1] == "#":
            return ""
    if v[:1] == "'":
        end = v.find("'", 1)
        if end < 0:
            return None
        out, rest = v[1:end], v[end + 1:]
    elif v[:1] == '"':
        out, i = [], 1
        while i < len(v) and v[i] != '"':
            if v[i] == "\\" and i + 1 < len(v) and v[i + 1] in '"\\$`':
                i += 1
            out.append(v[i])
            i += 1
        if i == len(v):
            return None
        out, rest = "".join(out), v[i + 1:]
    else:
        out = re.split(r"\s#", v, maxsplit=1)[0].rstrip()
        return None if "'" in out or '"' in out else out
    return out if not rest.strip() or re.match(r"\s+#", rest) else None


def load_env_file(config: dict) -> dict[str, str]:
    """Read from ~/.env (override with AIU_ENV_FILE) only the names the config
    refers to: each `key_env`, the Mistral cookie and the Gemini CLI OAuth
    client. Returns them; the caller puts them in ENV_FILE, not os.environ."""
    path = Path(os.path.expanduser(os.environ.get("AIU_ENV_FILE", "~/.env")))
    if not path.is_file():
        return {}
    wanted = set(_ENV_ALWAYS)
    wanted.update(s["key_env"] for s in config.values()
                  if isinstance(s, dict) and isinstance(s.get("key_env"), str))
    found: dict[str, str] = {}
    for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        m = _ENV_LINE_RE.fullmatch(line)
        if not m or m.group(1) not in wanted:
            continue
        v = _env_value(m.group(2))
        if v is None:
            # Most often a quoted value continued on the next lines.
            print(tr("[aiu] {path}:{line}: {name} has unmatched quotes, ignored "
                     "({file} values must fit on one line)",
                     path=path, line=n, name=m.group(1), file=path.name), file=sys.stderr)
            continue
        found[m.group(1)] = v
    if found and path.stat().st_mode & 0o044:
        print(tr("[aiu] {path} holds keys and other users can read it: chmod 600 {path}",
                 path=path), file=sys.stderr)
    return found


KEY_SOURCES = {"key_env": ("string", None), "key_cmd": ("string", None),
               "key_file": ("string", None)}
