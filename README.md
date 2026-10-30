# aiu — one snapshot of every AI subscription you pay for

A single local poller. No daemon, no telemetry, keys never stored in the tool.
Run `aiu` and you get one table: Claude Max session quotas + month-to-date API
spend across your other providers.

```
aiu                       # pretty table
aiu --advise              # where to route: earliest-expiring surplus first
aiu --alert               # warn (+ desktop notify) on quota lost at reset / running dry
aiu --json                # machine-readable
aiu --html /tmp/u.html    # local HTML page
aiu --only claude         # subset, ignores enabled flags
watch -n 60 aiu           # poor man's live view
```

## Install
Needs Python ≥ 3.11. With [`pipx`](https://pipx.pypa.io/) (or
`uv tool install` with the same URL), which provides both `aiu` and `consult`:

```bash
pipx install git+https://github.com/citizenu03bb/aiu
```

Or run straight from a clone with [`uv`](https://docs.astral.sh/uv/): the script
declares its own dependencies.

```bash
git clone https://github.com/citizenu03bb/aiu.git
ln -s "$PWD/aiu/aiu.py" ~/.local/bin/aiu
ln -s "$PWD/aiu/consult.py" ~/.local/bin/consult   # optional, see consult.py
```

The repository also ships a **GNOME Shell panel indicator** that shows the
same snapshot and routing advice: see [gnome-extension/](gnome-extension/README.md).

## Strategy: maximize the flat-fee subscriptions
Claude Max, ChatGPT/Codex, and Google One (Gemini) are **flat fees** — marginal
use is free and unused quota **resets to waste**. DeepSeek/OpenRouter are
**pay-per-token**. So route agentic/CLI work onto the subscriptions first (by
task fit, not raw headroom), spill to paid APIs only when a window is tapped.
`aiu --advise` projects each weekly window to its reset at your current pace
(pace = used ÷ time elapsed in the window). A window that will reset with quota
left is **burn**; one that will run dry before its reset is **conserve**. It
tells you to send flexible work to the burn windows (and promo credits) that
expire **first**, and to keep conserve subscriptions for what only they can do.
Remaining headroom alone is not the signal: 16% left that resets in 6h is worth
using now, 30% left that must last 5 days is not. Earliest-deadline-first is
optimal for work any subscription can do (why this rule:
[docs/routing-algorithms.md](docs/routing-algorithms.md)). `aiu --json` carries the same
ordering under `"advice"`, which the [GNOME indicator](gnome-extension/README.md)
and [`consult.py`](consult.py) `auto` read instead of keeping their own logic.
`aiu --alert` flags long windows projected to lose ≥5% of their cap at a reset
within 48h, windows projected to sit empty ≥24h before their reset, promo
credits with ≥25% unspent within 14 days of expiry, and probe errors, and
shows them in **one** desktop notification. On GNOME it goes through
`org.gtk.Notifications`: each run replaces that notification in place (its
title gives the snapshot time), and a run with nothing to report withdraws it,
so stale warnings never pile up. For this `aiu --alert` writes
`~/.local/share/applications/io.github.citizenu03bb.aiu.desktop` (hidden from
the app grid); it also puts **aiu** in Settings → Notifications, where you can
mute it or turn off its banners. Clicking the notification opens
`aiu --advise` in a terminal. Elsewhere it falls back to plain `notify-send`.

To run the alert automatically (twice daily, with desktop notifications), a
**systemd --user timer** is the right tool — it runs inside your graphical
session so notifications have the D-Bus session they need (plain cron does not):
```ini
# ~/.config/systemd/user/aiu-alert.service
[Service]
Type=oneshot
ExecStart=%h/.local/bin/aiu --alert
# ~/.config/systemd/user/aiu-alert.timer
[Timer]
OnCalendar=*-*-* 09,18:00:00
Persistent=true
[Install]
WantedBy=timers.target
```
`systemctl --user enable --now aiu-alert.timer`

Runs via `uv` with inline deps (PEP 723) — first run installs httpx+rich into a
cached env, nothing global. Config: `~/.config/ai-usage/config.toml`
(auto-written on first run).

## Providers

| Provider     | What it shows                | Setup needed |
|--------------|------------------------------|--------------|
| `claude`     | 5h / weekly / monthly quota  | none — reads `~/.claude/.credentials.json` |
| `codex`      | ChatGPT/Codex 5h + weekly quota | none — reads `~/.codex/auth.json` (OAuth) |
| `openai`     | month-to-date USD spend      | an **Admin** API key (off unless you use the API) |
| `deepseek`   | remaining balance            | a normal API key |
| `openrouter` | total spend + remaining credits | a normal API key |
| `gemini`     | Gemini 5h + weekly quota     | none — the Antigravity desktop app must be open |
| `mistral`    | monthly Vibe Code + API allowance (€ spent / € included) | the admin.mistral.ai **session cookie** (no API key works) |

**`codex`** covers OpenAI usage that runs on a ChatGPT subscription (the Codex
CLI and Pi both sign in via ChatGPT OAuth — no API key). It's a quota, not API
spend, so the OpenAI Costs API never sees it; `aiu` reads the OAuth token from
`~/.codex/auth.json` and polls `chatgpt.com/backend-api/wham/usage`. If you *also*
hit the OpenAI API directly, enable `openai` separately.

**`mistral`** reports the two allowances every Mistral plan includes, Free
included: €8.5/month for Vibe Code and €8.5/month for API/Studio, both reset on
the 1st of the calendar month (local time), not on the renewal date. There is
no public usage API, and API keys are rejected (verified Oct 2026: the Vibe key
only opens `/api/vibe/whoami`). `aiu` calls the same tRPC endpoints as the
console's Usage page (`usage.costBreakdown` for tokens per billing metric,
`usage.prices` for €/token) and multiplies them; this matches the dashboard's
"Total Estimated Cost" to the cent. Setup: in admin.mistral.ai, DevTools →
Network → any `/api/local-trpc/` request → copy the whole `Cookie` request
header into `MISTRAL_ADMIN_COOKIE`, in single quotes (the cookie contains `;`
and spaces, and `~/.env` is read like a shell reads it; better, keep it in a
keyring, see *Secrets*), then set `enabled = true`.
When the session expires the row says "session rejected": copy it again.
Vibe's local session logs (`~/.vibe/logs`) are not a substitute, because they
undercount the server's token totals by roughly 3×.

