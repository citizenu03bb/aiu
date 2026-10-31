# SPDX-License-Identifier: GPL-3.0-or-later
"""aiu — one local snapshot of every AI subscription/quota you pay for.

One module per concern; aiu.cli.main is the `aiu` command:
  probes/   one module per provider (a Probe each), the registry and collect()
  rows      Row, the normalized record every probe returns
  keys      key_env / key_cmd / key_file, and ~/.env
  config    the starter config, the trust check, `aiu config show/set`
  clis      vendors' command-line tools: which binary, how to run it, a cache
  cookies   `aiu cookie import mistral`
  history   the pace history file
  routing   SURPLUS-EDF: route_order (--json, consult) and advise (--advise)
  render    the table, --html and --json
  notify    --alert and its desktop notification
  i18n      tr() and the French table
  util      who else can write a file
"""

# The release version: pyproject.toml reads it, and a v<version> tag must match it
# (tests/test_version.py, .github/workflows/release.yml).
__version__ = "0.6.0"
