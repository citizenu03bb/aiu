# The JSON that `aiu` prints

The GNOME extension runs `aiu --json` every five minutes and
`aiu config show --json` from its preferences window; `consult` runs
`aiu --json` too. This page lists every field they read, its type and whether
it can be `null`. It is the contract between `aiu` and the extension, which
are installed and updated separately.

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
| `unit` | string: `%`, `USD`, `CNY`…, may be empty | no | extension |
| `reset` | string, human (`in 2h 0m`, `expires in 10d 0h`, `now`) | yes | extension, when `reset_epoch` is null |
| `note` | string, may be empty: plan name, allowance, or why an `error` row failed | no | extension |
| `reset_epoch` | number, Unix seconds of the reset | yes | extension |
| `window_secs` | integer, the quota window length | yes | not read |

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
| `detail` | string, a sentence in English | no | consult |

## `aiu config show --json`

An object. Keys in this order:

| Field | Type | Null | Read by |
|---|---|---|---|
| `schema` | integer | no | extension |
| `path` | string, the config file | no | extension (Open config file) |
| `key_cmd_allowed` | boolean: false when the file fails the trust check | no | extension |
| `key_cmd_problems` | array of strings, why it fails | no | extension |
| `providers` | object, provider id → provider, in aiu's order | no | extension |

A provider:

| Field | Type | Null | Read by |
|---|---|---|---|
| `section` | boolean, whether the file has the provider's table | no | not read |
| `options` | object, option name → option, in aiu's order | no | extension |

An option (`enabled`, `key_env`, `key_cmd`, `key_file`, `claude_cmd`,
`vibe_allowance_eur`, `api_allowance_eur`):

| Field | Type | Null | Read by |
|---|---|---|---|
| `type` | string: `bool`, `string` or `number` | no | extension |
| `value` | the value of that type | yes | extension |
| `source` | string: `file` or `default` | no | extension |

The window shows `enabled` as the provider's switch and each `string` or
`number` option as a field; `key_cmd` and `claude_cmd` are read-only when
`key_cmd_allowed` is false. An option of another type is left out.
