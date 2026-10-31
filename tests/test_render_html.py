# SPDX-License-Identifier: GPL-3.0-or-later
"""--html page: provider text is untrusted (M3). Every value is escaped and the
page carries a CSP that allows no script, so markup in a row stays text."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiu.probes import gemini  # noqa: E402
from aiu.render import render_html  # noqa: E402
from aiu.rows import Row  # noqa: E402

CSP = ("<meta http-equiv=\"Content-Security-Policy\" "
       "content=\"default-src 'none'; style-src 'unsafe-inline'\">")
SCRIPT = "<script>alert(1)</script>"
IMG = "<img src=x onerror=alert(2)>"


def render(tmp_path, *rows):
    out = tmp_path / "out.html"
    render_html(list(rows), out)
    return out.read_text()


def test_plain_rows(tmp_path):
    page = render(tmp_path,
                  Row("claude", "5-Hour Limit", "quota", used=0.42, limit=1.0, unit="%", reset="in 2h"),
                  Row("deepseek", "Balance", "balance", used=12.5, unit="USD"),
                  Row("openai", "—", "error", note="no key"))
    assert "<td>claude</td><td>5-Hour Limit</td><td align=right>42.0%</td><td>in 2h</td>" in page
    assert "<td align=right>12.50 USD</td>" in page
    assert "<td align=right>err</td><td>no key</td>" in page


def test_csp_comes_before_any_content(tmp_path):
    page = render(tmp_path, Row("claude", "5-Hour Limit", "quota", used=0.1, limit=1.0))
    assert page.index(CSP) < page.index("<table")


def test_every_field_is_escaped(tmp_path):
    page = render(tmp_path,
                  Row(SCRIPT, SCRIPT, "quota", used=0.5, limit=1.0, reset=IMG, note=SCRIPT),
                  Row("claude", f"Credit ({IMG})", "credit", used=3.0, limit=5.0, unit=SCRIPT,
                          note="locked: " + SCRIPT),
                  Row("deepseek", "Balance", "balance", used=1.0, unit=IMG),
                  Row("x", "y", "error", note="a & b \"quoted\" 'single'"))
    assert "<script" not in page and "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;img src=x onerror=alert(2)&gt;" in page
    assert "a &amp; b &quot;quoted&quot; &#x27;single&#x27;" in page


def test_forged_agy_reply_stays_text(tmp_path):
    # A reply that passes as agy's /usage names its own group: the metric is made from it.
    rows = gemini._parse_groups([{"name": SCRIPT, "buckets": [
        {"window": "5h", "remaining_fraction": 0.5, "reset_time": IMG}]}])
    page = render(tmp_path, *rows)
    assert rows[0].metric == "scriptalert1script 5-Hour" and rows[0].reset is None
    assert "<script" not in page and "<img" not in page and CSP in page
