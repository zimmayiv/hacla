"""Render the Flask app to static files for GitHub Pages: python scripts/freeze.py [output_dir]"""
import os
import sys

from flask_frozen import Freezer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hacla_db import HoldingsDB, person_slug  # noqa: E402
from webapp.app import create_app  # noqa: E402


def freeze(dest):
    app = create_app()
    db = HoldingsDB()
    # Relative links let the site work under any repository name, or at a custom domain.
    app.config.update(FREEZER_DESTINATION=os.path.abspath(dest), FREEZER_RELATIVE_URLS=True, FREEZER_IGNORE_MIMETYPE_WARNINGS=True, FREEZER_REDIRECT_POLICY="error")
    freezer = Freezer(app, with_no_argument_rules=False, log_url_for=False)

    @freezer.register_generator
    def pages():
        for endpoint in ("index", "entities", "people", "sources", "issues", "not_found_page", "data_holdings", "data_csv", "data_entities", "data_people", "data_stats"):
            yield endpoint, {}
        for r in db.holdings_export():
            yield "holding", {"holding_id": r["id"]}
            yield "data_holding", {"holding_id": r["id"]}
        for e in db.entities():
            yield "entity", {"entity_id": e["id"]}
        for p in db.people():
            yield "person", {"slug": person_slug(p["name"])}

    urls = freezer.freeze()
    # GitHub serves 404.html at whatever path was missed, so its relative links need the site root.
    page = os.path.join(dest, "404.html")
    html = open(page).read().replace("<head>", """<head><script>if (location.hostname.endsWith("github.io")) document.write('<base href="/' + location.pathname.split("/")[1] + '/">');</script>""", 1)
    open(page, "w").write(html)
    # Stops GitHub Pages from running the output through Jekyll.
    open(os.path.join(dest, ".nojekyll"), "w").close()
    return urls


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_site")
    print(len(freeze(out)), "files written to", out)