**Can't be polled** (verified June 2026): Cohere exposes no aggregate
usage/spend API (dashboard only).
The Gemini CLI's `/stats` is REPL-only and per-session, not account quota.
HuggingFace `whoami-v2` gives plan status (free/PRO) but no spend counter.
`gcloud` isn't installed. None of these yield a scriptable usage number without
local token accounting (routing calls through a proxy). Left out by design.

### Secrets
Each provider is enabled with `enabled = true` in the config, and its key is
resolved at runtime, in priority order:

1. `key_env`: a variable from your environment, else from `~/.env`;
2. `key_cmd`: a shell command; the first line of its stdout is the key;
3. `key_file`: a file; its first line is the key.

Only the first line of a value counts (a `pass` entry often holds more lines
after the password). A value that still holds control characters is refused,
and the warning does not quote it.

**`~/.env`** (override with `AIU_ENV_FILE=/path`) is read by `aiu` itself. It
takes only the names the config refers to (each `key_env`, plus
`MISTRAL_ADMIN_COOKIE`, `GEMINI_CLIENT_ID` and `GEMINI_CLIENT_SECRET`) and
ignores every other line, so a `HTTPS_PROXY` or `LD_PRELOAD` meant for another
tool changes nothing. Variables already in your environment win. The keys are
kept inside `aiu`, not exported, so `key_cmd` and the other commands `aiu` runs
don't see them.
Values are read like a shell reads them, without `$` expansion: `export KEY=v`
and bare `KEY=v` both work, an unquoted value stops at ` #`, `'...'` is
literal, and a line whose quotes don't match is skipped with a warning. `aiu`
warns when other users can read the file: `chmod 600 ~/.env`.

```sh
DEEPSEEK_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
OPENAI_ADMIN_KEY=sk-admin-...   # Costs API needs an ADMIN key, not a normal sk-
MISTRAL_ADMIN_COOKIE='...'      # whole Cookie header from admin.mistral.ai; quotes required, it expires
```

**Prefer a keyring to `~/.env`.** The file is plain text, often shared with
docker-compose or other scripts. `key_cmd` can read the key from the GNOME
keyring or `pass` instead:

```sh
secret-tool store --label='aiu openai' service aiu provider openai
```
```toml
[openai]
enabled = true
key_cmd = "secret-tool lookup service aiu provider openai"
```

**What each one can do.** Treat these like `~/.bashrc`: anyone who can write
them can run code as you.

- `key_cmd` is a shell command (`sh -c`). It runs on every `aiu` run: every
  5 minutes and on each refresh with the GNOME extension, twice a day with
  the timer. A command that unlocks a keyring or a GPG key can prompt that
  often. It inherits `aiu`'s environment, its stderr is discarded, and when it
  fails `aiu` moves on to `key_file`, so a broken command only shows as "no
  key". Like OpenSSH, `aiu` ignores every `key_cmd`, with a warning, when the
  config file or its directory is not yours or can be written by group or
  others. It writes the starter config `0600` in a `0700` directory.
- `~/.env` can only supply the names above, and only as keys sent to their
  own provider over TLS.
