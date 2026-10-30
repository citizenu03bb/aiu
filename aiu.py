#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "rich>=13.7",
# ]
# ///
# SPDX-License-Identifier: GPL-3.0-or-later
"""
aiu — one local snapshot of every AI subscription/quota you pay for.

Sources (each is a pluggable probe):
  - claude     : Claude Max session quotas (5h / weekly / monthly), read from the
                 same OAuth-usage endpoint Claude Code uses. Token is read locally
                 from ~/.claude/.credentials.json. Nothing leaves your machine
                 except the authenticated call to Anthropic. Also reports promo
                 dollar credits (e.g. the cloud-session credit) found in the same
                 response.
  - codex      : ChatGPT/Codex subscription quotas (5h / weekly), OAuth token
                 read from ~/.codex/auth.json.
  - openai     : month-to-date USD spend via the Costs API (needs an Admin key).
  - deepseek   : remaining balance via /user/balance.
  - openrouter : total spend + remaining credits.
  - gemini     : Gemini 5h / weekly quota from the Antigravity desktop app's local
                 server while it runs; else Gemini CLI OAuth (~/.gemini/oauth_creds.json),
                 which Google now refuses for individual accounts.
  - mistral    : monthly Vibe Code + API allowances (€ spent / € included), via
                 the admin console's tRPC API; needs the browser session cookie.

Usage:
  ./aiu.py                 # pretty table
  ./aiu.py --json          # machine-readable
  ./aiu.py --html out.html # local HTML page
  ./aiu.py --only claude,deepseek
  ./aiu.py --config ~/.config/ai-usage/config.toml
  AIU_LANG=fr ./aiu.py --advise   # French messages (also picked from a French LANG)

Config lives at ~/.config/ai-usage/config.toml (a template is written on first run).
No secret is ever stored in this script; keys are resolved at runtime from env,
a file, or a shell command — your choice, per provider.
"""
from __future__ import annotations

import argparse
import glob
import grp
import json
import math
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

# The release version: pyproject.toml reads it, and a v<version> tag must match it
# (tests/test_version.py, .github/workflows/release.yml).
__version__ = "0.3.0"

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
# Claude Max needs no key here: the OAuth token is read from
# ~/.claude/.credentials.json automatically.

[claude]
enabled = true

# Codex CLI + Pi sign in via ChatGPT OAuth (no API key) -> subscription quota,
# like claude. Token read from ~/.codex/auth.json.
[codex]
enabled = true

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

[openrouter]
enabled = false
key_env = "OPENROUTER_API_KEY"

# Mistral's monthly allowances (Vibe Code + API, €8.5 each on Free) are only
# readable with the admin.mistral.ai browser session: put the whole Cookie
# request header (DevTools > Network > any /api/local-trpc/ call) in
# MISTRAL_ADMIN_COOKIE, in single quotes in ~/.env (it contains ';').
# API keys are rejected. Re-copy it when the session expires.
[mistral]
enabled = false
key_env = "MISTRAL_ADMIN_COOKIE"
# vibe_allowance_eur = 8.5   # 0 hides that row
# api_allowance_eur = 8.5

# Note: Cohere exposes NO aggregate usage/spend endpoint (dashboard only), so it
# can't be polled as a simple number without local token accounting.

# Gemini (Antigravity / Google One AI Pro) -> subscription quota, read from the
# Antigravity desktop app while it is open. No key needed.
[gemini]
enabled = false
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
# Claude Max n'a pas besoin de clé ici : le jeton OAuth est lu
# automatiquement dans ~/.claude/.credentials.json.

[claude]
enabled = true

# Codex CLI + Pi se connectent par OAuth ChatGPT (pas de clé API) -> quota
# d'abonnement, comme claude. Jeton lu dans ~/.codex/auth.json.
[codex]
enabled = true

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

[openrouter]
enabled = false
key_env = "OPENROUTER_API_KEY"

# Les forfaits mensuels de Mistral (Vibe Code + API, 8,5 € chacun en Free) ne se
# lisent qu'avec la session navigateur d'admin.mistral.ai : mets tout l'en-tête
# Cookie de la requête (DevTools > Réseau > n'importe quel appel /api/local-trpc/)
# dans MISTRAL_ADMIN_COOKIE, entre apostrophes dans ~/.env (il contient des ';').
# Les clés API sont refusées. Recopie-le quand la session expire.
[mistral]
enabled = false
key_env = "MISTRAL_ADMIN_COOKIE"
# vibe_allowance_eur = 8.5   # 0 masque cette ligne
# api_allowance_eur = 8.5

# Note : Cohere n'expose AUCUN point d'accès d'usage ou de dépense agrégé (tableau
# de bord seulement), donc impossible de le relever sans compter les jetons soi-même.

# Gemini (Antigravity / Google One AI Pro) -> quota d'abonnement, lu dans
# l'application de bureau Antigravity tant qu'elle est ouverte. Pas de clé.
[gemini]
enabled = false
"""


# ---------------------------------------------------------------------------
# Interface language: English, or French when AIU_LANG (else the locale) says
# so. Messages are written in English and looked up in FR by their exact text.
# Probe notes, metric names and --json keys stay English: they are data, and
# some (a credit's "locked: ..." note) are matched on.
# ---------------------------------------------------------------------------
def ui_lang() -> str:
    """'fr' when AIU_LANG, else the first set of LC_ALL, LC_MESSAGES and LANG,
    starts with fr; 'en' otherwise. Read on every call."""
    for var in ("AIU_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        if v := os.environ.get(var):
            return "fr" if v.lower().startswith("fr") else "en"
    return "en"


def tr(msg: str, **fields) -> str:
    """msg in the interface language, with its {fields} filled in."""
    if ui_lang() == "fr":
        msg = FR.get(msg, msg)
    return msg.format(**fields) if fields else msg


FR = {
    # stderr warnings
    "[aiu] {name}: key from {source} holds control characters, ignored":
        "[aiu] {name} : la clé lue par {source} contient des caractères de contrôle, ignorée",
    "aiu: claude_cmd {path} is not an executable file":
        "aiu : claude_cmd {path} n'est pas un fichier exécutable",
    "aiu: not running {path}: writable by other users":
        "aiu : {path} non lancé : d'autres utilisateurs peuvent l'écrire",
    "[aiu] gemini: refreshed token kept for this run only, {path} left as is: {reason}":
        "[aiu] gemini : jeton rafraîchi gardé pour cette exécution seulement, "
        "{path} laissé tel quel : {reason}",
    "not owned by you": "il ne t'appartient pas",
    "not a JSON object": "ce n'est pas un objet JSON",
    "signed in again meanwhile": "nouvelle connexion entre-temps",
    "[aiu] {path}:{line}: {name} has unmatched quotes, ignored ({file} values must fit on one line)":
        "[aiu] {path}:{line} : guillemets non appariés pour {name}, ignoré "
        "(une valeur de {file} doit tenir sur une seule ligne)",
    "[aiu] {path} holds keys and other users can read it: chmod 600 {path}":
        "[aiu] {path} contient des clés et d'autres utilisateurs peuvent le lire : chmod 600 {path}",
    "[aiu] wrote a starter config to {path} — edit it, then re-run.":
        "[aiu] config de départ écrite dans {path} — modifie-la, puis relance.",
    "{path} is not yours": "{path} ne t'appartient pas",
    "{path} is writable by group or others": "{path} est modifiable par le groupe ou les autres",
    "[aiu] key_cmd ignored for {names}: {reasons}": "[aiu] key_cmd ignoré pour {names} : {reasons}",
    "[aiu] wrote {path}": "[aiu] {path} écrit",
    # table and HTML page
    "AI usage snapshot": "Relevé d'usage IA",
    "Provider": "Fournisseur",
    "Metric": "Mesure",
    "Usage": "Utilisation",
    "Resets / note": "Reset / note",
    "{used}/{limit} {unit} left": "{used}/{limit} {unit} restants",
    # --advise
    "deep reasoning / agentic": "raisonnement profond / agentique",
    "volume / throwaway tasks (Flash)": "volume / tâches jetables (Flash)",
    "second coding voice / diversity": "2e voix de code / diversité",
    "Vibe / Le Chonk (1M context)": "Vibe / Le Chonk (contexte 1M)",
    "{metric} {used}/{limit} {unit}, expires in {ttr} (≈{per_day} {unit}/day to use it all)":
        "{metric} {used}/{limit} {unit}, expire dans {ttr} (≈{per_day} {unit}/jour pour tout utiliser)",
    "{metric} {left}% free: runs dry in ~{runway}, reset in {ttr} → ~{gap} without {provider}":
        "{metric} {left}% libre : à sec dans ~{runway}, reset dans {ttr} → ~{gap} sans {provider}",
    "{metric} {left}% free, reset in {ttr} (unknown pace)":
        "{metric} {left}% libre, reset dans {ttr} (allure inconnue)",
    "{metric} {left}% free, reset unknown (unknown pace)":
        "{metric} {left}% libre, reset inconnu (allure inconnue)",
    "{metric}: reset in {ttr}, ~{leftover}% of the cap lost at your pace "
    "({pace}%/h; using it all = {need}%/h, ×{ratio})":
        "{metric} : reset dans {ttr}, ~{leftover}% du plafond perdu à ton allure "
        "({pace}%/h ; tout utiliser = {need}%/h, ×{ratio})",
    "{metric} {left}% free, reset in {ttr} — on track":
        "{metric} {left}% libre, reset dans {ttr} — au rythme",
    " (pace from past windows)": " (allure tirée des fenêtres passées)",
    "; {metric} runs out in ~{runway}": " ; {metric} épuisé dans ~{runway}",
    "\n[bold]Routing advice[/bold] — earliest-expiring capacity first "
    "(surplus projected at your current pace):\n":
        "\n[bold]Conseil de routage[/bold] — la capacité qui expire le plus tôt d'abord "
        "(surplus projeté à ton allure actuelle) :\n",
    "\n  [bold]On track (no surplus to give):[/bold]":
        "\n  [bold]Au rythme (pas de surplus à offrir) :[/bold]",
    "\n  [bold red]Conserve[/bold red] (runs dry before the reset — keep it for "
    "what only it can do):":
        "\n  [bold red]À ménager[/bold red] (s'épuise avant le reset — garde-le pour "
        "ce que lui seul sait faire) :",
    "\n  [dim]Paid (overflow only; currencies not added up):[/dim]":
        "\n  [dim]Payant (débordement seulement ; devises non additionnées) :[/dim]",
    "\n  [bold green]→ Burn {provider} first[/bold green] (expires in {ttr}): "
    "send it everything it can do":
        "\n  [bold green]→ Brûle d'abord {provider}[/bold green] (expire dans {ttr}) : "
        "envoie-lui tout ce qu'il sait faire",
    "\n  [bold green]→ Burn {provider} first[/bold green] (expires in {ttr}, unknown pace): "
    "send it everything it can do":
        "\n  [bold green]→ Brûle d'abord {provider}[/bold green] (expire dans {ttr}, "
        "allure inconnue) : envoie-lui tout ce qu'il sait faire",
    ", including what you would give {names}.": ", y compris ce que tu donnerais à {names}.",
    " Conserve {names} for what only it can do.": " Ménage {names} pour ce que lui seul sait faire.",
    " Conserve {names} for what only they can do.":
        " Ménage {names} pour ce qu'eux seuls savent faire.",
    "\n  [bold]→ No expiring surplus[/bold]: route by task fit to {providers} (on track)":
        "\n  [bold]→ Aucun surplus qui expire[/bold] : route par fit de tâche vers "
        "{providers} (au rythme)",
    ", conserve {names}.": ", ménage {names}.",
    "\n  [bold yellow]→ No subscription has surplus[/bold yellow]: keep each for what "
    "only it can do; overflow the rest to the cheapest paid API.\n":
        "\n  [bold yellow]→ Aucun forfait n'a de surplus[/bold yellow] : réserve chacun à ce "
        "que lui seul sait faire ; déborde le reste sur l'API payante la moins chère.\n",
    # --alert
    "⚠ {provider} · {metric}: {left} {unit} unspent, {reset}":
        "⚠ {provider} · {metric}: {left} {unit} non dépensés, {reset}",
    "⚠ {provider} · {metric}: ~{waste}% of the cap lost at reset, reset {reset}":
        "⚠ {provider} · {metric}: ~{waste}% du plafond perdu au reset, reset {reset}",
    "⚠ {provider} · {metric}: runs dry in ~{runway} at your pace, reset {reset} "
    "→ ~{gap} without {provider}":
        "⚠ {provider} · {metric}: à sec dans ~{runway} à ton allure, reset {reset} "
        "→ ~{gap} sans {provider}",
    "⚠ {provider}: data unavailable ({note})": "⚠ {provider}: données indisponibles ({note})",
    # aiu config
    "aiu config: {error}": "aiu config : {error}",
    "unknown provider; one of: {providers}": "fournisseur inconnu ; au choix : {providers}",
    "unknown option for {provider}; it takes: {options}":
        "réglage inconnu pour {provider} ; il accepte : {options}",
    "{option} takes true or false": "{option} prend true ou false",
    "{option} takes a number, 0 or more": "{option} prend un nombre, 0 ou plus",
    "{option} cannot hold control characters":
        "{option} ne peut pas contenir de caractères de contrôle",
    "key_env takes a variable name (letters, digits and _) or an empty string":
        "key_env prend un nom de variable (lettres, chiffres et _) ou une chaîne vide",
    "{option} refused: {reasons}": "{option} refusé : {reasons}",
    "{path} does not parse: {error}": "{path} est illisible : {error}",
    "{name} in {path} is not a table": "{name} n'est pas une table dans {path}",
    "{path} is not UTF-8": "{path} n'est pas en UTF-8",
    "cannot change {option} in place: edit it by hand":
        "impossible de modifier {option} sur place : modifie-le à la main",
    "{option} set in {path}": "{option} réglé dans {path}",
    "Config: {path}": "Config : {path}",
    "key_cmd is ignored: {reasons}": "key_cmd est ignoré : {reasons}",
    "Option": "Réglage",
    "Value": "Valeur",
    "Source": "Provenance",
    "file": "fichier",
    "default": "par défaut",
    "AI quota · snapshot {time}": "Quota AI · relevé {time}",
    "✓ nothing will be lost at reset or run dry for long":
        "✓ rien ne sera perdu au reset ni à sec longtemps",
}


# ---------------------------------------------------------------------------
# Normalized record every probe returns
# ---------------------------------------------------------------------------
@dataclass
class Row:
    provider: str
    metric: str                 # human label, e.g. "5-Hour Limit", "Spend (MTD)"
    kind: str                   # "quota" | "spend" | "balance" | "credit" | "error"
    used: float | None = None   # quota: 0..1 ; spend/balance: amount ; credit: amount left
    limit: float | None = None  # quota: 1.0 ; spend: optional budget ; credit: grant size
    unit: str = ""              # "%", "USD", "CNY", ...
    reset: str | None = None    # human reset string
    note: str = ""
    reset_epoch: float | None = None  # machine reset time (for advise/alert)
    window_secs: int | None = None    # quota window length, when known

    def to_public(self) -> dict:
        d = asdict(self)
        if self.kind == "quota" and self.used is not None:
            d["used_pct"] = round(self.used * 100, 1)
        return d


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------
# Values read from ~/.env by load_env_file: only the names the config refers to,
# and never exported to os.environ, so key_cmd and other children don't see them.
ENV_FILE: dict[str, str] = {}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _private_group(gid: int) -> bool:
    """True for the user's private group: named after the user, with no other
    member and no other user's primary group (umask 002 setups, as on Ubuntu)."""
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
        g = grp.getgrgid(gid)
    except KeyError:
        return False
    return (g.gr_name == user and set(g.gr_mem) <= {user}
            and not any(u.pw_gid == gid and u.pw_name != user for u in pwd.getpwall()))


