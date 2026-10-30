# Security review

Review of `aiu.py`, `consult.py` and `gnome-extension/` at commit `875a8d3`,
before the project is announced (ROADMAP wave 1, task C). Line numbers refer
to that commit; the findings below are kept as written then. Where each one
stands in v0.1.0, and which function holds the fix today, is in **Status**
(its module since `aiu.py` became the `aiu/` package in ROADMAP wave 7).

**Method.** I read the code only. I made no calls to providers and used no real
credentials. Three behaviours were checked locally with fake values:

- the httpx error text for a malformed header value (M1);
- `render_html` output for a row that contains markup (M3);
- `Path.write_text` on a 0600 file reached through a symlink (M2).

**Scope.** Token handling, the `key_cmd` trust model, what `--json`, `--html` and
the GNOME extension expose or run, and injection and path issues in all of
these. Fixes are out of scope here. They belong to the follow-up wave, one PR
per coherent group, in severity order.

## Status in v0.1.0

Every finding was addressed in ROADMAP wave 2 (#6 to #12). Two leave a
documented residual risk.

| ID | Status | Fixed in | Where now |
|----|--------|----------|-----------|
| H1 | Fixed | #7 | `_query_local_quota_summary` (`aiu/probes/gemini.py`): only sockets of a user-owned Antigravity `language_server`, its own CSRF token, no port spray, no `verify=False` |
| M1 | Fixed | #8, #9 | `resolve_key` (`aiu/keys.py`) keeps the first line and refuses control characters; probes write their own words into notes and `collect` (`aiu/probes/__init__.py`) redacts token-like text (`_redact`, `aiu/probes/common.py`) |
| M2 | Fixed | #6 | `_gemini_persist` (`aiu/probes/gemini.py`): `flock`, temp file 0600, `fsync`, `os.replace`; nothing written if the refresh token changed meanwhile |
| M3 | Fixed | #7 | `render_html` (`aiu/render.py`) escapes every value and sets `default-src 'none'` |
| L1 | Fixed | #8, #12 | `load_config` (`aiu/config.py`) drops every `key_cmd` when the config or its directory is someone else's or writable by others (group write is accepted for the user's private group: `_writable_by_others`, `aiu/util.py`) |
| L2 | Fixed | #8, #11 | `load_env_file` (`aiu/keys.py`) reads only the names the config uses, parses like a shell, warns on a readable file, and exports nothing. A launcher that sources `~/.env` (as older installs did) undoes this: run `aiu.py` directly |
| L3 | Fixed, one residual | #8, #31 | `aiu cookie import mistral` (`cookie_import`, `aiu/cookies.py`) copies the `admin.mistral.ai` cookies from one browser profile into the keyring (`secret-tool store`, value on stdin) and sets `key_cmd`; nothing else touches the disk. Residual: it is still a full console session, readable by any process of the user that can query the unlocked keyring, and the manual `~/.env` copy remains as a fallback |
| L4 | Fixed, one residual; off by default since wave 8 | #10, #12, task R | `_refresh_claude_token` (`aiu/probes/claude.py`) runs `claude` only from known locations or `claude_cmd`, never one others can write, and backs off for an hour after a failure. A race with an interactive `claude` over the same refresh token is not addressed. Since wave 8 the whole token read, refresh included, runs only with `claude.read_token = true` (see below) |
| L5 | Fixed | #10 | `AiuClient` kills `aiu` on cancel and disable, with a 120 s watchdog |
| L6 | Fixed, one residual | #10 | `consult` feeds `codex` and `pi` on stdin; `agy` has no documented stdin mode and still gets the prompt as the value of `-p` |

L3 was taken further in ROADMAP wave 6 (task N), after v0.4.1: see its
status note at the end of the L3 finding.

**Claude and Anthropic's terms (ROADMAP wave 8, task R).** Anthropic's
legal and compliance page says "developers may not collect, store, or
intermediate Claude.ai credentials or session tokens"
(code.claude.com/docs/en/legal-and-compliance). Until v0.5.0 aiu read Claude
Code's OAuth token from `~/.claude/.credentials.json` on every run and sent it
to `api/oauth/usage`. Now:

- By default the `claude` probe reads `claude-usage.json` in aiu's state
  directory, written by the `aiu-claude-usage` plugin in `claude-code-plugin/`
  from Claude Code's plugin API (`session.measure`, `$.session.usage()`). The
  plugin writes four fields per window and a timestamp, reads no token or
  conversation content, runs no program and calls no network. aiu then opens
  neither the credentials file nor a connection, and never runs `claude` (so
  L4 does not arise). Tests assert that with the HTTP fake and a guard on
  opening the credentials file.
- aiu treats that file as untrusted input: at most 64 KiB, `schema` 1 and
  `source` `claude-code`, every field type-checked, otherwise absent
  (`docs/json-schema.md`). Its values only become numbers and labels in rows;
  nothing in it is run, followed or sent anywhere. Anyone who can write the
  user's `~/.local/state` can already change aiu's history and much else.
- The token read, the `claude auth status` refresh (L4) and the endpoint call
  run only with `claude.read_token = true`, off by default and absent from the
  starter configs, whose comment quotes the line above. `aiu config set
  claude.read_token true` prints the same notice the first time it turns the
  option on, and the preferences window shows it under the switch.
- What changes for existing users: anyone who relied on the token read (for
  credits such as `Cloud Credit`, or with no plugin installed) must set
  `read_token = true`; until they install the plugin or do so, the Claude row
  is an error that says which.

The two bugs found outside the security scope (the `--config` parent
directory, inline comments in `~/.env`) were fixed in #8. I3 was updated in
#14 for the translated terminal prompt.

## Summary

| ID | Severity | Finding | Where |
|----|----------|---------|-------|
| H1 | High | Antigravity fallback sends the CSRF token to every listening port on the machine and trusts the reply | `aiu.py:671-719` |
| M1 | Medium | A multi-line key is echoed in full into error notes (notification, `--json`, `--html`, panel menu) | `aiu.py:150-162`, `235`, `321`, `422`, `439`, `471`, `877` |
| M2 | Medium | `oauth_creds.json` is rewritten in place: no atomic write, no lock, errors silently ignored | `aiu.py:542-550` |
| M3 | Medium | `--html` writes provider text into the page without escaping it | `aiu.py:1001-1017` |
| L1 | Low | `key_cmd` runs from a config file whose permissions and owner are never checked, every 5 minutes once the extension is on | `aiu.py:150-158`, `936-943` |
| L2 | Low | The `~/.env` loader imports every variable (`LD_PRELOAD`, `HTTPS_PROXY`, `SSL_CERT_FILE`…), exports all secrets to child processes, and parses values differently from a shell | `aiu.py:914-933` |
| L3 | Low | The Mistral admin-console session cookie is kept in plain text in `~/.env` | `aiu.py:98-105`, `README.md:130` |
| L4 | Low | The stale-token refresh runs whichever `claude` is first on `PATH`, unattended, and may race an interactive session | `aiu.py:199-219` |
| L5 | Low | The extension never kills or times out `aiu`: a cancelled fetch leaves the process running | `gnome-extension/aiuClient.js:69-136` |
| L6 | Low | `consult` passes the prompt as a bare positional argument: other local users can read it, and it can be parsed as an option | `consult.py:41-51` |
| I1–I9 | Info | What `--json`, `--html` and the extension expose and run, plus checks that passed | see below |

No critical finding. No path sends a token to a party other than its provider,
except H1 (the Antigravity CSRF token) and the cases L2 makes possible.

## Trust model

What aiu trusts, and why:

- **The user's own files are code.** `~/.config/ai-usage/config.toml` (through
  `key_cmd`), `~/.env` (through the variables it can set, L2), the `aiu` and
  `claude` executables found on `PATH`, and the git checkout the extension is
  symlinked from (I5) can all run code as the user. Anyone who can write to
  them already has the user's account. That is the same model as `~/.bashrc`,
  and it is acceptable, provided the docs say so and aiu refuses files that
  other users can write (L1).
