# Roadmap

Work is split into independent tasks, each ending in **one pull request**
with green CI. Tasks in the same wave touch disjoint files, or disjoint
functions when they share `aiu.py`, so they can run in parallel without real
conflicts. A wave starts once the previous one is merged.

Ground rules for every task:

- **No network calls to providers and no secrets.** Tests use recorded or
  hand-written JSON responses only.
- Keep the routing behaviour described in
  [docs/routing-algorithms.md](docs/routing-algorithms.md) (SURPLUS-EDF)
  unchanged unless the task says otherwise.
- Stay inside the files listed for the task. Bugs found elsewhere are
  reported in the PR description, not fixed in passing.

## Wave 1: done

Merged on 2026-10-30: A in #4, B in #1 (follow-up #3), C in #2.

### A. Probe tests

**Goal:** every provider probe is covered by offline tests.

- Fake responses for `claude`, `codex`, `gemini` (local Antigravity path and
  OAuth path), `deepseek`, `openrouter`, and `openai`.
- Cases: normal response, 401 / 403 / 500, network error, missing or renamed
  fields, locked or expired credits, `Extra Usage`, Gemini OAuth client lookup
  (environment, installed CLI bundle, neither).

**Files:** `tests/` only.
**Done when:** the PR is green in CI and lists any probe bug it found.

### B. Packaging and CI

**Goal:** installable CLI, publishable extension, CI covering both.

- `pyproject.toml` so `pipx install git+https://github.com/citizenu03bb/aiu`
  provides `aiu` and `consult`, while `aiu.py` still runs standalone with `uv`.
- `make pack` in `gnome-extension/` builds the zip expected by
  extensions.gnome.org.
- Run the GJS unit tests in CI: split `tests/test_aiuClient.js` so the part
  that calls the real `aiu` runs only locally.
- Release workflow on tags: attach the extension zip to the GitHub release.

**Files:** `pyproject.toml`, `gnome-extension/Makefile`,
`gnome-extension/tests/`, `.github/`, install sections of the READMEs.
**Done when:** CI is green and produces the extension zip as an artifact.

### C. Security review

**Goal:** a written assessment before the project is announced.

- Token handling: reading and rewriting `~/.gemini/oauth_creds.json`, the
  Claude and Codex credential files, the `~/.env` loader.
- `key_cmd` runs a shell command from the config: document the trust model.
- What `--json` and `--html` expose, and what the GNOME extension runs.
- Injection or path issues in any of the above.

**Files:** `docs/security-review.md` only. No code changes.
**Done when:** the report lists findings by severity, each with a proposed fix.

## Wave 2: done

Merged on 2026-10-30: E in #7, F in #9, G in #6, H in #8 (follow-up #11),
I in #10. #12 lets the permission checks of H and I accept group write by
the user's private group (umask 002). Every `xfail` is gone.

Fixes for the probe bugs A pinned as strict `xfail` tests and the findings in
[docs/security-review.md](docs/security-review.md). Bug numbers (A1–A8) are
the ones in A's PR description (#4); finding IDs (H1, M1…) are the report's.

Every task in this wave:

- removes the `xfail` marker of each bug it fixes (strict mode fails the run
  otherwise) and adds a test for each finding it fixes;
- leaves every other `xfail` alone;
- rebases on `main` before merging. If another task already added a helper
  it needs, it reuses that one rather than adding a second.

### E. Gemini local path and HTML escaping

**Goal:** fix the only high finding, and the forged-reply path into HTML.

- H1: query only sockets owned by the user and tied to an Antigravity
  process; pair a CSRF token only with the ports of the process it was read
  from; no `verify=False`; match the executable's basename.