def _writable_by_others(st: os.stat_result) -> bool:
    """Whether someone besides the user can write it. Group write counts only when
    the group is not the user's private one, as Debian's OpenSSH StrictModes does."""
    return bool(st.st_mode & 0o002 or (st.st_mode & 0o020 and not _private_group(st.st_gid)))


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


# ---------------------------------------------------------------------------
# Probe: Claude Max
# ---------------------------------------------------------------------------
ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_QUOTA_LABELS = {
    "five_hour": "5-Hour Limit",
    "seven_day": "Weekly (all models)",
    "seven_day_sonnet": "Weekly Sonnet",
    "monthly_limit": "Monthly Limit",
    "extra_usage": "Extra Usage",
}
CLAUDE_WINDOW = {  # window length in seconds, for advise/alert
    "five_hour": 5 * 3600,
    "seven_day": 7 * 86400,
    "seven_day_sonnet": 7 * 86400,
    "monthly_limit": 30 * 86400,
    "extra_usage": 30 * 86400,
}


def _read_claude_token() -> tuple[str | None, int | None]:
    """Return (access_token, expires_at_ms) from the local Claude Code creds."""
    p = Path(os.path.expanduser("~/.claude/.credentials.json"))
    if not p.is_file():
        return None, None
    try:
        d = json.loads(p.read_text())
        oauth = d.get("claudeAiOauth", {})
        return oauth.get("accessToken"), oauth.get("expiresAt")
    except Exception:
        return None, None


# Where the installers put `claude`, in order: native installer, the old
# local npm install, then system-wide ones. `claude_cmd` overrides the list.
CLAUDE_LOCATIONS = ("~/.local/bin/claude", "~/.claude/local/claude",
                    "/usr/local/bin/claude", "/usr/bin/claude", "/opt/homebrew/bin/claude")
CLAUDE_REFRESH_BACKOFF = 3600  # seconds without retrying after a failed refresh


def _refresh_claude_token(cfg: dict | None = None) -> bool:
    """Have the CLI refresh ~/.claude/.credentials.json. Any `claude` start does
    it, `auth status` without spending a request; the desktop app keeps its own
    token and never rewrites the file, so it goes stale 8h after the last CLI run.
    Clean environment: a host Claude session's variables (ANTHROPIC_BASE_URL,
    host-managed auth) must not steer the child.

    The binary is never taken from PATH (gnome-shell's, when run from the
    extension): only `claude_cmd` from the [claude] config, or else the first
    known install location. It is resolved once to its real path, skipped when
    someone else can write it or its directory, and that exact file is run.
    A failed run is not retried for CLAUDE_REFRESH_BACKOFF seconds.
    It can race an interactive `claude` over the same refresh token; the
    backoff keeps that to one attempt per stale period."""
    marker = Path(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")) \
        / "aiu" / "claude-refresh-failed"
    try:
        if time.time() - marker.stat().st_mtime < CLAUDE_REFRESH_BACKOFF:
            return False
    except OSError:
        pass
    configured = (cfg or {}).get("claude_cmd")
    candidates = [configured] if configured else CLAUDE_LOCATIONS
    exe = None
    for c in candidates:
        path = Path(os.path.expanduser(str(c)))
        real = Path(os.path.realpath(path))
        if not (real.is_file() and os.access(real, os.X_OK)):
            if configured:
                print(tr("aiu: claude_cmd {path} is not an executable file", path=path),
                      file=sys.stderr)
            continue
        # The link, its target and both directories: none writable by others.
        try:
            safe = all((st.st_uid in (0, os.getuid()) and not _writable_by_others(st))
                       for st in (os.stat(p) for p in {path.parent, real, real.parent}))
        except OSError:
            safe = False
        if safe:
            exe = str(real)
        else:
            print(tr("aiu: not running {path}: writable by other users", path=path),
                  file=sys.stderr)
        break
    if not exe:
        return False
    env = {k: os.environ[k] for k in ("HOME", "PATH", "USER", "LANG") if k in os.environ}
    try:
        ok = subprocess.run([exe, "auth", "status"], env=env, capture_output=True,
                            timeout=60).returncode == 0
    except Exception:
        ok = False
    try:
        if ok:
            marker.unlink(missing_ok=True)
        else:
            marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            marker.touch(mode=0o600)
            os.utime(marker, (time.time(),) * 2)
    except OSError:
        pass
    return ok


def probe_claude(cfg: dict) -> list[Row]:
    token, expires_at = _read_claude_token()
    if token and expires_at and expires_at / 1000 < time.time() + 60 and _refresh_claude_token(cfg):
        token, expires_at = _read_claude_token()
    if not token:
        return [Row("claude", "—", "error",
                    note="no token in ~/.claude/.credentials.json (run `claude` once)")]
    warn = ""
    if expires_at and expires_at / 1000 < time.time():
        warn = "token expired — run `claude` to refresh; "
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.1.69",
    }
    try:
        r = httpx.get(ANTHROPIC_USAGE_URL, headers=headers, timeout=30)
    except Exception as e:
        return [Row("claude", "—", "error", note=warn + _network_note(e))]
    if r.status_code in (401, 403):
        return [Row("claude", "—", "error",
                    note=f"{warn}auth rejected ({r.status_code}) — run `claude` to refresh")]
    if r.status_code != 200:
        return [Row("claude", "—", "error", note=f"{warn}HTTP {r.status_code}")]
    rows: list[Row] = []
    try:
        data = _json_object(r)
        for key, label in CLAUDE_QUOTA_LABELS.items():
            entry = data.get(key)
            if not entry or entry.get("utilization") is None:
                continue
            if entry.get("is_enabled") is False:
                continue
            reset = entry.get("resets_at")
            reset_h = _humanize_reset(reset) if reset else None
            # API reports utilization already as a 0-100 percentage (Claude console
            # "1% used" == API value 1.0). Store as a 0-1 fraction internally.
            rows.append(Row("claude", label, "quota",
                            used=float(entry["utilization"]) / 100.0, limit=1.0, unit="%",
                            reset=reset_h, note=warn.strip(),
                            reset_epoch=_iso_epoch(reset) if reset else None,
                            window_secs=CLAUDE_WINDOW.get(key)))
        rows.extend(_claude_credits(data, warn))
    except Exception as e:
        return [Row("claude", "—", "error", note=warn + _malformed_note(e))]
    if not rows:
        rows.append(Row("claude", "—", "quota", note=f"{warn}no active quotas reported"))
    return rows


