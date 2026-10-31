"""Freezes the site and checks what GitHub Pages would actually serve."""
import csv
import io
import json
import os
import re
import sys
from urllib.parse import unquote, urlsplit

import pytest

from hacla_db import HoldingsDB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    from freeze import freeze

    dest = tmp_path_factory.mktemp("site") / "out"
    freeze(str(dest))
    return str(dest)


def html_files(site):
    for folder, _, files in os.walk(site):
        for name in files:
            if name.endswith(".html"):
                yield os.path.join(folder, name)


def test_expected_pages_exist(site):
    db = HoldingsDB()
    for r in db.holdings_export():
        assert os.path.exists(os.path.join(site, "holding", r["id"], "index.html"))
        assert os.path.exists(os.path.join(site, "data", "holding", r["id"] + ".json"))
    for p in ("index.html", "404.html", ".nojekyll", "people/index.html", "person/cielo-castro/index.html", "data/holdings.csv", "static/holdings.js"):
        assert os.path.exists(os.path.join(site, p)), p
    assert not os.path.exists(os.path.join(site, "api"))


def test_all_internal_links_resolve(site):
    bad = []
    checked = 0
    for page in html_files(site):
        text = re.sub(r"<script.*?</script>", "", open(page).read(), flags=re.S)
        for link in re.findall(r'(?:href|src)="([^"]+)"', text):
            parts = urlsplit(link)
            if parts.scheme or link.startswith(("#", "mailto:")):
                continue
            checked += 1
            target = os.path.normpath(os.path.join(os.path.dirname(page), unquote(parts.path)))
            if os.path.isdir(target):
                target = os.path.join(target, "index.html")
            if link.startswith("/") or not os.path.exists(target):
                bad.append((os.path.relpath(page, site), link))
    assert checked > 1000
    assert not bad, bad[:10]


def test_every_holding_page_has_sources(site):
    for r in HoldingsDB().holdings_export():
        html = open(os.path.join(site, "holding", r["id"], "index.html")).read()
        assert 'class="src ev-' in html and "Sources for this holding" in html, r["id"]


def test_no_personal_contact_details_published(site):
    pattern = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\b\d{3}-\d{3}-\d{4}\b")
    for folder, _, files in os.walk(site):
        if os.path.basename(folder) == "static":
            continue
        for name in files:
            text = open(os.path.join(folder, name), errors="ignore").read()
            assert not pattern.search(text), (name, pattern.search(text).group())


def test_static_csv_is_complete(site):
    rows = list(csv.DictReader(io.StringIO(open(os.path.join(site, "data", "holdings.csv")).read())))
    assert len(rows) == 177 and rows[0]["id"]


def test_table_data_embedded_safely(site):
    html = open(os.path.join(site, "index.html")).read()
    block = re.search(r'<script id="holdings-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert len(json.loads(block)) == 177


@pytest.fixture(scope="module")
def js():
    quickjs = pytest.importorskip("quickjs")
    ctx = quickjs.Context()
    ctx.eval(open(os.path.join(ROOT, "webapp", "static", "holdings.js")).read())
    ctx.set("DATA", json.dumps(HoldingsDB().holdings_export()))
    ctx.eval("var ROWS = JSON.parse(DATA);")
    return ctx


CASES = [
    ({}, {}),
    ({"q": "jordan"}, {"q": "jordan"}),
    ({"q": "clarendon"}, {"q": "clarendon"}),
    ({"q": "north hollywood"}, {"q": "north hollywood"}),
    ({"q": "wsh"}, {"q": "wsh"}),
    ({"q": "lomod south"}, {"q": "lomod south"}),
    ({"relationship": "commercial"}, {"relationship": "commercial"}),
    ({"relationship": "partnership"}, {"relationship": "partnership"}),
    ({"status": "under_construction"}, {"status": "under_construction"}),
    ({"program": "PSH"}, {"program": "PSH"}),
    ({"program": "Public Housing"}, {"program": "Public Housing"}),
    ({"ami_at_most": 30}, {"ami_at_most": "30"}),
    ({"ami_at_most": 50, "program": "LIHTC"}, {"ami_at_most": "50", "program": "LIHTC"}),
    ({"council_district": 15}, {"council_district": "15"}),
    ({"min_units": 200}, {"min_units": "200"}),
    ({"built_after": 2000, "built_before": 2010}, {"built_after": "2000", "built_before": "2010"}),
    ({"manager": "WSH Management"}, {"manager": "WSH Management"}),
    ({"ownership_status": "inferred"}, {"ownership_status": "inferred"}),
    ({"has_capital_stack": True}, {"stack": "yes"}),
    ({"has_capital_stack": False}, {"stack": "no"}),
    ({"q": "zzzznotathing"}, {"q": "zzzznotathing"}),
]


@pytest.mark.parametrize("py,browser", CASES)
def test_javascript_filters_match_python(js, py, browser):
    expected = {r["id"] for r in HoldingsDB().search(limit=1000, **py).rows}
    got = set(json.loads(js.eval(f"JSON.stringify(HoldingsTable.filterRows(ROWS, {json.dumps(browser)}).map(function (r) {{ return r.id; }}))")))
    assert got == expected, (sorted(got - expected)[:5], sorted(expected - got)[:5])


@pytest.mark.parametrize("key,sort,desc", [("name", "name", False), ("total_units", "units", True), ("total_units", "units", False), ("year_built", "year_built", True), ("acquired_date", "acquired", True), ("city", "city", False)])
def test_javascript_sort_matches_python(js, key, sort, desc):
    expected = [r["id"] for r in HoldingsDB().search(limit=1000, sort=sort, desc=desc).rows]
    script = f"JSON.stringify(HoldingsTable.sortRows(HoldingsTable.filterRows(ROWS, {{}}), {json.dumps(key)}, {json.dumps(desc)}).map(function (r) {{ return r.id; }}))"
    assert json.loads(js.eval(script)) == expected


def test_csv_guards_against_spreadsheet_formulas(js):
    out = js.eval('HoldingsTable.toCsv([{id:"=SUM(A1)", name:"a,b"}])')
    assert "'=SUM(A1)" in out and '"a,b"' in out
