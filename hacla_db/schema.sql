-- Every fact table carries provenance: source_id, page, quote, evidence.
-- evidence: document_text (quote verified against the source at build) | document_image | structured_data | page_summary | search_excerpt | inferred
CREATE TABLE sources (id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL, doc_date TEXT, local_text TEXT);
CREATE TABLE holdings (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  relationship TEXT NOT NULL CHECK (relationship IN ('public_housing_direct','owned_affiliate','partnership','partnership_noncontrolling','pipeline_acquisition','pipeline_development','commercial')),
  status TEXT NOT NULL, total_units INTEGER, low_income_units INTEGER,
  br0 INTEGER, br1 INTEGER, br2 INTEGER, br3 INTEGER, br4 INTEGER,
  program_raw TEXT, population TEXT, hud_code TEXT, ctcac_apps TEXT, in_asset_list INTEGER NOT NULL DEFAULT 0,
  address TEXT, city TEXT, zip TEXT, lat REAL, lon REAL, council_district INTEGER, apn TEXT,
  year_built INTEGER, year_built_basis TEXT, acquired_date TEXT, acquired_basis TEXT,
  owner_name TEXT, ownership_status TEXT NOT NULL CHECK (ownership_status IN ('documented','inferred','not_itemized')),
  manager TEXT, has_capital_stack INTEGER NOT NULL DEFAULT 0, has_ami INTEGER NOT NULL DEFAULT 0,
  source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL
);
CREATE TABLE holding_programs (holding_id TEXT NOT NULL REFERENCES holdings(id), tag TEXT NOT NULL, PRIMARY KEY (holding_id, tag));
CREATE TABLE sites (
  id INTEGER PRIMARY KEY, holding_id TEXT NOT NULL REFERENCES holdings(id), address TEXT, city TEXT, zip TEXT, units INTEGER,
  apn TEXT, ain TEXT, lat REAL, lon REAL, council_district INTEGER, year_built INTEGER, effective_year INTEGER, assessor_units INTEGER,
  use_description TEXT, land_base_year INTEGER, match_status TEXT, geocode_source TEXT, assessor_url TEXT, source_id TEXT REFERENCES sources(id), page INTEGER
);
CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, parent_id TEXT REFERENCES entities(id), governance TEXT, source_id TEXT REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT);
CREATE TABLE entity_reconciliation (entity_id TEXT PRIMARY KEY REFERENCES entities(id), published_properties INTEGER, published_units INTEGER, db_properties INTEGER, db_units INTEGER);
CREATE TABLE roles (id INTEGER PRIMARY KEY, holding_id TEXT NOT NULL REFERENCES holdings(id), entity_id TEXT REFERENCES entities(id), entity_name TEXT NOT NULL, role TEXT NOT NULL, note TEXT, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL);
CREATE TABLE financing (id INTEGER PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL, provider TEXT, amount REAL, phase TEXT, stack TEXT, date TEXT, rate TEXT, maturity TEXT, status TEXT, is_portfolio INTEGER NOT NULL DEFAULT 0, note TEXT, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL);
CREATE TABLE financing_holdings (financing_id INTEGER NOT NULL REFERENCES financing(id), holding_id TEXT NOT NULL REFERENCES holdings(id), PRIMARY KEY (financing_id, holding_id));
CREATE TABLE ctcac_reports (app TEXT PRIMARY KEY, holding_id TEXT NOT NULL REFERENCES holdings(id), report_date TEXT, permanent_total REAL, permanent_sum REAL, source_id TEXT REFERENCES sources(id));
CREATE TABLE ami_restrictions (id INTEGER PRIMARY KEY, holding_id TEXT NOT NULL REFERENCES holdings(id), ami_pct INTEGER NOT NULL, units INTEGER, restriction_type TEXT NOT NULL, note TEXT, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL);
CREATE TABLE events (id INTEGER PRIMARY KEY, holding_id TEXT NOT NULL REFERENCES holdings(id), event_type TEXT NOT NULL, date TEXT NOT NULL, note TEXT, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL);
CREATE TABLE notes (id INTEGER PRIMARY KEY, holding_id TEXT NOT NULL REFERENCES holdings(id), text TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL);
CREATE TABLE resident_stats (holding_id TEXT PRIMARY KEY REFERENCES holdings(id), occupied INTEGER, pct_occupied REAL, avg_household_income REAL, pct_under_30_ami REAL, pct_under_50_ami REAL, pct_under_80_ami REAL, avg_tenant_rent REAL, avg_people_per_unit REAL, pct_age_62_plus REAL, pct_disabled REAL, as_of TEXT, source_id TEXT NOT NULL REFERENCES sources(id));
CREATE TABLE data_issues (id INTEGER PRIMARY KEY, holding_id TEXT REFERENCES holdings(id), field TEXT NOT NULL, text TEXT NOT NULL, source_id TEXT REFERENCES sources(id));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE people (name TEXT PRIMARY KEY, hacla_role TEXT);
-- status: current (latest evidence shows the seat held) | former. as_of is the date of the document behind the row.
CREATE TABLE board_seats (id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id), person TEXT NOT NULL REFERENCES people(name), seat TEXT NOT NULL, is_chair INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL CHECK (status IN ('current','former')), as_of TEXT, end_date TEXT, note TEXT, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL);
CREATE TABLE board_info (entity_id TEXT PRIMARY KEY REFERENCES entities(id), rule TEXT, rule_source_id TEXT REFERENCES sources(id), rule_page INTEGER, rule_quote TEXT, open_question TEXT, no_board_note TEXT);
CREATE TABLE commissioners (person TEXT PRIMARY KEY REFERENCES people(name), title TEXT NOT NULL, as_of TEXT, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER, quote TEXT, evidence TEXT NOT NULL);
CREATE INDEX idx_seats_entity ON board_seats(entity_id);
CREATE INDEX idx_seats_person ON board_seats(person);
CREATE VIRTUAL TABLE holdings_fts USING fts5(id UNINDEXED, name, address, city, program_raw, owner_name, manager, tokenize='porter unicode61');
CREATE INDEX idx_sites_holding ON sites(holding_id);
CREATE INDEX idx_roles_holding ON roles(holding_id);
CREATE INDEX idx_roles_entity ON roles(entity_id);
CREATE INDEX idx_ami_holding ON ami_restrictions(holding_id);
CREATE INDEX idx_events_holding ON events(holding_id);
