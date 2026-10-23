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

This script does not copy chart images. An existing tab thumbnail (static/sheets/thumbs/<book>/<gid>.jpg)
is kept when that book and gid are still in the new snapshot and the file is still on disk.
Build or replace thumbnails with scripts/sheets_thumbs.py from a local folder of Drive-exported PNGs.
The app shows a thumbnail only while the Sheets search box has a query. Full-size PNGs are not committed.
"""
import argparse, json, os, re, sys, datetime

BOOKS = [
    ("bot1", "Bot experiments", "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"),
    ("bot2", "bot experiments 2", "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"),
    ("bot3", "My Bot's Sheets", "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"),
]
BY_ID = {sid: (bid, name) for bid, name, sid in BOOKS}
NOTE = (
    "Offline snapshot. Render does not call Google. Regenerate tabs with scripts/sheets_catalog.py. "
    "Search previews are optional compressed thumbnails under static/sheets/thumbs/, built by scripts/sheets_thumbs.py "
    "from a local folder of chart PNGs. The Sheets tab shows a thumbnail only while the search box has a query. "
    "Full-resolution chart PNGs stay on Drive and are not committed."
)
# book id + gid, relative to static/. Rejects anything that could escape the thumbs directory.
THUMB_REL = re.compile(r"^sheets/thumbs/([A-Za-z0-9][A-Za-z0-9_-]{0,24})/(\d+)\.jpg$")

def thumb_ok(book, gid, thumb, repo):
    """Return thumb if it names this tab and the JPEG is a real file inside static/sheets/thumbs."""
    m = THUMB_REL.fullmatch(str(thumb or ""))
    if not m or m.group(1) != book or int(m.group(2)) != int(gid): return None
    root = os.path.realpath(os.path.join(repo, "static", "sheets", "thumbs"))
    path = os.path.realpath(os.path.join(repo, "static", thumb))
    if os.path.commonpath([root, path]) != root: return None
    if not os.path.isfile(path) or os.path.getsize(path) <= 0: return None
    return thumb

def load_thumb_index(repo):
    """(book, gid) -> thumb path, plus the previous thumbsGenerated stamp. Missing catalog is empty."""
    dest = os.path.join(repo, "static", "sheets", "catalog.json")
    try: old = json.load(open(dest, encoding="utf-8"))
    except (OSError, ValueError): return {}, None
    keep = {}
    for t in old.get("tabs") or []:
        book, gid = t.get("book"), t.get("gid")
        if book is None or gid is None: continue
        ok = thumb_ok(str(book), gid, t.get("thumb"), repo)
        if ok: keep[(str(book), int(gid))] = ok
    stamp = old.get("thumbsGenerated")
    return keep, stamp if isinstance(stamp, str) and keep else None

def write_catalog(dest, doc):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest + ".tmp", "w", encoding="utf-8") as f: json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(dest + ".tmp", dest)

def prune_thumbs(repo, tabs):
    """Delete JPEGs under static/sheets/thumbs that the catalog no longer references."""
    root = os.path.join(repo, "static", "sheets", "thumbs")
    if not os.path.isdir(root): return 0
    keep = {t.get("thumb") for t in tabs if t.get("thumb")}
    removed = 0
    for dirpath, dirnames, files in os.walk(root, topdown=False):
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, os.path.join(repo, "static")).replace(os.sep, "/")
            if rel not in keep and fn.lower().endswith(".jpg"):
                os.remove(full); removed += 1
        if dirpath != root and not os.listdir(dirpath):
            try: os.rmdir(dirpath)
            except OSError: pass
    return removed

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
        row = {"book": bid, "tab": title, "gid": int(gid), "charts": chart_titles(s)}
        tabs.append(row)
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
    kept, stamp = load_thumb_index(a.repo)
    n_thumb = 0
    for t in grouped:
        prev = kept.get((t["book"], t["gid"]))
        if prev: t["thumb"] = prev; n_thumb += 1
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"generated": generated, "note": NOTE}
    if stamp and n_thumb: doc["thumbsGenerated"] = stamp
    doc["books"] = books
    doc["tabs"] = grouped
    dest = os.path.join(a.repo, "static", "sheets", "catalog.json")
    write_catalog(dest, doc)
    dropped = prune_thumbs(a.repo, grouped)
    print(f"wrote {dest} ({len(grouped)} tabs, {n_thumb} thumbs kept, {dropped} orphan thumbs removed)")

if __name__ == "__main__": main()
