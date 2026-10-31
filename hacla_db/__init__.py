"""Query HACLA's housing holdings, with sources for every fact.

    from hacla_db import HoldingsDB
    db = HoldingsDB()
    db.search(q="jordan", min_units=50, ami_at_most=30)
    db.holding("clarendon-apartments")
"""
from .query import HoldingsDB, Page, QueryError, person_slug, source_link

__all__ = ["HoldingsDB", "Page", "QueryError", "person_slug", "source_link"]
