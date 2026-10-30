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
                 server while it runs (nothing else: open Antigravity to read it).
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

The code is the aiu/ package next to this script, which runs aiu.cli.main;
an installed `aiu` command runs the same function.
"""
import os
import sys

# Import the aiu/ package next to this file, whatever the current directory and
# whether or not another aiu is installed. A package wins over a module of the
# same name in one directory, so `import aiu` is aiu/, never this file.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from aiu.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
