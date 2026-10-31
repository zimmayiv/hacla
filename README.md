# HACLA Holdings

A sourced database of the Housing Authority of the City of Los Angeles's housing holdings, a query package, and a small Flask app. Every fact links to the official document behind it, and PDF links open at the cited page.

## Run it

```sh
cd hacla
uv venv && uv pip install -e ".[static,test]"
.venv/bin/flask --app webapp.app run      # local site, http://127.0.0.1:5000
.venv/bin/pytest                          # checks the data, the site and the JavaScript filters
```

## Publish on GitHub Pages

The public site is a static export of the Flask app, written by `python scripts/freeze.py` to `_site/`. To preview it, run `python -m http.server -d _site`.

After pushing to GitHub, go to **Settings > Pages** and set **Source** to **GitHub Actions**. `.github/workflows/pages.yml` then runs the tests, freezes the site and publishes it on every push to `main`. Links are relative, so any repository name works.

The holdings table filters in the browser (`webapp/static/holdings.js`), and filtered views can be shared by URL. It mirrors `HoldingsDB.search`, and `tests/test_static.py` runs both and compares results. Static data files are at `data/holdings.json`, `data/holdings.csv`, `data/holding/<id>.json`, `data/entities.json`, `data/people.json` and `data/stats.json`.

## Query it

```python
from hacla_db import HoldingsDB
db = HoldingsDB()

page = db.search(program="PSH", min_units=40, ami_at_most=30, sort="units", desc=True)
page.total, page.total_units, page.rows

h = db.holding("clarendon-apartments")
h["financing"], h["ami"], h["roles"], h["events"], h["sources"]
h["financing"][0]["source_url"]    # opens the PDF at the cited page
h["financing"][0]["quote"]         # the sentence supporting it

db.search(entity="la-cienega")     # includes subsidiaries such as LOMOD PDS LLC
db.capital_stack("jd-nueva-vida")  # permanent sources
db.board("housing-pathways")       # directors, officers, appointment rule, open questions
db.person("Cielo Castro")          # every recorded seat for one person
```

Or from the shell: `python -m hacla_db.cli search --q jordan --csv`. Local Flask also serves filtered queries at `/api/holdings` and `/api/entities/<id>/board`; these need a server, so they aren't on the public site.

Filters are validated. A misspelled program or status raises `QueryError` listing the valid values, so a typo can't pass for an empty result. The connection is read-only and all values are bound parameters.

## What's a holding

`relationship` says what HACLA's tie to the property is. Totals mean little without it, so filter on it.

| relationship | meaning |
|---|---|
| `public_housing_direct` | HACLA-owned, under an Annual Contributions Contract |
| `owned_affiliate` | Owned by HACLA or an instrumentality |
| `partnership` | Tax-credit partnership in which a HACLA affiliate is a GP, AGP or Class A LP |
| `partnership_noncontrolling` | HACLA affiliate is a non-managing member of the AGP |
| `pipeline_acquisition`, `pipeline_development` | Not yet owned, or in predevelopment |
| `commercial` | Offices; excluded from searches by default |

Left out on purpose: properties HACLA only finances as conduit issuer (such as Valley Pride Village), private buildings that only receive its project-based vouchers, and Homekey properties it acquired on the City's behalf.

## How facts are verified

`evidence` on each fact records how far to trust it. The app flags the weaker levels in amber.

| evidence | meaning |
|---|---|
| `document_text` | The quote was found in the source's text at build time, and the page number derived from it. The build fails otherwise. |
| `document_image` | Read by eye from a table image in the PDF, such as Clarendon's covenant schedule |
| `structured_data` | Parsed from a dataset or table (HUD, CTCAC, the Assessor, HACLA's property list) |
| `page_summary`, `search_excerpt` | Seen only through a summary or search snippet, not the document |
| `inferred` | A judgment, explained in the fact's note |

The tests check that LIHTC permanent sources add up to each CTCAC report's total, that entity counts match HACLA's published ones where the data is complete, that all references resolve, and that the verifier rejects a fabricated quote.

## Boards

`board_seats` records each director and officer by entity, with the document behind it and an `as_of` date. `board_info` holds the appointment rule and anything unresolved. Rosters come from documents of different dates, so `status: current` means the latest document found shows the seat held, not that it was confirmed today. FY2024 annual-report rosters are dated `2024-12`. LLCs and partnerships have no board of their own, so `db.board()` follows them to their parent.

Seats aren't inferred. The December 2025 appointee to Alexandra Suh's seats isn't named in any document found, so that seat stays open rather than going to a likely commissioner.

## Sources

- **HACLA:** Asset Management property list (updated 3/23/2026), FY2024 and FY2019 audits, 2027 Agency Plan, 2026 Budget, June 2026 Mid-Year Report, board reports, RAD pages, entity annual reports
- **HUD:** Public Housing Developments and Buildings layers (units, resident income, Date of Full Availability)
- **CTCAC:** project list and staff reports (AMI targeting, partners, sources of funds)
- **LA County Assessor:** parcel layer (APN, year built, coordinates); **LA City GeoHub:** council districts; **US Census:** fallback geocoding

## Rebuild the database

Raw downloads aren't in the repository (see `data/raw/README.md`), so this needs local copies. Publishing doesn't: the workflow uses the committed `data/hacla.sqlite`.

`hacla.org` blocked scripted downloads during this work, so its PDFs were saved by hand or through a fetch tool into `data/raw/hacla_inbox/`, `data/raw/hacla_fetched/` and `data/raw/pdf/`. Text extracted from them lives in `data/raw/txt*/`.

```sh
python scripts/parse_all_properties.py data/raw/pdf/all_properties_20220720.pdf data/raw/all_properties.json
python scripts/parse_ctcac.py data/raw/ctcac/staff_txt data/raw/ctcac/staff data/raw/ctcac/list_of_projects.xlsx data/raw/ctcac/staff/urls.txt data/raw/ctcac.json
python scripts/build_db.py --export-sites
python scripts/enrich_sites.py data/raw/sites_to_enrich.json data/raw/assessor/parcels.json    # cached, resumable
python scripts/build_db.py
```

To add a fact, edit `data/curated/facts.yaml` and register any new document in `data/curated/sources.yaml`. Give the exact quote and the build finds its page.

## Field caveats

- **`year_built`** is the first of: a board report, HUD's Date of Full Availability, the tax-credit placed-in-service date (new construction only), or the Assessor's year. `year_built_basis` says which.
- **`acquired_date`** is only set when documented, or for public housing, where HUD's first-availability date stands in. Many older properties have none. `land_base_year` on a site hints at when a parcel last changed hands, but it isn't an acquisition date.
- **Unit totals** mix operating and pipeline properties unless you filter by status.
- **Authorized amounts** (`status: authorized`) are board ceilings, not closing figures.
- **`/issues`** lists every conflict between sources and every correction applied.
