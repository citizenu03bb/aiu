# SPDX-License-Identifier: GPL-3.0-or-later
"""consult hands the prompt to codex and pi on stdin, never in argv (L6 in
docs/security-review.md). The CLIs are fakes in tmp/bin that record what they got."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import consult  # noqa: E402

BIG = "x" * (200 * 1024)  # over MAX_ARG_STRLEN (128 KiB): fails as an argument


@pytest.fixture
def fake_cli(tmp_path):
    """Install a fake CLI on PATH; return a function reading back its (argv, stdin)."""
    record = tmp_path / "record.json"

    def install(name):
        cli = tmp_path / "bin" / name
        cli.write_text(f"#!{sys.executable}\n"
                       "import json, sys\n"
                       f"json.dump([sys.argv[1:], sys.stdin.read()], open({str(record)!r}, 'w'))\n"
                       "print('answer')\n")
        cli.chmod(0o755)
        return lambda: tuple(json.loads(record.read_text()))
    return install


@pytest.mark.parametrize("prompt", ["--help", "-", "line one\n--version\n", BIG])
def test_codex_reads_prompt_from_stdin(fake_cli, prompt):
    got = fake_cli("codex")
    assert consult.call_codex(prompt) == (0, "answer", "")
    argv, stdin = got()
    assert argv == ["exec", "--skip-git-repo-check", "-"] and stdin == prompt


@pytest.mark.parametrize("prompt", ["--help", "--model=other", BIG])
def test_pi_reads_prompt_from_stdin(fake_cli, prompt):
    got = fake_cli("pi")
    assert consult.call_deepseek(prompt) == (0, "answer", "")
    argv, stdin = got()
    assert prompt not in argv and stdin == prompt
    assert argv[:1] == ["--print"] and "--no-tools" in argv


def test_agy_gets_prompt_as_option_value_and_no_stdin(fake_cli):
    got = fake_cli("agy")
    assert consult.call_gemini("hello")[0] == 0
    argv, stdin = got()
    assert argv[-2:] == ["-p", "hello"] and stdin == ""


def test_option_like_prompt_from_command_line(fake_cli, monkeypatch, capsys):
    got = fake_cli("codex")
    monkeypatch.setattr(sys, "argv", ["consult", "codex", "--help"])
    assert consult.main() == 0
    assert got() == (["exec", "--skip-git-repo-check", "-"], "--help")
    assert capsys.readouterr().out == "answer\n"


def test_missing_cli():
    assert consult.call_codex("hi")[0] == 127
