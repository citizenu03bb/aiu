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
from aiu import config, i18n, routing  # noqa: E402
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
    assert i18n.ui_lang() == lang


def nodes(module):
    """Every AST node of a module, or of every file of a package."""
    path = Path(module.__file__)
    for src in sorted(path.parent.rglob("*.py")) if path.name == "__init__.py" else [path]:
        yield from ast.walk(ast.parse(src.read_text()))


def tr_literals(module) -> set[str]:
    """The first argument of every tr("...") call whose text is a literal."""
    return {n.args[0].value for n in nodes(module)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tr"
            and n.args and isinstance(n.args[0], ast.Constant)}


def constants(module) -> set[str]:
    return {n.value for n in nodes(module) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


@pytest.mark.parametrize("module, table", [(aiu, i18n.FR), (consult, consult.FR)], ids=["aiu", "consult"])
def test_every_message_has_a_french_text(module, table):
    assert tr_literals(module) - table.keys() == set()
    # No stale entry: each key is still written somewhere in the source.
    assert table.keys() - constants(module) == set()
    for en, fr in table.items():
        assert fields(fr) == fields(en), en
        assert fr != en, en


def test_provider_hints_have_a_french_text():
    assert set(routing.PROVIDER_HINTS.values()) <= i18n.FR.keys()


def test_french_starter_config_has_the_same_settings():
    assert tomllib.loads(config.CONFIG_TEMPLATE_FR) == tomllib.loads(config.CONFIG_TEMPLATE)


def test_starter_config_and_warnings_in_french(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    path = tmp_path / "config.toml"
    config.load_config(path)
    assert path.read_text() == config.CONFIG_TEMPLATE_FR
    assert "config de départ écrite dans" in capsys.readouterr().err


def test_english_starter_config_by_default(tmp_path):
    path = tmp_path / "config.toml"
    config.load_config(path)
    assert path.read_text() == config.CONFIG_TEMPLATE


def test_consult_messages_in_french(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["consult", "nope", "hi"])
    assert consult.main() == 2
    assert "unknown provider 'nope'" in capsys.readouterr().err
    monkeypatch.setenv("AIU_LANG", "fr")
    assert consult.main() == 2
    assert "fournisseur inconnu 'nope'" in capsys.readouterr().err


def test_config_notices_have_a_french_text():
    assert config.CONFIG_NOTICES and set(config.CONFIG_NOTICES.values()) <= i18n.FR.keys()
