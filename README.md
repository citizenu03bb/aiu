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
aiu config show           # what the config sets (see `aiu config` below)
watch -n 60 aiu           # poor man's live view
```

## Language
Messages are in English. For French, set `AIU_LANG=fr`; without it `aiu`
follows your locale (`LC_ALL`, then `LC_MESSAGES`, then `LANG`), so a French
desktop gets French messages, and `AIU_LANG=en` keeps English on one. This
covers `--advise`, `--alert` and its notification, the table headings, the
warnings on stderr and the starter config's comments. Probe notes, metric
names and the `--json` keys stay English: they are data, read by the
[GNOME indicator](gnome-extension/README.md) and by scripts. The indicator has
its own English and French translations and follows your GNOME session's
language.

## Install
Needs Python ≥ 3.11. With [`pipx`](https://pipx.pypa.io/) (or
`uv tool install` with the same URL), which provides both `aiu` and `consult`:

```bash
pipx install git+https://github.com/citizenu03bb/aiu
```

Or run straight from a clone with [`uv`](https://docs.astral.sh/uv/): the script
declares its own dependencies, and runs the `aiu/` package next to it.

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

`consult` hands a sub-task to `codex` (ChatGPT) or, paid, to DeepSeek through
`pi`, never to Gemini: Antigravity's terms
([antigravity.google/terms](https://antigravity.google/terms), §6) call "using
third party software, tools, or services to access the Service" a breach that
may suspend the Antigravity and Gemini CLI accounts, and forum replies
attributed to Google staff apply it to running `agy` from another agent.

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
| `claude`     | 5h / weekly quota (+ credits with `read_token`) | aiu's Claude Code plugin, `aiu-claude-usage` (see [claude](#claude)) |
| `codex`      | ChatGPT/Codex 5h + weekly quota, credits, free resets | the Codex CLI, installed and signed in (aiu asks `codex app-server`) |
| `openai`     | month-to-date USD spend      | an **Admin** API key (off unless you use the API) |
| `deepseek`   | remaining balance            | a normal API key |
| `openrouter` | total spend + remaining credits | `aiu login openrouter` (or a normal API key) |
| `gemini`     | Antigravity 5h + weekly quotas (Gemini, Claude and GPT, per model) | the Antigravity CLI `agy`, installed and signed in (see [gemini](#gemini)) |
| `mistral`    | monthly Vibe Code + API allowance (€ spent / € included) | the admin.mistral.ai **session cookie** (no API key works) |

**`codex`** covers OpenAI usage that runs on a ChatGPT subscription (the Codex
CLI and Pi both sign in via ChatGPT OAuth — no API key). It's a quota, not API
spend, so the OpenAI Costs API never sees it. `aiu` asks the Codex CLI itself:
it starts `codex app-server` (Codex's JSON-RPC server, marked experimental),
asks `account/rateLimits/read` and stops it, about 1.5 s. Codex answers with its
own sign-in and refreshes it itself; `aiu` reads no Codex token and never opens
`~/.codex/auth.json`. So the Codex CLI must be installed and signed in (run
`codex` once). `aiu` looks for it in `~/.local/bin`, `~/.npm-global/bin`,
`/usr/local/bin`, `/usr/bin` and Homebrew's `bin`, never on `PATH`; elsewhere
(an nvm install, say), set `aiu config set codex.codex_cmd ~/path/to/codex`.
The rows are each limit's 5-hour and weekly windows (with the plan in the
note), the `Credits` balance, and one `credit` row per free rate-limit reset
Codex has granted ("Full reset (Weekly + 5 hr)", with its expiry),
so `--alert` warns before one expires unused. The row says what to do when
Codex is missing ("install it"), signed out ("run `codex` to sign in") or
speaks a protocol aiu does not know ("update aiu or Codex"). If you *also* hit
the OpenAI API directly, enable `openai` separately.

**`mistral`** reports the two allowances every Mistral plan includes, Free
included: €8.5/month for Vibe Code and €8.5/month for API/Studio, both reset on
the 1st of the calendar month (local time), not on the renewal date. There is
no public usage API, and API keys are rejected (verified Oct 2026: the Vibe key
only opens `/api/vibe/whoami`). `aiu` calls the same tRPC endpoints as the
console's Usage page (`usage.costBreakdown` for tokens per billing metric,
`usage.prices` for €/token) and multiplies them; this matches the dashboard's
"Total Estimated Cost" to the cent.

Setup: sign in to admin.mistral.ai in your browser, then import that session
into the GNOME keyring. This is the way in: aiu has no default source for the
cookie, so a `MISTRAL_ADMIN_COOKIE` in your environment or `~/.env` is not
read unless you name it (below).

```sh
aiu cookie import mistral --browser firefox   # or chrome, chromium, brave
aiu config set mistral.enabled true
aiu --only mistral                            # check it
```

`cookie import` reads the cookies a browser sends to `admin.mistral.ai` (its own
and the `.mistral.ai` ones that hold the session, no other subdomain) from one
browser profile (`Default`, or Firefox's default profile; pick another with
`--profile NAME`, a directory like `Profile 1` or the name the browser shows),
stores them with `secret-tool` (label *aiu mistral cookie*), and sets
`mistral.key_cmd` to `secret-tool lookup service aiu provider mistral`
through `aiu config set`. Running it again replaces the stored session. You
run it yourself: `aiu`, `--json`, `--alert` and the GNOME extension never do.

Before it reads anything it prints a notice. The session is a whole
admin.mistral.ai session, not a read-only key: until it expires it can create
API keys and change billing. The import reads the browser's cookie store. And
Mistral's terms forbid using "any method to extract any content from the
Mistral AI Products other than as permitted through the Mistral AI Products"
(the consumer terms that cover the Free plan, §3 *Restrictions* (f), EEA and
rest of the world; the commercial terms for Studio and the API, §2.2(g);
[legal.mistral.ai/terms](https://legal.mistral.ai/terms), checked October
2026). No API key reads the allowances, and Vibe's plugins and hooks carry no
usage, so this session is the only way aiu can show them. Whether that is a
use you accept is your call: `mistral` stays off until you turn it on.

- It needs `secret-tool` (Debian/Ubuntu: `libsecret-tools`). Chrome, Chromium
  and Brave encrypt their cookies, so for them it also needs the
  `cryptography` package: `pipx inject aiu cryptography` (or install
  `aiu[cookies]`); with the standalone script,
  `uv run --with cryptography ./aiu.py cookie import mistral --browser chrome`.
  Firefox needs neither.
- For those three it reads the browser's own *Safe Storage* secret from the
  keyring (that browser's entry only), so the keyring must be unlocked.
- The cookie database is opened read-only where it is; while the browser is
  writing to it, it is copied into a private temporary directory (`0700`),
  read there and deleted. The session and the browser's key are written
  nowhere but the keyring, and no message prints them.
Only on a desktop without `secret-tool` (so without a keyring aiu can use):
in admin.mistral.ai, DevTools → Network → any `/api/local-trpc/` request →
copy the whole `Cookie` request header into `MISTRAL_ADMIN_COOKIE`, in single
quotes (the cookie contains `;` and spaces, and `~/.env` is read like a shell
reads it), and name it: `aiu config set mistral.key_env MISTRAL_ADMIN_COOKIE`.
The same notice applies, and the session then sits in plain text.

With no session the row says "no session: run `aiu cookie import mistral`".
When the session expires it says "session rejected": sign in again and re-run
the import (or copy the header again).
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
takes only the names the config refers to (each `key_env`) and ignores every
other line, so a `HTTPS_PROXY` or `LD_PRELOAD` meant for another tool changes
nothing. Variables already in your environment win. The keys are
kept inside `aiu`, not exported, so `key_cmd` and the other commands `aiu` runs
don't see them.
Values are read like a shell reads them, without `$` expansion: `export KEY=v`
and bare `KEY=v` both work, an unquoted value stops at ` #`, `'...'` is
literal, and a line whose quotes don't match is skipped with a warning.
Values must fit on one line: a quoted value continued on the next lines is
skipped the same way, and the warning says so. `aiu` warns when other users
can read the file: `chmod 600 ~/.env`.

