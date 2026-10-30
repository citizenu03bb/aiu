# extensions.gnome.org listing kit

Everything to paste or prepare when uploading `aiu-indicator` to
[extensions.gnome.org](https://extensions.gnome.org/upload/).

## 1. Upload

- Zip: `aiu-indicator@citizenu03bb.github.io.shell-extension.zip` from the
  latest [GitHub release](https://github.com/citizenu03bb/aiu/releases) (v0.3.0 or later).
- Sign in to extensions.gnome.org → **Upload** → pick the zip → accept the licence (GPL-3.0-or-later).
- The page's description comes from `metadata.json`. Paste the long text below
  when you edit the page after upload.

## 2. Page description (paste as is)

```text
Your AI subscriptions at a glance, next to the clock: Claude, Codex (ChatGPT),
Gemini, Mistral, DeepSeek, OpenRouter and OpenAI. See how much of each quota is
left, when it resets, and which one to use first so paid-for capacity is not
lost at the reset.

⚠ REQUIRES THE aiu COMMAND-LINE TOOL — install it first:

    uv tool install git+https://github.com/citizenu03bb/aiu
or
    pipx install git+https://github.com/citizenu03bb/aiu

Not installed yet? Open the panel: it shows the command with a Copy button.

Getting started
1. Install aiu (above).
2. Enable this extension.
3. On its first run aiu writes ~/.config/ai-usage/config.toml. Turn on the
   providers you pay for and sign in to their tools (claude, codex…). The
   panel's "Open config" button takes you there, and each provider's note
   says what it still needs.

What you get
• A dot in the top bar: green all good, yellow free quota expires within 24 h,
  red a quota is full or a provider failed, grey setup not finished.
• Each subscription with its usage bars, reset times and a routing badge
  (burn first / on track / conserve).
• Advice on which subscription to burn first.
• Prepaid credits and API balances.

Privacy: the extension itself makes no network calls and stores no secrets.
aiu reads your keys from where you tell it (environment, a command such as
secret-tool, or a file) and talks only to each provider's own API.

English and French. GNOME 45 to 51.
Source, docs and issues: https://github.com/citizenu03bb/aiu
```

## 3. Screenshots

Take them at 100 % scale, dark style, menu open:

1. **Normal use** — several subscriptions, the advice box, credits.
2. **First run** — the "One step left: install aiu" box with Copy command.

Reproducible from a nested shell:

```bash
MUTTER_DEBUG_DUMMY_MODE_SPECS=1280x800 dbus-run-session -- gnome-shell --devkit --wayland
```

## 4. Notes for the reviewer (paste in the upload comment)

```text
- The extension runs the user-installed `aiu --json` (~/.local/bin/aiu or PATH)
  with Gio.Subprocess, every 5 minutes and on refresh. No shell, fixed argv.
  The process is killed on disable() and after a 120 s watchdog.
- If aiu is missing, the menu only shows the install command and copies it to
  the clipboard on request. The extension never installs anything.
- No network access, no GSettings, no secrets: all data comes from aiu's JSON
  and is shown with St.Label text (never markup).
- "Open aiu in a terminal" launches gnome-terminal with a fixed, quoted command.
- Links open with Gio.AppInfo.launch_default_for_uri.
- Translations: gettext domain aiu-indicator, compiled .mo in locale/.
- Everything is created in enable() and destroyed in disable().
```

## 5. After approval

- Add the extensions.gnome.org link to the README install sections.
- For each release: bump the version, tag, upload the new zip from the release.
