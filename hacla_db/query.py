"""Read-only query layer over data/hacla.sqlite.

All filters are validated and bound as parameters. Unknown filter values raise QueryError rather than returning
an empty result, so a typo cannot look like a real finding.
"""
import os
import re
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlencode

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hacla.sqlite")

SORTS = {
    "name": "h.name COLLATE NOCASE",
    "units": "h.total_units",
    "year_built": "h.year_built",
    "acquired": "h.acquired_date",
    "city": "h.city COLLATE NOCASE",
}
HOUSING_RELATIONSHIPS = ("public_housing_direct", "owned_affiliate", "partnership", "partnership_noncontrolling")
EVIDENCE_LABELS = {
    "document_text": "Quote verified against the document",
    "document_image": "Read from a table image in the document",
    "structured_data": "Parsed from a structured source",
    "page_summary": "Seen through a page summary, not checked against the page",
    "search_excerpt": "From a search excerpt, not checked against the document",
    "inferred": "Inferred, not stated by the source",
}
RELATIONSHIP_LABELS = {
    "public_housing_direct": "Public housing (HACLA-owned)",
    "owned_affiliate": "Owned by HACLA or an instrumentality",
    "partnership": "Partnership with a HACLA role",
    "partnership_noncontrolling": "Partnership, non-controlling HACLA role",
    "pipeline_acquisition": "Pipeline: acquisition",
    "pipeline_development": "Pipeline: development",
    "commercial": "Commercial",
}


class QueryError(ValueError):
    """A filter value was invalid."""


@dataclass
class Page:
    rows: list
    total: int
    total_units: int
    limit: int
    offset: int
    filters: dict = field(default_factory=dict)


def source_link(url, page=None):
    """Deep link to a source; PDF viewers honor #page=N."""
    if page and ".pdf" in url.lower():
        return f"{url}#page={page}"
    return url


def person_slug(name):
    """URL-safe form of a person's name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fts_query(text):
    """Turn free text into a safe FTS5 prefix query. Punctuation can't reach the FTS parser."""
    terms = re.findall(r"[\w]+", text, flags=re.UNICODE)
    return " ".join(f'"{t}"*' for t in terms)


