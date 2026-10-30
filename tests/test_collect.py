# SPDX-License-Identifier: GPL-3.0-or-later
"""collect(): one broken probe never takes the run down, and no note carries a
token-like string — no network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu.probes  # noqa: E402
import aiu.rows  # noqa: E402


@pytest.fixture
def probes(monkeypatch):
    """Replace the probe table with fakes; returns the dict to fill."""
    table = {}
    monkeypatch.setattr(aiu.probes, "PROBES", table)
    return table


def test_probe_exception_becomes_an_error_row(probes):
    def broken(cfg):
        raise RuntimeError("Bearer sk-SECRET leaked through the message")

    probes["broken"] = broken
    probes["fine"] = lambda cfg: [aiu.rows.Row("fine", "Balance", "balance", used=1.0)]
    err, ok = aiu.probes.collect({}, {"broken", "fine"})
    assert (err.provider, err.kind, err.note) == ("broken", "error", "probe failed (RuntimeError)")
    assert (ok.provider, ok.used) == ("fine", 1.0)


def test_only_enabled_probes_run(probes):
    probes["on"] = lambda cfg: [aiu.rows.Row("on", "x", "balance")]
    probes["off"] = lambda cfg: pytest.fail("disabled probe ran")
    assert [r.provider for r in aiu.probes.collect({"on": {"enabled": True}, "off": {}}, None)] == ["on"]


@pytest.mark.parametrize("note", [
    "Illegal header value b'Bearer abc.def\\nlogin: me'",
    "key sk-ant-oat01-AbCdEf123 was refused",
    "sk-or-v1-0123456789abcdef",
    "cookie ory_session_x=0123456789abcdef; csrf",
    "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
])
def test_token_like_notes_are_redacted(probes, note):
    probes["p"] = lambda cfg: [aiu.rows.Row("p", "x", "error", note=note)]
    [row] = aiu.probes.collect({}, {"p"})
    assert "[redacted]" in row.note
    for secret in ("abc.def", "AbCdEf123", "0123456789abcdef", "eyJhbGci"):
        assert secret not in row.note


@pytest.mark.parametrize("note", [
    "token expired — run `claude` to refresh;",
    "locked: verify_phone",
    "€1.28 / €8.50 ; 2 unpriced group(s)",
    "plan plus: no active windows",
    "malformed response: no `total_credits`",
    "auth rejected (401) — run `codex` to refresh",
])
def test_ordinary_notes_are_kept(probes, note):
    probes["p"] = lambda cfg: [aiu.rows.Row("p", "x", "error", note=note)]
    [row] = aiu.probes.collect({}, {"p"})
    assert row.note == note
