#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""consult — delegate ONE self-contained sub-task to another AI subscription/CLI.

Usage:
  consult <gemini|codex|deepseek|auto> "<prompt>"
  consult <provider> -            # read the prompt from stdin
  echo "..." | consult auto

Prints ONLY the model's answer to stdout (chosen provider goes to stderr).

For Claude Code (or you) to offload independent / parallel / second-opinion
sub-tasks onto idle flat-fee subscriptions (Gemini via `agy`, ChatGPT via
`codex`) instead of spending its own Claude quota or a paid API.

Route by TASK FIT, not idle quota. `auto` only breaks ties toward the
subscription whose spare quota expires first (`advice` from `aiu --json`); it
falls back to DeepSeek (paid) when every reachable subscription is projected to
run dry before its reset.

The prompt goes to `codex` and `pi` on stdin, so it is neither visible in
/proc/<pid>/cmdline nor parsed as an option nor capped at 128 KiB. `agy` has no
documented way to read a prompt from stdin (its print mode is known to misbehave
with a piped stdin), so it still gets it as the value of `-p`.
The delegated CLIs run with their own sandbox and approval defaults: consult
does not restrict them, except `pi`'s `--no-tools`.
"""
import json
import subprocess
import sys

TIMEOUT = 300
SUBS = ("gemini", "codex")  # flat-fee subs reachable here (claude = the caller)


def _run(cmd: list[str], stdin: str | None = None):
    """Run cmd, feeding it stdin (or /dev/null: a CLI waiting on our terminal
    would hang until the timeout)."""
    try:
        p = subprocess.run(cmd, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {TIMEOUT}s"
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"


def call_gemini(prompt: str):
    # agy print mode is clean + non-interactive (no trust/permission flag needed)
    return _run(["agy", "--print-timeout", "5m", "-p", prompt])


def call_codex(prompt: str):
    # NB: codex exec tends to echo its answer twice — dedup when reading.
    # `-` reads the prompt from stdin.
    return _run(["codex", "exec", "--skip-git-repo-check", "-"], stdin=prompt)


def call_deepseek(prompt: str):
    return _run(["pi", "--print", "--provider", "deepseek", "--model", "deepseek-v4-pro",
                 "--no-tools", "--no-skills", "--no-context-files"], stdin=prompt)


CALLERS = {"gemini": call_gemini, "codex": call_codex, "deepseek": call_deepseek}


def pick_auto() -> str:
    """Pick the subscription whose surplus expires first (aiu's "advice" order);
    fall back to deepseek (paid) when every reachable sub must be conserved."""
    try:
        p = subprocess.run(["aiu", "--json"], capture_output=True, text=True, timeout=30)
        advice = json.loads(p.stdout).get("advice", [])
    except Exception:
        return "deepseek"
    for a in advice:  # burn / unknown pace by deadline, then on track, then conserve
        if a["status"] == "conserve":
            break
        if a["provider"] == "claude" and a["status"] == "burn" and a is advice[0]:
            print(f"[consult] note: claude's own surplus expires first "
                  f"({a['detail']}) — doing it yourself may be better", file=sys.stderr)
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
        print("consult: empty prompt", file=sys.stderr)
        return 2
    if provider == "auto":
        provider = pick_auto()
        print(f"[consult] → {provider}", file=sys.stderr)
    fn = CALLERS.get(provider)
    if not fn:
        print(f"consult: unknown provider {provider!r} "
              "(gemini|codex|deepseek|auto)", file=sys.stderr)
        return 2
    rc, out, err = fn(prompt)
    if out:
        print(out)
    elif rc != 0:
        print(f"[consult] {provider} failed (rc={rc}): {err[:300]}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
