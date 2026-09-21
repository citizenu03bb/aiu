#!/usr/bin/env python3
"""consult — delegate ONE self-contained sub-task to another AI subscription/CLI.

Usage:
  consult <gemini|codex|deepseek|auto> "<prompt>"
  consult <provider> -            # read the prompt from stdin
  echo "..." | consult auto

Prints ONLY the model's answer to stdout (chosen provider goes to stderr).

For Claude Code (or you) to offload independent / parallel / second-opinion
sub-tasks onto idle flat-fee subscriptions (Gemini via `agy`, ChatGPT via
`codex`) instead of spending its own Claude quota or a paid API.

Route by TASK FIT, not idle quota. `auto` only breaks ties toward the freest
subscription (read live from `aiu --json`); it falls back to DeepSeek (paid) if
no subscription has headroom.
"""
import json
import subprocess
import sys

TIMEOUT = 300
SUBS = ("gemini", "codex")  # flat-fee subs reachable here (claude_max = the caller)


def _run(cmd: list[str]):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
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
    return _run(["codex", "exec", "--skip-git-repo-check", prompt])


def call_deepseek(prompt: str):
    return _run(["pi", "--print", "--provider", "deepseek", "--model", "deepseek-v4-pro",
                 "--no-tools", "--no-skills", "--no-context-files", prompt])


CALLERS = {"gemini": call_gemini, "codex": call_codex, "deepseek": call_deepseek}


def pick_auto() -> str:
    """Pick the subscription with the most headroom; fall back to deepseek (paid)."""
    try:
        p = subprocess.run(["aiu", "--json"], capture_output=True, text=True, timeout=30)
        rows = json.loads(p.stdout).get("rows", [])
    except Exception:
        return "deepseek"
    tight: dict[str, float] = {}
    for r in rows:
        if r.get("kind") == "quota" and r.get("provider") in SUBS and r.get("used") is not None:
            h = 100 - r["used"] * 100
            tight[r["provider"]] = min(tight.get(r["provider"], 101.0), h)
    if tight:
        best = max(tight, key=tight.get)
        if tight[best] > 10:
            return best
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
