# SPDX-License-Identifier: GPL-3.0-or-later
"""The config file: the starter config, loading it with the trust check, and
`aiu config show/set`."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tomllib
from pathlib import Path

from aiu.i18n import tr, ui_lang
from aiu.keys import _CONTROL_RE
from aiu.probes import REGISTRY
from aiu.render import JSON_SCHEMA
from aiu.util import _writable_by_others


CONFIG_DIR = Path(os.path.expanduser("~/.config/ai-usage"))
CONFIG_PATH = CONFIG_DIR / "config.toml"
PRICES_PATH = CONFIG_DIR / "prices.toml"

CONFIG_TEMPLATE = """\
# aiu config — enable the providers you use, tell aiu how to find each key.
# Key resolution per provider, in priority order:
#   key_env  = "NAME"          -> read environment variable NAME, else NAME
#                                 from ~/.env
#   key_cmd  = "shell command" -> run it, use the first line of stdout (great
#                                 for pulling a key out of an existing CLI
#                                 config); ignored if others can write this file
#   key_file = "/path"         -> read the file's first line
# A key that still holds control characters is refused.

# Claude Max needs no key: aiu reads the usage figures Claude Code reports,
# written by aiu's plugin for Claude Code. Install it at a claude prompt:
#   /plugin install aiu-claude-usage --marketplace citizenu03bb/aiu
# (desktop app: + next to the prompt box, Plugins, Add marketplace).
# With read_token on, aiu also reads Claude Code's sign-in token from
# ~/.claude/.credentials.json and sends it to Anthropic's usage endpoint, as
# the Claude apps do: that adds credits (Cloud Credit) and windows the plugin
# does not see. Off unless you turn it on. Anthropic's terms say "developers
# may not collect, store, or intermediate Claude.ai credentials or session
# tokens" (code.claude.com/docs/en/legal-and-compliance).
[claude]
enabled = true
# read_token = true

# Codex CLI + Pi sign in via ChatGPT OAuth (no API key) -> subscription quota,
# like claude. aiu asks the Codex CLI (`codex app-server`), which must be
# installed and signed in; aiu reads no Codex token. codex_cmd: where `codex`
# is, if not in ~/.local/bin, ~/.npm-global/bin, /usr/local/bin, /usr/bin or
# Homebrew's bin (PATH is not searched).
[codex]
enabled = true
# codex_cmd = "~/.nvm/versions/node/v22.11.0/bin/codex"

[openai]
# Only enable if you actually use the OpenAI *API* (not just Codex/ChatGPT).
# Costs API requires an ADMIN key (starts with sk-admin-...), not a normal sk- key.
enabled = false
key_env = "OPENAI_ADMIN_KEY"
# key_cmd = "pass show openai/admin"

[deepseek]
enabled = false
key_env = "DEEPSEEK_API_KEY"
# key_cmd = "jq -r .api_key ~/.config/deepseek/config.json"

# `aiu login openrouter` signs in in the browser, keeps the key OpenRouter
# issues in the keyring and sets key_cmd (comment key_env out, or leave the
# variable unset: key_env is read first).
[openrouter]
enabled = false
key_env = "OPENROUTER_API_KEY"

# Mistral's monthly allowances (Vibe Code + API, €8.5 each on Free) are only
# readable with the admin.mistral.ai browser session (API keys are rejected).
# That session can also create keys and change billing, and Mistral's terms
# forbid extracting content "other than as permitted": the import says more.
# Sign in there, then run `aiu cookie import mistral --browser firefox` (or
# chrome, chromium, brave): it keeps the session in the keyring and sets key_cmd.
# Do it again when the session expires.
# The plan (Free, Pro) is asked of Mistral Vibe, when it is installed and
# signed in: vibe_cmd says where vibe-app-server is, if not in ~/.local/bin,
# /usr/local/bin, /usr/bin or Homebrew's bin.
[mistral]
enabled = false
# vibe_allowance_eur = 8.5   # 0 hides that row
# api_allowance_eur = 8.5
# vibe_cmd = "~/.local/bin/vibe-app-server"

# Note: Cohere exposes NO aggregate usage/spend endpoint (dashboard only), so it
# can't be polled as a simple number without local token accounting.