- A6: the `main.log` CSRF fallback also applies to a found server whose
  command line has no token (only for that server's ports).
- M3: escape every value in `render_html`, add the restrictive CSP.

**Functions:** `_parse_summary_response`, `_query_local_quota_summary`,
`render_html`. **Files:** those in `aiu.py`, `tests/test_probe_gemini.py`
(local-path tests only), a new `tests/test_render_html.py`.
**Done when:** CI is green and no request leaves for a port aiu has not tied
to an Antigravity process owned by the user.

### F. Probe error handling

**Goal:** one bad provider never breaks the run, and errors say what happened.

- A1: a non-JSON or malformed 200 becomes an error row; `collect()` turns any
  exception from a probe into an error row for that provider.
- A2: missing or renamed fields are an error, never a zero.
- A3: label HTTP status and malformed bodies as such, not `network:`.
- A7: a missing `is_available` is not "account not available".
- M1 (notes part): never put exception text into a note; use the exception
  class or a fixed message, and redact anything that looks like a token.
- A4 (Codex balance as a string) only if a real response confirms the shape;
  otherwise leave its `xfail` and say so in the PR.

**Functions:** `probe_claude`, `_claude_credits`, `probe_codex`,
`probe_openai`, `probe_deepseek`, `probe_openrouter`, `_mistral_trpc`,
`probe_mistral`, `collect`. Not `probe_gemini` (task G).
**Files:** those in `aiu.py`, `tests/test_probe_claude.py`,
`tests/test_probe_codex.py`, `tests/test_probe_api_keys.py` (probe tests
only), `tests/test_probe_mistral_http.py`.
**Done when:** CI is green and the A1, A2, A3 and A7 `xfail` markers are gone.

### G. Gemini OAuth

**Goal:** never lose the user's Gemini sign-in, and report OAuth errors right.

- M2: atomic, locked rewrite of `oauth_creds.json` (or no rewrite, keeping
  the refreshed token in memory); log a reason instead of `pass`.
- A5: a 403 after the forced refresh is a permission error, not "refresh
  token invalid".
- A1 for Gemini: a non-JSON body is an error row.
- A8 and the plan-note observation: read the plan from `currentTier` /
  `allowedTiers` as recorded in `tests/test_gemini_fallback.py`, and never
  produce a note starting with `:`.

**Functions:** `_gemini_oauth_client`, `_gemini_refresh`,
`_gemini_access_token`, `probe_gemini`. **Files:** those in `aiu.py`,
`tests/test_probe_gemini.py` (OAuth and token tests only),
`tests/test_gemini_fallback.py`.
**Done when:** CI is green, the A5, A8 and Gemini A1 `xfail` markers are
gone, and a test shows an interrupted write leaves the old file intact.

### H. Keys, config and `~/.env`

**Goal:** secrets come only from where the config says, and the trust model
is written down.

- M1 (key part): keep the first line of a key; reject one that still holds
  control characters, without quoting it.
- L2: load only the names the config references from `~/.env`, into a dict
  rather than `os.environ`; parse like a shell (an unquoted value stops at
  ` #`, quotes must match); warn when the file is readable by others.
- L1: refuse `key_cmd` when the config or its directory is not the user's or
  is group/world-writable; create them 0700 / 0600; `mkdir` the parent of a
  `--config` path.
- L3 and the trust model: the README *Secrets* section says what `key_cmd`,
  `~/.env` and the Mistral cookie can do; recommend a keyring.

**Functions:** `resolve_key`, `load_env_file`, `load_config`.
**Files:** those in `aiu.py`, `tests/test_probe_api_keys.py` (key and
`~/.env` tests only), the *Secrets* section of `README.md`.
**Done when:** CI is green and the README example with an inline comment
yields the bare key.

### I. Claude refresh, extension subprocess, consult

**Goal:** the low findings outside the probes.

- L4: resolve `claude` once from known locations or a `claude_cmd` key,
  skip it when others can write it, back off after a failed refresh.
- L5: the extension kills `aiu` on cancel and disable, with a ~120 s
  watchdog.
- L6: `consult` passes the prompt on stdin where the CLI supports it,
  otherwise after `--`.

**Functions:** `_refresh_claude_token`. **Files:** that in `aiu.py`,
`gnome-extension/aiuClient.js`, `gnome-extension/extension.js`,
`gnome-extension/tests/`, `consult.py`, a new `tests/test_consult.py`.
**Done when:** CI is green and the extension leaves no `aiu` process behind
after disable.

## Wave 3: done

Merged on 2026-10-30: D in #14.

### D. English interface

**Goal:** English by default, French available.

- CLI: `--advise` and `--alert` messages, error notes, the starter config.
- GNOME extension: gettext with `fr` translations; labels, badges, advice.
- Docs in English, with the French UI mentioned as an option.
- Refresh the README "Development" section and the extension's Make
  command list after B: `make test` needs only gjs, `make test-live` needs
  `aiu`, and `pack` and `clean` are new.
- `load_env_file`: a quoted value that spans several lines is reported as
  "unmatched quotes"; say that `~/.env` values must fit on one line.

**Why last:** it touches strings in almost every file, so it would conflict
with every other task, and A's tests guard it against regressions.

**Files:** `aiu.py`, `consult.py`, `gnome-extension/`, `README.md`, `docs/`.
**Done when:** CI is green, both languages render, and A's tests still pass.

## Release v0.1.0

- One version: `aiu.__version__` is the source, `pyproject.toml` reads it,
  the extension's `version-name` repeats it (`tests/test_version.py`), and
  `aiu --version` prints it.
- On a `v*` tag, the release workflow fails unless the tag is
  `v<__version__>` and matches `version-name`, then attaches the extension zip.
- `docs/security-review.md` records where each finding stands.
- Left to the maintainer: upload the zip to extensions.gnome.org.

To cut the next release: bump `__version__` and `version-name`, merge, then
tag `v<version>` on `main`.

Released on 2026-10-30.

## Wave 4: done

Merged on 2026-10-30: J in #19 (checked in a headless GNOME Shell 50.1 at
1280×720 and 1920×1080), K in #18. Released in v0.2.0 on 2026-10-30.

### J. Panel fits the screen

**Goal:** the extension's menu never grows taller than the desktop.

- With every provider enabled, the menu now overflows the screen height:
  its bottom items (the terminal and web links) end up off-screen.
- Put the data section (`_dataSection` in `menuView.js`) in a scroll view
  capped to the monitor's work area, keeping the header and the bottom items
  always visible; consider a more compact row layout (one line per metric)
  as well.
- Text stays plain `St.Label({ text })` (security review I4).

**Files:** `gnome-extension/menuView.js`, `gnome-extension/stylesheet.css`,
`gnome-extension/tests/`, `gnome-extension/po/` if strings change.
**Done when:** CI is green and, with a snapshot of 20+ rows on a 1080p
screen, the whole menu fits and the data section scrolls.

