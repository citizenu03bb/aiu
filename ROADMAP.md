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

## Wave 2: fixes from wave 1, in parallel

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

## Wave 3: after wave 2 is merged

### D. English interface

**Goal:** English by default, French available.

- CLI: `--advise` and `--alert` messages, error notes, the starter config.
- GNOME extension: gettext with `fr` translations; labels, badges, advice.
- Docs in English, with the French UI mentioned as an option.
- Refresh the README "Development" section and the extension's Make
  command list after B: `make test` needs only gjs, `make test-live` needs
  `aiu`, and `pack` and `clean` are new.

**Why last:** it touches strings in almost every file, so it would conflict
with every other task, and A's tests guard it against regressions.

**Files:** `aiu.py`, `consult.py`, `gnome-extension/`, `README.md`, `docs/`.
**Done when:** CI is green, both languages render, and A's tests still pass.

## Later

- **Snapshot history.** Keep a small local history of readings so the pace
  can be estimated right after a reset, when the current window has less than
  10% elapsed and is shown as "unknown pace" (SPARE's main idea in
  docs/routing-algorithms.md).
- **Versioning.** Tie the `pyproject.toml` version and the extension's
  `metadata.json` version to the release tag.
- **Price list.** Per-task-type prices for the paid fallbacks, only if it
  starts to matter which kind of work gets the spare quota.