# Gemini (Antigravity / Google One AI Pro) -> subscription quota, asked of the
# Antigravity CLI: aiu runs `agy -p /usage --output-format json` (agy's own
# /usage command, no model turn) at most every 15 minutes. Install agy and sign
# in with it; no key needed, and aiu never reads agy's sign-in. agy_cmd says
# which agy to run (else ~/.local/bin/agy and the usual places, never PATH).
[gemini]
enabled = false
# agy_cmd = "~/.local/bin/agy"
"""

# Same settings as CONFIG_TEMPLATE, French comments (tests check they parse alike).
CONFIG_TEMPLATE_FR = """\
# Config aiu — active les fournisseurs que tu utilises, dis à aiu où trouver
# chaque clé. Résolution de la clé par fournisseur, par ordre de priorité :
#   key_env  = "NOM"           -> lit la variable d'environnement NOM, sinon NOM
#                                 dans ~/.env
#   key_cmd  = "commande"      -> la lance et prend la première ligne de sa
#                                 sortie (pratique pour extraire une clé de la
#                                 config d'une CLI) ; ignorée si d'autres
#                                 peuvent écrire ce fichier
#   key_file = "/chemin"       -> lit la première ligne du fichier
# Une clé qui contient encore des caractères de contrôle est refusée.

# Claude Max n'a pas besoin de clé : aiu lit les chiffres d'usage que Claude
# Code annonce, écrits par le plugin d'aiu pour Claude Code. Installe-le à une
# invite claude :
#   /plugin install aiu-claude-usage --marketplace citizenu03bb/aiu
# (application de bureau : + à côté de la zone de saisie, Plugins, Add marketplace).
# Avec read_token activé, aiu lit aussi le jeton de connexion de Claude Code
# dans ~/.claude/.credentials.json et l'envoie au point d'accès d'usage
# d'Anthropic, comme les applications Claude : cela ajoute les crédits (Cloud
# Credit) et les fenêtres que le plugin ne voit pas. Désactivé tant que tu ne
# l'actives pas. Les conditions d'Anthropic disent : « developers may not
# collect, store, or intermediate Claude.ai credentials or session tokens »
# (code.claude.com/docs/en/legal-and-compliance).
[claude]
enabled = true
# read_token = true

# Codex CLI + Pi se connectent par OAuth ChatGPT (pas de clé API) -> quota
# d'abonnement, comme claude. aiu le demande à la Codex CLI (`codex
# app-server`), qui doit être installée et connectée ; aiu ne lit aucun jeton
# Codex. codex_cmd : où est `codex`, s'il n'est pas dans ~/.local/bin,
# ~/.npm-global/bin, /usr/local/bin, /usr/bin ou le bin de Homebrew (le PATH
# n'est pas parcouru).
[codex]
enabled = true
# codex_cmd = "~/.nvm/versions/node/v22.11.0/bin/codex"

[openai]
# N'active que si tu utilises vraiment l'*API* OpenAI (pas seulement Codex/ChatGPT).
# L'API Costs exige une clé ADMIN (commence par sk-admin-...), pas une clé sk- normale.
enabled = false
key_env = "OPENAI_ADMIN_KEY"
# key_cmd = "pass show openai/admin"

[deepseek]
enabled = false
key_env = "DEEPSEEK_API_KEY"
# key_cmd = "jq -r .api_key ~/.config/deepseek/config.json"

# `aiu login openrouter` se connecte dans le navigateur, garde la clé que
# donne OpenRouter dans le trousseau et règle key_cmd (commente key_env, ou
# laisse la variable vide : key_env est lu en premier).
[openrouter]
enabled = false
key_env = "OPENROUTER_API_KEY"

# Les forfaits mensuels de Mistral (Vibe Code + API, 8,5 € chacun en Free) ne se
# lisent qu'avec la session navigateur d'admin.mistral.ai (les clés API sont
# refusées). Cette session peut aussi créer des clés et modifier la facturation,
# et les conditions de Mistral interdisent d'extraire du contenu « other than as
# permitted » : l'import en dit plus. Connecte-toi, puis lance
# `aiu cookie import mistral --browser firefox` (ou chrome, chromium, brave) :
# la session va dans le trousseau et key_cmd est réglé. Recommence quand elle expire.
# L'offre (Free, Pro) est demandée à Mistral Vibe, s'il est installé et
# connecté : vibe_cmd dit où est vibe-app-server, s'il n'est pas dans
# ~/.local/bin, /usr/local/bin, /usr/bin ou le bin de Homebrew.
[mistral]
enabled = false
# vibe_allowance_eur = 8.5   # 0 masque cette ligne
# api_allowance_eur = 8.5
# vibe_cmd = "~/.local/bin/vibe-app-server"

