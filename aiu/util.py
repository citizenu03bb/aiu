# SPDX-License-Identifier: GPL-3.0-or-later
"""Who else can write a file: shared by the config trust check (aiu.config)
and the choice of a vendor's binary to run (aiu.clis). And where aiu keeps
its state: the pace history, the Claude Code plugin's readings and the
vendor clients' cached readings."""
from __future__ import annotations

import grp
import os
import pwd
from pathlib import Path


def _private_group(gid: int) -> bool:
    """True for the user's private group: named after the user, with no other
    member and no other user's primary group (umask 002 setups, as on Ubuntu)."""
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
        g = grp.getgrgid(gid)
    except KeyError:
        return False
    return (g.gr_name == user and set(g.gr_mem) <= {user}
            and not any(u.pw_gid == gid and u.pw_name != user for u in pwd.getpwall()))


def _writable_by_others(st: os.stat_result) -> bool:
    """Whether someone besides the user can write it. Group write counts only when
    the group is not the user's private one, as Debian's OpenSSH StrictModes does."""
    return bool(st.st_mode & 0o002 or (st.st_mode & 0o020 and not _private_group(st.st_gid)))


def state_dir() -> Path:
    """$XDG_STATE_HOME/aiu when that is an absolute path, else ~/.local/state/aiu.
    The Claude Code plugin (claude-code-plugin/hooks/register.ts) writes here too."""
    state = os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if os.path.isabs(state) else Path(os.path.expanduser("~/.local/state"))
    return base / "aiu"
