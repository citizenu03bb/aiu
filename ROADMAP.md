# Roadmap

Work is split into independent tasks, each ending in **one pull request**
with green CI. Tasks in the same wave touch disjoint files, so they can run
in parallel without conflicts. A wave starts once the previous one is merged.

Ground rules for every task:

- **No network calls to providers and no secrets.** Tests use recorded or
  hand-written JSON responses only.
- Keep the routing behaviour described in
  [docs/routing-algorithms.md](docs/routing-algorithms.md) (SURPLUS-EDF)
  unchanged unless the task says otherwise.
- Stay inside the files listed for the task. Bugs found elsewhere are
  reported in the PR description, not fixed in passing.

## Wave 1: in parallel

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

## Wave 2: after wave 1 is merged

### D. English interface

**Goal:** English by default, French available.

- CLI: `--advise` and `--alert` messages, error notes, the starter config.
- GNOME extension: gettext with `fr` translations; labels, badges, advice.
- Docs in English, with the French UI mentioned as an option.
- Refresh the README "Development" section and the extension's Make
  command list after B: `make test` needs only gjs, `make test-live` needs
  `aiu`, and `pack` and `clean` are new.

**Why after wave 1:** it touches strings in almost every file, so it would
conflict with A, B and C, and A's tests guard it against regressions.

**Files:** `aiu.py`, `consult.py`, `gnome-extension/`, `README.md`, `docs/`.
**Done when:** CI is green, both languages render, and A's tests still pass.

## Follow-up: fixes from wave 1

Fix the probe bugs reported by A and the findings from C, in order of
severity, one PR per coherent group.

## Later

- **Snapshot history.** Keep a small local history of readings so the pace
  can be estimated right after a reset, when the current window has less than
  10% elapsed and is shown as "unknown pace" (SPARE's main idea in
  docs/routing-algorithms.md).
- **Versioning.** Tie the `pyproject.toml` version and the extension's
  `metadata.json` version to the release tag.
- **Price list.** Per-task-type prices for the paid fallbacks, only if it
  starts to matter which kind of work gets the spare quota.