```sh
DEEPSEEK_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...    # only if you don't use `aiu login openrouter`
OPENAI_ADMIN_KEY=sk-admin-...   # Costs API needs an ADMIN key, not a normal sk-
MISTRAL_ADMIN_COOKIE='...'      # only without secret-tool, and with mistral.key_env set; quotes required
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
- Every option named `*_cmd` runs a program: `key_cmd`, and `claude_cmd`,
  `codex_cmd` and `agy_cmd`, the vendor clients aiu asks for their own figures. The trust check above drops
  them all, and `aiu config set` refuses them, in a config that fails it. A
  vendor client is never taken from `PATH`: only its `*_cmd`, or else a known
  install location, and never one that someone else can write. It runs with
  a clean environment (`HOME`, `PATH`, `USER`, `LANG`), and it is killed,
  with whatever it started, at its timeout and once aiu has its answer.
- `~/.env` can only supply the names above, and only as keys sent to their
  own provider over TLS.
- The Mistral cookie is a whole admin.mistral.ai session, not a read-only
  key: until it expires it can create API keys and change billing. `aiu
  cookie import mistral` keeps it in the keyring and sets `key_cmd` (see
  `mistral` above); aiu reads it from `~/.env` only if you name it with
  `mistral.key_env`.

### `aiu config`
Reads and changes the config for scripts and the GNOME indicator, so nothing
else parses or writes `config.toml`. Both create the starter config first if
there is none, and both take `--config PATH` like `aiu` itself.

```sh
aiu config show                    # one line per provider setting
aiu config show --json             # the same, for scripts
aiu config set openai.enabled true
aiu config set openai.key_cmd "secret-tool lookup service aiu provider openai"
aiu config set mistral.vibe_allowance_eur 0
aiu config set openai.key_env ""   # an empty string turns a key source off
```

`show` lists each provider's settings: `enabled`, the key sources it uses
(`key_env`, `key_cmd`, `key_file`), and its own options (`read_token`,
`claude_cmd`, `codex_cmd`, Mistral's allowances), each with its value and whether the file sets it
(`"source": "file"`) or aiu's default applies. `--json` also gives the config
path and whether the `*_cmd` options may run (`key_cmd_allowed`, with the
reasons when not). It shows values only: it never runs `key_cmd`, reads a `key_file` or
`~/.env`, or prints a key.

`set` changes one setting per call: a known provider and option, with
`true`/`false` for `enabled` and `read_token`, a number for the allowances, and a variable name
for `key_env`. Errors go to stderr with a non-zero exit, and they never quote
the value you typed. The edit is line-level: comments, blank lines, order and
keys `aiu` doesn't know stay as they are. It changes the value in place,
uncomments a `# key_cmd = ...` line in the same section rather than adding a
second one, and otherwise adds the setting after the section's last one (or the
section at the end of the file). The result must parse back to the old config
plus that one value, or nothing is written. The file is replaced atomically,
`0600`, and a symlinked config stays a symlink. `set` refuses a config that is
not yours, and refuses every `*_cmd` option when the config fails the
check above (yours, not writable by group or others).

