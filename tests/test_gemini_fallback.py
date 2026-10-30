# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini OAuth fallback when Google refuses the Gemini CLI client — no network."""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu.probes import gemini  # noqa: E402

# loadCodeAssist for an individual account, Oct 2026 (trimmed).
LOAD = {"allowedTiers": [{"id": "standard-tier", "name": "Gemini Code Assist"}],
        "ineligibleTiers": [{"reasonCode": "UNSUPPORTED_CLIENT", "tierId": "free-tier",
                             "tierName": "Gemini Code Assist for individuals",
                             "reasonMessage": "This client is no longer supported..."}]}
DENIED = {"error": {"code": 403, "status": "PERMISSION_DENIED"}}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_unsupported_client_is_named(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini, "_query_local_quota_summary", lambda: None)
    monkeypatch.setattr(gemini, "_gemini_access_token", lambda force=False: calls.append(force) or "tok")
    monkeypatch.setattr(httpx, "post", lambda url, **kw:
                        Resp(200, LOAD) if url.endswith("loadCodeAssist") else Resp(403, DENIED))
    [row] = gemini.probe_gemini({})
    assert row.kind == "error"
    assert "no longer accepts the Gemini CLI" in row.note and "Antigravity" in row.note
    assert calls == [False]  # no pointless forced refresh


def test_no_desktop_and_no_token(monkeypatch):
    monkeypatch.setattr(gemini, "_query_local_quota_summary", lambda: None)
    monkeypatch.setattr(gemini, "_gemini_access_token", lambda force=False: None)
    [row] = gemini.probe_gemini({})
    assert row.kind == "error" and "open Antigravity" in row.note


def test_plan_from_allowed_tiers(monkeypatch):
    # Same recorded shape, no currentTier, but the quota call goes through.
    load = {"allowedTiers": LOAD["allowedTiers"]}
    monkeypatch.setattr(gemini, "_query_local_quota_summary", lambda: None)
    monkeypatch.setattr(gemini, "_gemini_access_token", lambda force=False: "tok")
    monkeypatch.setattr(httpx, "post", lambda url, **kw:
                        Resp(200, load) if url.endswith("loadCodeAssist") else
                        Resp(200, {"buckets": [{"modelId": "gemini-2.5-pro", "remainingFraction": 0.5}]}))
    [row] = gemini.probe_gemini({})
    assert row.kind == "quota" and row.note == "Gemini Code Assist"
