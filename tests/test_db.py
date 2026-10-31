import importlib.util
import os
import sqlite3
import sys

import pytest

from hacla_db import HoldingsDB, QueryError, source_link
from hacla_db.query import fts_query

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "hacla.sqlite")


@pytest.fixture(scope="module")
def db():
    return HoldingsDB()


@pytest.fixture(scope="module")
def con():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def test_foreign_keys_hold(con):
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_every_fact_has_a_source(con):
    for table in ("holdings", "roles", "financing", "ami_restrictions", "events", "notes"):
        assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE source_id IS NULL OR source_id = ''").fetchone()[0] == 0, table


def test_source_urls_are_official_or_public_records(con):
    allowed = ("hacla.org", "treasurer.ca.gov", "arcgis.com", "lacounty.gov")
    for sid, url in con.execute("SELECT id, url FROM sources"):
        assert any(d in url for d in allowed), (sid, url)


def test_document_text_facts_carry_quote(con):
    for table in ("roles", "financing", "ami_restrictions", "events", "notes"):
        assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE evidence = 'document_text' AND (quote IS NULL OR quote = '')").fetchone()[0] == 0, table


def test_lihtc_stacks_reconcile_to_staff_reports(con):
    assert con.execute("SELECT COUNT(*) FROM ctcac_reports").fetchone()[0] == 17
    assert con.execute("SELECT app FROM ctcac_reports WHERE permanent_total IS NULL OR permanent_total <> permanent_sum").fetchall() == []


@pytest.mark.parametrize("entity,props,units", [("hoc", 7, 550), ("s8-lomods", 7, 35), ("lafayette-cc", 1, 120), ("housing-pathways", 1, 335), ("maida-sepulveda", 1, 36), ("mclaughlin-village", 1, 26)])
def test_entities_reconcile_to_published_counts(con, entity, props, units):
    assert con.execute("SELECT db_properties, db_units FROM entity_reconciliation WHERE entity_id = ?", (entity,)).fetchone() == (props, units)


def test_jordan_scattered_sites_match_hud(db):
    h = db.holding("jordan-scattered")
    assert h["total_units"] == 34
    assert sum(s["units"] for s in h["sites"]) == 34


def test_clarendon_full_record(db):
    h = db.holding("clarendon-apartments")
    assert {a["ami_pct"]: a["units"] for a in h["ami"] if a["restriction_type"] == "covenant"} == {30: 24, 50: 42, 80: 46, 120: 133, 150: 88}
    assert h["owner_name"] == "Housing Pathways, Inc."
    assert any(f["amount"] == 141900000 for f in h["financing"])
    assert h["acquired_date"] == "2024-12-23"
    assert h["sources"]


def test_search_filters(db):
    assert all("Public Housing" in r["programs"] for r in db.search(program="Public Housing").rows)
    deep = db.search(ami_at_most=30, limit=500)
    assert deep.total and all(r["deepest_ami"] <= 30 for r in deep.rows)
    assert all(r["total_units"] >= 200 for r in db.search(min_units=200, limit=500).rows)
    assert db.search(q="clarendon").rows[0]["id"] == "clarendon-apartments"


def test_commercial_excluded_by_default(db):
    assert not [r for r in db.search(limit=1000).rows if r["relationship"] == "commercial"]
    assert db.search(relationship="commercial").total == 3


def test_entity_filter_includes_subsidiaries(db):
    ids = {r["id"] for r in db.search(entity="la-cienega", limit=500).rows}
    assert "pueblo-del-sol-1" in ids  # held through LOMOD PDS LLC


@pytest.mark.parametrize("kwargs", [{"program": "Nope"}, {"relationship": "x"}, {"sort": "1; DROP TABLE holdings"}, {"min_units": "many"}, {"acquired_after": "last year"}, {"entity": "nobody"}, {"ami_at_most": "low"}])
def test_bad_filters_raise(db, kwargs):
    with pytest.raises(QueryError):
        db.search(**kwargs)


@pytest.mark.parametrize("text", ['"', "a AND", "NOT (", "x*y:z", "'; DROP TABLE holdings; --", "NEAR("])
def test_search_text_cannot_break_fts(db, text):
    db.search(q=text)


def test_fts_query_quotes_terms():
    assert fts_query('jordan" OR x') == '"jordan"* "OR"* "x"*'


def test_connection_is_read_only(db):
    with pytest.raises(sqlite3.OperationalError):
        db.con.execute("DELETE FROM holdings")