### K. Snapshot history

**Goal:** a pace right after a reset, instead of "unknown pace".

- Keep a small local history of readings (bounded size, user-only file
  permissions) and estimate the pace from it when the current window has
  less than 10% elapsed (SPARE's main idea in docs/routing-algorithms.md).
- SURPLUS-EDF stays the routing rule; only the pace estimate changes.

**Files:** `aiu.py` (pace and storage), `tests/`, `docs/routing-algorithms.md`.
**Done when:** CI is green and `--advise` shows a projected pace right after a
reset when the history holds the previous window.

## Release v0.3.0: first run

Released on 2026-10-30, before the first upload to extensions.gnome.org.

- Without `aiu`, the panel explains what it needs and shows the install
  command (uv, else pipx) with Copy command, Install guide and Check again
  buttons, instead of a raw error. It never installs anything itself.
- When `aiu` reads nothing yet, an Open config box sits above the probe notes.
- Both states keep the dot grey. Checked in an isolated headless GNOME Shell.
- The extensions.gnome.org description says up front that `aiu` is required.

Left to the maintainer: upload the v0.3.0 zip to extensions.gnome.org.

## Wave 5: done

Merged on 2026-10-30: L in #24, M in #26 (the window checked in a headless
GNOME Shell 50.1). Released in v0.4.0.

v0.4.1 adds a Preferences… item to the panel menu (#28) and stops the
progress bars from logging "not in the stage" warnings on every refresh.

The extension gets a preferences window for aiu's config. aiu stays the only
program that reads or writes `config.toml`: the extension goes through a new
`aiu config` subcommand, so it never parses TOML and every rule from the
security review (0600 file, `key_cmd` trust check) stays in one place.
No secret is ever typed into the UI: it only says *where* aiu finds a key.

### L. `aiu config` subcommand

**Goal:** read and change the config from scripts and the extension, as JSON.

- `aiu config show --json`: every provider section with its settings
  (`enabled`, `key_env`, `key_cmd`, `key_file`, provider-specific options),
  where each value comes from, and the config path. Values only: never a
  resolved key, never the contents of `~/.env`.
- `aiu config set <provider>.<option> <value>`: one change at a time,
  validated (known provider, known option, right type). `enabled` and the key
  source options at least; `key_cmd` only when the config passes the existing
  trust check.
- Edits are line-level: comments, order and unknown keys survive. No new
  dependency. A setting that is commented out (as in the starter config) is
  uncommented in place rather than duplicated.
- Atomic write that keeps mode 0600 (temp file + `os.replace`), refusing a
  config that is not the user's, as `load_config` does.
- New user-facing text goes through `tr()` with French.

**Files:** `aiu.py` (new `config` subcommand and its helpers; `main` for
dispatch), `tests/`, `README.md` (a short section on `aiu config`).
**Done when:** CI is green; `show` then `set` round-trips on the starter
config and on a hand-edited one with comments, which stay intact.

### M. Preferences window (`prefs.js`)

**Goal:** set up aiu from GNOME's Extensions app.

- `prefs.js` (Adwaita, GNOME 45+ `ExtensionPreferences`): one row per
  provider with an on/off switch and its key source (environment variable,
  command, file) read and written through `aiu config`; the state from the
  last snapshot ("signed in", "run `claude` once"); an *Open config file*
  button; a link to the install guide when `aiu` is missing.
- Extension-only settings in GSettings (`schemas/`): refresh interval.
- Never a field that holds a secret; the key-source help points to
  `secret-tool` as the README *Secrets* section does.
- Strings in gettext with French.

**Files:** `gnome-extension/prefs.js`, `gnome-extension/schemas/`,
`gnome-extension/extension.js` (read the interval), `gnome-extension/Makefile`
(pack the schema), `gnome-extension/po/`, `gnome-extension/tests/`,
`gnome-extension/README.md`.
**Done when:** CI is green and the window, checked locally in a nested or
headless GNOME Shell, turns a provider on and off through `aiu config`.

## Release v0.3.1: clipboard declared

extensions.gnome.org's automatic check flagged `St.Clipboard` (EGO-A-005,
manual review). Its rule is that clipboard access must be declared in the
description, so v0.3.1 says there that **Copy command** writes the install
command to the clipboard on click and never reads it.

## Wave 6: done

N merged on 2026-10-30 in #31. The first real import (Chrome) showed that
Mistral sets its session on `.mistral.ai`, not `admin.mistral.ai`; #33 reads
what a browser sends to `admin.mistral.ai` (RFC 6265 domain match). The
maintainer's session now lives in the keyring and `MISTRAL_ADMIN_COOKIE` is
gone from their `~/.env`. Released in v0.5.0.

### N. `aiu cookie import mistral`

**Goal:** stop copying the admin.mistral.ai `Cookie` header out of DevTools by
hand (security review L3), without making that session easier to leak.

Prior art: codexbar-gnome's `scripts/codexbar-cookie-importer` reads the
browser's "Safe Storage" key from the keyring and decrypts Chromium cookies.
Same idea, minus its weak spots: it copies the cookie database to a guessable
`/tmp` name, writes every keyring key it finds to a `/tmp` file, and tries
them all. Ideas only, no code from it (its licence asks for credit on
derivatives).