- **`key_cmd` is a shell command by design.** `resolve_key` runs it with
  `shell=True` (`aiu.py:152-153`) and uses its stdout as the key. The config
  should be documented as follows:
  - `key_cmd` runs every time `aiu` runs, which with the GNOME extension
    means every 300 s (`gnome-extension/extension.js:10`) plus each manual
    refresh, and twice a day with the systemd timer. A command that unlocks a
    keyring or a GPG key (`pass show …`) prompts or decrypts that often.
  - It inherits aiu's whole environment, including every secret loaded from
    `~/.env` (L2).
  - stderr is discarded and failures fall through silently to `key_file`, so
    a broken command only shows up as "no key".
  - Only the first line of the output should be the key: see M1 for what a
    multi-line `pass` entry does today.
- **Providers are trusted over TLS** for the numbers they return, but their
  text (plan names, `locked_reason`, codenamed credit keys, error bodies)
  should be handled as untrusted when it is rendered (M3, I7).
- **Local listeners are not trusted.** Any process of any user can bind a port
  on 127.0.0.1, so nothing secret should be sent to a port aiu has not tied to
  a process the user owns (H1).

## Findings

### H1. Antigravity fallback sends the CSRF token to every listening port

**Where.** `aiu.py:671-699` (fallback discovery), `aiu.py:702-719` (requests).

