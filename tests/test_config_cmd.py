# SPDX-License-Identifier: GPL-3.0-or-later
"""`aiu config show` / `aiu config set`: the only way the GNOME preferences
window reads or changes config.toml. Values only, never a key; line-level
edits that leave every other byte alone; the security review's rules (0600,
key_cmd trust check, never echoing what was typed) hold here too."""
import json
import os
import sys
import tomllib
from pathlib import Path

import pytest

from aiu import cli, config, keys, probes, util
from aiu.probes import mistral

# A hand-edited config: comments everywhere, odd spacing, a quoted key, a
# section aiu doesn't know, an unknown key, CRLF-free but no final newline.
HAND_EDITED = """\
# my aiu setup — keep these notes!
# key_cmd = "this one is in the preamble, not in a section"

[claude]   # the main one
enabled=true # spacing as I like it

[openai]
# Admin key only.
"enabled" = false   # off for now
key_env = 'OPENAI_ADMIN_KEY'   # literal string
#key_cmd = "pass show openai/admin"   # once I move to pass
team = "unknown keys stay"

[my.own.table]
answer = 42

# Mistral notes, before its header.
[mistral]
enabled = true
    # vibe_allowance_eur = 8.5   # 0 hides that row
    api_allowance_eur = 5
# trailing comment of the mistral section"""


def run(monkeypatch, capsys, path, *args):
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(path), *args])
    capsys.readouterr()  # drop what the test's own setup printed
    code = cli.main()
    out, err = capsys.readouterr()
    return code, out, err


def show(monkeypatch, capsys, path) -> dict:
    code, out, _ = run(monkeypatch, capsys, path, "config", "show", "--json")
    assert code == 0
    return json.loads(out)


def opt(snap, provider, option) -> dict:
    return snap["providers"][provider]["options"][option]


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    """The (old, new) pairs of lines that differ; an inserted line pairs with ''."""
    a, b = before.splitlines(), after.splitlines()
    if len(b) > len(a):  # one inserted line: find it
        i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), len(a))
        assert a[i:] == b[i + 1:], "more than one line inserted"
        return [("", b[i])]
    assert len(a) == len(b)
    return [(x, y) for x, y in zip(a, b) if x != y]


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    path.parent.mkdir(mode=0o700)
    return path


def test_options_cover_every_probe():
    assert set(config.CONFIG_OPTIONS) == set(probes.PROBES)
    for options in config.CONFIG_OPTIONS.values():
        assert options["enabled"] == ("bool", False)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------
def test_show_creates_the_starter_config(monkeypatch, capsys, cfg):
    snap = show(monkeypatch, capsys, cfg)
    assert cfg.read_text() == config.CONFIG_TEMPLATE
    assert cfg.stat().st_mode & 0o777 == 0o600
    assert snap["path"] == str(cfg)
    assert snap["key_cmd_allowed"] is True and snap["key_cmd_problems"] == []
    assert list(snap["providers"]) == list(config.CONFIG_OPTIONS)
    assert opt(snap, "claude", "enabled") == {"type": "bool", "value": True, "source": "file"}
    assert opt(snap, "openai", "key_env") == {"type": "string", "value": "OPENAI_ADMIN_KEY",
                                              "source": "file"}
    # Commented out in the template: still the default.
    assert opt(snap, "openai", "key_cmd") == {"type": "string", "value": None, "source": "default"}
    assert opt(snap, "mistral", "vibe_allowance_eur") == {
        "type": "number", "value": mistral.MISTRAL_ALLOWANCE_EUR, "source": "default"}
    assert set(snap["providers"]["codex"]["options"]) == {"enabled"}


