# aiu-claude-usage

A Claude Code plugin for [aiu](https://github.com/citizenu03bb/aiu). It writes
the rate-limit figures Claude Code already shows you (the 5-hour and weekly
windows, as a percentage used and the time each one resets) to a small file
that aiu reads. With it, aiu shows your Claude Max quotas without ever reading
your Claude.ai sign-in token.

## What it writes, and where

One file, replaced each time:

- `$XDG_STATE_HOME/aiu/claude-usage.json` when `XDG_STATE_HOME` is set to an
  absolute path,
- else `~/.local/state/aiu/claude-usage.json`.

That is the directory aiu keeps its pace history in. The file looks like this:

```json
{
  "schema": 1,
  "source": "claude-code",
  "written_at": "2026-10-30T22:11:46.367Z",
  "rate_limits": [
    { "kind": "five_hour", "percent_used": 19, "resets_at": "2026-10-31T02:30:00.000Z" },
    { "kind": "seven_day", "percent_used": 16, "resets_at": "2026-11-06T15:00:00.000Z" }
  ]
}
```

Its format is described field by field in aiu's
[`docs/json-schema.md`](../docs/json-schema.md#claude-usagejson).

It is written when a session starts with a reading already in hand, and after
a turn whenever a window moved by a whole point. A session that is not on a
Claude subscription, or has had no response yet, writes nothing.

## What it does not do

- It reads **no token** and no credentials file. The figures come from Claude
  Code's plugin API (`session.measure`, `$.session.usage()`), the same ones its
  status line shows.
- It reads **no prompt, reply, file or tool call**, and writes nothing but the
  figures above.
- It makes no network call and runs no program.

## Install

At the prompt of a Claude Code session in a terminal:

```
/plugin install aiu-claude-usage --marketplace citizenu03bb/aiu
```

Answer `y` to add the marketplace, then pick the user scope so it runs in every
session. From a shell:

```sh
claude plugin install aiu-claude-usage --marketplace citizenu03bb/aiu
```

In the Claude desktop app, where `/plugin` does not run: click **+** next to
the prompt box, then **Plugins**, **Add marketplace** with `citizenu03bb/aiu`,
then **Add plugin**. A plugin installed from a terminal at the user scope runs
in the desktop app too.

The file appears after the next response in any session. Check it with
`aiu --only claude`.

## Developing

```sh
claude plugin validate claude-code-plugin
claude plugin test claude-code-plugin
claude --plugin-dir claude-code-plugin    # a session that loads this folder
```

## License

GPL-3.0-or-later, like aiu. See [LICENSE](LICENSE).
