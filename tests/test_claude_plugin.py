# SPDX-License-Identifier: GPL-3.0-or-later
"""The Claude Code plugin in claude-code-plugin/ and the probe that reads its
file agree: the manifests list it where the README's install line says, and
register.ts writes the path and fields _read_claude_usage reads
(docs/json-schema.md, "claude-usage.json"). `claude plugin validate` and
`claude plugin test claude-code-plugin` check the plugin itself; CI has no
`claude`, so this only reads the files."""
import json
import re
from pathlib import Path

from aiu.probes import claude

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "claude-code-plugin"


def test_manifests():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert (manifest["name"], manifest["version"], manifest["license"]) == (
        "aiu-claude-usage", "0.1.0", "GPL-3.0-or-later")
    assert json.loads((PLUGIN / "hooks" / "hooks.json").read_text()) == {"modules": ["./register.ts"]}
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    [entry] = market["plugins"]
    assert entry["name"] == manifest["name"]
    assert (ROOT / entry["source"]).resolve() == PLUGIN
    assert claude.CLAUDE_PLUGIN_INSTALL == \
        f"/plugin install {manifest['name']} --marketplace citizenu03bb/aiu"
    assert claude.CLAUDE_PLUGIN_INSTALL in (PLUGIN / "README.md").read_text()
    assert (PLUGIN / "LICENSE").read_text() == (ROOT / "LICENSE").read_text()


def test_register_writes_what_the_probe_reads():
    src = (PLUGIN / "hooks" / "register.ts").read_text()
    assert f"/aiu/{claude.CLAUDE_USAGE_FILE}`" in src
    assert f"/.local/state/aiu/{claude.CLAUDE_USAGE_FILE}`" in src
    assert "schema: 1," in src and "source: 'claude-code'," in src
    assert set(re.findall(r"^\s+(\w+): ", src[src.index("const body"):], re.M)) >= {
        "schema", "source", "written_at", "rate_limits", "kind", "percent_used", "resets_at"}