- An explicit command, run by the user only: never by a probe, `--json` or
  the GNOME extension. `--browser chrome|chromium|brave|firefox` picks one
  profile; no browser is searched by default.
- Only the cookies of `admin.mistral.ai` (the ones the tRPC calls need, if
  fewer than the whole header is enough).
- Chromium family: the one Safe Storage entry of the named browser, the
  v10/v11 scheme (AES-128-CBC, PBKDF2-SHA1 `saltysalt`, 1 iteration).
  Firefox: `cookies.sqlite` is not encrypted.
- The database is opened read-only through SQLite's `file:…?mode=ro&immutable=1`
  URI, or copied into a private temporary directory (0700, removed on exit).
  No key and no cookie ever touches the disk outside the keyring.
- The result goes to the keyring (`secret-tool store`), and
  `aiu config set mistral.key_cmd "secret-tool lookup …"` points aiu at it,
  instead of `~/.env`. Re-running replaces it.
- AES: Python's standard library has none, so either an `openssl`
  subprocess (keys passed on stdin or as a file descriptor, never on the
  command line) or the `cryptography` package as an optional extra. Decide in
  the PR.
- Errors say what is missing (keyring locked, not signed in, unknown
  profile) and never print a cookie or a key.

**Files:** `aiu.py` (new `cookie` subcommand), `tests/` (encrypted and plain
fixture databases built in the test, a fake `secret-tool`), `README.md` (the
Mistral setup), `docs/security-review.md` (L3 status).
**Done when:** CI is green, a test decrypts a Chromium fixture and reads a
Firefox one into the keyring stub without writing outside a private
directory, and the maintainer's real Mistral session is imported once by hand.

## Wave 7: done

Merged on 2026-10-30: O in #36 (the `aiu/` package, `--json` checked
byte-identical against 7204525), P in #37 (`"schema": 1`, `docs/json-schema.md`,
fixtures shared by the Python and GJS tests), Q in #39 (an early, fast pace
is `conserve`, not "unknown pace"). Released in v0.5.0, with wave 6.

`aiu.py` had grown to about 2,800 lines and 91 top-level functions: seven
probes, keys and `~/.env`, config editing, cookie import, history, routing,
rendering, alerts, the French table and the CLI. Waves 2 and 4 could only run
in parallel by splitting it by function, and a new provider means editing the
core. The `--json` output the extension reads every five minutes has no
version and no test shared by both sides. Behaviour does not change in this
wave.

### O. Split `aiu.py` into a package

**Goal:** one module per concern, so tasks touch different files and a
provider is a file of its own.

- An `aiu/` package: `probes/` (one module per provider behind a small
  `Probe` protocol: name, options it reads, `probe(cfg) -> list[Row]`, and a
  registry replacing `PROBES`), `rows.py` (`Row`), `keys.py` (key resolution,
  `~/.env`), `config.py` (load, trust check, `config show/set`), `cookies.py`,
  `history.py`, `routing.py` (SURPLUS-EDF, advise), `render.py` (table, HTML,
  JSON), `notify.py` (alert), `i18n.py` (`tr`, `FR`), `cli.py` (`main`).
- Pure move: no behaviour change, no new dependency. All existing tests pass
  with only their imports and monkeypatch targets updated; any test that has
  to change in substance is a sign the move changed behaviour.
- These must keep working, unchanged for the user:
  - the maintainer's launcher `~/.local/bin/aiu` → `~/Projects/aiu/aiu.py`;
  - `./aiu.py` run directly with uv (its inline script metadata stays);
  - `pipx install` / `uv tool install` from git, providing `aiu` and `consult`;
  - `aiu --json` output, byte-for-byte on the same input.

  The usual way: `aiu.py` at the root becomes a thin script that keeps the
  inline metadata, puts its own directory on `sys.path`, and calls
  `aiu.cli.main()`. Check that `import aiu` resolves to the package
  everywhere (tests, the wheel, the script), not to the root file.
- `consult.py` stays as it is unless it imports from `aiu.py`.
- Runs alone: it touches every Python file, so no other task runs meanwhile.

**Files:** `aiu.py`, a new `aiu/`, `tests/` (imports only), `pyproject.toml`
(packages instead of `py-modules`), `README.md` and `docs/` where they name
functions or line numbers.
**Done when:** CI is green, the test count is unchanged, `aiu --json` gives
the same output as before on recorded fixtures, and the launcher, `./aiu.py`
and a `uv tool install` from the branch all run.

### P. A versioned `--json` contract

**Goal:** the CLI and the extension cannot drift apart unnoticed.

- `aiu --json` gains a top-level `"schema": 1`. The fields the extension
  relies on are written down in `docs/json-schema.md`.
- One fixture (`tests/fixtures/snapshot.json`) is produced by a Python test
  from recorded probe answers and read by a GJS test through `AiuClient`, so
  a change on either side fails CI.
- The extension accepts schema 1, and shows a clear "update aiu" or "update
  the extension" message for an unknown one, instead of a parse error.
- `aiu config show --json` gets the same `schema` field.