When the `/proc` scan finds no `language_server` process that has a listening
socket, `_query_local_quota_summary` does three things:

1. It takes the last `--csrf_token` found in
   `~/.config/Antigravity/logs/main.log` (`:673-682`).
2. It builds the candidate list from **every** listening TCP socket on
   127.0.0.1 or 0.0.0.0 in `/proc/net/tcp` (`:685-699`). That file lists the
   sockets of every user in the network namespace, and the code does not
   check the socket's owner (the `uid` column, `cols[7]`).
3. It POSTs to each port over http and then https, with
   `x-codeium-csrf-token: <token>` and `verify=False` (`:707-713`). It accepts
   the first 200 reply that parses as a quota summary.

**Scenario.** Antigravity runs, but its language server cannot be matched in
`/proc`. That happens if the binary is renamed in an update, the server runs
in another network namespace (a sandbox), or `/proc` is mounted with
`hidepid`. Another local user, or a container published on 0.0.0.0 (Docker's
`-p` binds the host side), listens on a port. On the next run, which comes
within 5 minutes with the extension on, aiu sends it the live CSRF token.

That token is what stands between other local processes and the language
server's RPC API. I did not check what that API allows. Antigravity is an
agent IDE, so assume it can read workspace content and drive the agent. The
listener can also answer with a forged quota summary: it is shown as real,
and its `displayName` reaches the HTML page unescaped (`:603` → M3).

When Antigravity is closed, the token in the log is stale, so the leak is
worth less. The port spray still happens on every run, and it costs up to
1 s per local port (0.5 s timeout, two schemes).

**Proposed fix.**
- Drop the port spray. Only query sockets whose inode belongs to a PID owned
  by `os.getuid()` whose `/proc/<pid>/exe` resolves inside the Antigravity
  install. `/proc/net/tcp`'s uid column is a cheap first filter.
- Pair a CSRF token only with the ports of the process it was read from.
  Never pair a token from `main.log` with an unrelated port.
- If the log fallback stays, use it only for ports already tied to an
  Antigravity PID.
- Try `http` only, or keep `https` without `verify=False`. The server is on
  loopback, so TLS adds nothing.
- The broad `b"language_server" in part` match (`:635`) also hits unrelated
  own processes (an editor opened on `language_server.py`). Match on the
  executable's basename instead.

### M1. A multi-line key is echoed in full into error notes

**Where.** `aiu.py:155-156` and `:162` strip only the ends of the value, so
newlines inside it survive. Every probe turns an exception into
`note=f"network: {e}"`: `:235`, `:321`, `:422`, `:439`, `:471`, `:877`.

**Scenario.** The starter config itself suggests
`key_cmd = "pass show openai/admin"` (`aiu.py:87`). A `pass` entry often holds
more lines after the password (`login: …`, `url: …`). The key then contains
`\n`. h11 refuses the header and its message quotes the value. Checked
locally with a fake key against a local server:

```
LocalProtocolError -> network: Illegal header value b'Bearer sk-FAKE-SECRET-123\nlogin: me@example.com'
```

That note is not sent over the network, but it is shown and stored in every
local output:

- the terminal table;
- `--json` (read by the extension and by `consult`);
- the `--html` file, which the user may publish;
- the GNOME panel menu (`menuView.js:240-243`);
- the desktop notification from `--alert` (`aiu.py:1317`, `:1325`), shown on
  screen and possibly on the lock screen.

A `key_file` that ends with a comment line, or a `\r\n` file, does the same.