def test_show_never_resolves_a_key(monkeypatch, capsys, cfg, home, tmp_path):
    """Values only: no environment value, nothing from ~/.env, no key_cmd run,
    no key_file read."""
    marker = tmp_path / "key_cmd-ran"
    secret_file = tmp_path / "key.txt"
    secret_file.write_text("sk-FROM-FILE\n")
    cfg.write_text(f'[openai]\nenabled = true\nkey_env = "OPENAI_ADMIN_KEY"\n'
                   f'key_cmd = "touch {marker}; printf sk-%s FROMCMD"\nkey_file = "{secret_file}"\n'
                   f'[mistral]\nenabled = true\n')
    cfg.chmod(0o600)
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-FROM-ENV")
    (home / ".env").write_text("MISTRAL_ADMIN_COOKIE='session=FROM-DOTENV'\n")
    for args in (("--json",), ()):
        code, out, err = run(monkeypatch, capsys, cfg, "config", "show", *args)
        assert code == 0
        for secret in ("sk-FROM-ENV", "sk-FROMCMD", "sk-FROM-FILE", "FROM-DOTENV"):
            assert secret not in out + err
    assert not marker.exists()
    snap = show(monkeypatch, capsys, cfg)
    assert opt(snap, "openai", "key_file")["value"] == str(secret_file)  # the path, not the key
    assert opt(snap, "mistral", "key_env") == {"type": "string", "value": "MISTRAL_ADMIN_COOKIE",
                                               "source": "default"}


def test_show_reports_an_untrusted_config(monkeypatch, capsys, cfg):
    monkeypatch.setattr(util, "_private_group", lambda gid: False)
    config.ensure_config(cfg)
    cfg.chmod(0o620)
    snap = show(monkeypatch, capsys, cfg)
    assert snap["key_cmd_allowed"] is False
    assert snap["key_cmd_problems"] == [f"{cfg} is writable by group or others"]
    _, out, _ = run(monkeypatch, capsys, cfg, "config", "show")
    assert "key_cmd is ignored:" in out


def test_show_json_fixture_is_current(monkeypatch, capsys, home):
    """tests/fixtures/config-show.json is what `aiu config show --json` prints
    today for HAND_EDITED in an untrusted file, with the home directory written
    /home/me. gnome-extension/tests/test_aiuConfig.js reads the same file
    through parseConfigShow, so renaming a field on either side alone fails a
    test (docs/json-schema.md). AIU_RECORD_JSON=1 records it again."""
    monkeypatch.setattr(util, "_private_group", lambda gid: False)
    path = home / ".config" / "ai-usage" / "config.toml"
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(HAND_EDITED)
    path.chmod(0o620)
    code, out, err = run(monkeypatch, capsys, path, "config", "show", "--json")
    out = out.replace(str(home), "/home/me")
    fixture = Path(__file__).parent / "fixtures" / "config-show.json"
    if os.environ.get("AIU_RECORD_JSON"):
        fixture.write_text(out)
    assert code == 0 and err == ""
    assert out == fixture.read_text(), "aiu config show --json changed: see docs/json-schema.md"
    snap = json.loads(out)
    assert list(snap)[0] == "schema" and snap["schema"] == 1
    assert snap["key_cmd_problems"] == ["/home/me/.config/ai-usage/config.toml is writable by group or others"]


def test_show_table(monkeypatch, capsys, cfg):
    code, out, _ = run(monkeypatch, capsys, cfg, "config", "show")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == f"Config: {cfg}"
    assert lines[1].split() == ["Provider", "Option", "Value", "Source"]
    assert lines[2].split() == ["claude", "enabled", "true", "file"]
    assert ["openai", "key_cmd", "—", "default"] in [line.split() for line in lines]


def test_show_table_in_french(monkeypatch, capsys, cfg):
    monkeypatch.setenv("AIU_LANG", "fr")
    _, out, _ = run(monkeypatch, capsys, cfg, "config", "show")
    assert out.splitlines()[1].split() == ["Fournisseur", "Réglage", "Valeur", "Provenance"]
    assert ["claude", "enabled", "true", "fichier"] in [line.split() for line in out.splitlines()]


def test_show_refuses_a_config_that_does_not_parse(monkeypatch, capsys, cfg):
    cfg.write_text("[openai\nenabled = true\n")
    code, out, err = run(monkeypatch, capsys, cfg, "config", "show", "--json")
    assert code == 1 and out == ""
    assert err.startswith(f"aiu config: {cfg} does not parse:")


