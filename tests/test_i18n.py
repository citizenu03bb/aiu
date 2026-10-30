# SPDX-License-Identifier: GPL-3.0-or-later
"""English by default, French on request: the language choice, and every
message having a French text with the same placeholders."""
import ast
import string
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aiu  # noqa: E402
import consult  # noqa: E402


@pytest.mark.parametrize("env, lang", [
    ({}, "en"),
    ({"LANG": "fr_FR.UTF-8"}, "fr"),
    ({"LANG": "fr_FR.UTF-8", "LC_MESSAGES": "en_US.UTF-8"}, "en"),
    ({"LANG": "en_US.UTF-8", "LC_ALL": "fr_CA.UTF-8"}, "fr"),
    ({"LANG": "fr_FR.UTF-8", "AIU_LANG": "en"}, "en"),
    ({"AIU_LANG": "fr"}, "fr"),
    ({"AIU_LANG": "de"}, "en"),
])
def test_language_choice(monkeypatch, env, lang):
    monkeypatch.delenv("LANG", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert aiu.ui_lang() == lang


def tr_literals(module) -> set[str]:
    """The first argument of every tr("...") call whose text is a literal."""
    tree = ast.parse(Path(module.__file__).read_text())
    return {n.args[0].value for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tr"
            and n.args and isinstance(n.args[0], ast.Constant)}


def constants(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


@pytest.mark.parametrize("module", [aiu, consult], ids=["aiu", "consult"])
def test_every_message_has_a_french_text(module):
    assert tr_literals(module) - module.FR.keys() == set()
    # No stale entry: each key is still written somewhere in the source.
    assert module.FR.keys() - constants(module) == set()
    for en, fr in module.FR.items():
        assert fields(fr) == fields(en), en
        assert fr != en, en


def test_provider_hints_have_a_french_text():
    assert set(aiu.PROVIDER_HINTS.values()) <= aiu.FR.keys()


def test_french_starter_config_has_the_same_settings():
    assert tomllib.loads(aiu.CONFIG_TEMPLATE_FR) == tomllib.loads(aiu.CONFIG_TEMPLATE)


def test_starter_config_and_warnings_in_french(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    path = tmp_path / "config.toml"
    aiu.load_config(path)
    assert path.read_text() == aiu.CONFIG_TEMPLATE_FR
    assert "config de départ écrite dans" in capsys.readouterr().err


def test_english_starter_config_by_default(tmp_path):
    path = tmp_path / "config.toml"
    aiu.load_config(path)
    assert path.read_text() == aiu.CONFIG_TEMPLATE


def test_consult_messages_in_french(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["consult", "nope", "hi"])
    assert consult.main() == 2
    assert "unknown provider 'nope'" in capsys.readouterr().err
    monkeypatch.setenv("AIU_LANG", "fr")
    assert consult.main() == 2
    assert "fournisseur inconnu 'nope'" in capsys.readouterr().err
