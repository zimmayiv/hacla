"""Parse HACLA's Asset Management 'All Properties' PDF into JSON rows, using table geometry."""
import json, re, sys
import pdfplumber

src, out = sys.argv[1], sys.argv[2]
COLS = ["name", "program", "address", "city", "zip", "total", "br0", "br1", "br2", "br3", "br4", "site_mgr", "phone", "email"]
clean = lambda c: re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip()
rows = []
with pdfplumber.open(src) as pdf:
    for pno, page in enumerate(pdf.pages, 1):
        for table in page.extract_tables():
            for raw in table:
                cells = [clean(c) for c in raw]
                if len(cells) != len(COLS) or not cells[0] or cells[0].startswith(("Property", "Name")):
                    continue
                rows.append({**dict(zip(COLS, cells)), "page": pno})
json.dump(rows, open(out, "w"), indent=1)
print(len(rows), "rows;", sum(int(r["total"] or 0) for r in rows if r["total"].isdigit()), "units")