# ---------------------------------------------------------------------------
# set: round trips
# ---------------------------------------------------------------------------
STARTER_CHANGES = [
    # (option, value typed, value read back, (old line, new line))
    ("openai.enabled", "true", True, ("enabled = false", "enabled = true")),
    ("claude.enabled", "False", False, ("enabled = true", "enabled = false")),
    ("openai.key_env", "MY_KEY", "MY_KEY",
     ('key_env = "OPENAI_ADMIN_KEY"', 'key_env = "MY_KEY"')),
    # Commented out in the template: uncommented in place, not duplicated.
    ("openai.key_cmd", "secret-tool lookup service aiu provider openai",
     "secret-tool lookup service aiu provider openai",
     ('# key_cmd = "pass show openai/admin"',
      'key_cmd = "secret-tool lookup service aiu provider openai"')),
    ("deepseek.key_cmd", 'jq -r ".key" "C:\\new dir"', 'jq -r ".key" "C:\\new dir"',
     ('# key_cmd = "jq -r .api_key ~/.config/deepseek/config.json"',
      'key_cmd = "jq -r \\".key\\" \\"C:\\\\new dir\\""')),
    ("mistral.vibe_allowance_eur", "0", 0,
     ("# vibe_allowance_eur = 8.5   # 0 hides that row", "vibe_allowance_eur = 0   # 0 hides that row")),
    ("mistral.api_allowance_eur", "12.5", 12.5,
     ("# api_allowance_eur = 8.5", "api_allowance_eur = 12.5")),
    # Not in the section at all: appended after its last setting.
    ("openrouter.key_file", "~/.config/openrouter/key", "~/.config/openrouter/key",
     ("", 'key_file = "~/.config/openrouter/key"')),
    ("claude.claude_cmd", "~/bin/claude", "~/bin/claude", ("", 'claude_cmd = "~/bin/claude"')),
]


@pytest.mark.parametrize("option, typed, value, line", STARTER_CHANGES,
                         ids=[c[0] for c in STARTER_CHANGES])
def test_set_round_trips_on_the_starter_config(monkeypatch, capsys, cfg, option, typed, value, line):
    provider, name = option.split(".")
    before_snap = show(monkeypatch, capsys, cfg)
    before = cfg.read_text()
    code, out, err = run(monkeypatch, capsys, cfg, "config", "set", option, typed)
    assert (code, err) == (0, "")
    assert out == f"{option} set in {cfg}\n"
    after = cfg.read_text()
    assert changed_lines(before, after) == [line]
    snap = show(monkeypatch, capsys, cfg)
    assert opt(snap, provider, name) == {**opt(before_snap, provider, name),
                                         "value": value, "source": "file"}
    # Nothing else moved.
    snap["providers"][provider]["options"].pop(name)
    before_snap["providers"][provider]["options"].pop(name)
    assert snap == before_snap
    assert tomllib.loads(after) == {**tomllib.loads(before),
                                    provider: {**tomllib.loads(before)[provider], name: value}}


def test_appended_setting_goes_after_the_last_one_not_the_next_comment(monkeypatch, capsys, cfg):
    config.ensure_config(cfg)
    run(monkeypatch, capsys, cfg, "config", "set", "openrouter.key_file", "~/k")
    text = cfg.read_text()
    assert ('[openrouter]\nenabled = false\nkey_env = "OPENROUTER_API_KEY"\nkey_file = "~/k"\n\n'
            "# Mistral's monthly") in text


def test_setting_the_same_value_twice_is_stable(monkeypatch, capsys, cfg):
    run(monkeypatch, capsys, cfg, "config", "set", "openai.key_cmd", "pass show x")
    once = cfg.read_text()
    run(monkeypatch, capsys, cfg, "config", "set", "openai.key_cmd", "pass show x")
    assert cfg.read_text() == once
    assert once.count("key_cmd = ") == config.CONFIG_TEMPLATE.count("key_cmd = ")


