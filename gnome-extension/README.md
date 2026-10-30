# AI Usage Indicator (`aiu-indicator`)

GNOME Shell extension shipped with [`aiu`](../README.md); it shows the output of `aiu --json`.
It puts a small icon next to the clock in the top bar (GNOME 45 to 51+, tested on GNOME Shell 50.1, Wayland) and a menu with everything `aiu` collects.

## Getting started

1. **Install `aiu`**, the command-line tool this panel shows. It goes into `~/.local/bin`, where the extension looks first:

   ```bash
   uv tool install git+https://github.com/citizenu03bb/aiu
   ```

   Or `pipx install git+https://github.com/citizenu03bb/aiu` (`sudo apt install pipx` first if needed).
   Until `aiu` is there, the menu shows this command with **Copy command**, **Install guide** and **Check again** buttons, and the dot stays grey.
2. **Turn on the extension** from [extensions.gnome.org](https://extensions.gnome.org), or from the release zip (see [Install](#install)).
3. **Set up your providers.** On its first run `aiu` writes `~/.config/ai-usage/config.toml`. Turn on the subscriptions you pay for, and sign in to their command-line tools (`claude`, `codex`…). Until one can be read, the menu says so and has an **Open config** button. Each provider's error note says what it still needs. The [main README](../README.md#providers) has the details per provider.

## Features

- **Centre of the panel**: in the panel's centre box (`_centerBox`, index 1), just right of the clock.
- **Fully asynchronous**: `aiu` runs in the background through `Gio.Subprocess`, so GNOME Shell never freezes.
- **Status at a glance**:
  - Green dot: nothing to do.
  - Yellow dot: free quota will be lost at a reset within 24 h (use it).
  - Red dot: a window is full (≥ 95 %) or a probe failed (e.g. an expired Claude token).
  - Grey dot: setup is not finished (`aiu` is not installed, or no provider can be read yet).
- **Menu**:
  - **Subscriptions**: in routing order, each with a badge (burn first / unknown pace / on track / conserve), bars to scale coloured by what to do with them, and the time to the reset (e.g. `Reset in 4h27m · runs dry in ~11h33m at your pace`).
  - **Credits & paid APIs**: free credits with their expiry, prepaid balances, and lifetime spend (greyed out).
  - **Advice**: from the `advice` field of `aiu --json`: burn first the subscription (or credit) whose surplus expires first, and keep for later the ones that would run dry before their reset.
  - **Errors in full**: every probe error or expired token, with what to run to fix it.
  - **Refresh button**, plus an automatic refresh every 5 minutes (or when the menu opens and the data is over 2 minutes old).
  - **Shortcuts**: open `aiu` in a terminal, and links to the AI web consoles (AI Studio, Anthropic, ChatGPT, OpenRouter, DeepSeek).

## Languages

The menu is in English, or in French when your GNOME session is (Settings → System → Region & Language).
It uses gettext with the domain `aiu-indicator`: the sources are in `po/`, and `make mo`, `make pack` and `make install` compile them into `locale/`.
Text that comes from `aiu` itself, the probe errors and metric names, stays in English; the CLI's own `--advise` and `--alert` follow `AIU_LANG` or your locale (see the [main README](../README.md#language)).

To add a language or update one after changing strings (needs GNU gettext):

```bash
make pot                                         # po/aiu-indicator.pot
msginit -i po/aiu-indicator.pot -o po/de.po -l de   # new language
msgmerge -U po/fr.po po/aiu-indicator.pot           # existing one
```

Keep each `{placeholder}` as it is. The text is shown as plain text, never markup. `make check` fails when a `.po` file misses a string, keeps an unused one, or changes a placeholder.

## Install

Needs the `aiu` command in `~/.local/bin` or on the `PATH` (see the [main README](../README.md#install)).

```bash
cd gnome-extension   # in your clone of the aiu repository
make install         # then log out and back in (Wayland)
```

Without a clone, from the zip attached to each [GitHub release](https://github.com/citizenu03bb/aiu/releases) (or built with `make pack`, in the layout extensions.gnome.org expects):

```bash
gnome-extensions install --force aiu-indicator@citizenu03bb.github.io.shell-extension.zip
gnome-extensions enable aiu-indicator@citizenu03bb.github.io   # after logging out and back in
```

GNOME Shell keeps an extension's JavaScript in memory for the whole session: after an update, log out and back in (`make reload` does not reload the modules).

## Make commands

- `make check`: checks the syntax of every JavaScript file, `metadata.json`, and the translations against the strings in the code. Needs `node` and `python3`.
- `make test`: offline unit tests, the ones CI runs, in English and in French. Needs only `gjs` (and `python3` to compile the translations); the French run needs a UTF-8 locale other than `C`, or `localedef` with the glibc locale sources to build `fr_FR` on the fly.
- `make test-live`: runs the installed `aiu`, so it calls your providers. Local only, never in CI.
- `make mo`: compiles `po/*.po` into `locale/` (with `python3`, no GNU gettext needed).
- `make pot`: writes the translation template `po/aiu-indicator.pot` (needs `xgettext`).
- `make pack`: builds `aiu-indicator@citizenu03bb.github.io.shell-extension.zip` for extensions.gnome.org, compiled translations included.
- `make clean`: removes the zip and `locale/`.
- `make install`: compiles the translations, links this directory into `~/.local/share/gnome-shell/extensions/` and enables the extension.
- `make reload`: disables then re-enables the extension.
- `make uninstall`: disables the extension and removes the link.
- `make status`: shows the extension's state with `gnome-extensions info`.
- `make log`: follows GNOME Shell's log, filtered for this extension.

## Invariants and architecture

- **No misleading fallback**: when `aiu` fails or reports an error, the error is shown as text, never hidden behind made-up data.
- **Clean disable**: `disable()` removes every `GLib.Source` timeout, cancels the `Gio.Cancellable` in flight, kills a running `aiu` and destroys the Clutter actors.
- **Plain text only**: every label is `St.Label({ text })`, never markup, so provider text and translations cannot inject Pango markup.