def test_source_link_deep_links_pdf_only():
    assert source_link("https://x/a.pdf", 7) == "https://x/a.pdf#page=7"
    assert source_link("https://x/page", 7) == "https://x/page"


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "data", "raw", "txt", "audit_2024.txt")), reason="raw source text is not in the repository")
def test_quote_verifier_rejects_a_fabricated_quote():
    spec = importlib.util.spec_from_file_location("build_db_mod", os.path.join(ROOT, "scripts", "build_db.py"))
    src = open(spec.origin).read().rsplit("main()", 1)[0]  # Definitions only; don't rebuild.
    ns = {"__file__": spec.origin, "__name__": "build_db_mod"}
    exec(compile(src, spec.origin, "exec"), ns)
    lib = ns["LIB"]
    sid, page, quote, ev = lib.check({"source": "audit_2024", "quote": "thirty-five (35) blended component units"}, "real")
    assert (ev, page) == ("document_text", 26) and not ns["errors"]
    lib.check({"source": "audit_2024", "quote": "HACLA owns the moon"}, "fake")
    assert any("quote not found" in e for e in ns["errors"])
    ns["errors"].clear()
    lib.check({"source": "not_a_source"}, "bad")
    assert ns["errors"]


def test_parcel_matches_are_in_the_right_place(con):
    """Regression: loose matching once linked 1330 W 57th St to a Long Beach house."""
    rows = con.execute("SELECT s.apn, s.lat, s.lon FROM sites s WHERE s.apn IS NOT NULL").fetchall()
    assert rows
    for apn, lat, lon in rows:
        assert 33.7 < lat < 34.35 and -118.7 < lon < -118.15, apn
    assert con.execute("SELECT apn FROM sites WHERE address = '1330 West 57th Street'").fetchone()[0] is None
    assert con.execute("SELECT apn FROM sites WHERE address = '207 East Opp Street'").fetchone()[0] is None
    assert con.execute("SELECT apn FROM sites WHERE address = '327 N. Harbor Blvd.'").fetchone()[0] is None


def test_housing_pathways_board(db):
    b = db.board("housing-pathways")
    assert {s["person"] for s in b["directors"]} == {"Cielo Castro", "Delfino De La Cruz Jr.", "Marlene Garza", "Jenny Scanlin"}
    assert [s["person"] for s in b["former"] if s["seat"] == "director"] == ["Alexandra Suh"]
    assert "appointee" in b["open_question"] and "at least three" in b["rule"]
    assert {s["person"] for s in b["officers"]} >= {"Tina Smith-Booth", "Eric Chang"}


def test_la_lomod_has_its_own_board(db):
    b = db.board("la-lomod")
    assert {s["person"] for s in b["directors"]} == {"Lourdes Castro Ramirez", "Delfino De La Cruz Jr.", "Aileen Morales"}
    assert b["rule"] is None


def test_llc_points_to_parent_board(db):
    b = db.board("lomod-pds")
    assert b["no_board_note"] and b["governed_by"]["entity"] == "La Cienega LOMOD, Inc."


def test_holding_shows_owner_board(db):
    h = db.holding("clarendon-apartments")
    g = {x["entity_id"]: x for x in h["governance"]}
    assert g["housing-pathways"]["directors"]
    assert db.holding("pueblo-del-sol-1")["governance"][0]["governed_by"]["directors"]


def test_every_seat_is_sourced(con):
    assert con.execute("SELECT COUNT(*) FROM board_seats WHERE source_id IS NULL OR quote IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM board_seats WHERE person = 'Alexandra Suh' AND status = 'current'").fetchone()[0] == 0


def test_no_inferred_seats_for_new_commissioners(con):
    """Nothing names Thomas or Demuyakor on these boards, so the database must not seat them."""
    assert con.execute("SELECT COUNT(*) FROM board_seats WHERE person IN ('Aaron Thomas','Adam Demuyakor','Lucelia Hooper')").fetchone()[0] == 0


def test_published_entity_lists_reconcile(con):
    assert con.execute("SELECT db_properties, db_units FROM entity_reconciliation WHERE entity_id = 'new-vision'").fetchone() == (7, 557)
    props, units = con.execute("SELECT db_properties, db_units FROM entity_reconciliation WHERE entity_id = 'lomod-south'").fetchone()
    assert (props - 1, units - 302) == (77, 1599)  # Rosslyn Lofts was bought after HACLA's March 2026 count.


def test_elimination_is_labelled_inferred(con):
    assert con.execute("SELECT evidence FROM roles WHERE holding_id = '1059-e-48th-st' AND role = 'owner'").fetchone()[0] == "inferred"
    assert con.execute("SELECT ownership_status FROM holdings WHERE id = '1059-e-48th-st'").fetchone()[0] == "inferred"