### claude
aiu reads the figures Claude Code itself reports: the 5-hour and weekly
windows its status line shows. A small Claude Code plugin in this repository,
[`aiu-claude-usage`](claude-code-plugin/README.md), writes them to
`~/.local/state/aiu/claude-usage.json` (`$XDG_STATE_HOME/aiu/` when set) after
each turn that moves a window; aiu reads that file. The plugin reads no token
and no prompt content. Install it once, at the prompt of a Claude Code session
in a terminal:

```
/plugin install aiu-claude-usage --marketplace citizenu03bb/aiu
```

(`y` to add the marketplace, then the user scope.) In the Claude desktop app,
where `/plugin` does not run: **+** next to the prompt box, **Plugins**,
**Add marketplace** with `citizenu03bb/aiu`, then **Add plugin**. It works in
the desktop app as well as the CLI, and nothing else needs to run: aiu reads
the last reading Claude Code left.

A reading older than an hour says its age in the row's note (`Claude Code
reading from 2h 5m ago`): usage from claude.ai or another machine since then is
not in it. A window that has reset since the reading shows 0 % with its next
reset unknown; once every window has reset, the row says to use Claude Code to
take a new reading. Without the file (and without `read_token`), the row is an
error that says how to install the plugin.

**Credits and other windows: `read_token` (off by default).** The file holds
only the windows Claude Code reports. Dollar grants (the cloud-session credit,
`Cloud Credit`), `Weekly Sonnet`, `Weekly Opus` and the other windows only
Anthropic's usage endpoint knows need the token read that aiu used to do
unconditionally: it reads Claude Code's OAuth token from
`~/.claude/.credentials.json` and sends it to that endpoint, the one the Claude
apps use for their own usage card. Anthropic's terms say "developers may not
collect, store, or intermediate Claude.ai credentials or session tokens"
([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)),
so it only runs if you turn it on:

```sh
aiu config set claude.read_token true    # prints that notice
```

With it on, the plugin's windows come first and the token read adds what the
file lacks; without the plugin, it is the whole Claude reading, as before.
Only the `claude` CLI rewrites the credentials file: the desktop app keeps its
own token, so the file goes stale 8h after the last CLI run. When it has
expired, `aiu` runs `claude auth status`, which refreshes it without spending a
request. That needs the `claude` CLI installed (`claude_cmd` if it is not in
a usual place). Without it, run `claude` once when you see "token expired".

Any dollar grant in that response (an entry with `limit_dollars`) shows up as
a `credit` row: amount left / grant size, plus expiry. These entries use
codenamed keys that Anthropic renames, so they are matched by shape; unknown
ones render as `Credit (<key>)`.

**Upgrading from 0.5 or earlier.** aiu no longer reads Claude Code's token
unless `read_token = true`. Install the plugin (recommended), or set
`aiu config set claude.read_token true` to keep the old behaviour, credits
included. Until you do one of them, the Claude row is an error that says so.

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

### openrouter
Sign in once in the browser, and OpenRouter issues aiu a key of its own:

```sh
aiu login openrouter                     # --no-browser: only print the address
aiu config set openrouter.enabled true
aiu --only openrouter                    # check it
```

This is OpenRouter's documented [OAuth PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth)
flow. `aiu` listens once on `127.0.0.1` (a random port, loopback only),
opens `openrouter.ai/auth` with an S256 code challenge and `key_label=aiu`,
and prints that address too for a machine without a browser. After you
authorize it there, OpenRouter sends the browser back to the listener with a
code; `aiu` exchanges code and verifier at `POST /api/v1/auth/keys` for a user
API key named *aiu* (it shows in openrouter.ai/settings/keys, where you can
revoke it). The listener answers only `GET /callback?code=…` with its own
`Host`, and closes after 5 minutes or on Ctrl-C. The key is stored with
`secret-tool` (label *aiu openrouter key*, value on stdin) and
`openrouter.key_cmd` is set to `secret-tool lookup service aiu provider
openrouter` through `aiu config set` (when the config fails the trust check,
the command is printed for you to run). The key is never printed. You run it
yourself: `aiu`, `--json`, `--alert` and the GNOME extension never do.

- It needs `secret-tool` (Debian/Ubuntu: `libsecret-tools`).
- If `OPENROUTER_API_KEY` is still set in your environment or in `~/.env`,
  aiu reads it first (`key_env` comes before `key_cmd`); the command says so.
  Delete it, or `aiu config set openrouter.key_env ""`.

The probe reads [`GET /api/v1/key`](https://openrouter.ai/docs/api/reference/limits),
which describes the key it is called with. A key with a spending limit shows
that key's spend (`usage`, today's in the note) and what is left of its limit
(`limit_remaining`, with its reset period). A key without one (`aiu login`
asks for none) spends the account's credits: the rows then come from
`GET /api/v1/credits`, as before (account spend and balance). OpenRouter
documents that endpoint for management keys; if it turns the key away, only
the key's own spend is shown. A key `/key` does not describe (a management
key) falls back to `/credits` too. *Existing users:* a pasted key keeps
working unchanged, and an unlimited key shows the same two rows as before.

### gemini
A Google account's Gemini usage (Antigravity, Google One AI Pro) is a
**subscription quota**, not pay-per-token API spend, so there's nothing to bill
and no BigQuery needed.

```toml
[gemini]
enabled = true
```

**The source: the Antigravity CLI's own `/usage` command.** `aiu` runs
`agy -p /usage --output-format json --log-file /dev/null` and reads the quota
groups it prints: "Gemini Models" as `5-Hour Limit` and `Weekly Limit`, and
each other group after its name (`Claude and GPT 5-Hour`, `Claude and GPT
Weekly`, `Gemini 4 Argon Weekly`), each with its own reset, which the routing
advice treats as its own window. `/usage` runs no model and spends no tokens;
`agy` keeps its sign-in and makes the request, and `aiu` neither reads that
sign-in nor makes any network call for Gemini. What it needs is the CLI
installed and signed in (run `agy` once). Without it the row says "install the
Antigravity CLI (agy) to read the Gemini quota".

`aiu` looks for `agy` in `~/.local/bin/agy`, `/usr/local/bin/agy`,
`/usr/bin/agy` and `/opt/homebrew/bin/agy`, never on `PATH`; `agy_cmd` names
another (`aiu config set gemini.agy_cmd ~/bin/agy`; like every `*_cmd` it is
ignored in an untrusted config, see [Secrets](#secrets)).

Each run takes about 3 s and starts the MCP servers configured for `agy`
(nothing turns that off for one run), so `aiu` keeps a reading for 15 minutes
(5 after a failure): `agy` runs at most 96 times a day, however often you or
the panel refresh. A reply that is not a `/usage` answer (no `SUCCESS`, another
command, or a model turn or tokens counted) may mean `agy` took `/usage` as a
prompt and ran the model: the row says so, and `aiu` does not run `agy` again
for 24 hours.

**Antigravity's terms.** Antigravity's terms
([antigravity.google/terms](https://antigravity.google/terms), §6) call "using
third party software, tools, or services to access the Service" a breach that
may suspend the account. `aiu` reaches no Antigravity service itself: it runs
the CLI's `/usage`, which runs no model, and a reply on the Google AI forum
calls the CLI's `/usage` the supported way to check the quota. Up to v0.5.0
`aiu` instead found the Antigravity desktop app's language server in `/proc`,
took its CSRF token and called its local RPC; that is gone.

**No more Gemini CLI fallback (wave 8, task S).** Up to v0.5.0, with
Antigravity closed, `aiu` read `~/.gemini/oauth_creds.json`, refreshed that
token with the Gemini CLI's OAuth client and asked Code Assist
(`cloudcode-pa.googleapis.com`) for the quota. The
[Gemini CLI FAQ](https://geminicli.com/docs/resources/faq) calls third-party
software that piggybacks on the Gemini CLI's OAuth sign-in a "direct
violation" of Google's terms that "may be grounds for immediate suspension",
so that path is gone. `aiu` no longer reads or rewrites
`~/.gemini/oauth_creds.json`, no longer looks for the Gemini CLI's OAuth
client, and ignores `GEMINI_CLIENT_ID` / `GEMINI_CLIENT_SECRET` (you can delete
them from `~/.env`). **If you relied on that fallback**, read the quota through
`agy` instead (above). Signing in with OAuth on a Google Cloud project of
your own would not help: it reaches the Gemini API's per-project quotas, not
the Google AI plan used through Antigravity.

*(If instead you pay for the Gemini **API** per token, that cost lives only in
Cloud Billing and would need a BigQuery export — not implemented here, since the
subscription quota is what matters for the CLI.)*

## Adding a provider
A provider is one module, `aiu/probes/<name>.py`, that follows the `Probe`
protocol in `aiu/probes/__init__.py`: `NAME` (its config section), `OPTIONS`
(what `aiu config` shows and sets for it, e.g. `**KEY_SOURCES` from `aiu/keys.py`
for a key) and `probe(cfg) -> list[Row]`. Add it to `REGISTRY` there, and a
`[<name>]` block to both starter configs in `aiu/config.py`. A `Row`
(`aiu/rows.py`) is provider/metric/kind (`quota|spend|balance|credit`) +
`used`/`limit`/`unit`/`reset`/`note`.

## Unofficial endpoints & privacy
The subscription probes call **undocumented** endpoints, the same ones the
vendors' own CLIs or consoles use. They can change or break without notice,
and this project is not affiliated with Anthropic, OpenAI, Google or Mistral AI:

- `claude`, only with `read_token = true`: Claude Code's OAuth usage endpoint
  (if it 404s after a Claude update, re-check it against the `onWatch`
  source). The default reads a local file Claude Code's plugin API fills, and
  calls nothing;
- `codex`: nothing. aiu asks the Codex CLI (`codex app-server`), which calls
  its own endpoints with its own sign-in;
- `gemini`: nothing. `aiu` runs the Antigravity CLI's `/usage` command
  (`agy -p /usage --output-format json`), which uses no model turn; `agy`
  makes its own request with its own sign-in;
- `mistral`: the admin console's tRPC endpoints
  (`admin.mistral.ai/api/local-trpc/`, `usage.costBreakdown` and
  `usage.prices`), with the browser session you imported. Mistral's terms
  forbid extracting content "other than as permitted" (see `mistral` above).

Tokens and keys are read locally at run time and only ever sent to the
provider they belong to. `aiu` stores no secret (except in the keyring, when
you run `aiu cookie import` or `aiu login`), has no telemetry, and makes
no other network call. No probe writes to another program's files.

## Notes / limits
- On-demand only by design. Wrap in `watch` or a cron if you want polling.

## What changed in v0.6.0
One list for the release notes. Wave 8's changes are told in their own
sections (`claude`, `gemini`, `openrouter`); each wave 9 change adds its line.

- Every option named `*_cmd` runs a program: an untrusted config drops them
  all, not just `key_cmd` (it kept `claude_cmd` before), and `aiu config set`
  and the preferences window treat them alike.
- `consult` no longer offers `gemini` (it ran `agy -p`): Antigravity's terms
  forbid reaching it from another tool. `consult gemini` now exits 2 with the
  usage text, and `auto` picks `codex` or falls back to DeepSeek.
- `mistral` has no default `key_env`: a `MISTRAL_ADMIN_COOKIE` in your
  environment or `~/.env` is no longer read unless you name it (`aiu config
  set mistral.key_env MISTRAL_ADMIN_COOKIE`). `aiu cookie import mistral`, which
  keeps the session in the keyring, is the way in; with no session the row
  says "no session: run `aiu cookie import mistral`".
- `aiu cookie import mistral` first prints a notice: what the session can do,
  that it reads the browser's cookie store, and the clause of Mistral's terms
  that forbids extracting content "other than as permitted".
- `codex` asks the Codex CLI (`codex app-server`, `account/rateLimits/read`)
  instead of reading its token from `~/.codex/auth.json` and calling
  `chatgpt.com/backend-api/wham/usage`: aiu no longer opens that file. The
  Codex CLI must be installed and signed in; `codex_cmd` says where it is when
  it is not in a usual place. New rows: one `credit` per free rate-limit
  reset Codex granted, with its expiry, which `--alert` flags before it is
  lost. When not signed in, the row says "not signed in: run `codex` to sign
  in" (it said "no token in ~/.codex/auth.json" or "auth rejected").
- `gemini` reads the quota from the Antigravity CLI: `agy -p /usage
  --output-format json`, which runs no model, cached for 15 minutes. It needs
  `agy` installed and signed in, not the desktop app; `aiu` no longer scans
  `/proc` for Antigravity's language server or reads its CSRF token, and makes
  no network call for Gemini. New rows show the other groups `/usage` reports
  (`Claude and GPT 5-Hour` and `Weekly`, per-model weekly limits such as
  `Gemini 4 Argon Weekly`). A new `agy_cmd` option says which `agy` to run. A reply that looks
  like a model turn stops `agy` runs for 24 hours.
- The GNOME indicator says what turned its dot red or yellow, at the top of
  the menu. A used-up limit on one model only (Gemini 4 Argon, Claude's
  `Weekly Sonnet`) turns it yellow, not red: `--json` rows carry `sublimit`.

## Development
Every test is offline: providers are faked, and no key or network is needed.
The code is the `aiu/` package, one module per concern (its docstring lists
them); `aiu.py` at the root only runs it from a checkout.

```bash
uv run --with pytest --with httpx --with rich pytest -q tests   # CLI
make -C gnome-extension check test pack                         # extension, as CI runs it
```

- `make check` checks the JavaScript syntax (with `node`), `metadata.json`
  and the translations.
- `make test` runs the extension's unit tests with `gjs` only, in English and
  in French; it does not need `aiu`.
- `make test-live` runs the installed `aiu`, so it calls your providers: local
  only, never in CI.
- `make pack` builds the zip for extensions.gnome.org (with the compiled
  translations); `make clean` removes it and `locale/`.

User-facing CLI messages are written in English and translated through the
`FR` table in `aiu/i18n.py` (and in `consult.py`); `tests/test_i18n.py` fails when a
message has no French text. The extension's strings are in
`gnome-extension/po/`, see its [README](gnome-extension/README.md#languages).

## License
GPL-3.0-or-later, see [LICENSE](LICENSE).