# Note : Cohere n'expose AUCUN point d'accès d'usage ou de dépense agrégé (tableau
# de bord seulement), donc impossible de le relever sans compter les jetons soi-même.

# Gemini (Antigravity / Google One AI Pro) -> quota d'abonnement, demandé à la
# CLI Antigravity : aiu lance `agy -p /usage --output-format json` (la commande
# /usage d'agy, sans tour de modèle) au plus toutes les 15 minutes. Installe agy
# et connecte-toi avec ; pas de clé, et aiu ne lit jamais la connexion d'agy.
# agy_cmd dit quel agy lancer (sinon ~/.local/bin/agy et les endroits
# habituels, jamais le PATH).
[gemini]
enabled = false
# agy_cmd = "~/.local/bin/agy"
"""


def ensure_config(path: Path) -> None:
    """Write the starter config (0600, in a 0700 directory) unless there is one."""
    if path.is_file():
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as f:
        f.write(CONFIG_TEMPLATE_FR if ui_lang() == "fr" else CONFIG_TEMPLATE)
    print(tr("[aiu] wrote a starter config to {path} — edit it, then re-run.", path=path),
          file=sys.stderr)


def config_trust_problems(path: Path) -> list[str]:
    """Why the config may not run commands: the file, its directory or the
    directory of its real path belongs to someone else or can be written by
    others (group write is fine for the user's private group). Empty when trusted."""
    real = Path(os.path.realpath(path))
    unsafe = []
    for p in dict.fromkeys((path, path.parent, real.parent)):
        st = p.stat()
        if st.st_uid != os.getuid():
            unsafe.append(tr("{path} is not yours", path=p))
        elif _writable_by_others(st):
            unsafe.append(tr("{path} is writable by group or others", path=p))
    return unsafe


def runs_code(option: str) -> bool:
    """Whether an option makes aiu run a program: every `*_cmd` (key_cmd, and
    each vendor client's binary). The extension's aiuConfig.js follows the same rule."""
    return option.endswith("_cmd")


def load_config(path: Path) -> dict:
    """Read the config, writing the template on first run. Like OpenSSH, ignore
    every option that runs a program when config_trust_problems finds any."""
    ensure_config(path)
    with open(path, "rb") as f:
        config = tomllib.load(f)
    unsafe = config_trust_problems(path)
    cmds = [(name, opt) for name, s in config.items() if isinstance(s, dict)
            for opt in s if runs_code(opt)]
    if unsafe and cmds:
        print(tr("[aiu] ignored, since the config is not safe: {options} ({reasons})",
                 options=", ".join(f"{n}.{o}" for n, o in cmds), reasons="; ".join(unsafe)),
              file=sys.stderr)
        for name, opt in cmds:
            del config[name][opt]
    return config


# provider -> option -> (type, default when the file does not set it)
CONFIG_OPTIONS = {p.NAME: p.OPTIONS for p in REGISTRY}
# What `aiu config set` prints when an option is turned on: provider.option -> text.
CONFIG_NOTICES = {f"{p.NAME}.{name}": text for p in REGISTRY
                  for name, text in getattr(p, "NOTICES", {}).items()}
_VAR_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOML_HEADER_RE = re.compile(r"\s*\[(\[?)\s*(.+?)\s*\]\]?\s*(?:#.*)?")
_TOML_COMMENT_RE = re.compile(r"\s*(?:#.*)?")


class ConfigError(Exception):
    """A refused `aiu config` call; the message is for the user and never
    quotes the value they gave."""


def config_value(kind: str, option: str, raw: str):
    """raw as an option of this type."""
    if kind == "bool":
        if raw.strip().lower() in ("true", "false"):
            return raw.strip().lower() == "true"
        raise ConfigError(tr("{option} takes true or false", option=option))
    if kind == "number":
        try:
            v = float(raw)
        except ValueError:
            v = math.nan
        if not math.isfinite(v) or v < 0:
            raise ConfigError(tr("{option} takes a number, 0 or more", option=option))
        return int(v) if v.is_integer() and abs(v) < 2 ** 53 else v
    if _CONTROL_RE.search(raw):
        raise ConfigError(tr("{option} cannot hold control characters", option=option))
    if option == "key_env" and raw and not _VAR_NAME_RE.fullmatch(raw):
        raise ConfigError(tr("key_env takes a variable name (letters, digits and _) "
                             "or an empty string"))
    return raw


def _toml_literal(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_scalar_end(s: str) -> int | None:
    """Where the one-line value at the start of s ends; None if it goes on."""
    if s.startswith(('"""', "'''")):
        end = s.find(s[:3], 3)
        return None if end < 0 else end + 3
    if s[:1] == '"':
        i = 1
        while i < len(s):
            if s[i] == '"':
                return i + 1
            i += 2 if s[i] == "\\" else 1
        return None
    if s[:1] == "'":
        end = s.find("'", 1)
        return None if end < 0 else end + 1
    m = re.match(r"[^\s#\[\]{}]+", s)
    return m.end() if m else None


def _after_value(s: str) -> str | None:
    """What follows the value at the start of s (spaces, a comment), or None if
    the value spans lines or something else follows it."""
    end = _toml_scalar_end(s)
    return s[end:] if end is not None and _TOML_COMMENT_RE.fullmatch(s[end:]) else None


def config_edit(text: str, section: str, option: str, value) -> str:
    """text with [section] option = value, changing as few bytes as possible:
    the value of an existing line, else a commented-out `# option = ...` in the
    section, uncommented, else a new line after the section's last setting, else
    a new section at the end. The caller checks the result with tomllib."""
    literal = _toml_literal(value)
    nl = "\r\n" if "\r\n" in text else "\n"
    if text and not text.endswith("\n"):
        text += nl
    lines = text.split("\n")  # each ends with "\r" in a CRLF file; the last is ""
    start, end = None, len(lines)
    for i, line in enumerate(lines):
        m = _TOML_HEADER_RE.fullmatch(line.rstrip("\r"))
        if not m:
            continue
        if start is not None:
            end = i
            break
        if not m.group(1) and m.group(2) in (section, f'"{section}"'):
            start = i
    if start is None:
        sep = nl if text.strip() and not text.endswith(nl * 2) else ""
        return f"{text}{sep}[{section}]{nl}{option} = {literal}{nl}"

    key = re.escape(option)
    body = range(start + 1, end)
    set_re = re.compile(rf"""(\s*(?:{key}|"{key}"|'{key}')\s*=\s*)(.*)""")
    commented_re = re.compile(rf"(\s*)#\s*({key}\s*=\s*)(.*)")
    for i in body:
        line = lines[i].rstrip("\r")
        if m := set_re.fullmatch(line):
            rest = _after_value(m.group(2))
            if rest is None:
                raise ConfigError(tr("cannot change {option} in place: edit it by hand",
                                     option=f"{section}.{option}"))
            lines[i] = m.group(1) + literal + rest + lines[i][len(line):]
            return "\n".join(lines)
    for i in body:
        line = lines[i].rstrip("\r")
        if m := commented_re.fullmatch(line):
            lines[i] = m.group(1) + m.group(2) + literal + (_after_value(m.group(3)) or "") \
                + lines[i][len(line):]
            return "\n".join(lines)
    last = max((i for i in body if lines[i].strip() and not lines[i].lstrip().startswith("#")),
               default=start)
    indent = re.match(r"\s*", lines[last]).group() if last > start else ""
    lines.insert(last + 1, f"{indent}{option} = {literal}" + ("\r" if nl == "\r\n" else ""))
    return "\n".join(lines)


def _write_private(target: Path, data: bytes) -> None:
    """Replace target atomically with a 0600 file (fsync'd temp file next to it)."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:  # mkstemp: O_EXCL, mode 0600
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def config_set(path: Path, option: str, raw: str) -> None:
    """Set one `provider.option` in the config, creating the starter config
    first if there is none. Refuses a file that is not the user's, a command
    in a config that fails the trust check, and any edit that does not parse
    back to the old config plus this one value."""
    import fcntl
    provider, _, name = option.partition(".")
    if provider not in CONFIG_OPTIONS:
        raise ConfigError(tr("unknown provider; one of: {providers}",
                             providers=", ".join(CONFIG_OPTIONS)))
    if name not in CONFIG_OPTIONS[provider]:
        raise ConfigError(tr("unknown option for {provider}; it takes: {options}", provider=provider,
                             options=", ".join(CONFIG_OPTIONS[provider])))
    value = config_value(CONFIG_OPTIONS[provider][name][0], name, raw)
    ensure_config(path)
    target = Path(os.path.realpath(path))  # a symlinked config stays a symlink
    if target.stat().st_uid != os.getuid():
        raise ConfigError(tr("{path} is not yours", path=path))
    # A program to run is only set in a config that passes the trust check
    # (an empty value, which turns it off, is always allowed).
    if value and runs_code(name) and (problems := config_trust_problems(path)):
        raise ConfigError(tr("{option} refused: {reasons}", option=option,
                             reasons="; ".join(problems)))
    # Under a lock shared by every aiu config run: two quick toggles from the
    # preferences window must not lose one another's change.
    lock = os.open(target.with_name(f".{target.name}.aiu-lock"), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        text = target.read_bytes().decode()
        try:
            old = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(tr("{path} does not parse: {error}", path=path, error=e)) from None
        if not isinstance(old.get(provider, {}), dict):
            raise ConfigError(tr("{name} in {path} is not a table", name=provider, path=path))
        new_text = config_edit(text, provider, name, value)
        try:
            new = tomllib.loads(new_text)
        except tomllib.TOMLDecodeError:
            new = None
        if new != {**old, provider: {**old.get(provider, {}), name: value}}:
            raise ConfigError(tr("cannot change {option} in place: edit it by hand", option=option))
        if new_text != text:
            _write_private(target, new_text.encode())
    finally:
        os.close(lock)  # releases the flock


def _config_value_of(path: Path, option: str):
    """provider.option as the file sets it now; None when unset or unreadable."""
    provider, _, name = option.partition(".")
    try:
        with open(path, "rb") as f:
            section = tomllib.load(f).get(provider)
    except (OSError, ValueError):
        return None
    return section.get(name) if isinstance(section, dict) else None


def config_snapshot(path: Path) -> dict:
    """The config as `aiu config show --json` prints it: each provider's options
    with their value and whether the file sets it, and whether the options that
    run a program (`*_cmd`) may: `key_cmd_allowed` keeps its schema 1 name."""
    ensure_config(path)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(tr("{path} does not parse: {error}", path=path, error=e)) from None
    problems = config_trust_problems(path)
    providers = {}
    for provider, options in CONFIG_OPTIONS.items():
        section = data.get(provider)
        section = section if isinstance(section, dict) else {}
        providers[provider] = {
            "section": provider in data,
            "options": {name: {"type": kind, "value": section.get(name, default),
                               "source": "file" if name in section else "default"}
                        for name, (kind, default) in options.items()},
        }
    return {"path": str(path), "key_cmd_allowed": not problems, "key_cmd_problems": problems,
            "providers": providers}


def render_config(snap: dict) -> None:
    def show(v) -> str:
        return "—" if v is None else str(v).lower() if isinstance(v, bool) else str(v)

    print(tr("Config: {path}", path=snap["path"]))
    if snap["key_cmd_problems"]:
        print(tr("key_cmd and the other *_cmd options are ignored: {reasons}",
                 reasons="; ".join(snap["key_cmd_problems"])))
    head = (tr("Provider"), tr("Option"), tr("Value"), tr("Source"))
    rows = [(provider, name, show(o["value"]), tr("file") if o["source"] == "file" else tr("default"))
            for provider, p in snap["providers"].items() for name, o in p["options"].items()]
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(3)]
    for r in [head, *rows]:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[3])


def config_main(args: argparse.Namespace) -> int:
    path = Path(os.path.expanduser(args.config))
    try:
        if args.config_action == "show":
            snap = config_snapshot(path)
        else:
            was_on = _config_value_of(path, args.option) is True
            config_set(path, args.option, args.value)
    except ConfigError as e:
        print(tr("aiu config: {error}", error=e), file=sys.stderr)
        return 1
    except OSError as e:
        where = f"{e.filename}: " if e.filename else ""
        print(tr("aiu config: {error}", error=where + (e.strerror or type(e).__name__)),
              file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print(tr("aiu config: {error}", error=tr("{path} is not UTF-8", path=path)), file=sys.stderr)
        return 1
    if args.config_action == "set":
        print(tr("{option} set in {path}", option=args.option, path=path))
        notice = CONFIG_NOTICES.get(args.option)
        if notice and not was_on and _config_value_of(path, args.option) is True:  # just turned on
            print(tr(notice))
    elif args.config_json:
        print(json.dumps({"schema": JSON_SCHEMA, **snap}, indent=2, ensure_ascii=False, default=str))
    else:
        render_config(snap)
    return 0
