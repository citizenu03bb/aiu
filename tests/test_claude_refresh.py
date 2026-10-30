# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude probe refreshes a stale CLI token before calling the usage API — no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiu  # noqa: E402

NOW = 1793350914.544
USAGE = {"five_hour": {"utilization": 12.0, "resets_at": "2026-10-30T12:00:00+00:00"}}


class Resp:
    status_code = 200

    def json(self):
        return USAGE


def setup(monkeypatch, tokens, refresh_ok=True):
    """tokens: successive (token, expires_at_ms) values the credentials file holds."""
    reads, refreshes, sent = list(tokens), [], []
    monkeypatch.setattr(aiu.time, "time", lambda: NOW)
    monkeypatch.setattr(aiu, "_read_claude_token", lambda: reads.pop(0) if len(reads) > 1 else reads[0])
    monkeypatch.setattr(aiu, "_refresh_claude_token", lambda: refreshes.append(1) or refresh_ok)
    monkeypatch.setattr(aiu.httpx, "get",
                        lambda url, headers, timeout: sent.append(headers["Authorization"]) or Resp())
    return refreshes, sent


def test_expired_token_is_refreshed_first(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("old", (NOW - 3600) * 1000), ("new", (NOW + 8 * 3600) * 1000)])
    rows = aiu.probe_claude({})
    assert refreshes == [1] and sent == ["Bearer new"]
    assert rows[0].metric == "5-Hour Limit" and "expired" not in rows[0].note


def test_valid_token_is_left_alone(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("tok", (NOW + 3600) * 1000)])
    aiu.probe_claude({})
    assert refreshes == [] and sent == ["Bearer tok"]


def test_failed_refresh_still_warns(monkeypatch):
    refreshes, sent = setup(monkeypatch, [("old", (NOW - 3600) * 1000)], refresh_ok=False)
    rows = aiu.probe_claude({})
    assert refreshes == [1] and sent == ["Bearer old"]
    assert "token expired" in rows[0].note