HAND_EDITED_CHANGES = [
    # Trailing comment, spacing and the quoted key are kept.
    ("openai.enabled", "true", True,
     ('"enabled" = false   # off for now', '"enabled" = true   # off for now')),
    ("claude.enabled", "false", False,
     ("enabled=true # spacing as I like it", "enabled=false # spacing as I like it")),
    # A literal string becomes a basic string: only the value changes.
    ("openai.key_env", "OTHER", "OTHER",
     ("key_env = 'OPENAI_ADMIN_KEY'   # literal string", 'key_env = "OTHER"   # literal string')),
    # `#key_cmd` in [openai] is uncommented; the one in the preamble is left alone.
    ("openai.key_cmd", "pass show openai/admin", "pass show openai/admin",
     ('#key_cmd = "pass show openai/admin"   # once I move to pass',
      'key_cmd = "pass show openai/admin"   # once I move to pass')),
    # Indented commented-out line: uncommented with its indent.
    ("mistral.vibe_allowance_eur", "3", 3,
     ("    # vibe_allowance_eur = 8.5   # 0 hides that row",
      "    vibe_allowance_eur = 3   # 0 hides that row")),
    ("mistral.api_allowance_eur", "7.25", 7.25,
     ("    api_allowance_eur = 5", "    api_allowance_eur = 7.25")),
    # Missing from the section: after its last setting, with that setting's indent.
    ("mistral.key_env", "MISTRAL_COOKIE", "MISTRAL_COOKIE", ("", '    key_env = "MISTRAL_COOKIE"')),
]


@pytest.mark.parametrize("option, typed, value, line", HAND_EDITED_CHANGES,
                         ids=[c[0] for c in HAND_EDITED_CHANGES])
def test_set_keeps_a_hand_edited_config_byte_for_byte(monkeypatch, capsys, cfg,
                                                      option, typed, value, line):
    cfg.write_text(HAND_EDITED)
    cfg.chmod(0o600)
    provider, name = option.split(".")
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", option, typed)
    assert (code, err) == (0, "")
    after = cfg.read_text()
    assert changed_lines(HAND_EDITED, after) == [line]
    # The file had no final newline: the only other byte added is that one.
    assert after.endswith("# trailing comment of the mistral section\n")
    expected = tomllib.loads(HAND_EDITED)
    expected[provider][name] = value
    assert tomllib.loads(after) == expected
    assert opt(show(monkeypatch, capsys, cfg), provider, name)["value"] == value


def test_set_creates_a_missing_section_at_the_end(monkeypatch, capsys, cfg):
    cfg.write_text(HAND_EDITED + "\n")
    cfg.chmod(0o600)
    assert run(monkeypatch, capsys, cfg, "config", "set", "gemini.enabled", "true")[0] == 0
    assert cfg.read_text() == HAND_EDITED + "\n\n[gemini]\nenabled = true\n"
    snap = show(monkeypatch, capsys, cfg)
    assert snap["providers"]["gemini"]["section"] is True
    assert opt(snap, "gemini", "enabled") == {"type": "bool", "value": True, "source": "file"}
    assert tomllib.loads(cfg.read_text())["my"]["own"]["table"] == {"answer": 42}


def test_set_keeps_crlf_line_endings(monkeypatch, capsys, cfg):
    text = "# notes\r\n[openai]\r\nenabled = false\r\n\r\n[deepseek]\r\nenabled = false\r\n"
    cfg.write_bytes(text.encode())
    run(monkeypatch, capsys, cfg, "config", "set", "openai.key_env", "K")
    run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "true")
    run(monkeypatch, capsys, cfg, "config", "set", "codex.enabled", "true")
    assert cfg.read_bytes().decode() == (
        "# notes\r\n[openai]\r\nenabled = true\r\nkey_env = \"K\"\r\n\r\n"
        "[deepseek]\r\nenabled = false\r\n\r\n[codex]\r\nenabled = true\r\n")


def test_set_on_an_empty_file(monkeypatch, capsys, cfg):
    cfg.write_text("")
    run(monkeypatch, capsys, cfg, "config", "set", "codex.enabled", "true")
    assert cfg.read_text() == "[codex]\nenabled = true\n"


