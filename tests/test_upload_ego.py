# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the release uploader without credentials or EGO network calls."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/upload-ego.sh"
PASSWORD = "fake password with spaces $() `quoted` ' and \""


@pytest.fixture
def uploader(tmp_path, monkeypatch):
    log = tmp_path / "upload.json"
    exe = tmp_path / "bin/gnome-extensions"
    exe.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                   f"from pathlib import Path\nPath({str(log)!r}).write_text(json.dumps({{"
                   "'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
                   "'env': {k: os.environ.get(k) for k in ('EGO_USERNAME', 'EGO_PASSWORD')}}))\n"
                   "sys.exit(int(os.environ.get('FAKE_UPLOAD_EXIT', '0')))\n")
    exe.chmod(0o755)
    monkeypatch.setenv("EGO_USERNAME", "test-owner")
    monkeypatch.setenv("EGO_PASSWORD", PASSWORD)
    archive = tmp_path / "release with spaces.shell-extension.zip"
    archive.write_bytes(b"synthetic ZIP: never sent to EGO")
    return archive, log


def run(*args):
    return subprocess.run(["/bin/bash", "-x", str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=5)


def test_uploads_the_same_file_once_with_password_on_stdin(uploader):
    archive, log = uploader
    before = archive.read_bytes()
    result = run(archive)
    assert result.returncode == 0
    request = json.loads(log.read_text())
    assert request == {
        "argv": ["upload", "--accept-tos", "--user", "test-owner", "--password-file=-", str(archive)],
        "stdin": PASSWORD,
        "env": {"EGO_USERNAME": None, "EGO_PASSWORD": None},
    }
    assert archive.read_bytes() == before
    assert PASSWORD not in result.stdout + result.stderr


@pytest.mark.parametrize("missing", ["EGO_USERNAME", "EGO_PASSWORD"])
def test_missing_configuration_fails_before_any_upload(uploader, monkeypatch, missing):
    archive, log = uploader
    monkeypatch.delenv(missing)
    result = run(archive)
    assert result.returncode == 2 and "Set EGO_USERNAME" in result.stderr
    assert not log.exists()
    assert PASSWORD not in result.stdout + result.stderr


@pytest.mark.parametrize("args", [[], ["missing.shell-extension.zip"], ["one", "two"]])
def test_bad_archive_fails_before_any_upload(uploader, args):
    _, log = uploader
    result = run(*args)
    assert result.returncode == 2 and "Usage:" in result.stderr
    assert not log.exists()


def test_upload_failure_is_propagated_without_retrying(uploader, monkeypatch):
    archive, log = uploader
    monkeypatch.setenv("FAKE_UPLOAD_EXIT", "17")
    result = run(archive)
    assert result.returncode == 17 and log.exists()
    assert PASSWORD not in result.stdout + result.stderr
