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
aiu --only claude_max     # subset, ignores enabled flags
watch -n 60 aiu           # poor man's live view
```

## Install
Needs [`uv`](https://docs.astral.sh/uv/) (the script declares its own
dependencies) and Python ≥ 3.11.

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
ordering under `"advice"`, which `consult auto` uses.
`aiu --alert` flags long windows projected to lose ≥5% of their cap at a reset
within 48h, windows projected to sit empty ≥24h before their reset, promo
credits with ≥25% unspent within 14 days of expiry, and probe errors, and
fires a `notify-send` desktop notification.

To run the alert automatically (twice daily, with desktop notifications), a
**systemd --user timer** is the right tool — it runs inside your graphical
session so `notify-send` has the DBUS it needs (plain cron does not):
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
| `claude_max` | 5h / weekly / monthly quota  | none — reads `~/.claude/.credentials.json` |
| `codex`      | ChatGPT/Codex 5h + weekly quota | none — reads `~/.codex/auth.json` (OAuth) |
| `openai`     | month-to-date USD spend      | an **Admin** API key (off unless you use the API) |
| `deepseek`   | remaining balance            | a normal API key |
| `openrouter` | total spend + remaining credits | a normal API key |
| `gemini`     | Code Assist quota per model family | none — reads `~/.gemini/oauth_creds.json` (OAuth) |

**`codex`** covers OpenAI usage that runs on a ChatGPT subscription (the Codex
CLI and Pi both sign in via ChatGPT OAuth — no API key). It's a quota, not API
spend, so the OpenAI Costs API never sees it; `aiu` reads the OAuth token from
`~/.codex/auth.json` and polls `chatgpt.com/backend-api/wham/usage`. If you *also*
hit the OpenAI API directly, enable `openai` separately.

**Can't be polled** (verified June 2026): Cohere and Mistral expose no aggregate
usage/spend API (dashboard only; Mistral's `vibe` CLI isn't even a thing here).
The Gemini CLI's `/stats` is REPL-only and per-session, not account quota.
HuggingFace `whoami-v2` gives plan status (free/PRO) but no spend counter.
`gcloud` isn't installed. None of these yield a scriptable usage number without
local token accounting (routing calls through a proxy). Left out by design.

### Secrets
`aiu` reads your single key file — `~/.env` by default (override with
`AIU_ENV_FILE=/path`). The script loads it itself at startup (vars already in
your environment win), so `./aiu.py` and the `aiu` wrapper behave identically.
No separate secrets file to keep in sync. Both `export KEY=v` and bare `KEY=v`
lines work. Add whatever you have:

```sh
DEEPSEEK_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
OPENAI_ADMIN_KEY=sk-admin-...   # Costs API needs an ADMIN key, not a normal sk-
```

Each provider is enabled with `enabled = true` in the config. Keys are resolved
at runtime, in priority order: `key_env` (env var name) → `key_cmd` (shell
command, stdout used) → `key_file` (file contents). `secrets.env` populates the
`key_env` route; use `key_cmd`/`key_file` instead if a key already lives
elsewhere (e.g. `pass`, a CLI config).

### claude_max
Nothing to configure. The OAuth token is read locally from
`~/.claude/.credentials.json` (same source Claude Code uses). If you see
"auth rejected", just run `claude` once to refresh the token.

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
If you use the Gemini CLI signed in with a Google account (Code Assist /
Google One AI Pro), usage is a **subscription quota**, not pay-per-token API
spend — so there's nothing to bill and no BigQuery needed. `aiu` reads the same
OAuth token the CLI stores in `~/.gemini/oauth_creds.json` and queries the
internal Code Assist endpoint (`cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`),
showing usage per model family (Pro / Flash / Flash-Lite).

```toml
[gemini]
enabled = true
```

Access tokens expire ~hourly; `aiu` **refreshes them itself** via the refresh
token in `oauth_creds.json` and writes the fresh token back. Refreshing needs
the Gemini CLI's public OAuth client, which `aiu` does not ship: it reads it
from the installed `gemini` CLI bundle, or from `GEMINI_CLIENT_ID` /
`GEMINI_CLIENT_SECRET` in your env file if you only have `agy`. So it never
depends on a CLI having run recently,
and incidentally keeps the file fresh for `agy` too. "auth rejected" only
appears if the *refresh* token itself is invalid (then run `agy` to re-sign in).
**Antigravity CLI migration (done):** the successor `agy` CLI installs under
`~/.gemini/antigravity-cli/` and reuses this same `oauth_creds.json` + the
account-level Code Assist quota pool — so this probe keeps working unchanged and
covers `agy` usage too. (The Antigravity *desktop IDE* is a different beast:
local language server on 127.0.0.1 + CSRF token, only readable while running —
not implemented here.)

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

- `claude_max`: Claude Code's OAuth usage endpoint (if it 404s after a Claude
  update, re-check it against the `onWatch` source);
- `codex`: `chatgpt.com/backend-api/wham/usage`;
- `gemini`: Code Assist's `cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`.

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