**Files:** `aiu/render.py`, `aiu/config.py` (or the matching parts of
`aiu.py` if O has not landed), `tests/`, `gnome-extension/aiuClient.js`,
`gnome-extension/aiuConfig.js`, `gnome-extension/tests/`, `docs/`.
**Done when:** CI is green, and editing a field name on either side alone
makes a test fail.

### Q. Early fast pace means conserve (after O)

**Goal:** a window that is clearly running dry is flagged before 10% of it
has passed, instead of "unknown pace".

- Today, under 10% elapsed and with no history, `_project` returns no pace.
  Seen on 2026-10-31: Claude weekly at 12% used after 3.3% of the window,
  about 3.6× the sustainable pace, still shown as "unknown pace".
- Rule: under 10% elapsed, with no history, when used ≥ 2 × the elapsed
  fraction and used ≥ 5%, the window is `conserve`, with an "early, fast
  pace" hint. Slow early use stays unknown (it proves nothing about surplus).
  History, when present, keeps priority.
- Text through `tr()` with French; the panel badge follows the status.

**Files:** `aiu/routing.py` (or wherever O puts `_project`), `tests/`,
`docs/routing-algorithms.md`.
**Done when:** CI is green, with tests for early-fast (conserve), early-slow
(unknown) and history-present (history wins).

## Wave 8: done

Merged on 2026-10-30: R in #45, S in #44, T in #46. Not released: v0.5.0
was tagged before it, so it ships with wave 9 in v0.6.0.

A survey of each provider's official sign-in and usage APIs (2026-10-30)
found two places where aiu leans on another program's credentials in a way
the provider's terms address, and one clean official sign-in:

- **Anthropic**: "developers may not collect, store, or intermediate Claude.ai
  credentials or session tokens" (code.claude.com/docs/en/legal-and-compliance).
  aiu reads Claude Code's stored token and calls `api/oauth/usage`, the same
  endpoint, header and token type the Claude desktop app uses for its own usage
  card. The status line's `rate_limits` exist only in CLI sessions, but
  Claude Code's plugin API hands the same figures to a mod in the desktop app
  too (`session.measure`, `$.session.usage()`): a prototype mod wrote them to
  a file on 2026-10-30, matching the token read to the point. So aiu reads
  that file first, and the token read becomes an explicit opt-in.
- **Google**: the Gemini CLI FAQ calls third-party software that piggybacks on
  Gemini CLI's OAuth a "direct violation" that "may be grounds for immediate
  suspension". aiu's Gemini fallback refreshes and uses the Gemini CLI's
  token with the Gemini CLI's OAuth client. The Antigravity local path is not
  covered by that text.
- **OpenRouter** documents an OAuth PKCE flow that issues a user API key, and
  `GET /api/v1/key` reads that key's limit and usage (`/api/v1/credits`, used
  today, is documented for management keys).

### R. Claude from Claude Code's own figures, token read as an opt-in

- **The mod.** Ship `claude-code-plugin/` in this repository as an installable
  Claude Code plugin (`.claude-plugin/plugin.json`, `hooks/hooks.json`,
  `hooks/register.ts`, README, LICENSE; a `.claude-plugin/marketplace.json` at
  the repository root so `/plugin install aiu-claude-usage --marketplace
  citizenu03bb/aiu` works). On `session.measure` (when `rateLimits` changed)
  and at `session.start` (from `$.session.usage()`) it writes
  `$XDG_STATE_HOME/aiu/claude-usage.json` (else `~/.local/state/aiu/`):
  `{"schema": 1, "source": "claude-code", "written_at", "rate_limits": [{"kind",
  "percent_used", "resets_at"}]}`. Nothing else: no token, no prompt content.
  The prototype is in the maintainer's session dev-mods folder; start from it.
- **The probe.** `aiu/probes/claude.py` reads that file first. A reading is
  used when `written_at` is recent or a window's `resets_at` is still ahead
  (a window that reset since is shown at 0 % with its next reset unknown).
  Rows come out as today's (`5-Hour Limit`, `Weekly (all models)`), with a note
  of the reading's age when it is old. Credits (`Cloud Credit`) and windows
  only the endpoint knows are not in the file: say so in the README.
- **The token read** (Claude Code's `~/.claude/.credentials.json` and
  `api/oauth/usage`, plus `claude auth status` for a stale token) runs only
  with `claude.read_token = true`, off by default, with a comment in the
  starter config that says what it does and quotes Anthropic's policy line.
  `aiu config set claude.read_token true` prints that notice. With the opt-in
  on, the token read fills in what the file lacks (credits, extra windows).
- `claude.enabled` stays as it is in existing configs; with neither the file
  nor the opt-in, the row says how to install the plugin.
- While there: labels and window lengths for `seven_day_opus`,
  `seven_day_oauth_apps`, `seven_day_cowork` when the endpoint returns them.
- README, `docs/security-review.md` and `docs/json-schema.md` (if a row note
  changes) say what changed for existing users.

**Files:** `claude-code-plugin/`, `.claude-plugin/marketplace.json`,
`aiu/probes/claude.py`, `aiu/config.py` (starter config, `read_token`),
`aiu/i18n.py`, `tests/`, `README.md`, `docs/`,
`gnome-extension/prefsWindow.js` (the new option and notice),
`gnome-extension/po/`.

