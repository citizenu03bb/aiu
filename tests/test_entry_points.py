# SPDX-License-Identifier: GPL-3.0-or-later
"""The code is the aiu/ package; aiu.py at the root runs it from a checkout
(`./aiu.py`, or a launcher that execs it). `import aiu` is the package
everywhere, never the root script. No network."""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aiu  # noqa: E402
from aiu import cli  # noqa: E402


def test_import_aiu_is_the_package():
    assert Path(aiu.__file__) == ROOT / "aiu" / "__init__.py"
    assert Path(cli.__file__).parent == ROOT / "aiu"


def test_root_script_keeps_its_uv_metadata():
    src = (ROOT / "aiu.py").read_text()
    assert src.startswith("#!/usr/bin/env -S uv run --script\n# /// script\n")
    block = src.split("# /// script\n", 1)[1].split("# ///\n", 1)[0]
    meta = tomllib.loads("".join(line.removeprefix("#").removeprefix(" ") + "\n"
                                 for line in block.splitlines()))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert meta["dependencies"] == project["dependencies"]
    assert meta["requires-python"] == project["requires-python"]
    assert (ROOT / "aiu.py").stat().st_mode & 0o111


def launch(tmp_path, *args):
    """Run aiu.py the way the maintainer's ~/.local/bin/aiu does (a script that
    execs it), from another directory, with another `aiu` package on PYTHONPATH
    that must not be the one imported."""
    decoy = tmp_path / "decoy" / "aiu"
    decoy.mkdir(parents=True, exist_ok=True)
    (decoy / "__init__.py").write_text("raise SystemExit('imported the wrong aiu')\n")
    launcher = tmp_path / "bin" / "aiu"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "aiu.py"}" "$@"\n')
    launcher.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONSAFEPATH"}
    env["PYTHONPATH"] = str(decoy.parent)
    return subprocess.run([str(launcher), *args], cwd=decoy.parent, env=env,
                          capture_output=True, text=True, timeout=60)


def test_launcher_runs_the_checkout(tmp_path):
    out = launch(tmp_path, "--version")
    assert (out.returncode, out.stdout, out.stderr) == (0, f"aiu {aiu.__version__}\n", "")
    out = launch(tmp_path, "--help")
    assert out.returncode == 0 and out.stdout.startswith("usage: aiu.py ")


def test_launcher_json_snapshot(tmp_path, home):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[claude]\nenabled = false\n")
    cfg.chmod(0o600)
    out = launch(tmp_path, "--config", str(cfg), "--json")
    assert out.returncode == 0, out.stderr
    snap = json.loads(out.stdout)
    assert snap["rows"] == [] and snap["advice"] == []
