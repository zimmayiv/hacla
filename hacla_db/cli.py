"""Command line access: python -m hacla_db.cli search --q jordan --ami-at-most 30"""
import argparse
import csv
import json
import sys

from .query import HoldingsDB, QueryError


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hacla-db", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="Filter holdings")
    s.add_argument("--q")
    for name in ("relationship", "status", "program", "entity", "city", "zip", "manager", "ownership-status"):
        s.add_argument(f"--{name}")
    for name in ("council-district", "min-units", "max-units", "built-after", "built-before", "ami-at-most"):
        s.add_argument(f"--{name}", type=int)
    s.add_argument("--sort", default="name")
    s.add_argument("--desc", action="store_true")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--csv", action="store_true", help="CSV instead of JSON")
    show = sub.add_parser("show", help="One holding with all sources")
    show.add_argument("id")
    sub.add_parser("stats", help="Coverage counts")
    board = sub.add_parser("board", help="Directors and officers of an entity")
    board.add_argument("entity")
    args = parser.parse_args(argv)
    db = HoldingsDB()
    try:
        if args.command == "show":
            h = db.holding(args.id)
            if not h:
                sys.exit(f"No holding {args.id!r}")
            print(json.dumps(h, indent=1))
        elif args.command == "board":
            b = db.board(args.entity)
            if b is None:
                sys.exit(f"No entity {args.entity!r}")
            print(json.dumps(b, indent=1))
        elif args.command == "stats":
            print(json.dumps(db.stats(), indent=1))
        else:
            filters = {k: v for k, v in vars(args).items() if k not in ("command", "csv") and v is not None}
            page = db.search(**filters)
            if args.csv:
                w = csv.DictWriter(sys.stdout, fieldnames=list(page.rows[0]) if page.rows else ["id"], extrasaction="ignore")
                w.writeheader()
                w.writerows(page.rows)
            else:
                print(json.dumps({"total": page.total, "total_units": page.total_units, "rows": page.rows}, indent=1))
    except QueryError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