### S. Drop the Gemini CLI OAuth fallback

- Remove the path that refreshes `~/.gemini/oauth_creds.json` with the Gemini
  CLI's OAuth client (from the environment or the CLI bundle) and calls
  `cloudcode-pa` with it. Gemini is read from the Antigravity local server only;
  without it, the row says to open Antigravity.
- Remove `GEMINI_CLIENT_ID` / `GEMINI_CLIENT_SECRET` handling, the bundle
  scan, `_gemini_persist` and their tests; update README, the starter config
  and the security review (M2 and the Gemini notes become history).
- Own OAuth on a Google Cloud project is out of scope: it reaches the Gemini
  API's per-project quotas, not the Google AI plan used through Antigravity.

**Files:** `aiu/probes/gemini.py`, `aiu/keys.py` (Gemini env names),
`aiu/config.py`, `tests/`, `README.md`, `docs/security-review.md`.

### T. `aiu login openrouter`

- OAuth PKCE (S256) with a loopback callback on `127.0.0.1` and a random port,
  `key_label=aiu`; exchange the code at `POST /api/v1/auth/keys`; store the key
  with `secret-tool` and set `openrouter.key_cmd` through `aiu config set`, as
  the cookie import does. User-run only; nothing is printed but the outcome.
- The probe reads `GET /api/v1/key` (limit, `limit_remaining`, usage) and
  keeps `/api/v1/credits` only as a fallback for management keys.

**Files:** `aiu/probes/openrouter.py`, a new `aiu/login.py`, `aiu/cli.py`
(dispatch), `aiu/i18n.py`, `tests/`, `README.md`.

**Done when** (each task): CI is green, behaviour outside the task unchanged,
and the README says what changed for existing users.

## Wave 9: ask each vendor's own client

Wave 8 fixed Claude and Gemini one at a time. This wave applies one rule to
every provider: **aiu holds only credentials issued to aiu (`aiu login
openrouter`) or API keys the user created for that API. A sign-in that another
program holds stays in that program, and aiu asks that program for its
figures through an interface the program offers.**

| Tier | Source | Default |
|---|---|---|
| 1 | A documented API, with the user's own key: `openai`, `deepseek`, `openrouter` | on once configured |
| 2 | The vendor's own client reports its quota: `claude` (the plugin), `codex` (`codex app-server`), `gemini` (`agy`) | how aiu reads a subscription |
| 3 | Another program's token, or a web session: `claude.read_token`, the Mistral cookie | off; the user turns it on and gets a notice quoting the terms |

There is no silent fallback from tier 2 to tier 3. When the client is
missing, the row says what to install.

**Checked on 2026-10-31** on the maintainer's machine:

- **Codex 0.166.** `codex app-server` (JSON-RPC over stdio, marked
  experimental) answers `initialize` and then `account/rateLimits/read` in
  1.4 s. The answer has `rateLimits` and `rateLimitsByLimitId` (`primary` and
  `secondary` windows with `usedPercent`, `windowDurationMins` and `resetsAt`,
  plus `credits` and `planType`), and `rateLimitResetCredits`: a granted "Full
  reset (Weekly + 5 hr)" with its expiry, which aiu does not show today. Codex
  signs the request with its own token and refreshes that token itself.
  `codex app-server generate-json-schema` prints the schema. Since Codex 0.142
  the session logs no longer carry `rate_limits`.
- **Antigravity CLI.** `agy -p /usage --output-format json` runs the `/usage`
  command with no model turn (`num_turns` 0, no tokens) in 2.8 s.
  `command.data.groups[]` holds `name`, `description` and `buckets[]` (`id`,
  `name`, `window` `5h` or `weekly`, `remaining_fraction`, `reset_time`) for
  "Gemini Models", "Claude and GPT models" and per-model weekly limits
  ("Gemini 4 Argon"). A reply on the Google AI forum calls `/usage` the
  supported way to check quota. Each run writes a log under
  `~/.gemini/antigravity-cli/log/` and touches its MCP server cache. This was
  tested with the desktop app open; whether it works without the app has not
  been checked.
- **Claude Code.** `claude -p /usage` also answers with no turn, at $0, but in
  prose with resets in local time. The plugin stays.
- **Mistral Vibe 3.0.3.** Its plugins carry hooks (`post_agent`, `pre_tool`,
  `post_tool`, in the Vibe, Claude Code and Kimi protocols). Their payload is
  the session id, the transcript path, the cwd and the agent name: no usage.
  Each session's `meta.json` has `stats.session_cost` and token counts at list
  price, but only for Vibe CLI sessions on this machine (not API/Studio, not
  other machines), and local logs undercount the server's totals by about 3×
  (README, `mistral`). Vibe's app-server `account/read` gives the plan only.
  A Vibe plugin would feed the advice a lower bound and call a dry allowance
  *burn*, so it is not built. The cookie stays the only reading of Mistral's
  allowances.
- **consult.** `consult gemini` and `consult auto` run `agy -p "<prompt>"`,
  often from inside Claude Code. Antigravity's terms (§6) call "using third
  party software, tools, or services to access the Service" a breach that
  may suspend the Antigravity and Gemini CLI accounts. Forum replies
  attributed to Google staff apply this to running `agy` from another agent.
  `/usage` runs no model; a prompt does.

