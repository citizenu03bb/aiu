# SPDX-License-Identifier: GPL-3.0-or-later
"""Hide an unused Argon cap in human views, without losing source data."""
import json

import pytest

from aiu.render import render_html, render_json, render_table
from aiu.rows import Row

ARGON = "Gemini 4 Argon Weekly"


@pytest.mark.parametrize("used,shown", [(0, False), (0.00001, True), (0.2, True), (1, True), (None, True)])
def test_human_views_keep_json_measurement(used, shown, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "180")
    rows = [Row("gemini", ARGON, "quota", used=used, limit=1, sublimit=True,
                plan="Google AI Pro"),
            Row("gemini", "Weekly Limit", "quota", used=0, limit=1, plan="Google AI Pro")]
    render_table(rows)
    table = capsys.readouterr().out
    assert (ARGON in table) == shown
    assert "Weekly Limit" in table and "Google AI Pro" in table
    path = tmp_path / "snapshot.html"
    render_html(rows, path)
    page = path.read_text()
    assert (ARGON in page) == shown
    assert "Weekly Limit" in page and "Google AI Pro" in page
    capsys.readouterr()
    render_json(rows, hist=[])
    raw = json.loads(capsys.readouterr().out)
    assert len(raw["rows"]) == 2
    assert raw["rows"][0]["metric"] == ARGON and raw["rows"][0]["used"] == used
    assert len(rows) == 2


def test_errors_and_other_unused_quotas_remain_visible(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "180")
    rows = [Row("gemini", ARGON, "error", used=0, note="unreadable quota"),
            Row("gemini", "Claude and GPT Weekly", "quota", used=0),
            Row("claude", ARGON, "quota", used=0)]
    render_table(rows)
    table = capsys.readouterr().out
    assert table.count(ARGON) == 2
    assert "unreadable quota" in table and "Claude and GPT Weekly" in table
    path = tmp_path / "snapshot.html"
    render_html(rows, path)
    page = path.read_text()
    assert page.count(ARGON) == 2
    assert "unreadable quota" in page and "Claude and GPT Weekly" in page
