# The JSON that `aiu` prints

The GNOME extension runs `aiu --json` every five minutes and
`aiu config show --json` from its preferences window; `consult` runs
`aiu --json` too. This page lists every field they read, its type and whether
it can be `null`. It is the contract between `aiu` and the extension, which
are installed and updated separately.

A second contract, read by aiu and written by the Claude Code plugin in
`claude-code-plugin/`, is at the end: [`claude-usage.json`](#claude-usagejson).

## The schema number

Both outputs start with `"schema": 1`, the version of this contract
(`JSON_SCHEMA` in `aiu/render.py`, `SCHEMA` in `gnome-extension/aiuClient.js`).

- **Adding** a field keeps the number: readers ignore fields they do not know.
- **Renaming** or **removing** a field, or **changing its type** (including
  making a field `null` that never was), bumps it.

The extension reads schema 1. An aiu from before the field printed no
`schema` at all, which the extension reads as 1. Any other number gives a
message instead of a parse error, in the menu and in the preferences window:
"aiu speaks a newer format (schema 2): update the extension" for a higher
number, "aiu speaks an older format (schema 0): update aiu (…)" for anything
else. A bump therefore breaks every installed
extension until it is updated too: bump only when a field cannot simply be
added next to the old one.

To change the contract: change the field in aiu and in the extension, bump
both numbers if the rule above says so, record the fixtures again
(`AIU_RECORD_JSON=1 uv run --with pytest --with httpx --with rich pytest tests/test_json_output.py tests/test_config_cmd.py`),
update the GJS tests and this page.

## How the two sides are kept together

- `tests/fixtures/snapshot.json` is `aiu --json` on recorded probe answers
  with a frozen clock (`tests/test_json_output.py`): most providers answer,
  two fail, and the advice has `burn`, `?` and `conserve` lots.
- `tests/fixtures/config-show.json` is `aiu config show --json` on a
  hand-edited config that fails the trust check (`tests/test_config_cmd.py`).
- The Python tests print both again and fail when the output differs from the
  checked-in file. The GJS tests (`gnome-extension/tests/test_aiuClient.js`,
  `test_aiuConfig.js`) read the same files through `AiuClient._processData`
  and `parseConfigShow` and check every field below. A field renamed on one
  side only fails one of them.

## `aiu --json`

An object. Keys in this order:

| Field | Type | Null | Read by |
|---|---|---|---|
| `schema` | integer | no | extension |
| `captured_at` | string, ISO 8601 with offset (`2026-10-26T07:33:20+00:00`) | no | extension (header time) |
| `rows` | array of rows, in probe order | no | extension |
| `advice` | array of lots, in routing order | no | extension, consult |

### Rows

One per metric a probe read, or one `error` row for a probe that failed.

| Field | Type | Null | Read by |
|---|---|---|---|
| `provider` | string: `claude`, `codex`, `openai`, `deepseek`, `openrouter`, `gemini`, `mistral` | no | extension |
| `metric` | string, a label (`5-Hour Limit`, `Spend (MTD)`, `—` on an error) | no | extension |
| `kind` | string: `quota`, `spend`, `balance`, `credit` or `error` | no | extension |
| `used` | number. `quota`: fraction used, 0 to 1; `spend`, `balance`: amount; `credit`: amount left | yes | extension |
| `used_pct` | number, `used` × 100 rounded to 0.1. Only on a `quota` row whose `used` is a number; absent otherwise | absent | extension |
| `limit` | number. `quota`: 1; `credit`: the grant; otherwise a budget when known | yes | extension (credit) |
| `unit` | string: `%`, `USD`, `CNY`…, `reset` on a Codex rate-limit reset (a `credit` of 1, `used` 1 while it is unused), may be empty | no | extension |
| `reset` | string, human (`in 2h 0m`, `expires in 10d 0h`, `now`) | yes | extension, when `reset_epoch` is null |
| `note` | string, may be empty: allowance, the age of an old Claude Code reading (`Claude Code reading from 2h 5m ago`), or why an `error` row failed | no | extension (beside the provider's name, the first note among its rows) |
| `reset_epoch` | number, Unix seconds of the reset | yes | extension |
| `window_secs` | integer, the quota window length | yes | not read |
| `sublimit` | boolean: a quota on one model or product inside the plan (Gemini 4 Argon, Claude's `Weekly Sonnet`, a Codex limit other than `codex`). Used up, it stops only that one: the extension's dot turns yellow, not red. An aiu from before the field prints none, read as false | no | extension |
| `plan` | string, may be empty: the subscription's plan as the vendor's own client names it (`plus` from `codex app-server`, `pro` from `claude auth status`, `Free` from Mistral Vibe), on every row of that provider that is not an `error`; empty when the client says none (Gemini: agy reports no tier). At most 32 letters, digits, spaces and `_.+-`. Before it, Codex's plan was the first row's `note` | no | extension (a pill beside the provider's name) |

The extension groups rows by `kind`: `quota` rows are bars, `spend`,
`balance` and `credit` rows are amounts, `error` rows are alerts. An
unreadable `quota` (`used` null) shows as unknown, never as 0 %.

### Advice lots

The routing order (`aiu --advise`): `burn` and `?` by deadline, then
`on_track`, then `conserve`. See `docs/routing-algorithms.md`.

| Field | Type | Null | Read by |
|---|---|---|---|
| `provider` | string, as in rows | no | extension, consult |
| `metric` | string, the row's `metric` | no | extension |
| `status` | string: `burn`, `?` (unknown pace), `on_track` or `conserve` | no | extension, consult |
| `ttr_secs` | integer, seconds to the reset | yes | extension |
| `leftover` | number, fraction of the cap left at the reset at the current pace (< 0: runs dry first) | yes | extension |
| `runway_secs` | integer, seconds until a `conserve` lot runs dry | yes | extension |
| `pace_from_history` | boolean | no | not read |
| `early_pace` | boolean: true on a `conserve` lot whose window is under 10% elapsed, with no history, and already used fast (see `docs/routing-algorithms.md`) | no | not read |
| `detail` | string, a sentence in English | no | consult |

## `aiu config show --json`

An object. Keys in this order:

| Field | Type | Null | Read by |
|---|---|---|---|
| `schema` | integer | no | extension |
| `path` | string, the config file | no | extension (Open config file) |
| `key_cmd_allowed` | boolean: false when the file fails the trust check, so that no `*_cmd` option runs | no | extension |
| `key_cmd_problems` | array of strings, why it fails | no | extension |
| `providers` | object, provider id → provider, in aiu's order | no | extension |

A provider:

| Field | Type | Null | Read by |
|---|---|---|---|
| `section` | boolean, whether the file has the provider's table | no | not read |
| `options` | object, option name → option, in aiu's order | no | extension |

An option (`enabled`, `key_env`, `key_cmd`, `key_file`, `read_token`,
`claude_cmd`, `codex_cmd`, `agy_cmd`, `vibe_cmd`, `vibe_allowance_eur`, `api_allowance_eur`):

| Field | Type | Null | Read by |
|---|---|---|---|
| `type` | string: `bool`, `string` or `number` | no | extension |
| `value` | the value of that type | yes | extension |
| `source` | string: `file` or `default` | no | extension |

The window shows `enabled` as the provider's switch, each other `bool` option
(`read_token`) as a switch of its own, and each `string` or `number` option as
a field; every `*_cmd` field (`key_cmd`, `claude_cmd`, any vendor client's
program) is read-only when `key_cmd_allowed` is false. An option of another type is left out.

## `claude-usage.json`

Written by the `aiu-claude-usage` Claude Code plugin
(`claude-code-plugin/hooks/register.ts`), read by the `claude` probe
(`_read_claude_usage` in `aiu/probes/claude.py`). The plugin owns this format:
change both sides together, and bump `schema` on the same rule as above. They
are installed separately (the plugin from Claude Code, aiu with pipx or uv), so
aiu reads only `"schema": 1` and takes any other file as absent.

**Where.** `$XDG_STATE_HOME/aiu/claude-usage.json` when `XDG_STATE_HOME` is an
absolute path, else `~/.local/state/aiu/claude-usage.json`: the directory of
aiu's pace history (`state_dir` in `aiu/util.py`).

**When.** At `session.start`, from `$.session.usage()`, and on `session.measure`
when its `changed` names `rateLimits` (after each turn, when a window moved a
whole point, appeared or left). Never when there is no window to report.
The whole file is replaced each time.

An object:

| Field | Type | Null | Read by |
|---|---|---|---|
| `schema` | integer, `1` | no | aiu: any other value, the file is absent |
| `source` | string, `claude-code` | no | aiu: any other value, the file is absent |
| `written_at` | string, ISO 8601 UTC with milliseconds (`2026-10-30T22:11:46.367Z`) | no | aiu: the reading's age |
| `rate_limits` | array of windows, as Claude Code lists them | no | aiu |

A window (Claude Code's `SessionRateLimit`):

| Field | Type | Null | Read by |
|---|---|---|---|
| `kind` | string: `five_hour`, `seven_day`, or another window (a Claude gateway's `spend_limit`) | no | aiu: the row's label; a kind aiu has no label for is skipped |
| `percent_used` | number, 0 to 100 with at most one decimal (past 100 on an exceeded spend limit) | no | aiu: `used` = `percent_used` / 100 |
| `resets_at` | string, ISO 8601 | yes | aiu: `reset_epoch` |

How aiu reads it:

- The file is absent when it is missing, over 64 KiB, not JSON, of another
  `schema` or `source`, has a field of the wrong type (a `percent_used` that is
  not a number from 0 to 1000, a `resets_at` that is not a date), more than 32
  windows, a `written_at` more than 5 minutes ahead of the clock, or no window
  aiu has a label for.
- Rows come out as from the usage endpoint: `5-Hour Limit` (`five_hour`),
  `Weekly (all models)` (`seven_day`), and the other labels in
  `CLAUDE_QUOTA_LABELS`, each a `quota` row with its window length.
- A window whose `resets_at` has passed is at 0 %, its `reset` and
  `reset_epoch` null: it reset since the reading, and the next reset is not
  known until Claude Code reports again. A window without `resets_at` counts as
  reset once the reading is older than the window.
- A reading older than an hour (`CLAUDE_USAGE_STALE`) puts its age in each
  row's note (`Claude Code reading from 2h 5m ago`), and is used only while at
  least one window has yet to reset. Past that, the row is an `error` that says
  how old the last reading is.
- Credits (`Cloud Credit`) and windows only the usage endpoint reports are not
  in this file. With `claude.read_token = true` the token read adds them; the
  file's windows take precedence over the endpoint's for the same label.
