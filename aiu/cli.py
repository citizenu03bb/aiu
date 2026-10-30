# SPDX-License-Identifier: GPL-3.0-or-later
"""The `aiu` command line."""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from aiu import __version__, keys
from aiu.config import CONFIG_PATH, config_main, load_config
from aiu.cookies import cookie_main
from aiu.history import record_history
from aiu.notify import alert, ensure_desktop_entry
from aiu.probes import collect
from aiu.render import render_html, render_json, render_table
from aiu.routing import advise


def main() -> int:
    ap = argparse.ArgumentParser(description="One snapshot of every AI subscription you pay for.")
    ap.add_argument("--version", action="version", version=f"aiu {__version__}")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--only", help="comma-separated provider subset (ignores enabled flags)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--html", metavar="PATH", help="write an HTML page")
    ap.add_argument("--advise", action="store_true",
                    help="recommend where to route: earliest-expiring surplus first")
    ap.add_argument("--alert", action="store_true",
                    help="warn about quota lost at reset / running dry, expiring credits (cron-friendly)")
    # Without a subcommand, aiu takes its snapshot as it always has.
    sub = ap.add_subparsers(dest="command", metavar="{config,cookie}")
    cp = sub.add_parser("config", help="show or change the config (for scripts and the GNOME extension)")
    cp.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    csub = cp.add_subparsers(dest="config_action", required=True)
    show = csub.add_parser("show", help="every provider's settings, and whether the file sets them")
    show.add_argument("--json", dest="config_json", action="store_true", help="emit JSON")
    cset = csub.add_parser("set", help="change one setting, e.g. `aiu config set openai.enabled true`")
    cset.add_argument("option", help="<provider>.<option>")
    cset.add_argument("value")
    kp = sub.add_parser("cookie", help="import a browser session into the keyring (run by you only)")
    kp.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ksub = kp.add_subparsers(dest="cookie_action", required=True)
    kimp = ksub.add_parser("import", help="copy the admin.mistral.ai session from one browser "
                                          "profile into the keyring and set mistral.key_cmd")
    kimp.add_argument("provider", choices=["mistral"])
    kimp.add_argument("--browser", required=True, choices=["chrome", "chromium", "brave", "firefox"])
    kimp.add_argument("--profile", metavar="NAME",
                      help="profile directory or name (default: Default, or Firefox's default)")
    args = ap.parse_args()
    if args.command == "config":
        return config_main(args)
    if args.command == "cookie":
        return cookie_main(args)

    config = load_config(Path(os.path.expanduser(args.config)))
    keys.ENV_FILE.update(keys.load_env_file(config))
    if args.alert:
        ensure_desktop_entry()  # before probing, so GNOME has indexed it by notify time
    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    rows = collect(config, only)
    # Every run records, --only included: entries are per metric, so a filtered
    # run never drops the others' history.
    hist = record_history(rows, time.time())

    if args.json:
        render_json(rows, hist)
    if args.html:
        render_html(rows, Path(os.path.expanduser(args.html)))
    if args.advise:
        return advise(rows, hist=hist)
    if args.alert:
        return alert(rows, hist=hist)
    if not args.json and not args.html:
        render_table(rows)
    return 0