class HoldingsDB:
    def __init__(self, path=None):
        path = self.path = path or DEFAULT_DB
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} not found; run scripts/build_db.py")
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    def built_on(self):
        return self.con.execute("SELECT value FROM meta WHERE key = 'built_on'").fetchone()[0]

    def close(self):
        self.con.close()

    def _all(self, sql, args=()):
        return [dict(r) for r in self.con.execute(sql, args)]

    def _values(self, sql):
        return {r[0] for r in self.con.execute(sql)}

    def search(self, q=None, relationship=None, status=None, program=None, entity=None, city=None, zip=None,
               council_district=None, min_units=None, max_units=None, built_after=None, built_before=None,
               acquired_after=None, acquired_before=None, ami_at_most=None, has_capital_stack=None, has_ami=None,
               ownership_status=None, manager=None, housing_only=True, sort="name", desc=False, limit=50, offset=0):
        """Filter holdings; returns a Page. Each list-type filter accepts one value or a list (values are ORed)."""
        where, args = [], []

        def choice(column, value, table_values, label):
            values = [value] if isinstance(value, str) else list(value)
            unknown = [v for v in values if v not in table_values]
            if unknown:
                raise QueryError(f"Unknown {label}: {unknown}. Valid values: {sorted(x for x in table_values if x)}")
            where.append(f"{column} IN ({','.join('?' * len(values))})")
            args.extend(values)

        if q and q.strip():
            match = fts_query(q)
            if match:
                where.append("h.id IN (SELECT id FROM holdings_fts WHERE holdings_fts MATCH ?)")
                args.append(match)
        if relationship:
            choice("h.relationship", relationship, self._values("SELECT DISTINCT relationship FROM holdings"), "relationship")
        elif housing_only:
            where.append("h.relationship <> 'commercial'")
        if status:
            choice("h.status", status, self._values("SELECT DISTINCT status FROM holdings"), "status")
        if ownership_status:
            choice("h.ownership_status", ownership_status, self._values("SELECT DISTINCT ownership_status FROM holdings"), "ownership_status")
        if manager:
            choice("h.manager", manager, self._values("SELECT DISTINCT manager FROM holdings"), "manager")
        if city:
            choice("h.city", city, self._values("SELECT DISTINCT city FROM holdings"), "city")
        if zip:
            where.append("h.id IN (SELECT holding_id FROM sites WHERE zip = ?)")
            args.append(str(zip))
        if program:
            tags = [program] if isinstance(program, str) else list(program)
            valid = self._values("SELECT DISTINCT tag FROM holding_programs")
            unknown = [t for t in tags if t not in valid]
            if unknown:
                raise QueryError(f"Unknown program: {unknown}. Valid values: {sorted(valid)}")
            where.append(f"h.id IN (SELECT holding_id FROM holding_programs WHERE tag IN ({','.join('?' * len(tags))}))")
            args.extend(tags)
        if entity:
            if entity not in self._values("SELECT id FROM entities"):
                raise QueryError(f"Unknown entity id: {entity!r}")
            # Include subsidiaries, so La Cienega returns properties held through its LLCs.
            where.append("""h.id IN (SELECT holding_id FROM roles WHERE entity_id IN (
                WITH RECURSIVE tree(id) AS (SELECT ? UNION ALL SELECT e.id FROM entities e JOIN tree ON e.parent_id = tree.id) SELECT id FROM tree))""")
            args.append(entity)
        for column, value, op in (("h.council_district", council_district, "="), ("h.total_units", min_units, ">="), ("h.total_units", max_units, "<="),
                                  ("h.year_built", built_after, ">="), ("h.year_built", built_before, "<=")):
            if value is not None and value != "":
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    raise QueryError(f"Expected a whole number for {column.split('.')[1]}, got {value!r}")
                where.append(f"{column} {op} ?")
                args.append(number)
        for value, op in ((acquired_after, ">="), (acquired_before, "<=")):
            if value:
                if not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", str(value)):
                    raise QueryError(f"Expected a date as YYYY, YYYY-MM or YYYY-MM-DD, got {value!r}")
                where.append(f"h.acquired_date {op} ?")
                args.append(str(value))
        if ami_at_most is not None and ami_at_most != "":
            try:
                pct = int(ami_at_most)
            except (TypeError, ValueError):
                raise QueryError(f"Expected a whole number for ami_at_most, got {ami_at_most!r}")
            where.append("h.id IN (SELECT holding_id FROM ami_restrictions WHERE ami_pct <= ? AND COALESCE(units, 1) > 0)")
            args.append(pct)
        if has_capital_stack is not None:
            where.append("h.has_capital_stack = ?")
            args.append(int(bool(has_capital_stack)))
        if has_ami is not None:
            where.append("h.has_ami = ?")
            args.append(int(bool(has_ami)))
        if sort not in SORTS:
            raise QueryError(f"Unknown sort: {sort!r}. Valid values: {sorted(SORTS)}")
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total, units = self.con.execute(f"SELECT COUNT(*), COALESCE(SUM(h.total_units),0) FROM holdings h {clause}", args).fetchone()
        # Rows with no value sort last in either direction.
        order = f"{SORTS[sort]} IS NULL, {SORTS[sort]} {'DESC' if desc else 'ASC'}, h.name COLLATE NOCASE"
        rows = self._all(f"""SELECT h.*, (SELECT GROUP_CONCAT(tag, ', ') FROM holding_programs p WHERE p.holding_id = h.id) AS programs,
                             (SELECT MIN(ami_pct) FROM ami_restrictions a WHERE a.holding_id = h.id AND COALESCE(a.units,1) > 0) AS deepest_ami
                             FROM holdings h {clause} ORDER BY {order} LIMIT ? OFFSET ?""", [*args, limit, offset])
        return Page(rows, total, units, limit, offset)

    def _with_links(self, rows):
        """Attach the source title, deep link, and readable evidence level to fact rows."""
        cache = {}
        for r in rows:
            sid = r.get("source_id")
            if not sid:
                continue
            if sid not in cache:
                cache[sid] = dict(self.con.execute("SELECT title, url, kind, doc_date FROM sources WHERE id = ?", (sid,)).fetchone())
            s = cache[sid]
            r.update(source_title=s["title"], source_url=source_link(s["url"], r.get("page")), source_date=s["doc_date"], source_kind=s["kind"],
                     evidence_label=EVIDENCE_LABELS.get(r.get("evidence"), ""))
        return rows

    def holding(self, holding_id):
        """Everything known about one holding, each fact with its source. None if not found."""
        rows = self._all("SELECT * FROM holdings WHERE id = ?", (holding_id,))
        if not rows:
            return None
        h = self._with_links(rows)[0]
        hid = (holding_id,)
        h["programs"] = [r["tag"] for r in self._all("SELECT tag FROM holding_programs WHERE holding_id = ? ORDER BY tag", hid)]
        h["sites"] = self._with_links(self._all("SELECT * FROM sites WHERE holding_id = ? ORDER BY id", hid))
        h["roles"] = self._with_links(self._all("SELECT * FROM roles WHERE holding_id = ? ORDER BY role, entity_name", hid))
        h["ami"] = self._with_links(self._all("SELECT * FROM ami_restrictions WHERE holding_id = ? ORDER BY restriction_type, ami_pct", hid))
        h["events"] = self._with_links(self._all("SELECT * FROM events WHERE holding_id = ? ORDER BY date", hid))
        h["notes"] = self._with_links(self._all("SELECT * FROM notes WHERE holding_id = ?", hid))
        h["issues"] = self._with_links(self._all("SELECT * FROM data_issues WHERE holding_id = ?", hid))
        h["financing"] = self._with_links(self._all(
            """SELECT f.*, (SELECT COUNT(*) FROM financing_holdings x WHERE x.financing_id = f.id) AS n_holdings FROM financing f
               JOIN financing_holdings fh ON fh.financing_id = f.id WHERE fh.holding_id = ?
               ORDER BY f.stack IS NULL, f.stack DESC, f.phase DESC, f.amount DESC""", hid))
        h["reports"] = self._with_links(self._all("SELECT * FROM ctcac_reports WHERE holding_id = ? ORDER BY app DESC", hid))
        stats = self._all("SELECT * FROM resident_stats WHERE holding_id = ?", hid)
        h["resident_stats"] = self._with_links(stats)[0] if stats else None
        h["governance"] = self._governance_for(holding_id)
        h["sources"] = self._sources_for(h)
        return h

    GOVERNING_ROLES = ("owner", "planned_owner", "general_partner", "managing_general_partner", "administrative_general_partner", "class_a_limited_partner", "non_managing_member_of_agp")

    def _governance_for(self, holding_id):
        """Boards of the HACLA entities holding a role here. An LLC or partnership points to its parent's board."""
        marks = ",".join("?" * len(self.GOVERNING_ROLES))
        rows = self._all(f"SELECT DISTINCT entity_id, role FROM roles WHERE holding_id = ? AND entity_id IS NOT NULL AND role IN ({marks})", (holding_id, *self.GOVERNING_ROLES))
        out = []
        for r in rows:
            if r["entity_id"] == "hacla":
                continue
            board = self.board(r["entity_id"])
            if board:
                out.append({"entity_id": r["entity_id"], "role": r["role"], **board})
        return out

    def board(self, entity_id):
        """Directors, officers and rule for an entity, following LLCs and partnerships up to the governing parent."""
        rows = self._all("SELECT id, name, parent_id FROM entities WHERE id = ?", (entity_id,))
        if not rows:
            return None
        e = rows[0]
        info = self._all("SELECT * FROM board_info WHERE entity_id = ?", (entity_id,))
        info = info[0] if info else {}
        seats = self._with_links(self._all(
            """SELECT s.*, p.hacla_role FROM board_seats s JOIN people p ON p.name = s.person WHERE s.entity_id = ?
               ORDER BY s.status = 'former', s.seat <> 'director', s.is_chair DESC, s.person""", (entity_id,)))
        governed_by = None
        if info.get("no_board_note") and e["parent_id"]:
            governed_by = self.board(e["parent_id"])
        rule_source = self.source(info["rule_source_id"]) if info.get("rule_source_id") else None
        return {
            "entity": e["name"],
            "directors": [s for s in seats if s["seat"] == "director" and s["status"] == "current"],
            "officers": [s for s in seats if s["seat"] != "director" and s["status"] == "current"],
            "former": [s for s in seats if s["status"] == "former"],
            "rule": info.get("rule"),
            "rule_url": source_link(rule_source["url"], info.get("rule_page")) if rule_source else None,
            "rule_quote": info.get("rule_quote"),
            "open_question": info.get("open_question"),
            "no_board_note": info.get("no_board_note"),
            "governed_by": governed_by,
        }

    def people(self):
        """Everyone with a recorded seat, most seats first, showing how far the same people recur across boards."""
        return self._all(
            """SELECT p.name, p.hacla_role, c.title AS commissioner_title,
               SUM(s.seat = 'director' AND s.status = 'current') AS director_seats,
               SUM(s.seat <> 'director' AND s.status = 'current') AS officer_seats,
               SUM(s.status = 'former') AS former_seats
               FROM people p LEFT JOIN board_seats s ON s.person = p.name LEFT JOIN commissioners c ON c.person = p.name
               GROUP BY p.name ORDER BY director_seats DESC, officer_seats DESC, p.name""")

    def person(self, name):
        rows = self._all("SELECT * FROM people WHERE name = ?", (name,))
        if not rows:
            return None
        p = rows[0]
        p["seats"] = self._with_links(self._all(
            """SELECT s.*, e.name AS entity_name FROM board_seats s JOIN entities e ON e.id = s.entity_id WHERE s.person = ?
               ORDER BY s.status, s.seat <> 'director', e.name""", (name,)))
        comm = self._all("SELECT * FROM commissioners WHERE person = ?", (name,))
        p["commissioner"] = self._with_links(comm)[0] if comm else None
        return p

    def person_by_slug(self, slug):
        for row in self.con.execute("SELECT name FROM people"):
            if person_slug(row[0]) == slug:
                return self.person(row[0])
        return None

    def holdings_export(self):
        """Every holding with what the static site's table needs to filter and sort in the browser."""
        rows = self.search(housing_only=False, limit=1000).rows
        sites = {}
        for r in self.con.execute("SELECT holding_id, address, zip FROM sites"):
            sites.setdefault(r[0], []).append((r[1] or "", r[2] or ""))
        tags = {}
        for r in self.con.execute("SELECT holding_id, tag FROM holding_programs ORDER BY tag"):
            tags.setdefault(r[0], []).append(r[1])
        for row in rows:
            row["program_tags"] = tags.get(row["id"], [])
            row["site_addresses"] = [a for a, _ in sites.get(row["id"], [])]
            row["site_zips"] = sorted({z for _, z in sites.get(row["id"], []) if z})
            row["search_text"] = " ".join(str(v) for v in (row["name"], " ".join(row["site_addresses"]), row["city"], row["program_raw"], row["owner_name"], row["manager"]) if v)
            for key in ("quote", "source_id", "page", "evidence", "acquired_basis", "year_built_basis", "ctcac_apps", "hud_code", "apn", "population", "in_asset_list"):
                row.pop(key, None)
        return rows

    def commissioners(self):
        return self._with_links(self._all("SELECT c.*, p.hacla_role FROM commissioners c JOIN people p ON p.name = c.person ORDER BY c.title <> 'Chair', c.person"))

    @staticmethod
    def _sources_for(h):
        """Distinct documents behind a holding, most cited first, for the sources panel."""
        seen = {}
        groups = [[h], h["sites"], h["roles"], h["ami"], h["events"], h["notes"], h["financing"], h["reports"], h["issues"]]
        if h["resident_stats"]:
            groups.append([h["resident_stats"]])
        for group in groups:
            for f in group:
                if f.get("source_id"):
                    item = seen.setdefault(f["source_id"], {"id": f["source_id"], "title": f["source_title"], "url": f["source_url"].split("#")[0], "kind": f["source_kind"], "date": f["source_date"], "facts": 0})
                    item["facts"] += 1
        return sorted(seen.values(), key=lambda s: -s["facts"])

    def entities(self):
        return self._with_links(self._all(
            """SELECT e.*, r.published_properties, r.published_units, r.db_properties, r.db_units,
               (SELECT COUNT(DISTINCT holding_id) FROM roles x WHERE x.entity_id = e.id) AS n_holdings,
               (SELECT COUNT(*) FROM board_seats s WHERE s.entity_id = e.id AND s.seat = 'director' AND s.status = 'current') AS n_directors
               FROM entities e LEFT JOIN entity_reconciliation r ON r.entity_id = e.id ORDER BY e.type DESC, e.name"""))

    def entity(self, entity_id):
        rows = self._all("SELECT * FROM entities WHERE id = ?", (entity_id,))
        if not rows:
            return None
        e = self._with_links(rows)[0]
        e["children"] = self._all("SELECT id, name, type FROM entities WHERE parent_id = ? ORDER BY name", (entity_id,))
        e["parent"] = self._all("SELECT id, name FROM entities WHERE id = ?", (e["parent_id"],))
        e["holdings"] = self._with_links(self._all(
            """SELECT h.id, h.name, h.total_units, h.status, r.role, r.source_id, r.page, r.quote, r.evidence, r.note FROM roles r JOIN holdings h ON h.id = r.holding_id
               WHERE r.entity_id = ? ORDER BY h.name""", (entity_id,)))
        recon = self._all("SELECT * FROM entity_reconciliation WHERE entity_id = ?", (entity_id,))
        e["reconciliation"] = recon[0] if recon else None
        e["board"] = self.board(entity_id)
        return e

    def capital_stack(self, holding_id, phase="permanent"):
        """Financing rows for a holding. For LIHTC deals `phase` picks that side of the staff-report table."""
        return [f for f in (self.holding(holding_id) or {"financing": []})["financing"] if f["phase"] in (phase, None)]

    def source(self, source_id):
        rows = self._all("SELECT * FROM sources WHERE id = ?", (source_id,))
        return rows[0] if rows else None

    def sources(self):
        return self._all("""SELECT s.*, (SELECT COUNT(*) FROM holdings WHERE source_id = s.id) + (SELECT COUNT(*) FROM roles WHERE source_id = s.id)
                            + (SELECT COUNT(*) FROM financing WHERE source_id = s.id) + (SELECT COUNT(*) FROM ami_restrictions WHERE source_id = s.id)
                            + (SELECT COUNT(*) FROM events WHERE source_id = s.id) + (SELECT COUNT(*) FROM notes WHERE source_id = s.id)
                            + (SELECT COUNT(*) FROM board_seats WHERE source_id = s.id) AS n_facts
                            FROM sources s ORDER BY s.kind, s.doc_date DESC, s.title""")

    def facilities(self):
        """Portfolio-wide financing not attributed to named properties."""
        return self._with_links(self._all(
            "SELECT * FROM financing f WHERE NOT EXISTS (SELECT 1 FROM financing_holdings x WHERE x.financing_id = f.id) ORDER BY amount DESC"))

    def issues(self):
        return self._with_links(self._all(
            """SELECT i.*, h.name AS holding_name FROM data_issues i LEFT JOIN holdings h ON h.id = i.holding_id ORDER BY i.holding_id IS NOT NULL, h.name"""))

    def facets(self):
        """Valid filter values with counts, excluding commercial."""
        def count(sql):
            return self._all(sql)
        return {
            "relationship": count("SELECT relationship AS value, COUNT(*) AS n FROM holdings GROUP BY 1 ORDER BY 2 DESC"),
            "status": count("SELECT status AS value, COUNT(*) AS n FROM holdings GROUP BY 1 ORDER BY 2 DESC"),
            "program": count("SELECT tag AS value, COUNT(*) AS n FROM holding_programs GROUP BY 1 ORDER BY 2 DESC"),
            "manager": count("SELECT manager AS value, COUNT(*) AS n FROM holdings WHERE manager IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"),
            "council_district": count("SELECT council_district AS value, COUNT(*) AS n FROM holdings WHERE council_district IS NOT NULL GROUP BY 1 ORDER BY 1"),
            "ownership_status": count("SELECT ownership_status AS value, COUNT(*) AS n FROM holdings GROUP BY 1 ORDER BY 2 DESC"),
        }

    def stats(self):
        one = lambda sql: self.con.execute(sql).fetchone()[0]
        housing = "relationship NOT IN ('commercial')"
        return {
            "holdings": one(f"SELECT COUNT(*) FROM holdings WHERE {housing}"),
            "units_operating": one(f"SELECT COALESCE(SUM(total_units),0) FROM holdings WHERE {housing} AND status = 'operating'"),
            "public_housing_units": one("SELECT COALESCE(SUM(total_units),0) FROM holdings WHERE relationship = 'public_housing_direct' AND id <> 'jordan-downs-legacy'"),
            "with_location": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND lat IS NOT NULL"),
            "with_year": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND year_built IS NOT NULL"),
            "with_acquired": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND acquired_date IS NOT NULL"),
            "with_stack": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND has_capital_stack = 1"),
            "with_ami": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND has_ami = 1"),
            "owner_documented": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND ownership_status = 'documented'"),
            "owner_inferred": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND ownership_status = 'inferred'"),
            "owner_not_itemized": one(f"SELECT COUNT(*) FROM holdings WHERE {housing} AND ownership_status = 'not_itemized'"),
            "board_seats": one("SELECT COUNT(*) FROM board_seats WHERE status = 'current'"),
            "entities_with_board": one("SELECT COUNT(DISTINCT entity_id) FROM board_seats"),
            "sources": one("SELECT COUNT(*) FROM sources"),
            "issues": one("SELECT COUNT(*) FROM data_issues"),
        }

    def map_points(self, **filters):
        page = self.search(limit=1000, **filters)
        return [{k: r[k] for k in ("id", "name", "lat", "lon", "total_units", "relationship", "status")} for r in page.rows if r["lat"] is not None]

    def query_string(self, **filters):
        return urlencode({k: v for k, v in filters.items() if v not in (None, "")}, doseq=True)