**Proposed fix.**
- In `resolve_key`, keep the first line only, and reject a value that still
  holds control characters, with a note that does not quote it ("key from
  key_cmd has several lines").
- Never interpolate the exception text into a note. Use `type(e).__name__`,
  or a fixed message per exception class.
- As defence in depth, have `Row` or the probes redact anything that looks
  like `Bearer …`, `sk-…` or a cookie before it is stored in `note`.

### M2. `oauth_creds.json` is rewritten in place, without a lock

**Where.** `aiu.py:542-550`.

`_gemini_refresh` reads the file a second time and calls
`GEMINI_CREDS_PATH.write_text(...)`. The mode is kept (checked: a 0600 file
stays 0600) and the write goes through a symlink to its target (checked),
which is the right outcome for dotfile managers. But the call truncates the
file and then writes it: it is not atomic. Any error is swallowed by
`except Exception: pass`.

**Scenario.** The extension (every 5 minutes), the systemd timer and an
interactive `gemini` CLI run at the same moment, or the session is killed
during the write. The file is left empty or half-written, or one writer's
update overwrites another's. That `refresh_token` is the user's long-lived
Google sign-in for the CLI. Losing it means signing in again, and nothing
tells the user that aiu did it.

**Proposed fix.**
- Write a temporary file in the same directory (`os.path.realpath` of the
  path, so a symlink stays a symlink), created with mode 0600 through
  `os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o600)`, then `fsync` it and
  `os.replace` it over the target.
- Hold an `fcntl.flock` on a sibling lock file across the read and the write,
  and re-read under the lock so only the `access_token`, `expiry_date` and
  `id_token` fields change.
- Refuse to write when the target is not owned by the user.
- Log a short reason to stderr when persisting fails, instead of `pass`.
- Consider not persisting at all. aiu can keep the refreshed token in memory
  for the run and leave the file to the Gemini CLI. The cost is one extra
  refresh per run.

### M3. `--html` does not escape provider text

**Where.** `aiu.py:1013-1015`. `provider`, `metric`, the reset text and `note`
are put into the HTML as they are.

Several of these come from servers:

- `Credit ({key})` and `locked: {locked_reason}` from Anthropic (`:279-280`);
- the Codex `plan_type` (`:328`, `:341`);
- the Gemini `planName` and `tierName` (`:752`, `:756`, `:768`), and the
  local summary's `displayName` (`:603`, see H1);
- the DeepSeek `currency` (`:448`);
- the raw `resets_at` string, which `_humanize_reset` returns unchanged when
  it cannot be parsed (`:383-384`).

**Scenario.** A forged Antigravity reply (H1), a provider response that
contains markup, or a MITM made possible by L2 puts `<script>` into a row.
Checked locally:
`Credit (<img src=x onerror=alert(1)>)` and
`locked: <script>alert(2)</script>` come out as live markup. Opened from
`file://`, the script runs with that file's (opaque) origin. If the page is
published, as a "local HTML page" invites, the script runs on that site's
origin.

**Proposed fix.** Pass every interpolated value through `html.escape(...,
quote=True)`, including the computed `cell(r)`, and add
`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">`.
A test with a `<script>` metric locks it in.

### L1. `key_cmd` runs from a config file that is never checked

**Where.** `aiu.py:150-158` (`shell=True`), `aiu.py:936-943`.

`load_config` creates the directory and the template with the default umask
(typically 0755 and 0644), then runs whatever `key_cmd` the file holds. aiu
never checks who owns the file or who can write to it.

The function also writes the template to the `--config` path while creating
only the default `CONFIG_DIR` (`:938-939`). With a custom path whose parent
does not exist, it crashes.

**Scenario.** The config lives in a dotfiles repo, a synced folder, or a path
passed with `--config` that other users can write. One of them sets
`key_cmd = "curl … | sh"`. It runs as the user every 5 minutes from the
GNOME extension, with every `~/.env` secret in its environment (L2).

**Proposed fix.**
- As OpenSSH does, refuse to run `key_cmd` (and warn) when the config file or
  its directory is not owned by the user, or is writable by group or others.
- Create the directory with 0700 and the file with 0600.
- `mkdir` `path.parent` instead of `CONFIG_DIR`.
- Document the trust model above in the README's *Secrets* section.

### L2. The `~/.env` loader imports every variable

**Where.** `aiu.py:914-933`, called before anything else (`:1348`).

- **Every key is imported**, not just the ones the config names. Variables
  that are usually unset change how aiu works:
  - `HTTPS_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` and `SSL_CERT_DIR` are
    honoured by httpx (`trust_env` is on by default). They can route every
    bearer token through a proxy, or trust a CA, that the user set for
    another tool.
  - `LD_PRELOAD`, `BASH_ENV`, `ENV` and `PYTHON*` reach the children: the
    `key_cmd` shell, `gdbus`, `notify-send`.

  `~/.env` is a common, general-purpose file, often shared with
  docker-compose or other scripts, so it is easy to put such a variable there
  without realising aiu reads it. Only `_refresh_claude_token` uses a clean
  environment (`:208`), which is good.
- **Every secret becomes an environment variable** of the aiu process and of
  every child above. The `key_cmd` of one provider sees all the other keys.
- **No permission check.** A `~/.env` readable by anyone is loaded without a
  warning.
- **Parsing does not match a shell.** The README says the `aiu` wrapper
  sources the file with bash (`README.md:106`), while `aiu.py` parses it
  itself, so one file has two meanings. Checked: an inline comment becomes
  part of the value. The README's own example,
  `OPENAI_ADMIN_KEY=sk-admin-...   # Costs API needs an ADMIN key…`
  (`README.md:129`), yields the key followed by the comment, which is then
  sent as the bearer token. `strip('"').strip("'")` also removes unmatched
  quotes at either end.

**Proposed fix.**
- Load only the names the config references (each `key_env`, plus
  `GEMINI_CLIENT_ID` / `GEMINI_CLIENT_SECRET` and `MISTRAL_ADMIN_COOKIE`)
  into a dict that `resolve_key` reads, instead of `os.environ`. At minimum,
  deny `LD_*`, `*_PROXY`, `SSL_*`, `PYTHON*`, `BASH_ENV` and `ENV`.
- Warn when the file can be read by group or others.
- Parse like a shell: a quoted value runs to its matching quote, and an
  unquoted value stops at ` #`.
- Fix or drop the README's mention of a wrapper that sources the file.

### L3. The Mistral admin-console cookie is stored in plain text

**Where.** `aiu.py:98-105`, `aiu.py:860`, `README.md:106-107`, `README.md:130`.

The README asks for the whole browser `Cookie` header of `admin.mistral.ai` in
`~/.env`. That is a full console session, not a read-only key: it can create
API keys and manage billing until it expires. With L2 it also sits in the
environment of every aiu child. `follow_redirects=False` (`:835`) correctly
stops it from following a login redirect.

**Proposed fix.** Say in the README what the cookie grants. Recommend
`key_cmd` with a keyring (`secret-tool lookup …`) over `~/.env`. Keep only
the session cookie the tRPC calls need, if one is enough, rather than the
whole header. Never export it to `os.environ` (L2).

**Status (wave 6, task N).** `aiu cookie import mistral --browser
chrome|chromium|brave|firefox [--profile NAME]` replaces the DevTools copy:

- Run by the user only. No probe, `--json`, `--alert` or the GNOME extension
  calls it (a test checks the extension's sources).
- Reads only the cookies a browser sends to `admin.mistral.ai` (RFC 6265
  domain match): host `admin.mistral.ai`, `.admin.mistral.ai` or
  `.mistral.ai`, where Mistral sets its Ory session and CSRF token. Never
  other subdomains (`auth.`, `console.`, `chat.`) or a host-only
  `mistral.ai`. From the plain browsing context (no container tabs or
  partitioned cookies), unexpired, whose path covers `/api/local-trpc/`. The
  code cannot tell which of them the tRPC calls need, so it keeps all of them
  rather than guess at one session cookie.
- Chromium family: only the named browser's Safe Storage secret
  (`secret-tool lookup xdg:schema chrome_libsecret_os_crypt_password_v2
  application chrome|chromium|brave`, then the v1 schema), looked up only
  when a v11 value needs it; v10 uses Chromium's fixed `peanuts`. AES comes
  from the optional `cryptography` package: `openssl enc` would need the key
  on its command line (`-K`, readable in `/proc`), and its password modes
  (`-pass fd:`, `-kfile`) cannot reproduce Chromium's key, whose 9-byte salt
  OpenSSL truncates to 8.
- The cookie database is opened in place with `mode=ro&immutable=1`; with a
  non-empty `-wal` or `-journal`, or when SQLite reports it busy, it is copied
  (0600 files in a `TemporaryDirectory`, 0700) and the copy is removed.
- The header goes to `secret-tool store --label='aiu mistral cookie' service
  aiu provider mistral` on stdin, never in argv; `aiu config set` then points
  `mistral.key_cmd` at it, with its trust check (when refused, the command to
  run is printed). The command warns when `MISTRAL_ADMIN_COOKIE` would still
  win (`key_env` is read before `key_cmd`).
- Errors name the missing piece (no profile, no cookies, expired, no Safe
  Storage entry, locked keyring, no `secret-tool`, no `cryptography`) and
  never print a cookie, a key or `secret-tool`'s stderr.

### L4. The stale-token refresh runs whichever `claude` is on `PATH`

**Where.** `aiu.py:199-219`.

aiu never writes `~/.claude/.credentials.json` itself, which is good: it only
reads it (`:186-196`). When the token is within 60 s of expiry, it runs
`shutil.which("claude")` with `auth status` and a minimal environment
(`HOME`, `PATH`, `USER`, `LANG`), and the CLI rewrites the file.

**Scenario.**
- Run from the extension, `PATH` is gnome-shell's. An early, writable `PATH`
  entry, or a `claude` shim from a version manager, decides which binary runs,
  unattended, every 5 minutes as long as the token stays stale. The README
  says desktop-only users stay stale 8 h after their last CLI run.
- A background refresh can also race an interactive `claude` session over
  the same refresh token. If Anthropic rotates refresh tokens and the CLI
  does not lock the file, one of the two sessions is signed out. I could not
  verify that from this repository.

**Proposed fix.**
- Resolve `claude` once, accept it only from known install locations (or a
  `claude_cmd` config key), and skip it when the binary or its directory can
  be written by others.
- Back off after a failed refresh, for example with a timestamp file, so the
  CLI does not run every 5 minutes.
- Check with the Claude CLI's maintainers that concurrent `auth status` calls
  are safe, or document the risk.

### L5. The extension neither kills nor times out `aiu`

**Where.** `gnome-extension/aiuClient.js:69-74` (`cancel`),
`:95-105` (spawn and `communicate_utf8_async`),
`gnome-extension/extension.js:57-60`.

Cancelling the `Gio.Cancellable` stops the wait for `communicate_utf8_async`,
not the child process. A refresh that overlaps an earlier fetch, or disabling
the extension, leaves the earlier `aiu` running. That process can still run
`key_cmd`, `claude auth status` and, for Gemini, the port spray (H1). Nothing
limits how long a fetch takes either. The probe timeouts (15-60 s each) bound
it, so this cannot grow without limit, but disabling the extension does not
stop it.

**Proposed fix.** Keep the `Gio.Subprocess`. On `cancel()` and `disable()`,
call `proc.force_exit()`. Add a watchdog (`GLib.timeout_add_seconds`, about
120 s) that cancels and kills a fetch that is still running.

### L6. `consult` passes the prompt as a bare argument

**Where.** `consult.py:41`, `:46`, `:50-51`.

The prompt is a command-line argument of `agy`, `codex` and `pi`:

- It is readable by every local user through `/proc/<pid>/cmdline` for the
  whole run (up to 300 s). It can hold code or text that is not meant to be
  shared.
- For `codex exec` and `pi` it is a positional argument with no `--` before
  it. A prompt that is exactly an option name (the caller is an AI agent,
  so its prompts are generated text) is parsed as that option.
- Prompts over 128 KiB (`MAX_ARG_STRLEN`) fail.

**Proposed fix.** Feed the prompt on stdin wherever the CLI supports it
(`codex exec -` reads stdin). Otherwise put `--` before the positional
argument. Document that the delegated CLIs run with their own sandbox and
approval defaults: `consult` does not restrict them, except `pi`'s
`--no-tools`.

## Informational

**I1. What `--json` exposes** (`aiu.py:1355-1358`). It prints `captured_at`,
the rows (provider, metric, kind, used/limit/unit, reset, note, reset epoch,
window) and the routing `advice`. It holds no token, no Codex account id, and
no file path beyond `~/...` hints in error notes. It does reveal plan and tier
names, balances, spend and the timing of usage, which matters if the output
is shared (a bug report, a screenshot). The exception is M1: an error note
can carry a secret. Its consumers are the extension and `consult auto`
(`consult.py:61`). Both read it as data and do not execute it.

**I2. What `--html` exposes** (`aiu.py:1001-1018`). It holds the same data as
the table, without the advice. No token, except through M1. The file is
written to the path the user gave, with the default umask, through symlinks
and over any existing file. That path is the user's choice, so this is
acceptable, but the escaping is not (M3).

**I3. What the GNOME extension runs.**
- `~/.local/bin/aiu --json` if it is executable, else `aiu` from gnome-shell's
  `PATH`, as an argv list with no shell (`aiuClient.js:54-67`, `:95-98`).
  This happens at enable, every 300 s, and on each refresh click. Each run
  executes every `key_cmd`, may run `claude auth status` (L4) and scans
  `/proc` (H1).
- "Open aiu in a terminal" runs the fixed command line
  `gnome-terminal -- bash -c "aiu; …"` (`menuView.js:170-178`). No data is
  interpolated into it, only the translated "Press Enter to close…" prompt,
  quoted with `GLib.shell_quote` for bash and again for the command line
  parser (since the English interface, task D). Its `aiu` is resolved by
  bash's `PATH`, which can differ from the one the panel uses.
- The web links are fixed `https://` URLs opened with
  `launch_default_for_uri` (`menuView.js:149-167`).
- It makes no network calls of its own and writes no files.
- An error shows aiu's stderr, or the first 150 characters of invalid stdout,
  in the menu and in the journal (`aiuClient.js:111`, `:122`;
  `extension.js:85`). Neither contains secrets except through M1.

**I4. The extension renders text, not markup.** Every value reaches the menu
through `St.Label({ text })`. `use_markup` and `set_markup` appear nowhere in
`gnome-extension/`, so provider text cannot inject Pango markup. Keep it that
way when gettext arrives (wave 2).

**I5. Installing the extension.** `make install` (`gnome-extension/Makefile:20-27`)
runs `rm -rf` on the extension directory and symlinks it to the git checkout.
gnome-shell then runs whatever the working tree holds, including uncommitted
changes and whatever a `git pull` brings in. That is fine for development.
The extensions.gnome.org zip from task B is the path for users.

**I6. Codex and Claude credentials are only read.** `~/.codex/auth.json` is
read-only (`aiu.py:294-302`). Its token and account id go only to
`chatgpt.com` (`:310-319`). aiu never refreshes it or writes to it. The same
holds for `~/.claude/.credentials.json`: only the Claude CLI writes it (L4).
Since wave 8 aiu reads it only with `claude.read_token = true`.

**I7. Notifications quote their input correctly.**
- `_gvariant_str` (`aiu.py:1227-1228`) escapes `\`, `'` and newlines, and
  the payload goes to `gdbus` as an argv element, so a note cannot break out
  of the GVariant string.
- The `notify-send` fallback (`:1276`) passes title and body as arguments.
  Every warning starts with `⚠`, so neither can be read as an option.
- The freedesktop body *is* markup for some notification daemons, so error
  notes from providers can add formatting there. That is harmless on GNOME,
  which keeps only `<b>`, `<i>` and `<u>`.
- The `.desktop` file has a fixed `Exec` line and is written only if it is
  missing (`:1245-1255`).

**I8. TLS and outbound hosts.** Every provider call keeps httpx's default
certificate checks. `verify=False` appears only for 127.0.0.1 (H1). The Gemini
refresh token is sent only to the fixed `oauth2.googleapis.com` URL
(`:533`). The OAuth client found in the `gemini` bundle on `PATH`
(`:511-522`) can therefore make the refresh fail, but it cannot obtain the
refresh token.

**I9. The Antigravity CSRF token is public to local users anyway.**
Antigravity passes it as `--csrf_token` on its command line, which every local
user can read in `/proc/<pid>/cmdline`, and writes it to `main.log`. That is
Antigravity's design, not aiu's. It limits how much H1 adds on a shared host,
but H1 also reaches non-user listeners (containers, other services) and
accepts forged replies, so it stays a finding.

## Suggested order of fixes

Followed in ROADMAP wave 2: E (1), F and H (2), G (3), H (4), I (5).

1. H1 and M3 together: query only verified local ports, and escape the HTML.
2. M1 with the `resolve_key` part of L2: keep the first line of a key, stop
   echoing exception text, load only the referenced keys from `~/.env`.
3. M2: atomic, locked rewrite of `oauth_creds.json`, or no rewrite at all.
4. L1, L3 and the README trust-model text.
5. L4, L5 and L6.
