# SPDX-License-Identifier: GPL-3.0-or-later
"""aiu.clis: running a vendor's client (clean environment, the whole process
group killed at the timeout and at the end) and the reading cache. Which binary
runs is covered with `claude` in test_claude_refresh.py."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import clis  # noqa: E402


def script(path: Path, body: str) -> str:
    """A /bin/sh client; PATH holds only tmp/bin here (conftest), so tools are named in full."""
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is dead too (killed, not yet reaped by init).
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except (OSError, IndexError):
        return False


def wait_dead(pid: int) -> bool:
    for _ in range(50):
        if not alive(pid):
            return True
        time.sleep(0.02)
    return False


def test_run_cli_feeds_stdin_and_returns_stdout(tmp_path):
    cli = script(tmp_path / "echo-in", '/bin/cat; echo "args: $*"\n')
    assert clis.run_cli([cli, "a", "b"], timeout=5, input="hello\n") == (0, "hello\nargs: a b\n")


def test_run_cli_reports_the_exit_code(tmp_path):
    assert clis.run_cli([script(tmp_path / "fail", "exit 3\n")], timeout=5) == (3, "")


def test_run_cli_none_when_it_cannot_start(tmp_path):
    assert clis.run_cli([str(tmp_path / "missing")], timeout=5) is None


def test_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
    cli = script(tmp_path / "env", "/usr/bin/env\n")
    _, out = clis.run_cli([cli], timeout=5)
    names = {line.split("=", 1)[0] for line in out.splitlines()}
    assert names <= {"HOME", "PATH", "USER", "LANG", "PWD", "SHLVL", "_"}
    assert "HOME" in names and "LD_PRELOAD" not in names and "ANTHROPIC_BASE_URL" not in names


def test_timeout_kills_the_client_and_what_it_started(tmp_path):
    pids = tmp_path / "pids"
    cli = script(tmp_path / "hang", f'/bin/sleep 30 & echo $! > {pids}\necho $$ >> {pids}\nwait\n')
    start = time.monotonic()
    rc, _ = clis.run_cli([cli], timeout=0.5)
    assert rc < 0 and time.monotonic() - start < 5
    assert all(wait_dead(int(p)) for p in pids.read_text().split())


def test_leaving_spawn_kills_a_helper_left_behind(tmp_path):
    """A client that exits but leaves a helper running (an MCP server) does not
    leave it behind aiu."""
    pid_file = tmp_path / "helper"
    cli = script(tmp_path / "leaves", f'/bin/sleep 30 > /dev/null 2>&1 & echo $! > {pid_file}\necho ok\n')
    with clis.spawn([cli], timeout=10) as proc:
        assert proc.stdout.readline() == "ok\n"
        helper = int(pid_file.read_text())
        assert alive(helper)
    assert wait_dead(helper)


def test_spawn_talks_line_by_line(tmp_path):
    """What a JSON-RPC client needs: write, read the answer, then close."""
    cli = script(tmp_path / "rpc", 'while read line; do echo "got $line"; done\n')
    with clis.spawn([cli], timeout=5) as proc:
        proc.stdin.write("one\n")
        proc.stdin.flush()
        assert proc.stdout.readline() == "got one\n"


# run_tty: an interactive client on a pseudo-terminal ------------------------

def test_run_tty_gives_a_terminal_and_stops_once_done(tmp_path):
    seen, marker, pids = tmp_path / "seen", tmp_path / "done", tmp_path / "pids"
    cli = script(tmp_path / "tui", f'[ -t 0 ] && [ -t 1 ] && [ -t 2 ] && echo tty > {seen}\n'
                                   f'/bin/stty size >> {seen}\n/bin/pwd >> {seen}\n'
                                   f'/bin/sleep 30 & echo $! > {pids}\necho $$ >> {pids}\n'
                                   f': > {marker}\nwait\n')
    start = time.monotonic()
    assert clis.run_tty([cli], timeout=10, done=marker.exists, cwd=str(tmp_path)) is True
    assert time.monotonic() - start < 5
    assert seen.read_text() == f"tty\n24 80\n{tmp_path}\n"
    assert all(wait_dead(int(p)) for p in pids.read_text().split())


def test_run_tty_reads_away_a_busy_screen(tmp_path):
    """A client that draws a lot is not blocked on a full terminal."""
    marker = tmp_path / "done"
    cli = script(tmp_path / "chatty", f'/usr/bin/head -c 2000000 /dev/zero\n: > {marker}\n/bin/sleep 30\n')
    assert clis.run_tty([cli], timeout=10, done=marker.exists) is True


def test_run_tty_timeout_kills_the_client(tmp_path):
    pids = tmp_path / "pids"
    cli = script(tmp_path / "hang", f'/bin/sleep 30 & echo $! > {pids}\necho $$ >> {pids}\nwait\n')
    start = time.monotonic()
    assert clis.run_tty([cli], timeout=0.5, done=lambda: False) is False
    assert time.monotonic() - start < 5
    assert all(wait_dead(int(p)) for p in pids.read_text().split())


def test_run_tty_client_that_exits_first(tmp_path):
    start = time.monotonic()
    assert clis.run_tty([script(tmp_path / "quits", "echo bye\nexit 1\n")], timeout=10,
                        done=lambda: False) is False
    assert time.monotonic() - start < 5


def test_run_tty_none_when_it_cannot_start(tmp_path):
    assert clis.run_tty([str(tmp_path / "missing")], timeout=5, done=lambda: True) is None


def test_run_tty_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
    out = tmp_path / "env"
    cli = script(tmp_path / "env-tui", f"/usr/bin/env > {out}.tmp\n/bin/mv {out}.tmp {out}\n")
    clis.run_tty([cli], timeout=5, done=out.exists)
    names = {line.split("=", 1)[0] for line in out.read_text().splitlines()}
    assert "HOME" in names and names <= {"HOME", "PATH", "USER", "LANG", "PWD", "SHLVL", "_"}


# The reading cache ----------------------------------------------------------

def fetcher(*results):
    calls = []

    def fetch():
        calls.append(1)
        return results[min(len(calls), len(results)) - 1]
    return fetch, calls


def test_cached_reuses_a_fresh_value(home, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    fetch, calls = fetcher(({"rows": 1}, 60), ({"rows": 2}, 60))
    assert clis.cached("demo", fetch) == {"rows": 1}
    now[0] += 59
    assert clis.cached("demo", fetch) == {"rows": 1} and len(calls) == 1
    now[0] += 1
    assert clis.cached("demo", fetch) == {"rows": 2} and len(calls) == 2


def test_cached_failure_has_its_own_ttl(home, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    fetch, calls = fetcher(({"error": "not signed in"}, 600), ({"rows": 1}, 900))
    assert clis.cached("demo", fetch) == {"error": "not signed in"}
    now[0] += 300  # the extension's next refresh does not rerun the client
    assert clis.cached("demo", fetch) == {"error": "not signed in"} and len(calls) == 1
    now[0] += 300
    assert clis.cached("demo", fetch) == {"rows": 1}


def test_cached_ttl_zero_is_not_stored(home):
    fetch, calls = fetcher(({"rows": 1}, 0))
    clis.cached("demo", fetch)
    clis.cached("demo", fetch)
    assert len(calls) == 2 and not clis._cache_path("demo").exists()


def test_cache_file_is_private(home):
    clis.cached("demo", lambda: ({"rows": 1}, 60))
    path = clis._cache_path("demo")
    assert path.parent == home / ".local" / "state" / "aiu" / "readings"
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700


def test_cache_follows_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    clis.cached("demo", lambda: ({"rows": 1}, 60))
    assert (tmp_path / "state" / "aiu" / "readings" / "demo.json").is_file()


def test_a_reading_from_the_future_is_ignored(home, monkeypatch):
    """A clock set back must not keep an old reading forever."""
    clis.cached("demo", lambda: ({"rows": 1}, 60))
    path = clis._cache_path("demo")
    d = json.loads(path.read_text())
    path.write_text(json.dumps({**d, "written_at": time.time() + 3600}))
    assert clis.cached("demo", lambda: ({"rows": 2}, 60)) == {"rows": 2}


def test_a_damaged_cache_is_refetched(home):
    clis.cached("demo", lambda: ({"rows": 1}, 60))
    path = clis._cache_path("demo")
    for bad in ("not json", "[]", '{"written_at": "x", "ttl": 60, "value": 1}',
                '{"written_at": true, "ttl": 60, "value": 1}', '{"ttl": 60}'):
        path.write_text(bad)
        assert clis.cached("demo", lambda: ({"rows": 2}, 0)) == {"rows": 2}
    path.write_text(json.dumps({"written_at": time.time(), "ttl": 60, "value": "x" * clis.CACHE_MAX_BYTES}))
    assert clis.cached("demo", lambda: ({"rows": 3}, 0)) == {"rows": 3}


def test_a_cache_directory_not_ours_is_left_alone(home, monkeypatch):
    clis._cache_path("demo").parent.mkdir(parents=True)
    real_lstat = Path.lstat

    def lstat(self):
        st = real_lstat(self)
        if self == clis._cache_path("demo").parent:
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, os.getuid() + 1,
                                   *tuple(st)[5:]))
        return st
    monkeypatch.setattr(Path, "lstat", lstat)
    clis.cached("demo", lambda: ({"rows": 1}, 60))
    assert not clis._cache_path("demo").exists()
