# aiu

Instructions for any coding agent (Claude Code, Codex, Gemini/Antigravity, Cursor, Vibe…).

Python CLI (`aiu/`, `consult.py`) and a GNOME Shell extension
(`gnome-extension/`) that show AI subscription quotas. Public repo:
`pipx install git+…` installs `main`, and `/plugin install` reads the Claude
Code plugin (`claude-code-plugin/`) from `main`, so `main` must always work and
the README must match the code.

## Tests

- Python, offline only (no network, no real keys):
  `uv run --python 3.12 --with pytest --with httpx --with rich pytest -q tests`
- Extension: `cd gnome-extension && make check test` (GJS).
- `tests/conftest.py` gives each test an empty home, and a PATH holding only
  `tmp/bin`: shell scripts in tests must name tools in full (`/bin/cat`).
  Unscripted HTTP calls fail the test.
- `--json` fixtures in `tests/fixtures/` are recorded with
  `AIU_RECORD_JSON=1` (see `docs/json-schema.md`); the GJS tests read them too.

## Conventions

- Work follows `ROADMAP.md`: one task per PR, titled "… (wave N, task X)",
  squash-merged. Stay inside the task's listed files.
- `--json` and `config show --json` are at `"schema": 1`: add fields, never
  rename or remove them without bumping both sides.
- Every user-facing string goes through `tr()` with French in `aiu/i18n.py`
  (`tests/test_i18n.py` enforces it); extension strings go in `po/fr.po`.
- Every option named `*_cmd` runs a program (`runs_code`): trust-checked.
  Vendor CLIs are run through `aiu/clis.py`, never found on PATH.
- aiu never holds another program's sign-in: it asks the vendor's own client
  (the Claude Code plugin, `codex app-server`, `agy /usage`).
- Each user-visible change adds a line to the README's "What changed in
  v<next>" list.
