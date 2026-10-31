"""Minimal Flask front-end for the HACLA holdings database. Run: flask --app webapp.app run"""
import csv
import io

from flask import Flask, Response, abort, jsonify, render_template, request

from hacla_db import HoldingsDB, QueryError, person_slug
from hacla_db.query import EVIDENCE_LABELS, RELATIONSHIP_LABELS

API_FILTERS = ("q", "relationship", "status", "program", "entity", "city", "zip", "manager", "ownership_status", "council_district", "min_units", "max_units", "built_after", "built_before", "acquired_after", "acquired_before", "ami_at_most")
CSV_FIELDS = ["id", "name", "relationship", "status", "total_units", "low_income_units", "address", "city", "zip", "lat", "lon", "council_district", "year_built", "acquired_date", "owner_name", "ownership_status", "manager", "programs", "deepest_ami"]


def create_app(db_path=None):
    app = Flask(__name__)
    db = HoldingsDB(db_path)

    @app.template_filter("money")
    def money(v):
        return "" if v is None else f"${v:,.0f}"

    @app.template_filter("label")
    def label(v):
        return RELATIONSHIP_LABELS.get(v) or str(v or "").replace("_", " ")

    app.template_filter("slug")(person_slug)
    built_on = db.built_on()

    @app.context_processor
    def globals_():
        return {"evidence_labels": EVIDENCE_LABELS, "built_on": built_on}

    @app.errorhandler(QueryError)
    def bad_query(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith(("/api/", "/data/")):
            return jsonify(error="not found"), 404
        return render_template("404.html"), 404

    @app.route("/")
    def index():
        return render_template("index.html", rows=db.holdings_export(), facets=db.facets(), stats=db.stats(), labels=RELATIONSHIP_LABELS)

    @app.route("/holding/<holding_id>/")
    def holding(holding_id):
        h = db.holding(holding_id)
        if not h:
            abort(404)
        return render_template("holding.html", h=h)

    @app.route("/entities/")
    def entities():
        return render_template("entities.html", entities=db.entities(), facilities=db.facilities())

    @app.route("/entity/<entity_id>/")
    def entity(entity_id):
        e = db.entity(entity_id)
        if not e:
            abort(404)
        return render_template("entity.html", e=e)

    @app.route("/people/")
    def people():
        return render_template("people.html", people=db.people(), commissioners=db.commissioners())

    @app.route("/person/<slug>/")
    def person(slug):
        p = db.person_by_slug(slug)
        if not p:
            abort(404)
        return render_template("person.html", p=p)

    @app.route("/sources/")
    def sources():
        return render_template("sources.html", sources=db.sources())

    @app.route("/issues/")
    def issues():
        return render_template("issues.html", issues=db.issues())

    @app.route("/404.html")
    def not_found_page():
        return render_template("404.html")

    # Static data files, also published on GitHub Pages.
    @app.route("/data/holdings.json")
    def data_holdings():
        return jsonify(db.holdings_export())

    @app.route("/data/holdings.csv")
    def data_csv():
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(db.holdings_export())
        return Response(buf.getvalue(), mimetype="text/csv")

    @app.route("/data/holding/<holding_id>.json")
    def data_holding(holding_id):
        h = db.holding(holding_id)
        if not h:
            abort(404)
        return jsonify(h)

    @app.route("/data/entities.json")
    def data_entities():
        return jsonify(db.entities())

    @app.route("/data/people.json")
    def data_people():
        return jsonify(db.people())

    @app.route("/data/stats.json")
    def data_stats():
        return jsonify(db.stats())

    # Filtered queries need a server, so these exist only when running Flask locally.
    @app.route("/api/holdings")
    def api_holdings():
        filters = {k: request.args[k] for k in API_FILTERS if request.args.get(k)}
        result = db.search(**filters, sort=request.args.get("sort", "name"), desc=request.args.get("dir") == "desc", limit=request.args.get("limit", 50, type=int), offset=request.args.get("offset", 0, type=int))
        return jsonify(total=result.total, total_units=result.total_units, rows=result.rows)

    @app.route("/api/entities/<entity_id>/board")
    def api_board(entity_id):
        b = db.board(entity_id)
        if b is None:
            abort(404)
        return jsonify(b)

    return app


app = create_app()
