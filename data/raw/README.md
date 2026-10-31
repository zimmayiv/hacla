# Raw sources (not in this repository)

This folder holds local copies of the downloaded documents and datasets used to build `data/hacla.sqlite`. They are left out of git because they are large and because some contain site managers' contact details and resident stories, which the database deliberately omits.

Every document's official URL is listed in `data/curated/sources.yaml`. The committed database is enough to run the tests and publish the site; only rebuilding it with `scripts/build_db.py` needs these files.
