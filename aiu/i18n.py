# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface language: English, or French when AIU_LANG (else the locale) says
so. Messages are written in English and looked up in FR by their exact text.
Probe notes, metric names and --json keys stay English: they are data, and
some (a credit's "locked: ..." note) are matched on."""
from __future__ import annotations

import os


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
    " (early, fast pace)": " (début de fenêtre, allure rapide)",
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
    "aiu now reads Claude Code's sign-in token (~/.claude/.credentials.json) and sends it to "
    "Anthropic's usage endpoint, as the Claude apps do. Anthropic's terms say: \"developers may "
    "not collect, store, or intermediate Claude.ai credentials or session tokens\" "
    "(code.claude.com/docs/en/legal-and-compliance). The aiu-claude-usage plugin for Claude Code "
    "needs no token: /plugin install aiu-claude-usage --marketplace citizenu03bb/aiu. "
    "To turn this off: aiu config set claude.read_token false":
        "aiu lit désormais le jeton de connexion de Claude Code (~/.claude/.credentials.json) et "
        "l'envoie au point d'accès d'usage d'Anthropic, comme les applications Claude. Les "
        "conditions d'Anthropic disent : « developers may not collect, store, or intermediate "
        "Claude.ai credentials or session tokens » (code.claude.com/docs/en/legal-and-compliance). "
        "Le plugin aiu-claude-usage pour Claude Code n'a besoin d'aucun jeton : "
        "/plugin install aiu-claude-usage --marketplace citizenu03bb/aiu. "
        "Pour le désactiver : aiu config set claude.read_token false",
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
    # aiu cookie import
    "aiu cookie: {error}": "aiu cookie : {error}",
    "secret-tool not found: install it (Debian/Ubuntu: sudo apt install libsecret-tools)":
        "secret-tool introuvable : installe-le (Debian/Ubuntu : sudo apt install libsecret-tools)",
    "the keyring did not store the cookie (is it locked?); nothing was changed":
        "le trousseau n'a pas enregistré le cookie (est-il verrouillé ?) ; rien n'a été modifié",
    "cannot read the cookie database {path}": "impossible de lire la base de cookies {path}",
    "no {browser} profile directory ({dirs}): is {browser} installed, and has it been opened once?":
        "aucun dossier de profils {browser} ({dirs}) : {browser} est-il installé, "
        "et a-t-il été ouvert au moins une fois ?",
    "no {browser} profile named {profile}; profiles: {found}":
        "aucun profil {browser} nommé {profile} ; profils : {found}",
    "{browser} profile {profile} has no cookies yet: sign in at {url} in it, then run this again":
        "le profil {browser} {profile} n'a pas encore de cookies : connecte-toi à {url} "
        "dans ce profil, puis relance cette commande",
    "no {browser} Safe Storage secret in the keyring: unlock the keyring (log in to the desktop) "
    "and start {browser} once, then run this again":
        "aucun secret Safe Storage de {browser} dans le trousseau : déverrouille le trousseau "
        "(ouvre ta session de bureau) et lance {browser} une fois, puis relance cette commande",
    "decrypting {browser}'s cookies needs the cryptography package: pipx inject aiu cryptography "
    "(or pip install 'aiu[cookies]'), or run ./aiu.py with uv run --with cryptography":
        "déchiffrer les cookies de {browser} demande le paquet cryptography : "
        "pipx inject aiu cryptography (ou pip install 'aiu[cookies]'), "
        "ou lance ./aiu.py avec uv run --with cryptography",
    "{browser} encrypts its cookies in a way aiu does not know ({version})":
        "{browser} chiffre ses cookies d'une façon qu'aiu ne connaît pas ({version})",
    "{browser}'s cookies do not decrypt with its Safe Storage secret from this keyring "
    "(a profile copied from another account?)":
        "les cookies de {browser} ne se déchiffrent pas avec son secret Safe Storage de ce "
        "trousseau (un profil copié depuis un autre compte ?)",
    "no default Firefox profile in {dirs}: is Firefox installed, and has it been opened once? "
    "Else name one with --profile":
        "aucun profil Firefox par défaut dans {dirs} : Firefox est-il installé, et a-t-il été "
        "ouvert au moins une fois ? Sinon, désigne un profil avec --profile",
    "no {host} cookie in {browser} profile {profile}: sign in at {url} in it, then run this again":
        "aucun cookie {host} dans le profil {browser} {profile} : connecte-toi à {url} "
        "dans ce profil, puis relance cette commande",
    "the {host} session in {browser} profile {profile} has expired: sign in again at {url}, "
    "then run this again":
        "la session {host} du profil {browser} {profile} a expiré : reconnecte-toi à {url}, "
        "puis relance cette commande",
    "a {host} cookie holds characters a Cookie header cannot carry; nothing was stored":
        "un cookie {host} contient des caractères qu'un en-tête Cookie ne peut pas porter ; "
        "rien n'a été enregistré",
    "Stored the {host} session from {browser} ({profile}) in the keyring, labelled '{label}'.":
        "Session {host} de {browser} ({profile}) enregistrée dans le trousseau, "
        "sous le libellé '{label}'.",
    "aiu cookie: mistral.key_cmd not set ({error}). Once that is fixed, run:":
        "aiu cookie : mistral.key_cmd non défini ({error}). Une fois corrigé, lance :",
    "your environment": "ton environnement",
    "{name} is set in {where}, and aiu reads it before key_cmd: delete it there (it is the "
    "session in plain text) so the keyring copy is used.":
        "{name} est défini dans {where}, et aiu le lit avant key_cmd : supprime-le de là "
        "(c'est la session en clair) pour que la copie du trousseau serve.",
    "mistral is off: aiu config set mistral.enabled true":
        "mistral est désactivé : aiu config set mistral.enabled true",
    "Check it with: aiu --only mistral": "Vérifie avec : aiu --only mistral",
}