def test_set_creates_the_starter_config_first(monkeypatch, capsys, cfg):
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", "gemini.enabled", "true")
    assert code == 0 and "wrote a starter config" in err
    assert changed_lines(config.CONFIG_TEMPLATE, cfg.read_text()) == [
        ("enabled = false", "enabled = true")]
    assert cfg.stat().st_mode & 0o777 == 0o600
    assert cfg.parent.stat().st_mode & 0o777 == 0o700


def test_empty_string_turns_a_source_off(monkeypatch, capsys, cfg):
    run(monkeypatch, capsys, cfg, "config", "set", "openai.key_env", "")
    assert tomllib.loads(cfg.read_text())["openai"]["key_env"] == ""
    assert keys.resolve_key(tomllib.loads(cfg.read_text())["openai"], "openai") is None


# ---------------------------------------------------------------------------
# set: writing
# ---------------------------------------------------------------------------
def test_write_is_atomic_and_private(monkeypatch, capsys, cfg):
    config.ensure_config(cfg)
    cfg.chmod(0o644)
    inode = cfg.stat().st_ino
    run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "true")
    assert cfg.stat().st_mode & 0o777 == 0o600
    assert cfg.stat().st_ino != inode  # replaced, not rewritten in place
    leftovers = {p.name for p in cfg.parent.iterdir()} - {"config.toml", ".config.toml.aiu-lock"}
    assert leftovers == set()


def test_symlinked_config_stays_a_symlink(monkeypatch, capsys, cfg, tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir(mode=0o700)
    target = dotfiles / "aiu.toml"
    target.write_text(config.CONFIG_TEMPLATE)
    target.chmod(0o600)
    cfg.symlink_to(target)
    run(monkeypatch, capsys, cfg, "config", "set", "openai.key_cmd", "pass show openai")
    assert cfg.is_symlink() and cfg.resolve() == target
    assert tomllib.loads(target.read_text())["openai"]["key_cmd"] == "pass show openai"
    assert target.stat().st_mode & 0o777 == 0o600


def test_refuses_a_config_that_is_not_yours(monkeypatch, capsys, cfg):
    config.ensure_config(cfg)
    monkeypatch.setattr(os, "getuid", lambda: cfg.stat().st_uid + 1)
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "true")
    assert code == 1 and err == f"aiu config: {cfg} is not yours\n"
    assert cfg.read_text() == config.CONFIG_TEMPLATE


@pytest.mark.parametrize("option", ["openai.key_cmd", "claude.claude_cmd"])
@pytest.mark.parametrize("mode, dir_mode", [(0o620, 0o700), (0o602, 0o700), (0o600, 0o777)])
def test_refuses_a_command_in_an_untrusted_config(monkeypatch, capsys, cfg, option, mode, dir_mode):
    monkeypatch.setattr(util, "_private_group", lambda gid: False)
    config.ensure_config(cfg)
    cfg.chmod(mode)
    cfg.parent.chmod(dir_mode)
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", option, "echo sk-x")
    assert code == 1
    assert err.startswith(f"aiu config: {option} refused: ")
    assert "writable by group or others" in err and "echo sk-x" not in err
    assert cfg.read_text() == config.CONFIG_TEMPLATE
    # Other settings, and turning a command off, still work.
    assert run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "true")[0] == 0
    assert run(monkeypatch, capsys, cfg, "config", "set", option, "")[0] == 0


