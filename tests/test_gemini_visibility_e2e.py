# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline CLI -> JSON -> GJS menu filtering, with a fake vendor CLI."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GJS = shutil.which("gjs")  # conftest later isolates PATH from real vendor CLIs
ARGON = "Gemini 4 Argon Weekly"


@pytest.mark.skipif(not GJS, reason="GJS is needed for the CLI/extension integration")
def test_argon_visibility_through_cli_and_extension(home, tmp_path):
    reply = tmp_path / "reply.json"
    agy = tmp_path / "bin" / "agy"
    agy.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                   f"if sys.argv[1:3] != ['-p', '/usage']: sys.exit(1)\n"
                   f"sys.stdout.write(Path({str(reply)!r}).read_text())\n")
    agy.chmod(0o755)
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[gemini]\nenabled = true\nagy_cmd = "{agy}"\n')
    argv = [sys.executable, str(ROOT / "aiu.py"), "--config", str(cfg), "--only", "gemini"]
    script = tmp_path / "menu.mjs"
    script.write_text(f'''import GLib from 'gi://GLib';
import {{ AiuClient, visibleQuotas }} from '{(ROOT / "gnome-extension/aiuClient.js").as_uri()}';
const client = new AiuClient();
client._resolveCommand = () => {json.dumps([*argv, '--json'])};
const loop = GLib.MainLoop.new(null, false);
let failed = false;
const timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 15, () => {{
    failed = true; loop.quit(); return GLib.SOURCE_REMOVE;
}});
client.fetchSnapshot().then(result => {{
    if (!result.success) throw new Error(JSON.stringify(result));
    print(JSON.stringify({{
        all: result.data.quotas.map(q => q.metric),
        shown: visibleQuotas(result.data.quotas).map(q => q.metric),
        errors: result.data.errors.map(e => e.note),
        full: result.data.fullQuotas.map(q => q.metric),
    }}));
}}).catch(e => {{ failed = true; printerr(e); }}).finally(() => loop.quit());
loop.run();
GLib.source_remove(timer);
if (failed) throw new Error('CLI/menu integration failed');
''')
    env = {**os.environ, "COLUMNS": "180", "PYTHONDONTWRITEBYTECODE": "1"}
    cache = home / ".local/state/aiu/readings/gemini.json"
    # Successive refreshes, including a fraction that the JSON rounds to 0%.
    for remaining in (1, 0.99999, 0.8, 0, 1, None):
        if remaining is None:
            payload = {"status": "ERROR", "num_turns": 0}
        else:
            payload = {"status": "SUCCESS", "num_turns": 0, "usage": {"total_tokens": 0},
                       "command": {"name": "usage", "data": {"groups": [
                           {"name": "Gemini Models", "buckets": [
                               {"window": "weekly", "remaining_fraction": 1}]},
                           {"name": "Gemini 4 Argon", "buckets": [
                               {"window": "weekly", "remaining_fraction": remaining}]}]}}}
        reply.write_text(json.dumps(payload))
        cache.unlink(missing_ok=True)
        table = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=15, check=True).stdout
        report = subprocess.run([GJS, "-m", str(script)], env=env, text=True,
                                capture_output=True, timeout=20, check=True)
        menu = json.loads(report.stdout)
        if remaining is None:
            assert menu["errors"] and "agy" in table
        else:
            assert ARGON in menu["all"]  # JSON always retains the quota
            assert (ARGON in table) == (remaining < 1)
            assert (ARGON in menu["shown"]) == (remaining < 1)
            assert "Weekly Limit" in table and "Weekly Limit" in menu["shown"]
            assert (ARGON in menu["full"]) == (remaining == 0)
            assert not menu["errors"]