**Constraints**, because the repository and the extension are published:

- `pipx install git+…` installs `main`, and `/plugin install` reads the
  marketplace from `main`. Every PR leaves `main` working, with a README that
  matches the code.
- No backward compatibility: options, rows and notes may change or go. Each
  task adds its lines to one "What changed in v0.6.0" list in the README,
  which the release notes copy.
- `--json` keeps `"schema": 1`: new rows and options are additions. A task
  that needs to rename or remove a field stops and says so in its PR.
- Every option that runs a program is named `*_cmd`, and aiu and the
  extension treat them all alike (U).
- Two rounds: U, X and Y in parallel, then V and W in parallel once U is
  merged. V and W both re-record `tests/fixtures/`: whichever merges second
  rebases and records again. Tag v0.6.0 after all five.

### U. Run a vendor's CLI (first round)

- Move the executable lookup out of `_refresh_claude_token` into a new
  `aiu/clis.py`. It takes `<name>_cmd` from the config, or else the known
  install locations, never `PATH` (gnome-shell's, when the extension runs
  aiu). It resolves the path once to its real file and refuses one that other
  users can write, or that sits in a directory they can write.
- A runner next to it: fixed argv, a clean environment (`HOME`, `PATH`,
  `USER`, `LANG`), stdin from a pipe or `/dev/null`, and a timeout that kills
  the whole process group.
- A reading cache in aiu's state directory for slow or chatty clients: one
  0600 file per provider, with a time-to-live the probe sets. A failed run is
  cached for a short time too, so the extension does not retry it every
  refresh.
- Every option named `*_cmd` runs code. `CONFIG_RUNS_CODE`, `load_config`
  (which then drops every `*_cmd` from an untrusted config, closing the old
  Later item on `claude_cmd`), `config set` and `RUNS_CODE` in `aiuConfig.js`
  match the suffix instead of a list.
- Field titles for `codex_cmd` and `agy_cmd` in `aiuConfig.js`, with their
  French, so V and W leave the extension alone.
- `claude` behaves as before.

**Files:** a new `aiu/clis.py`, `aiu/probes/claude.py` (the lookup only),
`aiu/config.py`, `aiu/i18n.py`, `gnome-extension/aiuConfig.js`,
`gnome-extension/po/`, `tests/`, the README's *Secrets* lines on `*_cmd`,
`docs/security-review.md` (L1, L4).
**Done when:** CI is green, `tests/test_claude_refresh.py` passes with only
its imports changed, and a test shows an untrusted config dropping
`claude_cmd`.

### V. Codex from `codex app-server` (second round, after U)

- The `codex` probe starts `codex app-server` with U's runner, sends
  `initialize`, `initialized` and `account/rateLimits/read`, reads lines until
  the answer with that request's id (skipping notifications), then closes
  stdin. Timeout 20 s.
- Rows from `rateLimitsByLimitId`: each limit's windows (`5-Hour Limit` or
  `Weekly Limit`, from `windowDurationMins`), `credits.balance` as today, and
  one `credit` row per available `rateLimitResetCredits` entry, with its title
  and expiry, so `--alert` flags a reset about to expire unused. `planType`
  goes in the note. Every field is type-checked; a reply that fails the
  checks is "malformed", as with the Claude file.
- Errors: no `codex` ("install the Codex CLI"), not signed in (an error
  answer, or `authMode` null: "run `codex` to sign in"), and a changed
  protocol (an unknown-method error: "update aiu or Codex").
- Remove `_read_codex_token`, the `wham/usage` call and their tests: aiu no
  longer opens `~/.codex/auth.json`, and a test guards that file the way the
  Claude tests guard Claude's credentials.
- A `codex_cmd` option.
- README: the providers table, the `codex` paragraph and *Unofficial
  endpoints* (aiu calls nothing for Codex). Security review: the Codex notes.

**Files:** `aiu/probes/codex.py`, `aiu/config.py` (the `[codex]` blocks of
the starter configs), `aiu/i18n.py`, `tests/test_probe_codex.py`,
`tests/fixtures/`, `README.md` (Codex parts), `docs/security-review.md`,
`docs/json-schema.md` if a row note changes.
**Done when:** CI is green, the probe's test reply is a real reply with its
ids blanked, and the panel shows the new rows in a nested shell.

### W. Gemini from `agy /usage` (second round, after U)

- Run `agy -p /usage --output-format json` with U's runner and cache the
  reading for 15 minutes. Each run writes a log file, so aiu runs `agy` at
  most 96 times a day, whatever the extension's interval. First look for a
  flag that stops it from starting MCP servers or writing that log, and use
  it if there is one.
- Accept only a reply with `status` `SUCCESS`, `command.name` `usage` and
  `num_turns` 0. Anything else means `agy` may have taken `/usage` as a prompt
  and run the model: the row is an error, and aiu does not run `agy` again
  for 24 hours.
