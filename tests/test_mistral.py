# SPDX-License-Identifier: GPL-3.0-or-later
"""Mistral allowance probe on recorded admin-console responses — no network."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu import routing  # noqa: E402
from aiu.probes import mistral  # noqa: E402


def group(metric, billing, value, usage="vibe", zone="global"):
    return {"billingMetric": metric, "billingGroup": billing, "usageType": usage,
            "apiZone": zone, "serviceTier": "standard", "value": value}


def price(metric, billing, value, event="api_tokens", zone="global", tier="standard"):
    return {"billingMetric": metric, "billingGroup": billing, "eventType": event,
            "apiZone": zone, "serviceTier": tier, "price": value}


# October 2026, Free plan: the dashboard showed "Total Estimated Cost 0.32€".
GROUPS = [
    group("mistral-large-4-0-a", "cached", 1282816),
    group("mistral-large-4-0-a", "input", 207696),
    group("mistral-large-4-0-a", "output", 35099),
    group("mistral-medium-3-5", "cached", 112512),
    group("mistral-medium-3-5", "input", 22603),
    group("mistral-medium-3-5", "output", 2008),
]
# Decoys: same metric priced per audio second, in the EU zone, and at priority tier.
PRICES = [
    price("mistral-medium-3-5", "input", 0.00014166666666666668, event="api_audio_seconds"),
    price("mistral-medium-3-5", "cached", 1.275e-7),
    price("mistral-medium-3-5", "input", 0.000001275),
    price("mistral-medium-3-5", "output", 0.000006375),
    price("mistral-medium-3-5", "input", 0.0000014025, zone="eu"),
    price("mistral-medium-3-5", "input", 0.00000223125, tier="priority"),
    price("mistral-large-4-0-a", "cached", 5.95e-8),
    price("mistral-large-4-0-a", "input", 5.95e-7),
    price("mistral-large-4-0-a", "output", 0.000001785),
]


def test_spend_matches_dashboard():
    spend, unpriced = mistral._mistral_spend(GROUPS, PRICES)
    assert round(spend["vibe"], 2) == 0.32
    assert spend["api"] == 0 and unpriced == 0


def test_api_usage_and_unpriced_groups():
    extra = [group("mistral-medium-3-5", "input", 1_000_000, usage="usage"),
             group("unknown-model", "input", 5, usage="ocr")]
    spend, unpriced = mistral._mistral_spend(GROUPS + extra, PRICES)
    assert spend["api"] == pytest.approx(1.275)
    assert unpriced == 1


def fake_trpc(groups, has_more=False):
    def call(proc, payload, cookie):
        if proc == "usage.costBreakdown":
            return {"groups": groups, "metadata": {"hasMore": has_more}}
        return {"prices": PRICES}
    return call


def test_probe_rows(monkeypatch):
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "session=x")
    monkeypatch.setattr(mistral, "_mistral_trpc", fake_trpc(GROUPS))
    vibe, api = mistral.probe_mistral({})
    assert (vibe.metric, vibe.kind) == ("Vibe Monthly", "quota")
    assert vibe.used == pytest.approx(0.3185 / 8.5, abs=1e-4)
    assert vibe.note == "€0.32 / €8.50"
    assert api.used == 0 and api.note == "€0.00 / €8.50"
    assert vibe.window_secs > routing.RATE_WINDOW_MAX  # a volume budget for routing
    assert vibe.reset_epoch > time.time()


def test_allowance_override_and_partial(monkeypatch):
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "session=x")
    monkeypatch.setattr(mistral, "_mistral_trpc", fake_trpc(GROUPS, has_more=True))
    rows = mistral.probe_mistral({"vibe_allowance_eur": 20, "api_allowance_eur": 0})
    assert [r.metric for r in rows] == ["Vibe Monthly"]
    assert rows[0].note == "€0.32 / €20.00 ; partial (more pages)"


def test_missing_and_rejected_cookie(monkeypatch):
    monkeypatch.delenv("MISTRAL_ADMIN_COOKIE", raising=False)
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and "MISTRAL_ADMIN_COOKIE" in row.note

    def rejected(proc, payload, cookie):
        raise PermissionError(302)
    monkeypatch.setenv("MISTRAL_ADMIN_COOKIE", "session=expired")
    monkeypatch.setattr(mistral, "_mistral_trpc", rejected)
    [row] = mistral.probe_mistral({})
    assert row.kind == "error" and "session rejected (302)" in row.note