# Promo dollar grants sit under codenamed keys that change without notice
# (the Nov-2026 cloud-session credit is "iguana_necktie"), so match on shape.
CLAUDE_CREDIT_LABELS = {"iguana_necktie": "Cloud Credit"}


def _claude_credits(data: dict, warn: str) -> list[Row]:
    rows = []
    for key, entry in data.items():
        if not isinstance(entry, dict) or entry.get("limit_dollars") is None:
            continue
        label = CLAUDE_CREDIT_LABELS.get(key, f"Credit ({key})")
        limit = float(entry["limit_dollars"])
        left = entry.get("remaining_dollars")
        if left is None and entry.get("used_dollars") is not None:
            left = limit - float(entry["used_dollars"])
        if left is None:  # unknown spend: never assume the grant is untouched
            rows.append(Row("claude", label, "error",
                            note=warn + "malformed response: no `remaining_dollars` or `used_dollars`"))
            continue
        reset = entry.get("resets_at")
        note = warn + (f"locked: {entry['locked_reason']}" if entry.get("locked_reason") else "")
        rows.append(Row("claude", label, "credit",
                        used=round(float(left), 2), limit=limit, unit="USD",
                        reset=f"expires {_humanize_reset(reset)}" if reset else None,
                        note=note.strip(),
                        reset_epoch=_iso_epoch(reset) if reset else None))
    return rows


# ---------------------------------------------------------------------------
# Probe: Codex / ChatGPT subscription (used by Codex CLI and Pi — OAuth, no API key)
# ---------------------------------------------------------------------------
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _read_codex_token() -> tuple[str | None, str | None]:
    p = Path(os.path.expanduser("~/.codex/auth.json"))
    if not p.is_file():
        return None, None
    try:
        t = json.loads(p.read_text()).get("tokens", {})
        return t.get("access_token"), t.get("account_id")
    except Exception:
        return None, None


def probe_codex(cfg: dict) -> list[Row]:
    token, account = _read_codex_token()
    if not token:
        return [Row("codex", "—", "error",
                    note="no token in ~/.codex/auth.json (run `codex` to sign in)")]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "aiu/1.0",
    }
    if account:
        headers["X-Account-Id"] = account
        headers["chatgpt-account-id"] = account
    try:
        r = httpx.get(CODEX_USAGE_URL, headers=headers, timeout=15)
    except Exception as e:
        return [Row("codex", "—", "error", note=_network_note(e))]
    if r.status_code in (401, 403):
        return [Row("codex", "—", "error",
                    note=f"auth rejected ({r.status_code}) — run `codex` to refresh")]
    if r.status_code != 200:
        return [Row("codex", "—", "error", note=f"HTTP {r.status_code}")]
    rows: list[Row] = []
    try:
        data = _json_object(r)
        plan = data.get("plan_type", "")
        rl = data.get("rate_limit", {}) or {}
        for key, win in (("primary_window", rl.get("primary_window")),
                         ("secondary_window", rl.get("secondary_window"))):
            if not win or win.get("used_percent") is None:
                continue
            secs = win.get("limit_window_seconds") or 0
            label = "5-Hour Limit" if secs and secs <= 6 * 3600 else \
                    "Weekly Limit" if secs else key
            rows.append(Row("codex", label, "quota",
                            used=float(win["used_percent"]) / 100.0, limit=1.0, unit="%",
                            reset=_humanize_reset_unix(win.get("reset_at")),
                            note=plan if not rows else "",
                            reset_epoch=float(win["reset_at"]) if win.get("reset_at") else None,
                            window_secs=int(secs) or None))
        # The Codex CLI decodes `balance` as a string ("9.99"); take a number too.
        credits = (data.get("credits") or {}).get("balance")
    except Exception as e:
        return [Row("codex", "—", "error", note=_malformed_note(e))]
    if isinstance(credits, (int, float, str)) and not isinstance(credits, bool) and credits != "":
        try:
            rows.append(Row("codex", "Credits", "balance", used=round(float(credits), 2), unit="USD"))
        except ValueError:
            rows.append(Row("codex", "Credits", "error", note="malformed response: `balance` is not a number"))
    if not rows:
        rows.append(Row("codex", "—", "quota", note=f"plan {plan}: no active windows"))
    return rows


def _humanize_reset_unix(ts: int | None) -> str | None:
    if not ts:
        return None
    try:
        return _humanize_reset(datetime.fromtimestamp(int(ts), timezone.utc).isoformat())
    except Exception:
        return None


def _iso_epoch(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _humanize_reset(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = t - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "now"
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        if d:
            return f"in {d}d {h}h"
        if h:
            return f"in {h}h {m}m"
        return f"in {m}m"
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# Probe: OpenAI (month-to-date spend via Costs API; needs Admin key)
# ---------------------------------------------------------------------------
def probe_openai(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "openai")
    if not key:
        return [Row("openai", "Spend (MTD)", "error", note="no admin key (set OPENAI_ADMIN_KEY)")]
    start = _month_start_epoch()
    total = 0.0
    page = None
    for _ in range(12):  # safety bound on pagination
        params = {"start_time": start, "bucket_width": "1d", "limit": 31}
        if page:
            params["page"] = page
        try:
            r = httpx.get(
                "https://api.openai.com/v1/organizations/costs",
                headers={"Authorization": f"Bearer {key}"},
                params=params, timeout=30,
            )
        except Exception as e:
            return [Row("openai", "Spend (MTD)", "error", note=_network_note(e))]
        if r.status_code in (401, 403):
            return [Row("openai", "Spend (MTD)", "error",
                        note=f"auth rejected ({r.status_code}) — needs an ADMIN key")]
        if r.status_code != 200:
            return [Row("openai", "Spend (MTD)", "error", note=f"HTTP {r.status_code}")]
        try:
            body = _json_object(r)
            for bucket in _need(body, "data", list):
                for res in _need(bucket, "results", list):
                    total += float(_need(res, "amount", dict).get("value") or 0)
            page = body.get("next_page") if body.get("has_more") else None
        except Exception as e:
            return [Row("openai", "Spend (MTD)", "error", note=_malformed_note(e))]
        if not page:
            break
    return [Row("openai", "Spend (MTD)", "spend", used=round(total, 2), unit="USD")]


# ---------------------------------------------------------------------------
# Probe: DeepSeek (remaining balance)
# ---------------------------------------------------------------------------
def probe_deepseek(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "deepseek")
    if not key:
        return [Row("deepseek", "Balance", "error", note="no key (set DEEPSEEK_API_KEY)")]
    try:
        r = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"}, timeout=30,
        )
    except Exception as e:
        return [Row("deepseek", "Balance", "error", note=_network_note(e))]
    if r.status_code in (401, 403):
        return [Row("deepseek", "Balance", "error", note=f"auth rejected ({r.status_code})")]
    if r.status_code != 200:
        return [Row("deepseek", "Balance", "error", note=f"HTTP {r.status_code}")]
    rows = []
    try:
        data = _json_object(r)
        for info in _need(data, "balance_infos", list):
            rows.append(Row(
                "deepseek", f"Balance ({info.get('currency','?')})", "balance",
                used=float(_need(info, "total_balance")),
                unit=info.get("currency", ""),
                # Only an explicit false: a dropped flag is no reason to alarm.
                note="account not available" if data.get("is_available") is False else "",
            ))
    except Exception as e:
        return [Row("deepseek", "Balance", "error", note=_malformed_note(e))]
    if not rows:
        rows.append(Row("deepseek", "Balance", "balance", note="no balance info returned"))
    return rows


# ---------------------------------------------------------------------------
# Probe: OpenRouter (total spend + remaining credits)
# ---------------------------------------------------------------------------
def probe_openrouter(cfg: dict) -> list[Row]:
    key = resolve_key(cfg, "openrouter")
    if not key:
        return [Row("openrouter", "Credits", "error", note="no key (set OPENROUTER_API_KEY)")]
    try:
        r = httpx.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"}, timeout=30,
        )
    except Exception as e:
        return [Row("openrouter", "Credits", "error", note=_network_note(e))]
    if r.status_code in (401, 403):
        return [Row("openrouter", "Credits", "error", note=f"auth rejected ({r.status_code})")]
    if r.status_code != 200:
        return [Row("openrouter", "Credits", "error", note=f"HTTP {r.status_code}")]
    try:
        d = _need(_json_object(r), "data", dict)
        total = float(_need(d, "total_credits"))
        used = float(_need(d, "total_usage"))
    except Exception as e:
        return [Row("openrouter", "Credits", "error", note=_malformed_note(e))]
    return [
        Row("openrouter", "Spend (total)", "spend", used=round(used, 2), unit="USD"),
        Row("openrouter", "Remaining", "balance", used=round(total - used, 2), unit="USD"),
    ]


# ---------------------------------------------------------------------------
# Probe: Gemini Code Assist (Google One / AI Pro subscription quota via OAuth)
# The Gemini CLI signs in with Google OAuth — usage is a subscription quota, not
# pay-per-token API spend. Read the same token the CLI uses and hit the internal
# Code Assist quota endpoint. Since Oct 2026 Google refuses that client for
# individual accounts (UNSUPPORTED_CLIENT), so for them the Antigravity desktop
# app's local language server (+ CSRF token from its command line) is the only
# source, read first below. The `agy` CLI keeps its login in the system keyring,
# not in oauth_creds.json, and its own local server's CSRF token never leaves
# memory, so aiu can't read quota through agy.
# ---------------------------------------------------------------------------
GEMINI_CA_BASE = "https://cloudcode-pa.googleapis.com"
GEMINI_CREDS_PATH = Path(os.path.expanduser("~/.gemini/oauth_creds.json"))
GEMINI_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refreshing the token needs the Gemini CLI's public "installed app" OAuth client.
# It is not a user secret, but it is Google's to publish, not ours: take it from
# the environment, else read it out of the locally installed Gemini CLI bundle.
# The bundle holds several Google client IDs; only the named constants are the CLI's.
_GEMINI_CLIENT_ID_RE = re.compile(r'OAUTH_CLIENT_ID\s*=\s*"([^"]+\.apps\.googleusercontent\.com)"')
_GEMINI_CLIENT_SECRET_RE = re.compile(r'OAUTH_CLIENT_SECRET\s*=\s*"([^"]+)"')


