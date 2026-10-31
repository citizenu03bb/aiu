# SPDX-License-Identifier: GPL-3.0-or-later
"""Vendors' own command-line tools, which aiu asks for their figures instead of
holding their sign-in (`claude`, and the clients later probes run): which
binary to run, how to run it, and a cache for the slow or chatty ones.

The binary is never taken from PATH (gnome-shell's, when the extension runs
aiu): only the `<name>_cmd` option, or else the first known install location.
It is resolved once to its real path and refused when someone else can write
it or its directory. It runs with a fixed argv and a clean environment, in its
own process group, which is killed at the timeout and when aiu is done."""
from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from aiu.i18n import tr
from aiu.util import _writable_by_others, state_dir


CLEAN_ENV = ("HOME", "PATH", "USER", "LANG")


def find_cli(option: str, cfg: dict | None, locations: Sequence[str]) -> str | None:
    """The real path of the binary to run: cfg[option] when set, else the first
    of `locations` that exists. None when there is none, or when the one found
    (the link, its target or their directories) can be written by others."""
    configured = (cfg or {}).get(option)
    for c in [configured] if configured else locations:
        path = Path(os.path.expanduser(str(c)))
        real = Path(os.path.realpath(path))
        if not (real.is_file() and os.access(real, os.X_OK)):
            if configured:
                print(tr("aiu: {option} {path} is not an executable file", option=option, path=path),
                      file=sys.stderr)
            continue
        try:
            safe = all((st.st_uid in (0, os.getuid()) and not _writable_by_others(st))
                       for st in (os.stat(p) for p in {path.parent, real, real.parent}))
        except OSError:
            safe = False
        if safe:
            return str(real)
        print(tr("aiu: not running {path}: writable by other users", path=path), file=sys.stderr)
        return None
    return None


def _kill_group(proc: subprocess.Popen) -> None:
    # Before the leader is reaped its group id cannot be reused, so this only
    # reaches the client and whatever it started (MCP servers, helpers).
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


@contextmanager
def spawn(argv: Sequence[str], timeout: float, keep_env: Sequence[str] = (),
          cwd: str | None = None) -> Iterator[subprocess.Popen]:
    """argv in its own process group, with a clean environment (plus the
    variables named in keep_env, such as the session bus a client's keyring
    needs), text pipes on stdin and stdout and stderr discarded, in `cwd` when
    given. The group is killed after `timeout` seconds, and in any case on
    leaving the block."""
    env = {k: os.environ[k] for k in (*CLEAN_ENV, *keep_env) if k in os.environ}
    proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, start_new_session=True,
                            cwd=cwd, text=True, encoding="utf-8", errors="replace")
    timer = threading.Timer(timeout, _kill_group, (proc,))
    timer.daemon = True
    timer.start()
    try:
        yield proc
    finally:
        timer.cancel()
        _kill_group(proc)
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except OSError:
                pass
        proc.wait()


def run_cli(argv: Sequence[str], timeout: float, input: str = "") -> tuple[int, str] | None:
    """Run argv to completion with `input` on stdin: (exit code, stdout), the code
    negative when the timeout killed it. None when it could not start."""
    try:
        with spawn(argv, timeout) as proc:
            out, _ = proc.communicate(input)
            return proc.returncode, out
    except OSError:
        return None


TTY_SIZE = (24, 80)   # rows, columns
TTY_POLL = 0.1        # seconds between done() checks
TTY_READ = 1 << 16


def run_tty(argv: Sequence[str], timeout: float, done: Callable[[], bool],
            cwd: str | None = None) -> bool | None:
    """argv on a pseudo-terminal, for a client that says something only in its
    interactive mode: the clean environment and own process group of spawn(),
    a TTY_SIZE screen whose output is read and dropped, nothing typed. It runs
    until done() is true, it exits or `timeout` passes, and then its group is
    killed. Whether done() came true; None when it could not start."""
    env = {k: os.environ[k] for k in CLEAN_ENV if k in os.environ}
    try:
        master, slave = pty.openpty()
    except OSError:
        return None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", *TTY_SIZE, 0, 0))
        proc = subprocess.Popen(list(argv), stdin=slave, stdout=slave, stderr=slave, env=env,
                                start_new_session=True, cwd=cwd)
    except OSError:
        os.close(master)
        return None
    finally:
        os.close(slave)
    deadline = time.monotonic() + timeout
    try:
        while not done():
            left = deadline - time.monotonic()
            if left <= 0 or proc.poll() is not None:
                return done()
            if select.select([master], [], [], min(TTY_POLL, left))[0]:
                try:
                    os.read(master, TTY_READ)  # a screen nobody reads, but it must not fill up
                except OSError:  # EIO: the client has closed the terminal, let it end
                    try:
                        proc.wait(max(0.0, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        pass
                    return done()
        return True
    finally:
        _kill_group(proc)
        proc.wait()
        os.close(master)


# The reading cache: one file per client in aiu's state directory.
CACHE_DIR = "readings"
CACHE_MAX_BYTES = 256 * 1024


def _cache_path(name: str) -> Path:
    return state_dir() / CACHE_DIR / f"{name}.json"


def _cache_read(name: str):
    """The stored value while it is younger than its time-to-live, else None
    (also for a file from the future, which a clock change leaves behind)."""
    try:
        path = _cache_path(name)
        if path.stat().st_size > CACHE_MAX_BYTES:
            return None
        d = json.loads(path.read_text())
        written, ttl = d["written_at"], d["ttl"]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (written, ttl)):
            return None
        return d["value"] if 0 <= time.time() - written < ttl else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cache_write(name: str, value, ttl: float) -> None:
    """Atomic, user-only, like the history file: a 0600 temp file in a 0700
    directory, renamed over the old one. A failed write only costs a rerun."""
    path = _cache_path(name)
    tmp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = path.parent.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            return
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.chmod(path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{name}.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:  # mkstemp: O_EXCL, mode 0600
            json.dump({"written_at": time.time(), "ttl": ttl, "value": value}, f,
                      separators=(",", ":"))
        os.replace(tmp, path)
        tmp = None
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def cached(name: str, fetch: Callable[[], tuple[object, float]]):
    """What fetch() returned the last time, while its time-to-live lasts; else
    a new fetch(), stored. fetch returns (value, ttl in seconds): the value is
    JSON other than null, and a probe returns its failures as values too, with a shorter ttl,
    so a failing client is not rerun on every refresh."""
    if (value := _cache_read(name)) is not None:
        return value
    value, ttl = fetch()
    if ttl > 0:
        _cache_write(name, value, ttl)
    return value
