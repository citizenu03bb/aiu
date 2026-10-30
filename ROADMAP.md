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

## Later

- **`claude_cmd` from an untrusted config.** `aiu config set` refuses it like
  `key_cmd`, but `load_config` does not drop it from a config others can
  write. Low risk (the refresh still runs only a binary owned by root or the
  user that nobody else can write), but it should match `key_cmd`.
- **Price list.** Per-task-type prices for the paid fallbacks, only if it
  starts to matter which kind of work gets the spare quota.
- **ChatGPT sign-in without the Codex CLI (investigate first).** The codex
  probe reads the Codex CLI's `~/.codex/auth.json`. Plyph
  (github.com/ubaimutl/Plyph, `chatgpt.js`) signs in to ChatGPT itself: OAuth
  authorization code with PKCE at `auth.openai.com`, a loopback redirect,
  `client_id=dynamic_agent_client` with an agent host id, scopes up to
  `chatgpt.tokens.use.direct`, ID token checked against the JWKS, tokens in the
  keyring. Before any code, check whether such a token can read the usage
  endpoint the probe uses (`chatgpt.com/backend-api/wham/usage`) and what
  OpenAI documents about that agent program. If it can, `aiu login chatgpt`
  could replace the dependency on the Codex CLI; the same token could also let
  `consult` send prompts to the plan without `codex`. Ideas only, not code.