def _gemini_oauth_client() -> tuple[str, str] | None:
    cid, secret = (os.environ.get(k) or ENV_FILE.get(k)
                   for k in ("GEMINI_CLIENT_ID", "GEMINI_CLIENT_SECRET"))
    if cid and secret:
        return cid, secret
    cli = shutil.which("gemini")
    if not cli:
        return None
    bundle = Path(os.path.realpath(cli)).parent   # .../@google/gemini-cli/bundle
    for f in sorted(bundle.glob("*.js")):
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        m_id, m_secret = _GEMINI_CLIENT_ID_RE.search(text), _GEMINI_CLIENT_SECRET_RE.search(text)
        if m_id and m_secret:
            return m_id.group(1), m_secret.group(1)
    return None


def _gemini_refresh(refresh_token: str) -> str | None:
    """Exchange the refresh token for a fresh access token and persist it back to
    oauth_creds.json (Google does not rotate refresh tokens). Returns the new token,
    which is good for this run even when it could not be saved."""
    client = _gemini_oauth_client()
    if not client:
        return None
    try:
        r = httpx.post(GEMINI_TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": client[0], "client_secret": client[1]}, timeout=20)
        if r.status_code != 200:
            return None
        j = r.json()
        at = j.get("access_token")
        if not at:
            return None
        expiry = int((time.time() + int(j.get("expires_in", 3600))) * 1000)
    except Exception:
        return None
    # persist so the next call (and the CLIs) reuse it
    reason = _gemini_persist(refresh_token, at, expiry, j.get("id_token"))
    if reason:
        print(tr("[aiu] gemini: refreshed token kept for this run only, {path} left as is: {reason}",
                 path=GEMINI_CREDS_PATH, reason=tr(reason)), file=sys.stderr)
    return at


def _gemini_persist(refresh_token: str, access_token: str, expiry_ms: int,
                    id_token: str | None) -> str | None:
    """Store a refreshed token in oauth_creds.json without ever leaving it empty or
    half-written: under a lock shared by every aiu run, re-read the file, change only
    the token fields, write a 0600 temp file next to the real file (a symlink stays a
    symlink) and rename it over. Returns why nothing was written, or None."""
    import fcntl
    import tempfile
    target = Path(os.path.realpath(GEMINI_CREDS_PATH))
    tmp = None
    try:
        if target.stat().st_uid != os.getuid():
            return "not owned by you"
        lock = os.open(target.with_name(f".{target.name}.aiu-lock"),
                       os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            creds = json.loads(target.read_text())
            if not isinstance(creds, dict):
                return "not a JSON object"
            if creds.get("refresh_token") != refresh_token:
                return "signed in again meanwhile"
            creds["access_token"] = access_token
            creds["expiry_date"] = expiry_ms
            if id_token:
                creds["id_token"] = id_token
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:  # mkstemp: O_EXCL, mode 0600
                f.write(json.dumps(creds, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
            tmp = None
        finally:
            os.close(lock)  # releases the flock
    except (OSError, ValueError) as e:
        return type(e).__name__
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return None


def _gemini_access_token(force: bool = False) -> str | None:
    """Return a valid access token, refreshing via the refresh token if the stored
    one is expired/near-expiry (60s buffer) or `force` is set."""
    if not GEMINI_CREDS_PATH.is_file():
        return None
    try:
        creds = json.loads(GEMINI_CREDS_PATH.read_text())
    except Exception:
        return None
    tok = creds.get("access_token")
    exp = creds.get("expiry_date", 0)  # epoch ms
    stale = force or (not tok) or (exp and exp / 1000 < time.time() + 60)
    if stale and creds.get("refresh_token"):
        return _gemini_refresh(creds["refresh_token"]) or tok
    return tok


def _gemini_family(model_id: str) -> tuple[str, str]:
    m = model_id.lower()
    if "flash" in m and "lite" in m:
        return "flash_lite", "Gemini Flash Lite"
    if "pro" in m:
        return "pro", "Gemini Pro"
    if "flash" in m:
        return "flash", "Gemini Flash"
    return model_id, model_id


def _parse_summary_response(data: dict) -> list[Row] | None:
    # A local reply is checked like any server's: a wrong shape is no summary.
    resp = data.get("response") if isinstance(data, dict) else None
    groups = resp.get("groups") if isinstance(resp, dict) else None
    rows: list[Row] = []
    for g in groups if isinstance(groups, list) else []:
        if isinstance(g, dict) and g.get("displayName") == "Gemini Models":
            buckets = g.get("buckets")
            for b in buckets if isinstance(buckets, list) else []:
                frac = b.get("remainingFraction") if isinstance(b, dict) else None
                if isinstance(frac, bool) or not isinstance(frac, (int, float)) or frac != frac:
                    continue
                win = b.get("window", "")
                bid = b.get("bucketId", "")
                if win == "5h" or bid == "gemini-5h":
                    label = "5-Hour Limit"
                    secs = 18000
                elif win == "weekly" or bid == "gemini-weekly":
                    label = "Weekly Limit"
                    secs = 604800
                else:
                    label = str(b.get("displayName") or "")
                    secs = None
                reset = b.get("resetTime")
                if not isinstance(reset, str):
                    reset = None
                reset_h = _humanize_reset(reset) if reset else None
                rows.append(Row("gemini", label, "quota",
                                used=max(0.0, min(1.0, 1.0 - float(frac))),
                                limit=1.0, unit="%",
                                reset=reset_h,
                                reset_epoch=_iso_epoch(reset) if reset else None,
                                window_secs=secs))
    if not rows:
        return None
    order = {"5-Hour Limit": 0, "Weekly Limit": 1}
    rows.sort(key=lambda r: order.get(r.metric, 99))
    return rows


def _query_local_quota_summary() -> list[Row] | None:
    """Query Antigravity's local language server for quota summary.
    Antigravity IDE runs a local language server on localhost and requires the
    x-codeium-csrf-token header matching its --csrf_token command-line argument.
    Any local user can listen on loopback, so the token only goes to listening
    sockets that are ours and held by an Antigravity language_server of ours."""
    endpoint = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
    uid = os.getuid()
    # /proc/net/tcp{,6} local address -> the host a request must use to reach it.
    # Anything else (another interface, a v6-only wildcard) may be shadowed on
    # loopback by someone else's socket, so it is never queried.
    loopback = {
        "0100007F": "127.0.0.1", "00000000": "127.0.0.1",
        "00000000000000000000000001000000": "[::1]", "00000000000000000000000000000000": "[::1]",
        "0000000000000000FFFF00000100007F": "127.0.0.1",
    }

    def owned(pid: str) -> bool:
        # every uid of the process (real, effective, saved, fs) is ours
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("Uid:"):
                        return all(int(u) == uid for u in line.split()[1:])
        except Exception:
            pass
        return False

    def antigravity_server(pid: str) -> bool:
        # the executable, which unlike argv the process can't rewrite, is a
        # language_server binary inside an Antigravity install
        try:
            exe = os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")
        except Exception:
            return False
        dirs, _, name = exe.lower().rpartition("/")
        return name.startswith("language_server") and "antigravity" in dirs

    def main_log_csrf() -> str | None:
        log_path = os.path.expanduser("~/.config/Antigravity/logs/main.log")
        try:
            with open(log_path, "r", errors="ignore") as f:
                m = re.findall(r"--csrf_token\s+([0-9a-fA-F-]+)", f.read())
            return m[-1] if m else None
        except Exception:
            return None

    # 1. Discover our Antigravity language servers, their CSRF tokens and listening ports
    candidates: list[tuple[str | None, str, int]] = []
    seen: set[tuple[str, int]] = set()
    log_read, log_csrf = False, None

    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            pid = p.split("/")[2]
            if not owned(pid) or not antigravity_server(pid):
                continue
            with open(p, "rb") as f:
                parts = f.read().split(b"\0")
            csrf = None
            for i, part in enumerate(parts):
                if part == b"--csrf_token" and i + 1 < len(parts):
                    csrf = parts[i + 1].decode("utf-8", errors="ignore")
            if not csrf:
                # No token on the cmdline: fall back to the one Antigravity logged
                # when it spawned its server, for this server's ports only.
                if not log_read:
                    log_read, log_csrf = True, main_log_csrf()
                csrf = log_csrf

            inodes = set()
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(os.path.join(fd_dir, fd))
                        if target.startswith("socket:["):
                            inodes.add(target[8:-1])
                    except Exception:
                        pass
            except Exception:
                pass

            for netpath in ("/proc/net/tcp", "/proc/net/tcp6"):
                try:
                    with open(netpath, "r") as f:
                        for line in f.readlines()[1:]:
                            cols = line.strip().split()
                            if (len(cols) < 10 or cols[3] != "0A" or cols[7] != str(uid)
                                    or cols[9] not in inodes):
                                continue
                            ip_hex, port_hex = cols[1].split(":")
                            host, port = loopback.get(ip_hex.upper()), int(port_hex, 16)
                            if host and (host, port) not in seen:
                                seen.add((host, port))
                                candidates.append((csrf, host, port))
                except Exception:
                    pass
        except Exception:
            continue

    # 2. Query candidates. https is verified like any other host: an Antigravity
    # server with a self-signed certificate is skipped, never trusted blindly.
    for csrf, host, port in candidates:
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        }
        if csrf:
            headers["x-codeium-csrf-token"] = csrf

        for scheme in ("http", "https"):
            url = f"{scheme}://{host}:{port}{endpoint}"
            try:
                r = httpx.post(url, json={}, headers=headers, timeout=0.5)
                if r.status_code == 200:
                    rows = _parse_summary_response(r.json())
                    if rows:
                        return rows
            except Exception:
                pass

    return None


def probe_gemini(cfg: dict) -> list[Row]:
    local_rows = _query_local_quota_summary()
    if local_rows is not None:
        return local_rows

    # access tokens expire ~hourly; _gemini_access_token refreshes via the refresh
    # token (and persists) so aiu doesn't depend on a CLI having run recently.
    token = _gemini_access_token()
    if not token:
        return [Row("gemini", "—", "error",
                    note="Antigravity desktop not running, no ~/.gemini/oauth_creds.json fallback "
                         "— open Antigravity to read the quota")]
    r = None
    project, plan, unsupported = None, "", None
    for attempt in (1, 2):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "aiu/1.0",
        }
        try:
            tr = httpx.post(f"{GEMINI_CA_BASE}/v1internal:loadCodeAssist", headers=headers,
                            json={"metadata": {"ideType": "IDE_UNSPECIFIED",
                                               "platform": "PLATFORM_UNSPECIFIED",
                                               "pluginType": "GEMINI"}}, timeout=20)
            if tr.status_code == 200:
                j = tr.json()
                project = j.get("cloudaicompanionProject")
                # The tier the account is on, else the one it would get (Oct 2026
                # responses carry {id, name} objects; planName is the older field).
                allowed = [t for t in j.get("allowedTiers") or [] if isinstance(t, dict)]
                tier = j.get("currentTier") or next((t for t in allowed if t.get("isDefault")),
                                                    allowed[0] if allowed else {})
                plan = j.get("planName") or (tier.get("name") if isinstance(tier, dict) else None) or ""
                # Since Oct 2026 Google refuses the Gemini CLI client for individual
                # accounts ("migrate to Antigravity"): the quota calls then 403.
                if not j.get("currentTier"):
                    unsupported = next((t.get("tierName") or t.get("tierId") for t in
                                        j.get("ineligibleTiers") or []
                                        if t.get("reasonCode") == "UNSUPPORTED_CLIENT"), None)
        except Exception:
            pass
        try:
            r = httpx.post(f"{GEMINI_CA_BASE}/v1internal:retrieveUserQuota", headers=headers,
                           json=({"project": project} if project else {}), timeout=20)
        except Exception as e:
            return [Row("gemini", "—", "error", note=f"network: {e}")]
        if unsupported and r.status_code == 403:
            return [Row("gemini", "—", "error",
                        note=f"Google no longer accepts the Gemini CLI sign-in for {unsupported} "
                             "— open the Antigravity desktop app to read the quota")]
        if r.status_code in (401, 403) and attempt == 1:  # force one refresh + retry
            token = _gemini_access_token(force=True)
            if not token:
                break
            continue
        break
    if r is not None and r.status_code == 403:
        # Google answers 401 for a bad or expired token, so a 403 the refresh and
        # retry did not clear means the account or project may not read the quota.
        return [Row("gemini", "—", "error",
                    note="permission denied (403) — this account or its Code Assist project "
                         "may not read the quota; open the Antigravity desktop app instead")]
    if r is None or r.status_code == 401:
        if not _gemini_oauth_client():
            return [Row("gemini", "—", "error",
                        note="token expired and can't refresh: install gemini-cli or set "
                             "GEMINI_CLIENT_ID / GEMINI_CLIENT_SECRET")]
        return [Row("gemini", "—", "error",
                    note="auth rejected (401) — refresh token invalid, run `agy` to re-sign in")]
    if r.status_code != 200:
        return [Row("gemini", "—", "error", note=f"HTTP {r.status_code}")]
    fam: dict[str, tuple[str, float, str | None]] = {}
    try:
        for b in r.json().get("buckets") or []:
            frac = b.get("remainingFraction")
            if frac is None:
                continue
            fid, label = _gemini_family(b.get("modelId", ""))
            used_frac = 1.0 - float(frac)
            # Select the most constrained (highest usage) bucket per family
            if fid not in fam or used_frac > fam[fid][1]:
                fam[fid] = (label, used_frac, b.get("resetTime"))
    except (ValueError, TypeError, AttributeError):
        return [Row("gemini", "—", "error", note="malformed quota response (HTTP 200)")]
    order = {"pro": 0, "flash": 1, "flash_lite": 2}
    rows: list[Row] = []
    for fid in sorted(fam, key=lambda x: order.get(x, 9)):
        label, used, reset = fam[fid]
        rows.append(Row("gemini", label, "quota",
                        used=max(0.0, min(1.0, used)), limit=1.0, unit="%",
                        reset=_humanize_reset(reset) if reset else None,
                        note=plan if not rows else "",
                        reset_epoch=_iso_epoch(reset) if reset else None))
    if not rows:
        rows.append(Row("gemini", "—", "quota",
                        note=f"{plan}: no quota buckets returned" if plan else "no quota buckets returned"))
    return rows


# ---------------------------------------------------------------------------
# Probe: Mistral (monthly included allowances: Vibe Code + API/Studio)
# Every plan, Free included, gets two € allowances reset on the 1st of the
# calendar month. No API key reads them (Oct 2026: the Vibe key only opens
# /api/vibe/whoami); the admin console's Usage page calls tRPC endpoints that
# accept the browser session cookie only. They return token counts per billing
# metric and a price list; € spent is their product, which matches the
# dashboard's "Total Estimated Cost" to the cent.
# ---------------------------------------------------------------------------
MISTRAL_TRPC = "https://admin.mistral.ai/api/local-trpc/"
MISTRAL_ALLOWANCE_EUR = 8.5  # each allowance, Free plan; override per config
# usage_type -> price eventType; anything unlisted is priced per token.
MISTRAL_EVENT = {
    "ocr": "api_pages", "vibe_ocr": "api_pages",
    "audio_seconds": "api_audio_seconds", "vibe_audio_seconds": "api_audio_seconds",
    "audio_characters": "api_audio_characters", "vibe_audio_characters": "api_audio_characters",
    "connectors": "api_connectors", "vibe_connectors": "api_connectors",
    "libraries_tokens": "api_libraries_tokens", "libraries_pages": "api_libraries_pages",
    "libraries_audio": "api_libraries_audio",
}


def _mistral_trpc(proc: str, payload: dict, cookie: str) -> dict:
    r = httpx.get(MISTRAL_TRPC + proc, params={"input": json.dumps(payload)},
                  headers={"Cookie": cookie, "Accept": "application/json", "User-Agent": "aiu/1.0"},
                  timeout=30, follow_redirects=False)
    if r.is_redirect or r.status_code in (401, 403):  # no session -> login redirect
        raise PermissionError(r.status_code)
    r.raise_for_status()
    try:
        data = _json_object(r)["result"]["data"]["json"]
    except (KeyError, TypeError):
        raise Malformed("not a tRPC result") from None
    if not isinstance(data, dict):
        raise Malformed("not a tRPC result")
    return data


def _mistral_spend(groups: list[dict], prices: list[dict]) -> tuple[dict[str, float], int]:
    """€ spent per allowance ("vibe" | "api"), and how many usage groups had no price."""
    table = {(p["billingMetric"], p["billingGroup"], p["apiZone"], p["serviceTier"], p["eventType"]):
             p["price"] for p in prices}
    spend = {"vibe": 0.0, "api": 0.0}
    unpriced = 0
    for g in groups:
        ut = g.get("usageType") or ""
        price = table.get((g["billingMetric"], g["billingGroup"], g.get("apiZone"),
                           g.get("serviceTier"), MISTRAL_EVENT.get(ut, "api_tokens")))
        if price is None:
            unpriced += bool(g.get("value"))
            continue
        spend["vibe" if ut.startswith("vibe") else "api"] += price * float(g.get("value") or 0)
    return spend, unpriced


def probe_mistral(cfg: dict) -> list[Row]:
    cookie = resolve_key({"key_env": "MISTRAL_ADMIN_COOKIE", **cfg}, "mistral")
    if not cookie:
        return [Row("mistral", "—", "error", note="no session cookie (set MISTRAL_ADMIN_COOKIE)")]
    now = datetime.now()
    # Naive local -> aware: the console counts months in local time, DST included.
    start = datetime(now.year, now.month, 1).astimezone()
    reset = datetime(now.year + now.month // 12, now.month % 12 + 1, 1).astimezone()
    utc = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    span = {"json": {"start": utc(start), "end": utc(now.astimezone()), "untracked": False},
            "meta": {"values": {"start": ["Date"], "end": ["Date"]}, "v": 1}}
    try:
        usage = _mistral_trpc("usage.costBreakdown", span, cookie)
        prices = _mistral_trpc("usage.prices", {"json": {"workspaceId": None}}, cookie)
    except PermissionError as e:
        return [Row("mistral", "—", "error",
                    note=f"session rejected ({e.args[0]}) — copy a fresh Cookie header from admin.mistral.ai")]
    except httpx.HTTPStatusError as e:
        return [Row("mistral", "—", "error", note=f"HTTP {e.response.status_code}")]
    except Malformed as e:
        return [Row("mistral", "—", "error", note=_malformed_note(e))]
    except Exception as e:
        return [Row("mistral", "—", "error", note=_network_note(e))]
    try:
        spend, unpriced = _mistral_spend(usage.get("groups") or [], prices.get("prices") or [])
    except Exception as e:
        return [Row("mistral", "—", "error", note=_malformed_note(e))]
    warn = (f" ; {unpriced} unpriced group(s)" if unpriced else "") + \
           (" ; partial (more pages)" if (usage.get("metadata") or {}).get("hasMore") else "")
    rows = []
    for metric, kind in (("Vibe Monthly", "vibe"), ("API Monthly", "api")):
        allowance = float(cfg.get(f"{kind}_allowance_eur", MISTRAL_ALLOWANCE_EUR))
        if allowance <= 0:
            continue
        rows.append(Row("mistral", metric, "quota",
                        used=spend[kind] / allowance, limit=1.0, unit="%",
                        reset=_humanize_reset(reset.isoformat()),
                        note=f"€{spend[kind]:.2f} / €{allowance:.2f}" + warn,
                        reset_epoch=reset.timestamp(),
                        window_secs=int((reset - start).total_seconds())))
    return rows


# ---------------------------------------------------------------------------
# Probe errors. A note never quotes an exception: httpx puts the offending
# header value, key included, into its message. Notes say what failed in our
# own words, and collect() redacts anything token-like that still gets in.
# ---------------------------------------------------------------------------
class Malformed(ValueError):
    """A 200 whose body is not the shape the probe reads. The message is a
    fixed text of ours, so it can go into a note."""


def _json_object(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise Malformed("not JSON") from None
    if not isinstance(data, dict):
        raise Malformed("not a JSON object")
    return data


def _need(d, key: str, kind: type | tuple[type, ...] = (int, float, str)):
    """d[key], present and of `kind`: a renamed field is an error, never a zero."""
    v = d.get(key) if isinstance(d, dict) else None
    if not isinstance(v, kind) or isinstance(v, bool):
        raise Malformed(f"no `{key}`")
    return v


def _network_note(e: Exception) -> str:
    if isinstance(e, httpx.LocalProtocolError):  # e.g. a key with a newline in it
        return "invalid request header (check the key for extra lines)"
    if isinstance(e, httpx.TimeoutException):
        return "network: timed out"
    if isinstance(e, httpx.ConnectError):
        return "network: cannot connect"
    return f"network: {type(e).__name__}"


def _malformed_note(e: Exception) -> str:
    if isinstance(e, Malformed):
        return f"malformed response: {e}"
    return f"malformed response ({type(e).__name__})"


_SECRET_RE = re.compile(
    r"(?i)\b(?:bearer|basic)\s+\S+"  # an Authorization header value
    r"|\bsk-[\w-]+"                  # API keys: sk-…, sk-ant-…, sk-or-v1-…
    r"|\b[\w.-]+=[^\s;,]{8,}"        # cookie pairs
    r"|[\w.~+/-]{32,}=*"             # long opaque strings: JWTs, OAuth tokens
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text)


PROBES = {
    "claude": probe_claude,
    "codex": probe_codex,
    "openai": probe_openai,
    "deepseek": probe_deepseek,
    "openrouter": probe_openrouter,
    "gemini": probe_gemini,
    "mistral": probe_mistral,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _month_start_epoch() -> int:
    now = datetime.now(timezone.utc)
    return int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())


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


def load_config(path: Path) -> dict:
    """Read the config, writing the template on first run. Like OpenSSH, ignore
    every key_cmd when config_trust_problems finds any."""
    ensure_config(path)
    with open(path, "rb") as f:
        config = tomllib.load(f)
    unsafe = config_trust_problems(path)
    cmds = [name for name, s in config.items() if isinstance(s, dict) and "key_cmd" in s]
    if unsafe and cmds:
        print(tr("[aiu] key_cmd ignored for {names}: {reasons}",
                 names=", ".join(cmds), reasons="; ".join(unsafe)), file=sys.stderr)
        for name in cmds:
            del config[name]["key_cmd"]
    return config


def collect(config: dict, only: set[str] | None) -> list[Row]:
    rows: list[Row] = []
    for name, probe in PROBES.items():
        cfg = config.get(name, {})
        if only is not None:
            if name not in only:
                continue
        elif not cfg.get("enabled"):
            continue
        try:  # one broken provider must not take the whole run down
            got = probe(cfg)
        except Exception as e:
            got = [Row(name, "—", "error", note=f"probe failed ({type(e).__name__})")]
        for r in got:
            r.note = _redact(r.note)
        rows.extend(got)
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_table(rows: list[Row]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    t = Table(box=box.SIMPLE_HEAVY, title=tr("AI usage snapshot"), title_style="bold")
    t.add_column(tr("Provider"), style="cyan")
    t.add_column(tr("Metric"))
    t.add_column(tr("Usage"), justify="right")
    t.add_column(tr("Resets / note"), style="dim")

    for r in rows:
        if r.kind == "error":
            usage = "[red]err[/red]"
        elif r.kind == "quota" and r.used is not None:
            pct = r.used * 100
            color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
            bar = _bar(r.used)
            usage = f"[{color}]{bar} {pct:5.1f}%[/{color}]"
        elif r.kind in ("spend", "balance") and r.used is not None:
            usage = f"{r.used:,.2f} {r.unit}"
        elif r.kind == "credit" and r.used is not None:
            usage = _credit_left(r)
        else:
            usage = "—"
        tail = r.reset or ""
        if r.note:
            tail = f"{tail}  {r.note}".strip()
        t.add_row(r.provider, r.metric, usage, tail)
    console.print(t)


def _credit_left(r: Row) -> str:
    return tr("{used}/{limit} {unit} left", used=f"{r.used:,.2f}", limit=f"{r.limit:,.0f}", unit=r.unit)


def _bar(frac: float, width: int = 12) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def render_html(rows: list[Row], path: Path) -> None:
    def cell(r: Row) -> str:
        if r.kind == "quota" and r.used is not None:
            return f"{r.used*100:.1f}%"
        if r.kind in ("spend", "balance") and r.used is not None:
            return f"{r.used:,.2f} {r.unit}"
        if r.kind == "credit" and r.used is not None:
            return _credit_left(r)
        return "—" if r.kind != "error" else "err"
    # Provider text (plan names, credit keys, notes, a local server's reply) is
    # untrusted: every value is escaped, and the CSP blocks scripts regardless.
    from html import escape

    def esc(v) -> str:
        return escape(str(v), quote=True)
    lines = ["<table border=1 cellpadding=6 style='border-collapse:collapse;font-family:sans-serif'>",
             "<tr>" + "".join(f"<th>{esc(tr(h))}</th>"
                              for h in ("Provider", "Metric", "Usage", "Resets / note")) + "</tr>"]
    for r in rows:
        tail = " ".join(x for x in (r.reset or "", r.note) if x)
        lines.append(f"<tr><td>{esc(r.provider)}</td><td>{esc(r.metric)}</td>"
                     f"<td align=right>{esc(cell(r))}</td><td>{esc(tail)}</td></tr>")
    lines.append("</table>")
    path.write_text("<!doctype html><meta charset=utf-8>\n"
                    "<meta http-equiv=\"Content-Security-Policy\" "
                    "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
                    + "\n".join(lines))
    print(tr("[aiu] wrote {path}", path=path), file=sys.stderr)


# ---------------------------------------------------------------------------
# Advice / alerting — turn the snapshot into "where should I route, what's wasting"
# ---------------------------------------------------------------------------
# What each flat-fee subscription is best at (free at the margin -> route here first).
# English; advise() translates them.
PROVIDER_HINTS = {
    "claude": "deep reasoning / agentic",
    "gemini": "volume / throwaway tasks (Flash)",
    "codex": "second coding voice / diversity",
    "mistral": "Vibe / Le Chonk (1M context)",
}
# Alert thresholds for "paid-for capacity about to vanish unused".
ALERT_USED_MAX = 40.0        # only warn if a window is under this % used
ALERT_TTR_MAX = 48 * 3600    # ...and resets within this many seconds
ALERT_MIN_WINDOW = 6 * 3600  # ...and it's a long window (not a 5h burst bucket)
# One-shot credits vanish entirely at expiry, so warn earlier.
CREDIT_LEFT_MIN = 0.25       # warn if at least this fraction of the grant is unspent
CREDIT_TTR_MAX = 14 * 86400  # ...and it expires within this many seconds
ALERT_LEFTOVER_MIN = 0.05    # warn if a window is projected to lose this much of its cap at reset
ALERT_DRY_MIN = 24 * 3600    # warn if a window is projected to sit empty this long before reset


# Advice ranks by earliest-expiring SURPLUS (EDF), not raw headroom: quota is
# lost at reset, so the reset is each window's deadline. Surplus = what is
# projected to be left at reset if you keep the pace of the current period.
RATE_WINDOW_MAX = 6 * 3600   # windows this short are rate limits (5h); longer = volume budgets
PACE_MIN_FRAC = 0.10         # trust a window's pace once 10% of it has elapsed
ON_TRACK_BAND = 0.05         # projected leftover within ±5% of the cap counts as on track

# Snapshot history: the last reading of each recent volume window, so a window
# that just reset borrows the pace of the windows before it (see _project).
HISTORY_WINDOWS = 4          # windows kept per metric: the current one and 3 before it
HISTORY_DECAY = 0.5          # each older window weighs half as much in the blended pace
HISTORY_MAX_AGE = 90 * 86400  # readings older than this are dropped
HISTORY_MAX_ROWS = 256       # hard cap on the file, whatever the metrics
HISTORY_MAX_BYTES = 64 * 1024  # a bigger file is not ours: ignored, then rewritten


def _history_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if os.path.isabs(state) else Path(os.path.expanduser("~/.local/state"))
    return base / "aiu" / "history.json"


def _history_entry(e) -> dict | None:
    """A stored reading if it has the expected shape, else None."""
    if not isinstance(e, dict) or set(e) != {"p", "m", "u", "r", "w", "t"}:
        return None
    if not all(isinstance(e[k], str) and 0 < len(e[k]) <= 100 for k in ("p", "m")):
        return None
    if not all(isinstance(e[k], (int, float)) and not isinstance(e[k], bool)
               and math.isfinite(e[k]) for k in ("u", "r", "w", "t")):
        return None
    return e if 0 <= e["u"] <= 2 and e["w"] > 0 else None


def load_history() -> list[dict]:
    """Stored readings; [] when the file is missing, too big, or not ours to trust."""
    try:
        path = _history_path()
        if path.stat().st_size > HISTORY_MAX_BYTES:
            return []
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    rows = data.get("readings") if isinstance(data, dict) and data.get("v") == 1 else None
    return [e for e in map(_history_entry, rows) if e] if isinstance(rows, list) else []


def _same_window(a: dict, b: dict) -> bool:
    # Reset times jitter by seconds between reads; consecutive windows are a window apart.
    return (a["p"], a["m"]) == (b["p"], b["m"]) and abs(a["r"] - b["r"]) < a["w"] / 2


def record_history(rows: list[Row], now: float) -> list[dict]:
    """Merge this run's volume-window readings into the history file and return the
    merged history. One entry per window, overwritten by each run, so the file stays
    a few KB however often aiu runs. Only provider, metric, used fraction, reset,
    window length and time are stored: never notes. Best effort: a write that fails
    leaves the old file, and the merged history is still used for this run."""
    hist = load_history()
    fresh = [r for r in rows if r.kind == "quota" and r.used is not None and r.reset_epoch
             and (r.window_secs or 0) > RATE_WINDOW_MAX and r.reset_epoch > now]
    new = [e for r in fresh if (e := _history_entry({"p": r.provider, "m": r.metric, "u": r.used,
                                                       "r": r.reset_epoch, "w": r.window_secs,
                                                       "t": now}))]
    if not new:
        return hist
    for e in new:
        hist = [old for old in hist if not _same_window(old, e)]
        hist.append(e)
    # Prune: recent readings only, the last HISTORY_WINDOWS windows per metric.
    kept, per_metric = [], {}
    for e in sorted(hist, key=lambda e: -e["r"]):
        n = per_metric.get((e["p"], e["m"]), 0)
        if now - e["t"] <= HISTORY_MAX_AGE and n < HISTORY_WINDOWS:
            per_metric[(e["p"], e["m"])] = n + 1
            kept.append(e)
    hist = sorted(kept[:HISTORY_MAX_ROWS], key=lambda e: (e["p"], e["m"], e["r"]))
    _write_history(hist)
    return hist


def _write_history(hist: list[dict]) -> None:
    """Atomic, user-only write: a 0600 temp file in a 0700 directory, renamed over
    the old file. No lock: two runs racing lose at most one reading, which the next
    run writes again. No fsync either: a file lost in a crash is simply rebuilt."""
    import tempfile
    path = _history_path()
    tmp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = path.parent.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            return
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.chmod(path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".history.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:  # mkstemp: O_EXCL, mode 0600
            json.dump({"v": 1, "readings": hist}, f, separators=(",", ":"))
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _history_pace(r: Row, hist: list[dict] | None) -> float | None:
    """Average pace (fraction of the cap per second) of the windows before r's,
    newest first, each older one weighing HISTORY_DECAY times less; None without
    a usable reading. A reading counts once PACE_MIN_FRAC of its window had passed."""
    past = []
    for e in hist or ():
        # Same metric, same kind of window (a calendar month varies by a few days).
        if (e["p"], e["m"]) != (r.provider, r.metric) or abs(e["w"] - r.window_secs) > r.window_secs / 10:
            continue
        elapsed = e["w"] - (e["r"] - e["t"])
        if e["r"] <= r.reset_epoch - e["w"] / 2 and elapsed >= PACE_MIN_FRAC * e["w"]:
            past.append((e["r"], e["u"] / min(elapsed, e["w"])))
    if not past:
        return None
    weights = [HISTORY_DECAY ** i for i in range(len(past))]
    paces = [pace for _, pace in sorted(past, reverse=True)]
    return sum(w * pace for w, pace in zip(weights, paces)) / sum(weights)


def _dur(secs: float) -> str:
    secs = int(max(0, secs))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d{h}h" if d else f"{h}h{rem // 60:02d}m" if h else f"{rem // 60}m"


def _project(r: Row, now: float, hist: list[dict] | None = None) -> dict | None:
    """Extrapolate a quota window to its reset at the pace of its current period.
    Under PACE_MIN_FRAC elapsed that pace is noise, so the history fills in: the
    first PACE_MIN_FRAC of the window is assumed to hold what is used so far plus
    the past windows' pace for the rest of it. That blend slides from the history
    alone at the reset to the window's own pace at PACE_MIN_FRAC."""
    if r.used is None or not r.reset_epoch or not r.window_secs:
        return None
    ttr = r.reset_epoch - now
    elapsed = r.window_secs - ttr
    if ttr <= 0:
        return None
    from_history = elapsed < PACE_MIN_FRAC * r.window_secs
    if from_history:
        past = _history_pace(r, hist)
        if past is None:
            return None
        head = PACE_MIN_FRAC * r.window_secs
        pace = (r.used + past * (head - max(elapsed, 0))) / head
    else:
        pace = r.used / elapsed                # fraction of the cap per second
    left = 1.0 - r.used
    return {"r": r, "ttr": ttr, "pace": pace, "left": left, "history": from_history,
            "leftover": left - pace * ttr,     # > 0: expires unused ; < 0: runs dry first
            "runway": left / pace if pace else float("inf")}


def _lots(rows: list[Row], now: float, hist: list[dict] | None = None) -> list[dict]:
    """One routing lot per subscription (its volume windows) and per promo credit.
    status: burn (surplus expires unused) | ? (pace unknown) | on_track | conserve (runs dry).
    hist: past readings (load_history), for windows too young to have their own pace."""
    lots, quotas = [], {}
    for r in rows:
        if r.kind == "quota" and r.used is not None and r.metric != "Extra Usage":
            quotas.setdefault(r.provider, []).append(r)   # Extra Usage = paid overage
        elif (r.kind == "credit" and r.used and r.reset_epoch and r.reset_epoch > now
              and "locked" not in r.note):
            # Spent only by sessions you choose to run: no committed load, all surplus.
            ttr = r.reset_epoch - now
            lots.append({"prov": r.provider, "status": "burn", "ttr": ttr, "metric": r.metric,
                         "what": tr("{metric} {used}/{limit} {unit}, expires in {ttr} "
                                    "(≈{per_day} {unit}/day to use it all)",
                                    metric=r.metric, used=f"{r.used:,.2f}", limit=f"{r.limit:,.0f}",
                                    unit=r.unit, ttr=_dur(ttr), per_day=f"{r.used / ttr * 86400:,.2f}")})
    for prov, rs in quotas.items():
        vol = [r for r in rs if (r.window_secs or 0) > RATE_WINDOW_MAX] or rs
        projs = [_project(r, now, hist) for r in vol]
        known = [p for p in projs if p]
        dry = [p for p in known if p["leftover"] < -ON_TRACK_BAND]
        if dry:
            p = min(dry, key=lambda p: p["runway"])
            lot = {"status": "conserve", "ttr": p["ttr"], "dry": p["ttr"] - p["runway"],
                   "metric": p["r"].metric, "leftover": p["leftover"], "runway": p["runway"],
                   "what": tr("{metric} {left}% free: runs dry in ~{runway}, reset in {ttr} "
                              "→ ~{gap} without {provider}",
                              metric=p["r"].metric, left=f"{p['left'] * 100:.0f}",
                              runway=_dur(p["runway"]), ttr=_dur(p["ttr"]),
                              gap=_dur(p["ttr"] - p["runway"]), provider=prov)}
        elif len(known) < len(projs):
            # No usable pace: fall back to plain EDF and say so.
            r = min((r for r in vol if r.reset_epoch), key=lambda r: r.reset_epoch, default=vol[0])
            ttr = r.reset_epoch - now if r.reset_epoch else float("inf")
            free = f"{100 * (1 - r.used):.0f}"
            lot = {"status": "?", "ttr": ttr, "metric": r.metric,
                   "what": tr("{metric} {left}% free, reset in {ttr} (unknown pace)",
                              metric=r.metric, left=free, ttr=_dur(ttr)) if r.reset_epoch else
                           tr("{metric} {left}% free, reset unknown (unknown pace)",
                              metric=r.metric, left=free)}
        else:
            p = min(known, key=lambda p: p["ttr"])
            if all(q["leftover"] > ON_TRACK_BAND for q in known):
                need = p["left"] / p["ttr"]
                lot = {"status": "burn", "ttr": p["ttr"], "metric": p["r"].metric,
                       "leftover": p["leftover"],
                       "what": tr("{metric}: reset in {ttr}, ~{leftover}% of the cap lost at your pace "
                                  "({pace}%/h; using it all = {need}%/h, ×{ratio})",
                                  metric=p["r"].metric, ttr=_dur(p["ttr"]),
                                  leftover=f"{p['leftover'] * 100:.0f}", pace=f"{p['pace'] * 360000:.2f}",
                                  need=f"{need * 360000:.2f}",
                                  ratio=f"{need / p['pace']:.1f}" if p["pace"] else "∞")}
            else:
                lot = {"status": "on_track", "ttr": p["ttr"], "metric": p["r"].metric,
                       "leftover": p["leftover"],
                       "what": tr("{metric} {left}% free, reset in {ttr} — on track",
                                  metric=p["r"].metric, left=f"{p['left'] * 100:.0f}",
                                  ttr=_dur(p["ttr"]))}
        lot["history"] = lot["status"] != "?" and any(q["history"] for q in known)
        if lot["history"]:
            lot["what"] += tr(" (pace from past windows)")
        # Short windows only gate availability; flag them when they'd run dry first.
        for r in rs:
            if r in vol:
                continue
            p = _project(r, now)
            if p and p["leftover"] < 0:
                lot["what"] += tr("; {metric} runs out in ~{runway}",
                                  metric=r.metric, runway=_dur(p["runway"]))
        lot["prov"] = prov
        lots.append(lot)
    return lots


def route_order(rows: list[Row], now: float | None = None,
                hist: list[dict] | None = None) -> list[dict]:
    """Public advice for --json / consult: lots in routing order (burn & unknown by
    deadline, then on-track, then conserve by how long they'd stay dry)."""
    now = time.time() if now is None else now
    lots = _lots(rows, now, hist)
    rank = {"burn": 0, "?": 0, "on_track": 1, "conserve": 2}
    lots.sort(key=lambda l: (rank[l["status"]],
                             -l["dry"] if l["status"] == "conserve" else l["ttr"]))
    return [{"provider": l["prov"], "metric": l["metric"], "status": l["status"],
             "ttr_secs": None if l["ttr"] == float("inf") else round(l["ttr"]),
             # projected fraction of the cap left at reset (<0: short by that much)
             "leftover": round(l["leftover"], 3) if "leftover" in l else None,
             "runway_secs": round(l["runway"]) if "runway" in l else None,
             # the pace of a window that just reset, borrowed from the ones before it
             "pace_from_history": l.get("history", False),
             "detail": l["what"]} for l in lots]


def advise(rows: list[Row], now: float | None = None, hist: list[dict] | None = None) -> int:
    from rich.console import Console
    console = Console()
    now = time.time() if now is None else now
    lots = _lots(rows, now, hist)
    order = sorted((l for l in lots if l["status"] in ("burn", "?")), key=lambda l: l["ttr"])
    steady = sorted((l for l in lots if l["status"] == "on_track"), key=lambda l: l["ttr"])
    scarce = sorted((l for l in lots if l["status"] == "conserve"), key=lambda l: -l["dry"])
    console.print(tr("\n[bold]Routing advice[/bold] — earliest-expiring capacity first "
                     "(surplus projected at your current pace):\n"))
    for i, l in enumerate(order, 1):
        color = "green" if l["status"] == "burn" else "yellow"
        hint = tr(PROVIDER_HINTS[l["prov"]]) if l["prov"] in PROVIDER_HINTS else ""
        console.print(f"  {i}. [{color}]{l['prov']:<12}[/{color}] {l['what']}   [dim]{hint}[/dim]")
    if steady:
        console.print(tr("\n  [bold]On track (no surplus to give):[/bold]"))
        for l in steady:
            console.print(f"     {l['prov']:<12} {l['what']}")
    if scarce:
        console.print(tr("\n  [bold red]Conserve[/bold red] (runs dry before the reset — keep it for "
                         "what only it can do):"))
        for l in scarce:
            console.print(f"     [red]{l['prov']:<12}[/red] {l['what']}")
    # Lifetime spend is sunk, not capacity: only list what can still absorb overflow.
    paid = [r for r in rows if r.kind == "balance" and r.used is not None
            or r.kind == "spend" and r.used is not None and "total" not in r.metric.lower()]
    if paid:
        console.print(tr("\n  [dim]Paid (overflow only; currencies not added up):[/dim]"))
        for r in paid:
            console.print(f"     [dim]{r.provider:<12} {r.used:,.2f} {r.unit}  ({r.metric})[/dim]")
    for r in rows:
        if r.kind == "error":
            console.print(f"  [red]{r.provider}: {r.note}[/red]")
    keep = list(dict.fromkeys(l["prov"] for l in scarce))
    names = ", ".join(keep)
    if order:
        first = order[0]
        if first["status"] == "?":
            msg = tr("\n  [bold green]→ Burn {provider} first[/bold green] (expires in {ttr}, unknown pace): "
                     "send it everything it can do", provider=first["prov"], ttr=_dur(first["ttr"]))
        else:
            msg = tr("\n  [bold green]→ Burn {provider} first[/bold green] (expires in {ttr}): "
                     "send it everything it can do", provider=first["prov"], ttr=_dur(first["ttr"]))
        msg += tr(", including what you would give {names}.", names=names) if names else "."
        if names:
            msg += (tr(" Conserve {names} for what only it can do.", names=names) if len(keep) == 1
                    else tr(" Conserve {names} for what only they can do.", names=names))
        console.print(msg + "\n")
    elif steady:
        console.print(tr("\n  [bold]→ No expiring surplus[/bold]: route by task fit to {providers} (on track)",
                         providers=", ".join(dict.fromkeys(l["prov"] for l in steady)))
                      + (tr(", conserve {names}.", names=names) if names else ".") + "\n")
    else:
        console.print(tr("\n  [bold yellow]→ No subscription has surplus[/bold yellow]: keep each for what "
                         "only it can do; overflow the rest to the cheapest paid API.\n"))
    return 0


# Desktop notifications go through GNOME's org.gtk.Notifications (what GNotification
# uses): one app source, one notification id that each run replaces in place, and
# that an all-clear run removes. Plain notify-send (the fallback) opens a new source
# per process, so its notifications pile up and can't be withdrawn.
APP_ID = "io.github.citizenu03bb.aiu"
NOTIFICATION_ID = "alert"
NOTIFY_RETRIES = 16  # x 0.5s, only while the .desktop file is brand new
DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name=aiu
Comment=AI subscription quotas and routing advice
Exec=sh -c "aiu --advise; read -r _"
Terminal=true
Icon=utilities-system-monitor-symbolic
NoDisplay=true
X-GNOME-UsesNotifications=true
"""


def _desktop_entry_path() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(data) / "applications" / f"{APP_ID}.desktop"


def _gvariant_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _gtk_notifications(method: str, *args: str) -> bool:
    """Call org.gtk.Notifications; False when unavailable (not GNOME, unknown app...)."""
    if not shutil.which("gdbus"):
        return False
    try:
        p = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gtk.Notifications",
                            "--object-path", "/org/gtk/Notifications",
                            "--method", f"org.gtk.Notifications.{method}", APP_ID, *args],
                           capture_output=True, text=True, timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def ensure_desktop_entry() -> None:
    """GNOME only accepts notifications from an app it knows, and takes a few seconds
    to index a new .desktop file, so main() writes it early on every run."""
    entry = _desktop_entry_path()
    if entry.is_file():
        return
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(DESKTOP_ENTRY)
    except OSError:
        pass


def _notify(title: str, body: str) -> None:
    """Show (or update in place) aiu's single desktop notification."""
    ensure_desktop_entry()
    entry = _desktop_entry_path()
    try:
        fresh = time.time() - entry.stat().st_mtime < 15
    except OSError:
        fresh = False
    payload = (f"{{'title': <{_gvariant_str(title)}>, 'body': <{_gvariant_str(body)}>, "
               f"'priority': <'normal'>}}")
    # A just-written .desktop file takes GNOME a moment to index: retry briefly.
    for attempt in range(NOTIFY_RETRIES if fresh else 1):
        if attempt:
            time.sleep(0.5)
        if _gtk_notifications("AddNotification", NOTIFICATION_ID, payload):
            return
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-a", "aiu", title, body], timeout=5)
        except Exception:
            pass


def _withdraw_notification() -> None:
    """Remove a previous alert once nothing is wrong any more (no-op if none)."""
    if _desktop_entry_path().is_file():
        _gtk_notifications("RemoveNotification", NOTIFICATION_ID)


def alert(rows: list[Row], notify: bool = True, hist: list[dict] | None = None) -> int:
    """Warn about long-window quota projected to reset unused or to run dry long
    before its reset, and about expiring credits. Quiet-friendly for cron: prints only warnings (and a single OK line otherwise).
    notify=False keeps it off the desktop (tests, synthetic rows)."""
    now = time.time()
    warns = []
    for r in rows:
        if r.kind == "credit" and r.used is not None and r.limit and r.reset_epoch:
            ttr = r.reset_epoch - now
            if "locked" not in r.note and r.used / r.limit >= CREDIT_LEFT_MIN and 0 < ttr <= CREDIT_TTR_MAX:
                warns.append(tr("⚠ {provider} · {metric}: {left} {unit} unspent, {reset}",
                                provider=r.provider, metric=r.metric, left=f"{r.used:,.2f}",
                                unit=r.unit, reset=r.reset))
            continue
        if r.kind != "quota" or r.used is None or not r.reset_epoch:
            continue
        ttr = r.reset_epoch - now
        used_pct = r.used * 100
        long_window = (r.window_secs or 0) >= ALERT_MIN_WINDOW or \
                      (r.window_secs is None and ttr >= ALERT_MIN_WINDOW)
        if not long_window or ttr <= 0:
            continue
        p = _project(r, now, hist)  # None while the pace is unknown: keep the level rule
        waste = p["leftover"] if p else (1 - r.used) * (used_pct < ALERT_USED_MAX)
        if waste >= ALERT_LEFTOVER_MIN and ttr <= ALERT_TTR_MAX:
            warns.append(tr("⚠ {provider} · {metric}: ~{waste}% of the cap lost at reset, reset {reset}",
                            provider=r.provider, metric=r.metric, waste=f"{waste * 100:.0f}",
                            reset=r.reset))
        elif p and p["leftover"] < 0 and ttr - p["runway"] >= ALERT_DRY_MIN:
            warns.append(tr("⚠ {provider} · {metric}: runs dry in ~{runway} at your pace, reset {reset} "
                            "→ ~{gap} without {provider}",
                            provider=r.provider, metric=r.metric, runway=_dur(p["runway"]),
                            reset=r.reset, gap=_dur(ttr - p["runway"])))
    # Missing data is not an all-clear.
    warns += [tr("⚠ {provider}: data unavailable ({note})", provider=r.provider, note=r.note)
              for r in rows if r.kind == "error"]
    if warns:
        for w in warns:
            print(w)
        # The title carries the snapshot time, so the notification can't pass for
        # current data even if it sits in the tray.
        if notify:
            taken = datetime.fromtimestamp(now).strftime("%H:%M")
            _notify(tr("AI quota · snapshot {time}", time=taken), "\n".join(warns))
        return 1
    if notify:
        _withdraw_notification()
    print(tr("✓ nothing will be lost at reset or run dry for long"))
    return 0


# ---------------------------------------------------------------------------
# aiu config: read and change config.toml from scripts and the GNOME
# preferences window, so that aiu stays the only program that parses or writes
# it. Values only: never a resolved key, nothing from ~/.env, no key_cmd run.
# Edits are line-level, so comments, blank lines, order and unknown keys stay.
# ---------------------------------------------------------------------------
_KEY_SOURCES = {"key_env": ("string", None), "key_cmd": ("string", None),
                "key_file": ("string", None)}
# provider -> option -> (type, default when the file does not set it)
CONFIG_OPTIONS = {
    "claude": {"enabled": ("bool", False), "claude_cmd": ("string", None)},
    "codex": {"enabled": ("bool", False)},
    "openai": {"enabled": ("bool", False), **_KEY_SOURCES},
    "deepseek": {"enabled": ("bool", False), **_KEY_SOURCES},
    "openrouter": {"enabled": ("bool", False), **_KEY_SOURCES},
    "gemini": {"enabled": ("bool", False)},
    "mistral": {"enabled": ("bool", False), **_KEY_SOURCES,
                "key_env": ("string", "MISTRAL_ADMIN_COOKIE"),
                "vibe_allowance_eur": ("number", MISTRAL_ALLOWANCE_EUR),
                "api_allowance_eur": ("number", MISTRAL_ALLOWANCE_EUR)},
}
# Options that make aiu run a program: only set in a config that passes the
# trust check (an empty value, which turns them off, is always allowed).
CONFIG_RUNS_CODE = ("key_cmd", "claude_cmd")
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
    if value and name in CONFIG_RUNS_CODE and (problems := config_trust_problems(path)):
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


def config_snapshot(path: Path) -> dict:
    """The config as `aiu config show --json` prints it: each provider's options
    with their value and whether the file sets it, and whether key_cmd may run."""
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
        print(tr("key_cmd is ignored: {reasons}", reasons="; ".join(snap["key_cmd_problems"])))
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
    elif args.config_json:
        print(json.dumps(snap, indent=2, ensure_ascii=False, default=str))
    else:
        render_config(snap)
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="One snapshot of every AI subscription you pay for.")
    ap.add_argument("--version", action="version", version=f"aiu {__version__}")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--only", help="comma-separated provider subset (ignores enabled flags)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--html", metavar="PATH", help="write an HTML page")
    ap.add_argument("--advise", action="store_true",
                    help="recommend where to route: earliest-expiring surplus first")
    ap.add_argument("--alert", action="store_true",
                    help="warn about quota lost at reset / running dry, expiring credits (cron-friendly)")
    # Without a subcommand, aiu takes its snapshot as it always has.
    sub = ap.add_subparsers(dest="command", metavar="{config}")
    cp = sub.add_parser("config", help="show or change the config (for scripts and the GNOME extension)")
    cp.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    csub = cp.add_subparsers(dest="config_action", required=True)
    show = csub.add_parser("show", help="every provider's settings, and whether the file sets them")
    show.add_argument("--json", dest="config_json", action="store_true", help="emit JSON")
    cset = csub.add_parser("set", help="change one setting, e.g. `aiu config set openai.enabled true`")
    cset.add_argument("option", help="<provider>.<option>")
    cset.add_argument("value")
    args = ap.parse_args()
    if args.command == "config":
        return config_main(args)

    config = load_config(Path(os.path.expanduser(args.config)))
    ENV_FILE.update(load_env_file(config))
    if args.alert:
        ensure_desktop_entry()  # before probing, so GNOME has indexed it by notify time
    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    rows = collect(config, only)
    # Every run records, --only included: entries are per metric, so a filtered
    # run never drops the others' history.
    hist = record_history(rows, time.time())

    if args.json:
        print(json.dumps({"captured_at": datetime.now(timezone.utc).isoformat(),
                          "rows": [r.to_public() for r in rows],
                          "advice": route_order(rows, hist=hist)}, indent=2))
    if args.html:
        render_html(rows, Path(os.path.expanduser(args.html)))
    if args.advise:
        return advise(rows, hist=hist)
    if args.alert:
        return alert(rows, hist=hist)
    if not args.json and not args.html:
        render_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
