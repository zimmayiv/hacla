"""Build data/hacla.sqlite from raw extracts and curated facts. Fails loudly on bad references or unverifiable quotes.

  python scripts/build_db.py --export-sites   writes data/raw/sites_to_enrich.json
  python scripts/build_db.py                  writes data/hacla.sqlite
"""
import datetime as dt, json, os, re, sqlite3, sys
from collections import Counter, defaultdict
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CUR = os.path.join(ROOT, "data", "curated")
DB = os.path.join(ROOT, "data", "hacla.sqlite")
TEXT_EVIDENCE = "document_text"
errors = []


def norm(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("‐", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).lower().strip()


class Library:
    """Source registry plus page-indexed text used to verify quotes."""

    def __init__(self):
        self.sources = {s["id"]: s for s in yaml.safe_load(open(os.path.join(CUR, "sources.yaml")))}
        self.pages = {}
        self.whole = {}

    def add(self, sid, title, kind, url, date=None, text=None):
        self.sources[sid] = {"id": sid, "title": title, "kind": kind, "url": url, "date": date, "text": text}

    def _load(self, sid):
        if sid in self.pages:
            return
        rel = self.sources[sid].get("text")
        self.pages[sid] = []
        if rel:
            raw = open(os.path.join(RAW, rel), errors="ignore").read()
            parts = re.split(r"#### PAGE (\d+)\n", raw)
            self.pages[sid] = [(int(parts[i]), norm(parts[i + 1])) for i in range(1, len(parts) - 1, 2)] or [(None, norm(raw))]
            self.whole[sid] = norm(" ".join(t for _, t in self.pages[sid]))

    def check(self, fact, where):
        """Resolve (source_id, page, quote, evidence) for a curated fact, verifying any quote."""
        sid = fact.get("source")
        if sid not in self.sources:
            errors.append(f"{where}: unknown source {sid!r}")
            return None, None, None, "inferred"
        quote, evidence = fact.get("quote"), fact.get("evidence")
        page = fact.get("page")
        if quote:
            self._load(sid)
            if self.pages[sid]:
                q = norm(quote)
                hit = next((p for p, t in self.pages[sid] if q in t), None)
                if hit is None and q not in self.whole[sid]:
                    errors.append(f"{where}: quote not found in {sid}: {quote[:80]!r}")
                page = hit
                evidence = evidence or TEXT_EVIDENCE
            else:
                evidence = evidence or "page_summary"
        return sid, page, quote, evidence or "structured_data"


LIB = Library()
SLUG = lambda s: re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def to_int(x):
    x = str(x or "").replace(",", "").strip()
    return int(x) if x.isdigit() else None


PROGRAM_TAGS = [
    (r"market rate", "Market Rate"), (r"affordable", "Affordable"), (r"rad pbv", "RAD PBV"), (r"\b(pbv|pvb)\b", "PBV"),
    (r"pbra", "PBRA"), (r"psh", "PSH"), (r"bond", "Bond"), (r"tax credit", "LIHTC"), (r"public housing", "Public Housing"),
    (r"family", "Family"), (r"seniors? 62", "Seniors 62+"), (r"seniors? 55", "Seniors 55+"), (r"disabilit", "Persons with Disabilities"),
]


def tags_from(text):
    t = (text or "").lower()
    found = {tag for pat, tag in PROGRAM_TAGS if re.search(pat, t)}
    if "RAD PBV" in found and not re.search(r"(?<!rad )\bpbv", t):
        found.discard("PBV")
    return found


def manager_from(email):
    e = (email or "").replace(" ", "").lower()
    if "bell" in e:
        return "bell"
    if "wsh" in e:
        return "wsh"
    if "nati" in e or "core" in e or "ional" in e:
        return "core"
    return None


def add_holding(H, h):
    if h["id"] in H:
        errors.append(f"duplicate holding id {h['id']}")
    H[h["id"]] = {"programs": set(), "sites": [], "roles": [], "ami": [], "events": [], "financing": [], "notes": [], **h}
    return H[h["id"]]


def load_asset_list(H):
    rows = json.load(open(os.path.join(RAW, "all_properties.json")))
    facts = yaml.safe_load(open(os.path.join(CUR, "facts.yaml")))
    overrides = facts["list_overrides"]
    merged = defaultdict(list)
    for r in rows:
        if r["name"].startswith(("HACLA Asset", "Residential Prop")):
            continue
        name = r["name"].replace("St. (Harbor", "St. (Harbor Riviera)") if "(Harbor" in r["name"] else r["name"]
        name = name.replace("Clemson- Corbett", "Clemson-Corbett")
        city = r["city"].replace("Wilmingto n", "Wilmington").replace("Mount Washingto n", "Mount Washington")
        slug = SLUG(name)
        site = {"address": r["address"], "city": city, "zip": r["zip"], "units": to_int(r["total"]), "page": r["page"], "source": "all_properties"}
        if "Public Housing" in r["program"] and "yosemite" not in slug:
            merged["jordan-scattered"].append(site)
            continue
        if slug == "dana-street-apartments":
            merged[slug].append(site)
            continue
        o = overrides.get(slug, {})
        br = {k: to_int(r[k]) for k in ("br0", "br1", "br2", "br3", "br4")}
        br.update(o.get("br", {}))
        units = o.get("units", to_int(r["total"]))
        addr = o.get("address", r["address"])
        site.update(address=addr, units=units)
        h = add_holding(H, {"id": slug, "name": o.get("name", name), "relationship": "owned_affiliate", "status": "operating",
                            "total_units": units, **br, "program_raw": r["program"], "in_asset_list": 1,
                            "address": addr, "city": city, "zip": r["zip"], "manager_id": manager_from(r["email"]),
                            "source": "all_properties", "page": r["page"], "evidence": "structured_data"})
        h["programs"] = tags_from(r["program"])
        h["sites"] = [site]
    return merged, facts


def load_public_housing(H, merged, facts):
    dev = {f["attributes"]["DEVELOPMENT_CODE"]: f["attributes"] for f in json.load(open(os.path.join(RAW, "hud", "Public_Housing_Developments.json")))["features"]}
    bld = defaultdict(list)
    for f in json.load(open(os.path.join(RAW, "hud", "Public_Housing_Buildings.json")))["features"]:
        bld[f["attributes"]["DEVELOPMENT_CODE"]].append(f["attributes"])
    epoch = dt.datetime(1970, 1, 1)
    day = lambda ms: (epoch + dt.timedelta(milliseconds=ms)).date().isoformat()

    def hud_block(code):
        a = dev[code]
        dates = sorted(day(b["DOFA_ACTUAL_DT"]) for b in bld[code] if b["DOFA_ACTUAL_DT"] is not None)
        stats = {"occupied": a["TOTAL_OCCUPIED"], "pct_occupied": a["PCT_OCCUPIED"], "avg_household_income": a["HH_INCOME"], "pct_under_30_ami": a["PCT_LT30_MEDIAN"], "pct_under_50_ami": a["PCT_LT50_MEDIAN"],
                 "pct_under_80_ami": a["PCT_LT80_MEDIAN"], "avg_tenant_rent": a["RENT_PER_MONTH"], "avg_people_per_unit": a["PEOPLE_PER_UNIT"], "pct_age_62_plus": a["PCT_AGE62PLUS"],
                 "pct_disabled": a["PCT_DISABLED_ALL"], "as_of": day(a["LAST_UPDT_DTTM"]) if a["LAST_UPDT_DTTM"] else None}
        return a, dates, stats

    for p in facts["public_housing"]:
        a, dates, stats = hud_block(p["code"])
        addr = re.sub(r"\s+", " ", a["STD_ADDR"]).strip()
        h = add_holding(H, {"id": p["id"], "name": p["name"], "relationship": "public_housing_direct", "status": p.get("status", "operating"), "total_units": a["TOTAL_DWELLING_UNITS"],
                            "program_raw": "Public Housing (ACC)", "hud_code": p["code"], "address": addr, "city": a["STD_CITY"].strip(), "zip": a["STD_ZIP5"], "lat": a["LAT"], "lon": a["LON"], "source": "hud_dev", "evidence": "structured_data"})
        h["programs"] = {"Public Housing"}
        h["stats"] = stats
        h["sites"] = merged.get(p["id"]) or [{"address": addr, "city": h["city"], "zip": h["zip"], "units": h["total_units"], "lat": a["LAT"], "lon": a["LON"], "source": "hud_dev", "geocode_source": "HUD"}]
        h["roles"].append({"entity": "hacla", "role": "owner", "source": "hud_dev"})
        if dates:
            h["events"].append({"type": "first_availability", "date": dates[0], "source": "hud_bldg", "note": "HUD Date of Full Availability, earliest building" if dates[0] != dates[-1] else "HUD Date of Full Availability"})
            if dates[0] != dates[-1]:
                h["events"].append({"type": "last_availability", "date": dates[-1], "source": "hud_bldg", "note": "HUD Date of Full Availability, latest building"})
    hud_block.codes = set(dev)
    if merged.get("jordan-scattered"):
        total = sum(s["units"] or 0 for s in merged["jordan-scattered"])
        if total != H["jordan-scattered"]["total_units"]:
            errors.append(f"Jordan scattered sites sum to {total}, HUD says {H['jordan-scattered']['total_units']}")
    return hud_block


def classify(source):
    s = source.lower()
    if "tax credit equity" in s or "existing tax credit" in s:
        return "tax_credit_equity"
    if "deferred developer" in s or "deferred" in s or "developer fee" in s:
        return "deferred_fee"
    if re.match(r"hacla", s):
        return "hacla_loan"
    if re.search(r"general partner|nonprofit equity|capital contribution|\bequity\b", s):
        return "other_equity"
    if re.search(r"hcd|mhp|ahsc|iig|cni|calhfa|hhh|lahd|lacda|city of los angeles|hcid|mips|hud|cdbg|ula|tcc|homekey|cra|soft|prop", s):
        return "public_soft_loan"
    if re.search(r"tax.?exempt|taxable|bank|chase|citi|wells|u\.?s\.? bank|pnc|jpm|freddie|fannie|berkadia|pacific western|keybank|lender|perm|bond|loan|r4|mortgage", s):
        return "senior_debt"
    return "other"


def load_ctcac(H, facts, hud_block):
    data = json.load(open(os.path.join(RAW, "ctcac.json")))
    reports_out = []
    tiers = [("Units at or below 20% AMI", 20), ("Units at or below 30% AMI", 30), ("Units at 35% AMI", 35), ("Units at 40% AMI", 40), ("Units at 45% AMI", 45), ("Units at 50% AMI", 50), ("Units at 55% AMI", 55), ("Units at 60% AMI", 60), ("Units at 70% AMI", 70), ("Units at 80% AMI", 80)]
    for c in facts["holdings"]:
        sid, page, quote, ev = LIB.check(c, f"holding {c['id']}")
        h = add_holding(H, {"id": c["id"], "name": c["name"], "relationship": c["relationship"], "status": c["status"], "total_units": c.get("units"), "program_raw": ", ".join(c.get("program_tags", [])) or None,
                            "population": c.get("population"), "hud_code": c.get("hud_code"), "address": c.get("address"), "city": c.get("city"), "zip": c.get("zip"), "source": sid, "page": page, "quote": quote, "evidence": ev, **c.get("br", {})})
        h["programs"] = set(c.get("program_tags", []))
        if c.get("sites"):
            h["sites"] = [{"address": a, "city": "Los Angeles", "zip": None, "units": None, "source": sid, "page": page} for a in c["sites"]]
        elif h["address"]:
            h["sites"] = [{"address": h["address"], "city": h["city"], "zip": h["zip"], "units": h["total_units"], "source": sid, "page": page}]
        apps = []
        for app in c.get("ctcac", []):
            d = data["projects"].get(app)
            if not d:
                errors.append(f"{c['id']}: CTCAC {app} not in project list")
                continue
            apps.append(app)
            short = "CA-" + app[5:]
            rpt = data["reports"].get(short)
            pis = (d.get("Placed in Service (PIS) Date") or "")[:10] or None
            addr = re.sub(r"\s+", " ", d["Project Address"]).split(",")[0].strip()
            h["address"] = addr
            h["city"] = (d.get("Project City") or "").strip()
            h["zip"] = (d.get("Project Zip Code") or "")[:5]
            h["total_units"] = to_int(d.get("Total Units")) or h["total_units"]
            h["low_income_units"] = to_int(d.get("Low Income Units"))
            for k, field in (("br0", "Number of SRO/Studio Units"), ("br1", "Number of 1 Bedroom Units"), ("br2", "Number of 2 Bedroom Units"), ("br3", "Number of 3 Bedroom Units"), ("br4", "Number of 4 Bedroom Units")):
                h[k] = to_int(d.get(field))
            h["program_raw"] = f"LIHTC ({d.get('Type of Tax Credit Funding') or ''}), {d.get('Housing Type') or ''}".strip(", ")
            h["programs"] |= {"LIHTC"}
            h["population"] = d.get("Housing Type")
            h["ctcac_construction"] = d.get("Construction Type")
            h["sites"] = [{"address": addr, "city": h["city"], "zip": h["zip"], "units": h["total_units"], "source": "ctcac_list"}]
            h["manager_name"] = d.get("Management Company") or h.get("manager_name")
            if d.get("Owner or Applicant Name"):
                h["roles"].append({"entity_name": d["Owner or Applicant Name"], "role": "owner_partnership", "source": "ctcac_list", "note": app})
            for k in ("General Partner (1)", "General Partner (2)", "General Partner (3)"):
                if d.get(k) and d[k] != "To Be Formed":
                    h["roles"].append({"entity_name": d[k], "role": "general_partner_ctcac", "source": "ctcac_list", "note": app})
            if d.get("Developer"):
                h["roles"].append({"entity_name": d["Developer"], "role": "developer", "source": "ctcac_list", "note": app})
            if pis:
                h["events"].append({"type": "placed_in_service", "date": pis, "source": "ctcac_list", "note": app})
            amis = [(pct, to_int(d.get(col))) for col, pct in tiers if to_int(d.get(col))]
            src = "ctcac_list"
            if rpt:
                src = "ctcac_" + short
                LIB.add(src, f"CTCAC Staff Report {short}: {d['Project Name'].strip()}", "state_report", rpt["url"], rpt["report_date"], text=f"ctcac/staff_txt/{short}.txt")
                if rpt.get("investor"):
                    h["roles"].append({"entity_name": rpt["investor"], "role": "tax_credit_investor", "source": src, "note": app})
                if rpt.get("manager"):
                    h["manager_name"] = re.sub(r"^\(?s\)?:\s*", "", rpt["manager"])
                if rpt.get("parents"):
                    for parent in rpt["parents"].split(";"):
                        h["roles"].append({"entity_name": parent.strip(), "role": "gp_parent", "source": src, "note": app})
                if rpt.get("bond_issuer"):
                    h["roles"].append({"entity_name": rpt["bond_issuer"], "role": "bond_issuer", "source": src, "note": app})
                if rpt["ami"]:
                    amis = [(a["ami_pct"], a["units"]) for a in rpt["ami"]]
                total = None
                perm_sum = 0
                for f in rpt["financing"]:
                    if f["source"] == "TOTAL":
                        total = f["amount"]
                        continue
                    perm_sum += f["amount"] if f["phase"] == "permanent" else 0
                    h["financing"].append({"label": f["source"], "kind": classify(f["source"]), "amount": f["amount"], "phase": f["phase"], "stack": app, "date": rpt["report_date"], "source": src, "page": f["page"], "evidence": "structured_data"})
                reports_out.append({"app": app, "holding_id": h["id"], "report_date": rpt["report_date"], "permanent_total": total, "permanent_sum": perm_sum, "source_id": src})
            for pct, units in amis:
                h["ami"].append({"ami_pct": pct, "units": units, "type": "lihtc_regulatory", "source": src, "note": app, "evidence": "structured_data"})
        h["ctcac_apps"] = ",".join(apps) or None
        if c.get("hud_code") in hud_block.codes:
            a, dates, stats = hud_block(c["hud_code"])
            h["stats"] = stats
            h["programs"].add("Public Housing")
    return reports_out


def apply_curated(H, facts):
    E = {e["id"]: e for e in facts["entities"]}

    def holding(fact, where):
        h = H.get(fact["holding"])
        if not h:
            errors.append(f"{where}: unknown holding {fact['holding']!r}")
        return h

    for r in facts["ownership"]:
        h = holding(r, "ownership")
        if h:
            if r["entity"] not in E:
                errors.append(f"ownership: unknown entity {r['entity']}")
            h["roles"].append({"entity": r["entity"], "role": r["role"], "note": r.get("note"), **dict(zip(("source", "page", "quote", "evidence"), LIB.check(r, f"ownership {r['holding']}")))})
    for a in facts["ami"]:
        h = holding(a, "ami")
        if h:
            sid, page, quote, ev = LIB.check(a, f"ami {a['holding']}")
            for pct, units in a["tiers"].items():
                h["ami"].append({"ami_pct": int(pct), "units": units, "type": a["type"], "note": a.get("note"), "source": sid, "page": page, "quote": quote, "evidence": ev})
    for e in facts["events"]:
        h = holding(e, "event")
        if h:
            sid, page, quote, ev = LIB.check(e, f"event {e['holding']}")
            h["events"].append({"type": e["type"], "date": str(e["date"]), "note": e.get("note"), "source": sid, "page": page, "quote": quote, "evidence": ev})
    for n in facts["notes"]:
        h = holding(n, "note")
        if h:
            sid, page, quote, ev = LIB.check(n, f"note {n['holding']}")
            h["notes"].append({"text": n["text"], "source": sid, "page": page, "quote": quote, "evidence": ev})
    return E


def assign_by_elimination(H, facts):
    """New Vision and HOC publish full property lists, so the rest of the list pool belongs to LOMOD South."""
    pinned = {r["holding"] for r in facts["ownership"] if r["role"] in ("owner", "planned_owner")}
    for h in H.values():
        if h.get("in_asset_list") and h["id"] not in pinned and not any(r["role"] == "owner" for r in h["roles"]):
            h["roles"].append({"entity": "lomod-south", "role": "owner", "source": "ar_new_vision", "evidence": "inferred",
                               "note": "By elimination: on HACLA's property list, and not among the properties named for any other entity. LOMOD South's report names only three of its properties."})


def load_governance(gov, E):
    seats, info = [], {}
    people = gov["people"]
    alias = gov["quote_names"]
    latest = {}

    def seat(entity, person, title, source, quote, status="current", is_chair=0, end=None, note=None, evidence=None):
        if entity not in E:
            errors.append(f"governance: unknown entity {entity}")
        if person not in people:
            errors.append(f"governance: unknown person {person}")
        sid, page, q, ev = LIB.check({"source": source, "quote": quote, "evidence": evidence}, f"seat {entity}/{person}")
        seats.append({"entity": entity, "person": person, "seat": title, "is_chair": is_chair, "status": status, "as_of": "2024-12" if source.startswith("ar_") else LIB.sources[source].get("date"), "end": end, "note": note, "source": sid, "page": page, "quote": q, "evidence": ev})

    # Annual reports are published late; their rosters describe the fiscal year, which is why Suh still appears.
    for r in sorted(gov["rosters"], key=lambda r: LIB.sources[r["source"]].get("date") or ""):
        for ent in r["entities"]:
            for p in r["people"]:
                name = alias.get(p, p)
                former = p == "Alexandra Suh"
                note = "Initial director named in the formation resolution." if r.get("initial") else r.get("note")
                seat(ent, p, "director", r["source"], name, "former" if former else "current", int(p == r.get("chair")), gov["suh_resignation"]["date"] if former else None, note)
    for o in gov["officers"]:
        for ent in o["entities"]:
            src = gov["entity_board_source"][ent] if o.get("source_by_entity") else o["source"]
            seat(ent, o["person"], o["title"].lower(), src, o.get("quote") or o["person"], o.get("status", "current"), 0, o.get("end_date"), o.get("note"))
    # A later statement replaces an earlier one for the same person, seat and entity.
    for s in seats:
        key = (s["entity"], s["person"], s["seat"])
        if key not in latest or (s["as_of"] or "") >= (latest[key]["as_of"] or ""):
            latest[key] = s
    rule = LIB.check({**gov["standard_rule_source"]}, "standard rule")
    fill = gov["vacancy_fill_entities"]
    for ent in E:
        d = {"rule": None, "rule_source": None, "rule_page": None, "rule_quote": None, "open_question": None, "no_board_note": gov["no_board_note"].get(ent)}
        if ent in gov["standard_rule_entities"]:
            d.update(rule=gov["standard_rule"], rule_source=rule[0], rule_page=rule[1], rule_quote=rule[2])
        if ent in fill:
            d["open_question"] = "Alexandra Suh's seat was filled in December 2025 by appointment of the Chair, but the appointee isn't named in any document found."
        info[ent] = d
    for ent in ("hillhaven-lomod", "soto-lomod", "lafayette-cc", "lomod-bunker-hill"):
        info[ent]["open_question"] = (info[ent]["open_question"] or "") + " No roster for this board was found; only the shared bylaws and the Suh vacancy are documented."
    for ent in [e["id"] for e in gov["new_entities"][1:]]:
        info[ent]["open_question"] = "The formation resolution leaves the fifth director's name blank."
        info[ent].update(rule=gov["standard_rule"], rule_source=rule[0], rule_page=rule[1], rule_quote=rule[2])
    for ent in fill:
        LIB.check({"source": "boc_bylaws_2025", "quote": gov["suh_resignation"]["quote"]}, "suh")
    comm = []
    for c in gov["commissioners"]:
        name = alias.get(c["person"], c["person"])
        sid, page, q, ev = LIB.check({"source": c["source"], "quote": f"Commissioner {name}"}, f"commissioner {name}")
        comm.append((c["person"], c["title"], LIB.sources[c["source"]].get("date"), sid, page, q, ev))
    return list(latest.values()), info, comm


def enrich_sites(H):
    path = os.path.join(RAW, "assessor", "parcels.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    for h in H.values():
        for i, s in enumerate(h["sites"]):
            c = cache.get(f"{h['id']}#{i}", {})
            if c.get("AIN"):
                s.update(apn=c.get("APN"), ain=c.get("AIN"), year_built=to_int(c.get("YearBuilt1")) or None, effective_year=to_int(c.get("EffectiveYear1")) or None, assessor_units=c.get("Units1"), use_description=c.get("UseDescription"), land_base_year=to_int(c.get("Roll_LandBaseYear")) or None,
                         assessor_url=f"https://portal.assessor.lacounty.gov/parceldetail/{c['AIN']}", match_status=c.get("status"))
                if s.get("lat") is None and c.get("CENTER_LAT"):
                    s.update(lat=c["CENTER_LAT"], lon=c["CENTER_LON"], geocode_source="LA County Assessor")
            elif c:
                s.setdefault("match_status", c.get("status"))
            if c.get("census_lat") and s.get("lat") is None:
                s.update(lat=c["census_lat"], lon=c["census_lon"], geocode_source="US Census Geocoder")
            if c.get("council_district"):
                s["council_district"] = c["council_district"]
        located = next((s for s in h["sites"] if s.get("lat") is not None), None)
        if located:
            h.setdefault("lat", located["lat"])
            if h.get("lat") is None:
                h["lat"] = located["lat"]
            h["lon"] = h.get("lon") or located["lon"]
            h["council_district"] = located.get("council_district")
            h["apn"] = located.get("apn")


def derive(H, E):
    for h in H.values():
        evs = sorted(h["events"], key=lambda e: e["date"])
        years = [s["year_built"] for s in h["sites"] if s.get("year_built")]
        built = next((e for e in evs if e["type"] == "built"), None)
        first = next((e for e in evs if e["type"] == "first_availability"), None)
        pis = next((e for e in evs if e["type"] in ("placed_in_service", "completed")), None)
        if built:
            h["year_built"], h["year_built_basis"] = int(built["date"][:4]), "Board report"
        elif first:
            h["year_built"], h["year_built_basis"] = int(first["date"][:4]), "HUD Date of Full Availability"
        elif pis and "Rehab" not in (h.get("ctcac_construction") or ""):
            h["year_built"], h["year_built_basis"] = int(pis["date"][:4]), "Placed in service / completion"
        elif years:
            h["year_built"], h["year_built_basis"] = min(years), "LA County Assessor"
        acq = next((e for e in evs if e["type"] == "acquired"), None)
        if acq:
            h["acquired_date"], h["acquired_basis"] = acq["date"], "Documented acquisition"
        elif h["relationship"] == "public_housing_direct" and first:
            h["acquired_date"], h["acquired_basis"] = first["date"], "HUD first availability (built by HACLA)"
        owner = next((r for r in h["roles"] if r["role"] == "owner"), None)
        partner = next((r for r in h["roles"] if r["role"] == "owner_partnership"), None)
        chosen = owner or partner
        if chosen:
            h["owner_name"] = E[chosen["entity"]]["name"] if chosen.get("entity") else chosen["entity_name"]
            h["ownership_status"] = "inferred" if chosen.get("evidence") == "inferred" else "documented"
        elif h["relationship"] in ("partnership", "partnership_noncontrolling"):
            h["owner_name"] = "Partnership; HACLA affiliate holds a role"
            h["ownership_status"] = "documented"
        elif h["relationship"].startswith("pipeline") or h["relationship"] == "commercial":
            h["owner_name"] = "HACLA or affiliate" if h["relationship"] == "commercial" else "Not yet owned or not stated"
            h["ownership_status"] = "not_itemized"
        else:
            planned = next((r for r in h["roles"] if r["role"] == "planned_owner"), None)
            h["owner_name"] = f"HACLA instrumentality; transfer to {E[planned['entity']]['name']} planned" if planned else "HACLA instrumentality (not stated)"
            h["ownership_status"] = "not_itemized"
        mgr = h.get("manager_id")
        h["manager"] = h.get("manager_name") or (E[mgr]["name"] if mgr else ("HACLA" if h["relationship"] == "public_housing_direct" else None))


def main():
    H = {}
    merged, facts = load_asset_list(H)
    hud_block = load_public_housing(H, merged, facts)
    reports = load_ctcac(H, facts, hud_block)
    gov = yaml.safe_load(open(os.path.join(CUR, "governance.yaml")))
    facts["entities"] += [{**e, "type": "instrumentality", "parent": "hacla"} for e in gov["new_entities"]]
    E = apply_curated(H, facts)
    if "--export-sites" in sys.argv:
        out = [{"key": f"{h['id']}#{i}", "address": s["address"], "city": s.get("city"), "zip": s.get("zip")} for h in H.values() for i, s in enumerate(h["sites"]) if s.get("address")]
        json.dump(out, open(os.path.join(RAW, "sites_to_enrich.json"), "w"), indent=0)
        print(len(H), "holdings;", len(out), "sites exported")
        return
    enrich_sites(H)

    fin_rows = []
    for f in facts["financing"]:
        sid, page, quote, ev = LIB.check(f, f"financing {f['label']}")
        for hid in f["holdings"]:
            if hid not in H:
                errors.append(f"financing {f['label']}: unknown holding {hid}")
        fin_rows.append(({"label": f["label"], "kind": f["kind"], "provider": f.get("provider"), "amount": f.get("amount"), "phase": None, "stack": None, "date": str(f.get("date")) if f.get("date") else None, "rate": f.get("rate"), "maturity": f.get("maturity"), "status": f.get("status"), "is_portfolio": int(len(f["holdings"]) > 1), "note": f.get("note"), "source": sid, "page": page, "quote": quote, "evidence": ev}, f["holdings"]))
    for f in facts["facilities"]:
        sid, page, quote, ev = LIB.check(f, f"facility {f['label']}")
        fin_rows.append(({"label": f["label"], "kind": f["kind"], "provider": f.get("provider"), "amount": f.get("amount"), "phase": None, "stack": None, "date": str(f.get("date")), "rate": f.get("rate"), "maturity": f.get("maturity"), "status": None, "is_portfolio": 1, "note": f.get("note"), "source": sid, "page": page, "quote": quote, "evidence": ev}, []))
    for h in H.values():
        for f in h["financing"]:
            fin_rows.append(({**{k: None for k in ("provider", "rate", "maturity", "status", "note", "quote")}, "is_portfolio": 0, **f}, [h["id"]]))
    has_stack = {hid for _, hs in fin_rows for hid in hs}
    assign_by_elimination(H, facts)
    derive(H, E)
    seats, info, comm = load_governance(gov, E)
    for h in H.values():
        h["has_capital_stack"] = int(h["id"] in has_stack)
        h["has_ami"] = int(bool(h["ami"]))
    issue_rows = []
    for i in facts["issues"]:
        if i.get("holding") and i["holding"] not in H:
            errors.append(f"issue: unknown holding {i['holding']}")
        issue_rows.append((i.get("holding"), i["field"], i["text"], i.get("source")))
    for h in H.values():
        for s in h["sites"]:
            if s.get("assessor_units") and s.get("units") and abs(s["assessor_units"] - s["units"]) > max(2, 0.25 * s["units"]) and h["relationship"] != "public_housing_direct":
                issue_rows.append((h["id"], "units", f"HACLA lists {s['units']} units at {s['address']}; Assessor shows {s['assessor_units']} on the matched parcel (a building can span parcels).", "assessor"))
            if s.get("match_status") == "matched_other_zip":
                issue_rows.append((h["id"], "zip", f"Assessor parcel for {s['address']} is not in listed ZIP {s.get('zip')}; verify the address.", "assessor"))
    entity_rows = [(e["id"], e["name"], e["type"], e.get("parent"), e.get("governance"), *LIB.check(e, f"entity {e['id']}")) for e in facts["entities"]]
    if errors:
        print(f"{len(errors)} BUILD ERRORS:")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(open(os.path.join(ROOT, "hacla_db", "schema.sql")).read())
    con.execute("PRAGMA foreign_keys=ON")
    for s in LIB.sources.values():
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?)", (s["id"], s["title"], s["kind"], s["url"], s.get("date"), s.get("text")))
    for row in entity_rows:
        con.execute("INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?)", row)
    cols = "id name relationship status total_units low_income_units br0 br1 br2 br3 br4 program_raw population hud_code ctcac_apps in_asset_list address city zip lat lon council_district apn year_built year_built_basis acquired_date acquired_basis owner_name ownership_status manager has_capital_stack has_ami".split()
    for h in H.values():
        vals = [h.get(c) for c in cols]
        vals[cols.index("in_asset_list")] = h.get("in_asset_list", 0)
        con.execute(f"INSERT INTO holdings ({','.join(cols)},source_id,page,quote,evidence) VALUES ({','.join('?' * (len(cols) + 4))})", vals + [h["source"], h.get("page"), h.get("quote"), h.get("evidence") or "structured_data"])
        con.executemany("INSERT INTO holding_programs VALUES (?,?)", [(h["id"], t) for t in sorted(h["programs"])])
        con.execute("INSERT INTO holdings_fts VALUES (?,?,?,?,?,?,?)", (h["id"], h["name"], " ".join(s.get("address") or "" for s in h["sites"]), h.get("city"), h.get("program_raw"), h.get("owner_name"), h.get("manager")))
        for s in h["sites"]:
            con.execute("INSERT INTO sites (holding_id,address,city,zip,units,apn,ain,lat,lon,council_district,year_built,effective_year,assessor_units,use_description,land_base_year,match_status,geocode_source,assessor_url,source_id,page) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (h["id"], s.get("address"), s.get("city"), s.get("zip"), s.get("units"), s.get("apn"), s.get("ain"), s.get("lat"), s.get("lon"), s.get("council_district"), s.get("year_built"), s.get("effective_year"), s.get("assessor_units"), s.get("use_description"), s.get("land_base_year"), s.get("match_status"), s.get("geocode_source"), s.get("assessor_url"), s.get("source"), s.get("page")))
        for r in h["roles"]:
            name = E[r["entity"]]["name"] if r.get("entity") else r["entity_name"]
            ent = r.get("entity") or next((k for k, v in E.items() if norm(v["name"]) == norm(name)), None)
            con.execute("INSERT INTO roles (holding_id,entity_id,entity_name,role,note,source_id,page,quote,evidence) VALUES (?,?,?,?,?,?,?,?,?)", (h["id"], ent, name, r["role"], r.get("note"), r["source"], r.get("page"), r.get("quote"), r.get("evidence") or "structured_data"))
        for a in h["ami"]:
            con.execute("INSERT INTO ami_restrictions (holding_id,ami_pct,units,restriction_type,note,source_id,page,quote,evidence) VALUES (?,?,?,?,?,?,?,?,?)", (h["id"], a["ami_pct"], a["units"], a["type"], a.get("note"), a["source"], a.get("page"), a.get("quote"), a.get("evidence") or "structured_data"))
        for e in h["events"]:
            con.execute("INSERT INTO events (holding_id,event_type,date,note,source_id,page,quote,evidence) VALUES (?,?,?,?,?,?,?,?)", (h["id"], e["type"], e["date"], e.get("note"), e["source"], e.get("page"), e.get("quote"), e.get("evidence") or "structured_data"))
        for n in h["notes"]:
            con.execute("INSERT INTO notes (holding_id,text,source_id,page,quote,evidence) VALUES (?,?,?,?,?,?)", (h["id"], n["text"], n["source"], n.get("page"), n.get("quote"), n["evidence"]))
        if h.get("stats"):
            st = h["stats"]
            con.execute("INSERT INTO resident_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (h["id"], st["occupied"], st["pct_occupied"], st["avg_household_income"], st["pct_under_30_ami"], st["pct_under_50_ami"], st["pct_under_80_ami"], st["avg_tenant_rent"], st["avg_people_per_unit"], st["pct_age_62_plus"], st["pct_disabled"], st["as_of"], "hud_dev"))
    for f, hs in fin_rows:
        cur = con.execute("INSERT INTO financing (label,kind,provider,amount,phase,stack,date,rate,maturity,status,is_portfolio,note,source_id,page,quote,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f["label"], f["kind"], f.get("provider"), f.get("amount"), f.get("phase"), f.get("stack"), f.get("date"), f.get("rate"), f.get("maturity"), f.get("status"), f["is_portfolio"], f.get("note"), f["source"], f.get("page"), f.get("quote"), f.get("evidence") or "structured_data"))
        con.executemany("INSERT INTO financing_holdings VALUES (?,?)", [(cur.lastrowid, hid) for hid in hs])
    con.execute("INSERT INTO meta VALUES ('built_on', ?)", (dt.date.today().isoformat(),))
    con.executemany("INSERT INTO people VALUES (?,?)", list(gov["people"].items()))
    for s in seats:
        con.execute("INSERT INTO board_seats (entity_id,person,seat,is_chair,status,as_of,end_date,note,source_id,page,quote,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (s["entity"], s["person"], s["seat"], s["is_chair"], s["status"], s["as_of"], s["end"], s["note"], s["source"], s["page"], s["quote"], s["evidence"]))
    for ent, d in info.items():
        con.execute("INSERT INTO board_info VALUES (?,?,?,?,?,?,?)", (ent, d["rule"], d["rule_source"], d["rule_page"], d["rule_quote"], d["open_question"], d["no_board_note"]))
    con.executemany("INSERT INTO commissioners VALUES (?,?,?,?,?,?,?)", comm)
    for r in reports:
        con.execute("INSERT INTO ctcac_reports VALUES (?,?,?,?,?,?)", (r["app"], r["holding_id"], r["report_date"], r["permanent_total"], r["permanent_sum"], r["source_id"]))
    con.executemany("INSERT INTO data_issues (holding_id,field,text,source_id) VALUES (?,?,?,?)", issue_rows)
    for t in facts["entity_totals"]:
        p, u = con.execute("SELECT COUNT(DISTINCT h.id), COALESCE(SUM(h.total_units),0) FROM holdings h WHERE h.id IN (SELECT holding_id FROM roles WHERE entity_id=? AND role IN ('owner','managing_general_partner','general_partner','administrative_general_partner','class_a_limited_partner','non_managing_member_of_agp'))", (t["entity"],)).fetchone()
        con.execute("INSERT INTO entity_reconciliation VALUES (?,?,?,?,?)", (t["entity"], t["properties"], t["units"], p, u))
    con.commit()
    print("holdings", len(H), Counter(h["relationship"] for h in H.values()))
    print("units", {k: sum(h.get("total_units") or 0 for h in H.values() if h["relationship"] == k) for k in {h["relationship"] for h in H.values()}})
    print("ownership", Counter(h["ownership_status"] for h in H.values()))
    print("coverage: geo", sum(1 for h in H.values() if h.get("lat") is not None), "year", sum(1 for h in H.values() if h.get("year_built")), "acq", sum(1 for h in H.values() if h.get("acquired_date")), "stack", sum(h["has_capital_stack"] for h in H.values()), "ami", sum(h["has_ami"] for h in H.values()))
    for row in con.execute("SELECT * FROM entity_reconciliation"):
        print(" recon", row)
    bad = con.execute("SELECT app,permanent_total,permanent_sum FROM ctcac_reports WHERE permanent_total<>permanent_sum").fetchall()
    print("stack mismatches", bad)
    print("seats", con.execute("SELECT status,COUNT(*) FROM board_seats GROUP BY 1").fetchall(), "entities with seats", con.execute("SELECT COUNT(DISTINCT entity_id) FROM board_seats").fetchone()[0])
    print("issues", con.execute("SELECT COUNT(*) FROM data_issues").fetchone()[0], "sources", len(LIB.sources))


main()