def test_refuses_a_command_through_a_shared_symlink(monkeypatch, capsys, cfg, tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    target = shared / "config.toml"
    target.write_text(config.CONFIG_TEMPLATE)
    target.chmod(0o600)
    cfg.symlink_to(target)
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", "openai.key_cmd", "echo x")
    assert code == 1 and "shared is writable by group or others" in err


# ---------------------------------------------------------------------------
# set: validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("option, value, message", [
    ("nope.enabled", "true", "unknown provider; one of: claude, codex, openai, deepseek, "
                             "openrouter, gemini, mistral"),
    ("openai", "true", "unknown option for openai; it takes: enabled, key_env, key_cmd, key_file"),
    ("codex.key_env", "X", "unknown option for codex; it takes: enabled"),
    ("openai.api_key", "sk-SECRET", "unknown option for openai; it takes: enabled, key_env, "
                                    "key_cmd, key_file"),
    ("openai.enabled", "sk-SECRET", "enabled takes true or false"),
    ("openai.enabled", "1", "enabled takes true or false"),
    ("mistral.api_allowance_eur", "sk-SECRET", "api_allowance_eur takes a number, 0 or more"),
    ("mistral.api_allowance_eur", "-1", "api_allowance_eur takes a number, 0 or more"),
    ("mistral.api_allowance_eur", "nan", "api_allowance_eur takes a number, 0 or more"),
    ("mistral.api_allowance_eur", "inf", "api_allowance_eur takes a number, 0 or more"),
    # A key pasted where its variable's name goes.
    ("openai.key_env", "sk-SECRET-123", "key_env takes a variable name (letters, digits and _) "
                                        "or an empty string"),
    ("openai.key_cmd", "pass show x\nsk-SECRET", "key_cmd cannot hold control characters"),
    ("openai.key_file", "/tmp/sk-SECRET\x00", "key_file cannot hold control characters"),
])
def test_set_validates_and_never_echoes_the_value(monkeypatch, capsys, cfg, option, value, message):
    config.ensure_config(cfg)
    code, out, err = run(monkeypatch, capsys, cfg, "config", "set", option, value)
    assert (code, out) == (1, "")
    assert err == f"aiu config: {message}\n"
    assert "SECRET" not in err
    assert cfg.read_text() == config.CONFIG_TEMPLATE


def test_validation_message_in_french(monkeypatch, capsys, cfg):
    monkeypatch.setenv("AIU_LANG", "fr")
    config.ensure_config(cfg)
    _, _, err = run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "oui")
    assert err == "aiu config : enabled prend true ou false\n"


def test_refuses_to_edit_a_value_that_spans_lines(monkeypatch, capsys, cfg):
    text = '[openai]\nkey_cmd = """\npass show openai\n"""\n'
    cfg.write_text(text)
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", "openai.key_cmd", "x")
    assert code == 1
    assert err == "aiu config: cannot change openai.key_cmd in place: edit it by hand\n"
    assert cfg.read_text() == text


def test_refuses_an_edit_that_would_not_hold_the_value(monkeypatch, capsys, cfg):
    """A dotted key outside the section: a new [openai] table would not parse,
    so nothing is written."""
    text = 'openai.enabled = false\n'
    cfg.write_text(text)
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "true")
    assert code == 1 and "cannot change openai.enabled in place" in err
    assert cfg.read_text() == text


def test_refuses_a_config_that_does_not_parse(monkeypatch, capsys, cfg):
    text = "[openai]\nenabled = maybe\n"
    cfg.write_text(text)
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "true")
    assert code == 1 and err.startswith(f"aiu config: {cfg} does not parse:")
    assert cfg.read_text() == text


def test_refuses_a_provider_that_is_not_a_table(monkeypatch, capsys, cfg):
    cfg.write_text('openai = "on"\n')
    code, _, err = run(monkeypatch, capsys, cfg, "config", "set", "openai.enabled", "true")
    assert code == 1 and err == f"aiu config: openai in {cfg} is not a table\n"


# ---------------------------------------------------------------------------
# The rest of aiu is unchanged
# ---------------------------------------------------------------------------
def test_without_a_subcommand_aiu_takes_its_snapshot(monkeypatch, capsys, cfg):
    cfg.write_text("")  # nothing enabled: no probe, no network
    code, out, _ = run(monkeypatch, capsys, cfg, "--json")
    assert code == 0
    assert set(json.loads(out)) == {"schema", "captured_at", "rows", "advice"}


def test_config_option_after_the_subcommand(monkeypatch, capsys, cfg):
    monkeypatch.setattr(sys, "argv", ["aiu", "config", "--config", str(cfg), "show", "--json"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["path"] == str(cfg)


def test_config_needs_an_action(monkeypatch, capsys, cfg):
    monkeypatch.setattr(sys, "argv", ["aiu", "--config", str(cfg), "config"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
    assert not cfg.exists()
