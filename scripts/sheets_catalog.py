#!/usr/bin/env python3
"""Build Chartmaker's offline Sheets library catalog. Runs on a box with workbook metadata, not on Render.

Render has no Google credentials. The Sheets tab reads static/sheets/catalog.json only.

Usage:
  python3 scripts/sheets_catalog.py bot1.json bot2.json bot3.json
  python3 scripts/sheets_catalog.py --repo /workspace/chartmaker dump.json [...]

Each JSON file is one spreadsheets.get response (the Google Sheets `get_spreadsheet` tool returns this shape):
  spreadsheetId, properties.title, sheets[].properties.{sheetId,title,index,hidden}, sheets[].charts[].spec.title

The three workbooks, and the names Charts of the day already uses:
  Bot experiments      1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY
  bot experiments 2    1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ
  My Bot's Sheets      1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU

Refresh: save those three responses, re-run this script, commit static/sheets/catalog.json.
All three known workbooks must be present unless --allow-partial is set.
PNGs are not copied. The app shows a chart image only when that tab is also in today's Charts of the day.
"""
import argparse, json, os, sys, datetime

BOOKS = [
    ("bot1", "Bot experiments", "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"),
    ("bot2", "bot experiments 2", "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"),
    ("bot3", "My Bot's Sheets", "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"),
]
BY_ID = {sid: (bid, name) for bid, name, sid in BOOKS}

def chart_titles(sheet):
    out = []
    for ch in sheet.get("charts") or []:
        spec = ch.get("spec") or {}
        title = spec.get("title") if isinstance(spec, dict) else ""
        if isinstance(title, dict): title = title.get("text") or ""
        title = " ".join(str(title or "").split())
        if title and title not in out: out.append(title)
        if len(out) >= 6: break
    return out

def one(doc):
    sid = str(doc.get("spreadsheetId") or "").strip()
    if not sid: sys.exit("dump is missing spreadsheetId")
    if sid in BY_ID: bid, name = BY_ID[sid]
    else:
        name = ((doc.get("properties") or {}).get("title") or sid).strip()
        bid = "x" + sid[:8]
    sheets = doc.get("sheets") or []
    sheets = sorted(sheets, key=lambda s: (s.get("properties") or {}).get("index") or 0)
    tabs, hidden = [], 0
    for s in sheets:
        p = s.get("properties") or {}
        if p.get("hidden"): hidden += 1; continue
        title = str(p.get("title") or "").strip()
        gid = p.get("sheetId")
        if not title or gid is None: continue
        tabs.append({"book": bid, "tab": title, "gid": int(gid), "charts": chart_titles(s)})
    return {"id": bid, "name": name, "spreadsheetId": sid,
            "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"}, tabs, hidden

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dumps", nargs="+")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--allow-partial", action="store_true")
    a = ap.parse_args()
    books, tabs = [], []
    seen = set()
    for path in a.dumps:
        doc = json.load(open(path, encoding="utf-8"))
        book, rows, hidden = one(doc)
        if book["spreadsheetId"] in seen: sys.exit("duplicate spreadsheet " + book["spreadsheetId"])
        seen.add(book["spreadsheetId"])
        books.append(book); tabs.extend(rows)
        print(f"{book['name']}: {len(rows)} tabs ({hidden} hidden skipped)")
    missing = [name for bid, name, sid in BOOKS if sid not in seen]
    if missing and not a.allow_partial: sys.exit("missing workbook dumps: " + ", ".join(missing))
    order = {bid: i for i, (bid, _, _) in enumerate(BOOKS)}
    books.sort(key=lambda b: (order.get(b["id"], 99), b["name"]))
    # Within each dump, tabs are already in sheet-index order. Regroup into book order.
    grouped = []
    for b in books:
        grouped.extend(t for t in tabs if t["book"] == b["id"])
    doc = {
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Offline snapshot. Render does not call Google. Regenerate with scripts/sheets_catalog.py. "
                "A PNG is shown in the app only when the tab is also in today's Charts of the day.",
        "books": books,
        "tabs": grouped,
    }
    root = os.path.join(a.repo, "static", "sheets")
    os.makedirs(root, exist_ok=True)
    dest = os.path.join(root, "catalog.json")
    with open(dest + ".tmp", "w", encoding="utf-8") as f: json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(dest + ".tmp", dest)
    print(f"wrote {dest} ({len(grouped)} tabs)")

if __name__ == "__main__": main()