- `MISTRAL_ADMIN_COOKIE` is a whole admin.mistral.ai session, not a read-only
  key: until it expires it can create API keys and change billing. Keep it in
  a keyring (`key_cmd` in `[mistral]`) rather than in `~/.env`.

### claude
Nothing to configure, and no app needs to be open. The OAuth token is read
locally from `~/.claude/.credentials.json` (same source Claude Code uses).
Only the `claude` CLI rewrites that file: the desktop app keeps its own token,
so the file goes stale 8h after the last CLI run. When it has expired, `aiu`
runs `claude auth status`, which refreshes it without spending a request. That
needs the `claude` CLI installed. Without it, run `claude` once when you see
"token expired".

Besides quotas, any dollar grant in the same response (an entry with
`limit_dollars`, e.g. the cloud-session credit) shows up as a `credit` row:
amount left / grant size, plus expiry. These entries use codenamed keys that
Anthropic renames, so they are matched by shape; unknown ones render as
`Credit (<key>)`.

### openai
The Costs API needs an **Admin** key (starts with `sk-admin-...`), *not* a
regular `sk-...` key. Create one at platform.openai.com → Settings →
Organization → Admin keys. Then either:

```toml
[openai]
enabled = true
key_env = "OPENAI_ADMIN_KEY"
```
and `export OPENAI_ADMIN_KEY=sk-admin-...`, or point `key_cmd`/`key_file` at
wherever you keep it.

### deepseek
```toml
[deepseek]
enabled = true
key_env = "DEEPSEEK_API_KEY"
```
Shows balance from `GET /user/balance` (granted + topped-up).

### gemini
A Google account's Gemini usage (Antigravity, Google One AI Pro) is a
**subscription quota**, not pay-per-token API spend, so there's nothing to bill
and no BigQuery needed.

```toml
[gemini]
enabled = true
```

**Main source: the Antigravity desktop app, while it is open.** It runs a
local language server on 127.0.0.1; `aiu` finds it in `/proc`, takes the CSRF
token from its command line (or `~/.config/Antigravity/logs/main.log`) and asks
it for the quota summary: 5-hour and weekly windows. When the app is closed,
the row says so.

**The `agy` CLI can't stand in for it** (checked Oct 2026). It keeps its login
in the system keyring, not in `~/.gemini/oauth_creds.json`. It also runs its
own local server, but that server's CSRF token never leaves the process's
memory.

**Fallback: Gemini CLI OAuth, now refused for individual accounts.** If
`~/.gemini/oauth_creds.json` exists, `aiu` queries Code Assist
(`cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`) per model family,
refreshing the hourly access token itself and writing it back. Refreshing needs
the Gemini CLI's public OAuth client, which `aiu` does not ship: it reads it
from the installed `gemini` CLI bundle, or from `GEMINI_CLIENT_ID` /
`GEMINI_CLIENT_SECRET` in your env file. Since Oct 2026 Google answers
`UNSUPPORTED_CLIENT` ("migrate to Antigravity") for "Gemini Code Assist for
individuals", and the row says so. Paid Code Assist tiers may still work this
way; that is untested.

*(If instead you pay for the Gemini **API** per token, that cost lives only in
Cloud Billing and would need a BigQuery export — not implemented here, since the
subscription quota is what matters for the CLI.)*

## Adding a provider
Write a `probe_<name>(cfg) -> list[Row]` function, register it in `PROBES`, add
a `[<name>]` block to the config template. A `Row` is provider/metric/kind
(`quota|spend|balance|credit`) + `used`/`limit`/`unit`/`reset`/`note`.

## Unofficial endpoints & privacy
The subscription probes call **undocumented** endpoints, the same ones the
vendors' own CLIs use. They can change or break without notice, and this
project is not affiliated with Anthropic, OpenAI or Google:

- `claude`: Claude Code's OAuth usage endpoint (if it 404s after a Claude
  update, re-check it against the `onWatch` source);
- `codex`: `chatgpt.com/backend-api/wham/usage`;
- `gemini`: the Antigravity desktop language server's `RetrieveUserQuotaSummary`
  on 127.0.0.1, else Code Assist's `cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`.

Tokens and keys are read locally at run time and only ever sent to the
provider they belong to. `aiu` stores no secret, has no telemetry, and makes
no other network call. The `gemini` probe refreshes its access token and
writes it back to `~/.gemini/oauth_creds.json`, like the Gemini CLI does.

## Notes / limits
- On-demand only by design. Wrap in `watch` or a cron if you want polling.

## Development
```bash
uv run --with pytest --with httpx --with rich pytest -q tests   # CLI, no network
make -C gnome-extension check test                              # extension (test needs gjs + aiu)
```

## License
GPL-3.0-or-later, see [LICENSE](LICENSE).
