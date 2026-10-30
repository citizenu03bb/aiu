# SPDX-License-Identifier: GPL-3.0-or-later
"""One release version everywhere: aiu.__version__ is the source, pyproject.toml
reads it, and the extension's version-name repeats it."""
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aiu  # noqa: E402


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", aiu.__version__)


def test_pyproject_reads_the_version_from_aiu():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "aiu.__version__"}


def test_extension_version_name_matches():
    metadata = json.loads((ROOT / "gnome-extension" / "metadata.json").read_text())
    assert metadata["version-name"] == aiu.__version__
    assert isinstance(metadata["version"], int)  # extensions.gnome.org's own counter


def test_version_flag(capsys):
    sys.argv = ["aiu", "--version"]
    try:
        aiu.main()
    except SystemExit as e:
        assert e.code == 0
    assert capsys.readouterr().out.strip() == f"aiu {aiu.__version__}"
