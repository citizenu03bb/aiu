# SPDX-License-Identifier: GPL-3.0-or-later
"""Mistral probe down to the HTTP layer (tRPC envelope, login redirect, errors) —
no network. Pricing itself is covered by test_mistral.py."""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu.probes import mistral  # noqa: E402

USAGE_URL = mistral.MISTRAL_TRPC + "usage.costBreakdown"
PRICES_URL = mistral.MISTRAL_TRPC + "usage.prices"


def trpc(payload):
    return {"result": {"data": {"json": payload}}}


GROUPS = {"groups": [{"billingMetric": "mistral-medium-3-5", "billingGroup": "input", "usageType": "vibe",
                      "apiZone": "global", "serviceTier": "standard", "value": 1_000_000}],
          "metadata": {"hasMore": False}}
PRICES = {"prices": [{"billingMetric": "mistral-medium-3-5", "billingGroup": "input", "eventType": "api_tokens",
                      "apiZone": "global", "serviceTier": "standard", "price": 0.000001275}]}


@pytest.fixture
def cookie(monkeypatch):
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "ory_session_x=fake; csrf_token=fake")


def test_normal_response(cookie, http):
    http.on("GET", USAGE_URL, (200, trpc(GROUPS)))
    http.on("GET", PRICES_URL, (200, trpc(PRICES)))
    vibe, api = mistral.probe_mistral({})
    assert vibe.used == pytest.approx(1.275 / 8.5) and vibe.note == "€1.28 / €8.50"
    assert api.used == 0
    usage_call = http.to(USAGE_URL)[0]
    assert usage_call.headers["Cookie"] == "ory_session_x=fake; csrf_token=fake"
    span = json.loads(usage_call.params["input"])["json"]
    assert span["start"].endswith(".000Z") and span["untracked"] is False


def test_login_redirect(cookie, http):
    http.on("GET", USAGE_URL, (307, "", {"location": "https://auth.mistral.ai/ui/login"}))
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and row.note.startswith("session rejected (307)")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejected(cookie, http, status):
    http.on("GET", USAGE_URL, (status, {"error": {"json": {"message": "UNAUTHORIZED"}}}))
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and row.note.startswith(f"session rejected ({status})")


def test_network_error(cookie, http):
    http.on("GET", USAGE_URL, httpx.ConnectError("offline"))
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and row.note == "network: cannot connect"


def test_bad_cookie_header_never_quoted(cookie, http):
    http.on("GET", USAGE_URL, httpx.LocalProtocolError("Illegal header value b'ory_session_x=fake\\n'"))
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and "ory_session" not in row.note


def test_empty_month(cookie, http):
    http.on("GET", USAGE_URL, (200, trpc({"groups": None, "metadata": None})))
    http.on("GET", PRICES_URL, (200, trpc({"prices": None})))
    vibe, api = mistral.probe_mistral({})
    assert vibe.used == 0 and api.used == 0 and vibe.note == "€0.00 / €8.50"


@pytest.mark.parametrize("reply, note", [
    ((500, {"error": {"json": {"code": -32603}}}), "HTTP 500"),
    ((200, {"result": {"data": GROUPS}}), "malformed response: not a tRPC result"),  # no "json"
    ((200, "<html>maintenance</html>"), "malformed response: not JSON"),
    ((200, trpc([GROUPS])), "malformed response: not a tRPC result"),
])
def test_server_error_or_malformed_body(cookie, http, reply, note):
    http.on("GET", USAGE_URL, reply)
    http.on("GET", PRICES_URL, (200, trpc(PRICES)))
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and row.note == note


def test_malformed_price_list(cookie, http):
    http.on("GET", USAGE_URL, (200, trpc(GROUPS)))
    http.on("GET", PRICES_URL, (200, trpc({"prices": [{"billingMetric": "mistral-medium-3-5"}]})))
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and row.note == "malformed response (KeyError)"
