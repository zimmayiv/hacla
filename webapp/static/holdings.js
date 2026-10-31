/* Holdings table for the static site. filterRows mirrors HoldingsDB.search in Python; tests/test_static.py compares them. */
(function (root) {
  "use strict";

  function number(value) {
    if (value === undefined || value === null || value === "") return null;
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function terms(text) {
    return String(text || "").toLowerCase().match(/[\p{L}\p{N}_]+/gu) || [];
  }

  function filterRows(rows, f) {
    var want = terms(f.q);
    var min = number(f.min_units), from = number(f.built_after), to = number(f.built_before), ami = number(f.ami_at_most), cd = number(f.council_district);
    return rows.filter(function (r) {
      if (f.relationship) { if (r.relationship !== f.relationship) return false; }
      else if (r.relationship === "commercial") return false;
      if (f.status && r.status !== f.status) return false;
      if (f.manager && r.manager !== f.manager) return false;
      if (f.ownership_status && r.ownership_status !== f.ownership_status) return false;
      if (f.program && r.program_tags.indexOf(f.program) < 0) return false;
      if (cd !== null && r.council_district !== cd) return false;
      if (min !== null && !(r.total_units !== null && r.total_units >= min)) return false;
      if (from !== null && !(r.year_built !== null && r.year_built >= from)) return false;
      if (to !== null && !(r.year_built !== null && r.year_built <= to)) return false;
      if (ami !== null && !(r.deepest_ami !== null && r.deepest_ami <= ami)) return false;
      if (f.stack === "yes" && !r.has_capital_stack) return false;
      if (f.stack === "no" && r.has_capital_stack) return false;
      if (want.length) {
        var words = terms(r.search_text);
        var all = want.every(function (t) { return words.some(function (w) { return w.indexOf(t) === 0; }); });
        if (!all) return false;
      }
      return true;
    });
  }

  function sortRows(rows, key, desc) {
    return rows.slice().sort(function (a, b) {
      var x = a[key], y = b[key];
      var xNull = x === null || x === undefined || x === "", yNull = y === null || y === undefined || y === "";
      if (xNull !== yNull) return xNull ? 1 : -1; // Rows with no value sort last in either direction.
      var c = 0;
      if (!xNull) {
        c = typeof x === "number" ? x - y : String(x).toLowerCase() < String(y).toLowerCase() ? -1 : String(x).toLowerCase() > String(y).toLowerCase() ? 1 : 0;
        if (desc) c = -c;
      }
      return c || (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : a.name.toLowerCase() > b.name.toLowerCase() ? 1 : 0);
    });
  }

  var CSV_FIELDS = ["id", "name", "relationship", "status", "total_units", "low_income_units", "address", "city", "zip", "lat", "lon", "council_district", "year_built", "acquired_date", "owner_name", "ownership_status", "manager", "programs", "deepest_ami"];

  function toCsv(rows) {
    var cell = function (v) {
      var s = v === null || v === undefined ? "" : String(v);
      if (/^[=+\-@]/.test(s)) s = "'" + s; // Keep spreadsheets from treating text as a formula.
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [CSV_FIELDS.join(",")].concat(rows.map(function (r) { return CSV_FIELDS.map(function (k) { return cell(r[k]); }).join(","); })).join("\n") + "\n";
  }

  root.HoldingsTable = { filterRows: filterRows, sortRows: sortRows, toCsv: toCsv };
  if (typeof document === "undefined") return;

  var script = document.currentScript;
  var base = script.getAttribute("data-root").replace(/index\.html$/, "");
  var data = JSON.parse(document.getElementById("holdings-data").textContent);
  var labels = JSON.parse(document.getElementById("labels").textContent);
  var form = document.getElementById("filters");
  var body = document.getElementById("rows");
  var count = document.getElementById("count");
  var csv = document.getElementById("csv");
  var sortKey = "name", desc = false, csvUrl = null;
  var SORTS = ["name", "city", "total_units", "year_built", "acquired_date"];

  function label(v) { return labels[v] || String(v || "").replace(/_/g, " "); }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function row(r) {
    var tr = el("tr");
    var name = el("td");
    var link = el("a", null, r.name);
    link.href = base + "holding/" + encodeURIComponent(r.id) + "/";
    name.appendChild(link);
    if (r.status !== "operating") { name.appendChild(document.createTextNode(" ")); name.appendChild(el("span", "tag", label(r.status))); }
    name.appendChild(el("div", "sub", r.address || ""));
    tr.appendChild(name);
    tr.appendChild(el("td", null, r.city || ""));
    tr.appendChild(el("td", "num", r.total_units === null ? "" : r.total_units));
    tr.appendChild(el("td", "sub", r.programs || ""));
    tr.appendChild(el("td", "num", r.deepest_ami ? r.deepest_ami + "%" : ""));
    tr.appendChild(el("td", "num", r.year_built || ""));
    tr.appendChild(el("td", null, r.acquired_date || ""));
    var owner = el("td", "sub");
    if (r.ownership_status === "not_itemized") owner.appendChild(el("em", null, "Not stated"));
    else owner.textContent = r.owner_name || "";
    if (r.ownership_status === "inferred") { owner.appendChild(document.createTextNode(" ")); owner.appendChild(el("span", "tag", "inferred")); }
    tr.appendChild(owner);
    tr.appendChild(el("td", null, r.has_capital_stack ? "Yes" : ""));
    return tr;
  }

  function current() {
    var f = {};
    new FormData(form).forEach(function (v, k) { if (v !== "") f[k] = v; });
    return f;
  }

  function render() {
    var f = current();
    var rows = sortRows(filterRows(data, f), sortKey, desc);
    var frag = document.createDocumentFragment();
    rows.forEach(function (r) { frag.appendChild(row(r)); });
    body.replaceChildren(frag);
    if (rows.length === 0) { var tr = el("tr"), td = el("td", null, "No holdings match these filters."); td.colSpan = 9; tr.appendChild(td); body.appendChild(tr); }
    var units = rows.reduce(function (s, r) { return s + (r.total_units || 0); }, 0);
    count.replaceChildren(el("strong", null, rows.length), " holdings, ", el("strong", null, units.toLocaleString("en-US")), " units listed. Totals mix operating and pipeline unless you filter by status.");
    document.querySelectorAll("th button").forEach(function (b) {
      var on = b.getAttribute("data-sort") === sortKey;
      b.textContent = b.textContent.replace(/ [▲▼]$/, "") + (on ? (desc ? " ▼" : " ▲") : "");
      b.closest("th").setAttribute("aria-sort", on ? (desc ? "descending" : "ascending") : "none");
    });
    if (csvUrl) URL.revokeObjectURL(csvUrl);
    csvUrl = URL.createObjectURL(new Blob([toCsv(rows)], { type: "text/csv" }));
    csv.href = csvUrl;
    var params = new URLSearchParams(f);
    if (sortKey !== "name") params.set("sort", sortKey);
    if (desc) params.set("dir", "desc");
    var qs = params.toString();
    history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  }

  // Restore filters from the URL, so a filtered view can be shared as a link.
  var initial = new URLSearchParams(location.search);
  initial.forEach(function (v, k) { if (form.elements[k]) form.elements[k].value = v; });
  if (SORTS.indexOf(initial.get("sort")) >= 0) sortKey = initial.get("sort");
  desc = initial.get("dir") === "desc";

  form.hidden = false;
  form.addEventListener("input", render);
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  document.getElementById("clear").addEventListener("click", function () { form.reset(); render(); });
  document.querySelectorAll("th button").forEach(function (b) {
    b.addEventListener("click", function () {
      var key = b.getAttribute("data-sort");
      if (key === sortKey) desc = !desc; else { sortKey = key; desc = false; }
      render();
    });
  });
  render();
})(typeof globalThis !== "undefined" ? globalThis : this);
