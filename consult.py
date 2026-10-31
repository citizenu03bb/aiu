#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""consult — delegate ONE self-contained sub-task to another AI subscription/CLI.

Usage:
  consult <codex|deepseek|auto> "<prompt>"
  consult <provider> -            # read the prompt from stdin
  echo "..." | consult auto

Prints ONLY the model's answer to stdout (chosen provider goes to stderr).

For Claude Code (or you) to offload independent / parallel / second-opinion
sub-tasks onto an idle flat-fee subscription (ChatGPT via `codex`) instead of
spending its own Claude quota or a paid API. Gemini is not offered: Antigravity's
terms (section 6) call accessing it through third-party tools a breach, and
replies attributed to Google staff apply that to running `agy` from an agent.

Route by TASK FIT, not idle quota. `auto` reads `advice` from `aiu --json` and
picks codex, unless codex is missing from it or projected to run dry before its
reset: then it falls back to DeepSeek (paid).

The prompt goes to `codex` and `pi` on stdin, so it is neither visible in
/proc/<pid>/cmdline nor parsed as an option nor capped at 128 KiB.
The delegated CLIs run with their own sandbox and approval defaults: consult
does not restrict them, except `pi`'s `--no-tools`.

Messages are in French when AIU_LANG (else LC_ALL, LC_MESSAGES or LANG) is fr.
"""
import json
import os
import subprocess
import sys

TIMEOUT = 300
SUBS = ("codex",)  # flat-fee subs reachable here (claude = the caller)

# Same language choice as aiu's ui_lang(); kept here so consult needs only the stdlib.
FR = {
    "timeout after {secs}s": "délai dépassé après {secs} s",
    "command not found: {cmd}": "commande introuvable : {cmd}",
    "[consult] note: claude's own surplus expires first ({detail}) — doing it yourself may be better":
        "[consult] note : le surplus de claude lui-même expire en premier ({detail}) — "
        "le faire toi-même vaut peut-être mieux",
    "consult: empty prompt": "consult : prompt vide",
    "consult: unknown provider {provider} (codex|deepseek|auto)":
        "consult : fournisseur inconnu {provider} (codex|deepseek|auto)",
    "[consult] {provider} failed (rc={rc}): {err}": "[consult] échec de {provider} (rc={rc}) : {err}",
}


def tr(msg: str, **fields) -> str:
    """msg in French when AIU_LANG, else the locale, says so; {fields} filled in."""
    for var in ("AIU_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        if v := os.environ.get(var):
            if v.lower().startswith("fr"):
                msg = FR.get(msg, msg)
            break
    return msg.format(**fields) if fields else msg


def _run(cmd: list[str], stdin: str | None = None):
    """Run cmd, feeding it stdin (or /dev/null: a CLI waiting on our terminal
    would hang until the timeout)."""
    try:
        p = subprocess.run(cmd, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", tr("timeout after {secs}s", secs=TIMEOUT)
    except FileNotFoundError:
        return 127, "", tr("command not found: {cmd}", cmd=cmd[0])


def call_codex(prompt: str):
    # NB: codex exec tends to echo its answer twice — dedup when reading.
    # `-` reads the prompt from stdin.
    return _run(["codex", "exec", "--skip-git-repo-check", "-"], stdin=prompt)


def call_deepseek(prompt: str):
    return _run(["pi", "--print", "--provider", "deepseek", "--model", "deepseek-v4-pro",
                 "--no-tools", "--no-skills", "--no-context-files"], stdin=prompt)


CALLERS = {"codex": call_codex, "deepseek": call_deepseek}


def pick_auto() -> str:
    """Pick codex when aiu's "advice" puts it ahead of the first conserve window;
    fall back to deepseek (paid) otherwise."""
    try:
        p = subprocess.run(["aiu", "--json"], capture_output=True, text=True, timeout=30)
        advice = json.loads(p.stdout).get("advice", [])
    except Exception:
        return "deepseek"
    for a in advice:  # burn / unknown pace by deadline, then on track, then conserve
        if a["status"] == "conserve":
            break
        if a["provider"] == "claude" and a["status"] == "burn" and a is advice[0]:
            print(tr("[consult] note: claude's own surplus expires first ({detail}) "
                     "— doing it yourself may be better", detail=a["detail"]), file=sys.stderr)
        if a["provider"] in SUBS:
            return a["provider"]
    return "deepseek"


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    provider = args[0]
    rest = args[1:]
    prompt = sys.stdin.read() if (not rest or rest == ["-"]) else " ".join(rest)
    if not prompt.strip():
        print(tr("consult: empty prompt"), file=sys.stderr)
        return 2
    if provider == "auto":
        provider = pick_auto()
        print(f"[consult] → {provider}", file=sys.stderr)
    fn = CALLERS.get(provider)
    if not fn:
        print(tr("consult: unknown provider {provider} (codex|deepseek|auto)",
                 provider=repr(provider)), file=sys.stderr)
        return 2
    rc, out, err = fn(prompt)
    if out:
        print(out)
    elif rc != 0:
        print(tr("[consult] {provider} failed (rc={rc}): {err}", provider=provider, rc=rc, err=err[:300]),
              file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
