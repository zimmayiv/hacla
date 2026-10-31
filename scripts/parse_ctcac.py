"""Extract financing tables and project facts from CTCAC staff report text, plus the CTCAC project spreadsheet."""
import glob, json, os, re, sys
import openpyxl
import pdfplumber

txt_dir, pdf_dir, xlsx, url_file, out = sys.argv[1:6]
urls = dict(l.split() for l in open(url_file) if l.strip())
MONEY = re.compile(r"\$\s?[\d,]+")
TAIL = re.compile(r"^(TOTAL|\*Less|Determination of Credit|Standard Underwriting)", re.I)


def financing(pdf_path):
    """Rows of the 'Construction Financing / Permanent Financing' table, with the PDF page.

    Uses layout-preserving text so a row filled in only one column lands in the right phase.
    """
    rows, active, split = [], False, 0
    with pdfplumber.open(pdf_path) as pdf:
        numbered = [(i + 1, l) for i, p in enumerate(pdf.pages) for l in (p.extract_text(layout=True) or "").splitlines()]
    for page, line in numbered:
        if re.search(r"Construction Financing\s+Permanent Financing", line):
            active = "both"
            split = line.index("Permanent Financing") - 2
            continue
        if re.match(r"\s*Permanent Financing\s*$", line):
            active = "perm"
            continue
        if not active:
            continue
        if TAIL.match(line.strip()):
            if line.strip().upper().startswith("TOTAL"):
                total = MONEY.search(line)
                rows.append({"phase": "permanent", "source": "TOTAL", "amount": int(re.sub(r"\D", "", total.group())) if total else None, "page": page})
            break
        if line.strip().lower().startswith("source amount"):
            continue
        amounts = list(MONEY.finditer(line))
        if not amounts:
            continue
        if active == "both" and len(amounts) == 2:
            a, b = amounts
            rows.append({"phase": "construction", "source": line[: a.start()].strip(), "amount": int(re.sub(r"\D", "", a.group())), "page": page})
            rows.append({"phase": "permanent", "source": line[a.end(): b.start()].strip(), "amount": int(re.sub(r"\D", "", b.group())), "page": page})
        else:
            a = amounts[0]
            phase = "permanent" if active == "perm" or a.start() >= split else "construction"
            rows.append({"phase": phase, "source": line[: a.start()].strip(), "amount": int(re.sub(r"\D", "", a.group())), "page": page})
    return rows


def block(text, label, stop):
    """A labelled value that may continue on following lines, up to the next known label."""
    m = re.search(rf"^{label}:?\s*(.*?)(?=^{stop})", text, re.M | re.S | re.I)
    return re.sub(r"\s*\n\s*", "; ", m.group(1).strip()) if m else None


def field(text, label):
    m = re.search(rf"^{label}:?\s*(.+)$", text, re.M | re.I)
    return m.group(1).strip() if m else None


reports = {}
for path in sorted(glob.glob(os.path.join(txt_dir, "CA-*.txt"))):
    app = os.path.basename(path)[:-4]
    text = open(path).read()
    head = text[:900]
    date = re.search(r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})", head)
    ami = [{"ami_pct": int(a), "units": int(u)} for a, u in re.findall(r"^(?:At or Below )?(\d{2})% AMI:\s+(\d+)\s+\d+%", text, re.M)]
    reports[app] = {
        "url": urls.get(app[3:]),
        "report_date": date.group(1) if date else None,
        "summary": re.sub(r"\s+", " ", (re.search(r"Project Staff Report.*?\n(?:[A-Z][a-z]+ \d+, \d{4}\n)?(.*?)Project Number", text, re.S) or [None, ""])[1]).strip()[:700],
        "applicant": field(text, "Applicant"),
        "owner": field(text, "Owner"),
        "general_partners": block(text, r"General Partner\(s\) or Principal Owner\(s\)", "General Partner Type"),
        "gp_type": field(text, "General Partner Type"),
        "parents": block(text, r"Parent Company\(ies\)", "Developer"),
        "bond_issuer": field(text, "Bond Issuer"),
        "developer": field(text, "Developer"),
        "investor": field(text, "Investor/Consultant"),
        "manager": field(text, r"Management Agent(?:\(s\))?"),
        "housing_type": field(text, "Housing Type"),
        "total_units": field(text, r"Total # of Units"),
        "low_income_units": field(text, r"No\. / % of Low Income Units"),
        "federal_subsidy": block(text, "Federal Subsidy", r"(Information|\*|Housing Type|55-Year)"),
        "ami": ami,
        "financing": financing(os.path.join(pdf_dir, app + ".pdf")),
    }

wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
ws = wb["California Mapped Projects"]
it = ws.iter_rows(values_only=True)
header = [str(c).strip() for c in next(it)]
projects = {}
for row in it:
    d = dict(zip(header, row))
    app = str(d.get("Application Number") or "")
    # Keep the application-level row; site rows of scattered projects carry no unit counts.
    if app and (app not in projects or d.get("Total Units")):
        projects[app] = {k: (str(v) if v is not None else None) for k, v in d.items()}
json.dump({"reports": reports, "projects": projects}, open(out, "w"), indent=1)
print(len(reports), "staff reports", len(projects), "ctcac projects")
for app, r in reports.items():
    perm = [f for f in r["financing"] if f["phase"] == "permanent"]
    print(app, r["report_date"], "|", r["manager"], "|", r["investor"], "| AMI", [(a["ami_pct"], a["units"]) for a in r["ami"]], "| perm rows", len(perm), "total", next((f["amount"] for f in perm if f["source"] == "TOTAL"), None), "sum", sum(f["amount"] for f in perm if f["source"] != "TOTAL"))