- Rows: every group's `5h` and `weekly` buckets, with `used = 1 -
  remaining_fraction` and `reset_time`. "Gemini Models" keeps `5-Hour Limit`
  and `Weekly Limit`; other groups and per-model limits are named after their
  group. Their routing advice follows each bucket's own reset.
- Remove `_query_local_quota_summary` (the `/proc` scan and the CSRF token
  from the command line or `main.log`) and its tests: H1's code is gone.
- Check with the desktop app closed, and have the README say what Gemini
  needs.
- An `agy_cmd` option.
- README: the providers table, the `gemini` section (the paragraph saying
  `agy` can't stand in is now wrong) and *Unofficial endpoints*, plus a line
  on §6: `/usage` runs no model. Security review: H1 becomes history.

**Files:** `aiu/probes/gemini.py`, `aiu/config.py` (the `[gemini]` blocks),
`aiu/i18n.py`, `tests/test_probe_gemini.py`, `tests/fixtures/`, `README.md`
(Gemini parts), `docs/security-review.md`.
**Done when:** CI is green, a test feeds a reply with `num_turns` 1 and
checks that the back-off holds, and the row reads correctly with
Antigravity's desktop app closed.

### X. Mistral: the keyring only, with a notice (first round)

- No default `key_env` for `mistral`, and `MISTRAL_ADMIN_COOKIE` out of
  `_ENV_ALWAYS`. aiu reads the cookie where `aiu cookie import` put it, or
  from a source the user sets explicitly (`aiu config set mistral.key_env
  MISTRAL_ADMIN_COOKIE`).
- `aiu cookie import mistral` prints a notice before it stores anything: it
  keeps a whole admin.mistral.ai session, which can create keys and change
  billing; it reads the browser's cookie store; and Mistral's terms forbid
  extracting content "other than as permitted" (§2.2(g) of the commercial
  terms). Check the consumer terms that cover the Free plan, and quote the
  clause that applies.
- The empty-cookie note becomes "no session: run `aiu cookie import
  mistral`".
- README `mistral`: the import is the way in, and the DevTools copy is only
  for a desktop without `secret-tool`. Drop the paragraph about a leftover
  `MISTRAL_ADMIN_COOKIE`, and add Mistral's tRPC to *Unofficial endpoints*,
  which leaves it out today.

**Files:** `aiu/probes/mistral.py`, `aiu/keys.py`, `aiu/cookies.py`,
`aiu/config.py` (the `[mistral]` blocks), `aiu/i18n.py`, `tests/`,
`README.md` (Mistral, the `~/.env` list in *Secrets*, *Unofficial
endpoints*), `docs/security-review.md` (L2, L3).
**Done when:** CI is green and a test shows a `MISTRAL_ADMIN_COOKIE` in
`~/.env` ignored when the config does not name it.

### Y. consult without `agy` (first round)

- Remove `gemini` from `consult`: `SUBS`, `CALLERS`, `call_gemini`, the usage
  text and its French. `auto` picks `codex`, or else falls back to
  `deepseek`.
- README: one line in the consult paragraph saying why. Security review: L6's
  residual (`agy -p`) goes with it.

**Files:** `consult.py`, `tests/test_consult.py`, `README.md` (the consult
lines), `docs/security-review.md` (L6).
**Done when:** CI is green and `consult gemini` exits 2 with the usage text.

**Done when** (each task): CI is green, behaviour outside the task is
unchanged, and the task's lines are in the README's "What changed in v0.6.0".

## Release v0.6.0 (after wave 9)

- Waves 8 and 9. Bump `__version__` and `version-name`, merge, tag `v0.6.0`.
  The Claude plugin stays at 0.1.0 unless a task changed it.
- Release notes: the README's "What changed in v0.6.0".
- Then the extensions.gnome.org upload. The listing kit
  (`gnome-extension/EGO.md`, not in git yet) needs one line saying Gemini is
  read through the Antigravity CLI.

## Later

- **Price list.** Per-task-type prices for the paid fallbacks, only if it
  starts to matter which kind of work gets the spare quota.
- **ChatGPT sign-in without the Codex CLI (investigate first).** The codex
  probe reads the Codex CLI's `~/.codex/auth.json`. Plyph
  (github.com/ubaimutl/Plyph, `chatgpt.js`) signs in to ChatGPT itself: OAuth
  authorization code with PKCE at `auth.openai.com`, a loopback redirect,
  `client_id=dynamic_agent_client` with an agent host id, scopes up to
  `chatgpt.tokens.use.direct`, ID token checked against the JWKS, tokens in the
  keyring. OpenAI documents it as "Sign in with ChatGPT"
  (developers.openai.com/siwc): open to open-source apps for eligible Plus and
  Pro users, tokens for `/v1/responses` and `/v1/models` only, per-app caps
  managed in ChatGPT settings, no documented way to read usage or limits.
  So it suits `consult` (prompts on the plan without `codex`), not the codex
  probe, unless a test shows the token reads `wham/usage`. Ideas only, not code.
- **A Mistral usage API.** Ask Mistral for a read-only usage endpoint that an
  API or Vibe key can read. With one, the cookie import goes. A Vibe plugin
  only makes sense if Vibe ever reports the allowance to its hooks.
- **Claude without the plugin.** `claude -p /usage` answers at $0 but in
  prose. Use it only if Claude Code gains a structured output for it.
- **One Codex process.** `codex app-server daemon` and `proxy` could spare a
  process start on each run, if V's 1.4 s ever matters.
- **An official Antigravity quota API**, as asked for on the Google AI forum,
  would replace the `agy` run.
