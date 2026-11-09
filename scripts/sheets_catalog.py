#!/usr/bin/env python3
"""Build Chartmaker's offline Sheets library catalog. Runs on a box with workbook metadata, not on Render.

The Sheets tab reads static/sheets/catalog.json for the tab list. Full-size PNGs live at
static/sheets/full/<book>/<gid>.png, written by scripts/import_full_pngs.py.

Usage:
  python3 scripts/sheets_catalog.py bot1.json bot2.json bot3.json
  python3 scripts/sheets_catalog.py --repo /workspace/chartmaker dump.json [...]
  python3 scripts/sheets_catalog.py --self-test

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
The app shows a thumbnail only while the Sheets search box has a query. View PNG opens
static/sheets/full/<book>/<gid>.png when that file is committed, and the thumbnail otherwise.
"""
import argparse, json, os, re, sys, datetime

BOOKS = [
    ("bot1", "Bot experiments", "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"),
    ("bot2", "bot experiments 2", "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"),
    ("bot3", "My Bot's Sheets", "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"),
]
BY_ID = {sid: (bid, name) for bid, name, sid in BOOKS}
# Workbook tabs that must not re-enter the catalog. The sheet can still exist.
# ("bot1", 333185169) is "87 TSLA beta to Nasdaq": Mark deleted its only chart
# on 2026-11-06 (chartId 1193590693). Columns A–C stay on the tab. A later
# spreadsheets.get rebuild would otherwise add that chartless tab back.
# ("bot1", 1547332232) is "161 Nvidia P-S and P-E": chart
# "Nvidia valuation: price-to-sales and trailing P/E (month-end)"
# (chartId 727606877). Mark deleted the tab on 2026-11-07.
# ("bot1", 643299192) is "202 Nvidia revenue by geography": chart
# "Where Nvidia bills its sales: revenue share by customer billing location (quarterly)"
# (chartId 2093032125). Mark deleted the tab on 2026-11-07.
# ("bot1", 1791345316) is "158 Korea & Taiwan vs S&P": chart
# "Asia's AI chip exporters: Korea (EWY) and Taiwan (EWT) vs the S&P 500"
# (chartId 1633932513). Mark deleted the tab on 2026-11-09.
# ("bot1", 1370617918) is "163 Supercore vs core CPI": chart
# "The Fed's 'supercore': services ex-shelter inflation vs core and headline CPI"
# (chartId 1771336871). Mark deleted the tab on 2026-11-09.
# ("bot1", 1024131985) is Strategy tab 99 "99 MSTR BTC per share": chart
# "Strategy: bitcoin per diluted share vs share count (at each 8-K)"
# (chartId 878632585). Removed in PR #34. Mark deleted the tab. It was missing
# from this set and was only checked by the Charts of the day self-test.
EXCLUDED_TABS = {
    ("bot1", 333185169),
    ("bot1", 1547332232),
    ("bot1", 643299192),
    ("bot1", 1791345316),
    ("bot1", 1370617918),
    ("bot1", 1024131985),
}

def tab_excluded(book, gid):
    """True when a catalog or image refresh must not keep this workbook tab."""
    try:
        return (str(book), int(gid)) in EXCLUDED_TABS
    except (TypeError, ValueError):
        return False
NOTE = (
    "Offline tab list. Regenerate tabs with scripts/sheets_catalog.py. "
    "Search previews are compressed thumbnails under static/sheets/thumbs/, built by scripts/sheets_thumbs.py. "
    "The Sheets tab shows a thumbnail only while the search box has a query. "
    "View PNG and Download PNG open static/sheets/full/<book>/<gid>.png when that file is committed, "
    "and the thumbnail otherwise. Import full PNGs with scripts/import_full_pngs.py."
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

def prune_excluded_assets(repo):
    """Delete thumb, full, and per-chart PNGs for tabs that must stay out of the library."""
    removed = []
    sheets = os.path.realpath(os.path.join(repo, "static", "sheets"))
    if not os.path.isdir(sheets):
        return removed
    for book, gid in sorted(EXCLUDED_TABS):
        gid = int(gid)
        rels = [
            "sheets/thumbs/%s/%d.jpg" % (book, gid),
            "sheets/full/%s/%d.png" % (book, gid),
        ]
        full_dir = os.path.join(repo, "static", "sheets", "full", str(book))
        if os.path.isdir(full_dir):
            prefix = "%d_" % gid
            for fn in os.listdir(full_dir):
                stem = fn[len(prefix):-4] if fn.startswith(prefix) and fn.lower().endswith(".png") else ""
                if stem.isdigit():
                    rels.append("sheets/full/%s/%s" % (book, fn))
        for rel in rels:
            path = os.path.realpath(os.path.join(repo, "static", rel))
            if os.path.commonpath([sheets, path]) != sheets:
                continue
            if os.path.isfile(path):
                os.remove(path)
                removed.append(rel)
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

def chart_list(sheet):
    """Every chart in sheet order: [{"n": 1, "title": ...}]. n matches the PNG export's (n) suffix."""
    out = []
    for i, ch in enumerate(sheet.get("charts") or [], 1):
        spec = ch.get("spec") or {}
        title = spec.get("title") if isinstance(spec, dict) else ""
        if isinstance(title, dict): title = title.get("text") or ""
        out.append({"n": i, "title": " ".join(str(title or "").split())})
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
    tabs, hidden, excluded = [], 0, 0
    for s in sheets:
        p = s.get("properties") or {}
        if p.get("hidden"): hidden += 1; continue
        title = str(p.get("title") or "").strip()
        gid = p.get("sheetId")
        if not title or gid is None: continue
        if tab_excluded(bid, gid):
            excluded += 1
            continue
        row = {"book": bid, "tab": title, "gid": int(gid), "charts": chart_titles(s), "chartList": chart_list(s)}
        tabs.append(row)
    return {"id": bid, "name": name, "spreadsheetId": sid,
            "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"}, tabs, hidden, excluded

def self_test():
    import shutil, tempfile
    sid = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
    def sheet(title, gid, chart=None, hidden=False):
        props = {"title": title, "sheetId": gid, "index": 0}
        if hidden:
            props["hidden"] = True
        charts = [{"spec": {"title": chart}}] if chart else []
        return {"properties": props, "charts": charts}
    doc = {
        "spreadsheetId": sid,
        "properties": {"title": "Bot experiments"},
        "sheets": [
            sheet("87 TSLA beta to Nasdaq", 333185169, "Tesla beta"),
            sheet("161 Nvidia P-S and P-E", 1547332232,
                  "Nvidia valuation: price-to-sales and trailing P/E (month-end)"),
            sheet("202 Nvidia revenue by geography", 643299192,
                  "Where Nvidia bills its sales: revenue share by customer billing location (quarterly)"),
            sheet("201 Nvidia data center revenue", 1476428159,
                  "Nvidia became a data-center company: quarterly revenue, data center vs everything else"),
            sheet("194 Nvidia vs Cisco 2000", 999, "Is Nvidia the new Cisco?"),
            sheet("158 Korea & Taiwan vs S&P", 1791345316,
                  "Asia's AI chip exporters: Korea (EWY) and Taiwan (EWT) vs the S&P 500"),
            sheet("163 Supercore vs core CPI", 1370617918,
                  "The Fed's 'supercore': services ex-shelter inflation vs core and headline CPI"),
            sheet("99 MSTR BTC per share", 1024131985,
                  "Strategy: bitcoin per diluted share vs share count (at each 8-K)"),
            sheet("160 China vs Mexico vs Taiwan", 171599627,
                  "Where US imports come from: China, Mexico and Taiwan (AI servers) shares, rolling 12 months"),
            sheet("hidden junk", 5, "nope", hidden=True),
        ],
    }
    _book, tabs, hidden, excluded = one(doc)
    gids = [t["gid"] for t in tabs]
    assert gids == [1476428159, 999, 171599627], gids
    assert hidden == 1 and excluded == 6, (hidden, excluded)
    assert ("bot1", 333185169) in EXCLUDED_TABS
    assert tab_excluded("bot1", 1547332232) and tab_excluded("bot1", 643299192)
    assert tab_excluded("bot1", 1791345316) and tab_excluded("bot1", 1370617918)
    assert tab_excluded("bot1", 1024131985)
    assert not tab_excluded("bot1", 1476428159)
    assert not tab_excluded("bot1", 171599627)
    assert not tab_excluded("bot2", 645072508)
    titles = [c for t in tabs for c in t["charts"]]
    assert not any("trailing P/E" in c or "billing location" in c for c in titles)
    assert any("data-center company" in c for c in titles)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cat = json.load(open(os.path.join(repo, "static", "sheets", "catalog.json"), encoding="utf-8"))
    live = {(t["book"], t["gid"]) for t in cat["tabs"]}
    for gid in (333185169, 1547332232, 643299192, 1791345316, 1370617918, 1024131985):
        assert ("bot1", gid) not in live, gid
    assert ("bot1", 171599627) in live
    assert ("bot2", 645072508) in live
    assert len(cat["tabs"]) == 497, len(cat["tabs"])
    blob = json.dumps(cat)
    assert "Nvidia valuation: price-to-sales and trailing P/E (month-end)" not in blob
    assert "Where Nvidia bills its sales: revenue share by customer billing location (quarterly)" not in blob
    assert "161 Nvidia P-S and P-E" not in blob
    assert "202 Nvidia revenue by geography" not in blob
    assert "Asia's AI chip exporters: Korea (EWY) and Taiwan (EWT) vs the S&P 500" not in blob
    assert "The Fed's 'supercore': services ex-shelter inflation vs core and headline CPI" not in blob
    assert "158 Korea & Taiwan vs S&P" not in blob
    assert "163 Supercore vs core CPI" not in blob
    assert "99 MSTR BTC per share" not in blob
    assert "Strategy: bitcoin per diluted share vs share count (at each 8-K)" not in blob
    names = {t["tab"] for t in cat["tabs"]}
    for keep in (
        "201 Nvidia data center revenue",
        "194 Nvidia vs Cisco 2000",
        "151 Big Tech capex vs Nvidia",
        "225 Nvidia inventory & DSO",
        "229 Nvidia vs country GDPs",
        "334 Nvidia top-customer share",
        "19 Nvidia supply commitments",
        "23 Nvidia commitments table",
    ):
        assert keep in names, keep
    today = json.load(open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8"))
    today_blob = json.dumps(today)
    assert len(today["items"]) == 18, len(today["items"])
    assert "1547332232" not in today_blob and "643299192" not in today_blob
    assert "1791345316" not in today_blob and "1370617918" not in today_blob
    assert "161 Nvidia P-S and P-E" not in today_blob
    assert "202 Nvidia revenue by geography" not in today_blob
    assert "158 Korea & Taiwan vs S&P" not in today_blob
    assert "163 Supercore vs core CPI" not in today_blob
    assert "Taiwan's chip stocks surge" not in today["theme"]
    assert "October CPI is due Tuesday" not in today["theme"]
    images = [it["image"] for it in today["items"]]
    assert "cotd/2026-11-09/18.png" not in images and "cotd/2026-11-09/20.png" not in images
    assert "cotd/2026-11-09/17.png" in images and "cotd/2026-11-09/19.png" in images
    for rel in (
        "static/sheets/full/bot1/1547332232.png",
        "static/sheets/full/bot1/643299192.png",
        "static/sheets/thumbs/bot1/1547332232.jpg",
        "static/sheets/thumbs/bot1/643299192.jpg",
        "static/cotd/2026-11-07/05.png",
        "static/cotd/2026-11-07/06.png",
        "static/sheets/full/bot1/1791345316.png",
        "static/sheets/full/bot1/1370617918.png",
        "static/sheets/thumbs/bot1/1791345316.jpg",
        "static/sheets/thumbs/bot1/1370617918.jpg",
        "static/cotd/2026-11-09/18.png",
        "static/cotd/2026-11-09/20.png",
    ):
        assert not os.path.isfile(os.path.join(repo, rel)), rel
    assert os.path.isfile(os.path.join(repo, "static/sheets/full/bot2/645072508.png"))
    assert os.path.isfile(os.path.join(repo, "static/sheets/thumbs/bot2/645072508.jpg"))
    assert os.path.isfile(os.path.join(repo, "static/sheets/full/bot1/171599627.png"))
    full = os.path.join(repo, "static", "sheets", "full", "bot1")
    for fn in os.listdir(full):
        assert not fn.startswith("1547332232") and not fn.startswith("643299192"), fn
        assert not fn.startswith("1791345316") and not fn.startswith("1370617918"), fn
        assert not fn.startswith("1024131985"), fn
    scratch = tempfile.mkdtemp(prefix="excluded-assets-")
    try:
        for rel in (
            "static/sheets/thumbs/bot1/1547332232.jpg",
            "static/sheets/full/bot1/1547332232.png",
            "static/sheets/full/bot1/1547332232_2.png",
            "static/sheets/full/bot1/1476428159.png",
            "static/sheets/thumbs/bot1/333185169.jpg",
            "static/sheets/full/bot1/643299192.png",
            "static/sheets/full/bot1/1791345316.png",
            "static/sheets/full/bot1/1791345316_2.png",
            "static/sheets/thumbs/bot1/1370617918.jpg",
            "static/sheets/full/bot1/1024131985.png",
            "static/sheets/full/bot1/171599627.png",
            "static/sheets/full/bot2/645072508.png",
        ):
            path = os.path.join(scratch, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x")
        gone = prune_excluded_assets(scratch)
        assert not os.path.exists(os.path.join(scratch, "static/sheets/full/bot1/1547332232.png"))
        assert not os.path.exists(os.path.join(scratch, "static/sheets/full/bot1/1547332232_2.png"))
        assert not os.path.exists(os.path.join(scratch, "static/sheets/thumbs/bot1/1547332232.jpg"))
        assert os.path.isfile(os.path.join(scratch, "static/sheets/full/bot1/1476428159.png"))
        assert os.path.isfile(os.path.join(scratch, "static/sheets/full/bot1/171599627.png"))
        assert os.path.isfile(os.path.join(scratch, "static/sheets/full/bot2/645072508.png"))
        assert not os.path.exists(os.path.join(scratch, "static/sheets/full/bot1/1791345316.png"))
        assert not os.path.exists(os.path.join(scratch, "static/sheets/full/bot1/1791345316_2.png"))
        assert not os.path.exists(os.path.join(scratch, "static/sheets/thumbs/bot1/1370617918.jpg"))
        assert not os.path.exists(os.path.join(scratch, "static/sheets/full/bot1/1024131985.png"))
        assert any("643299192" in r for r in gone) and any("333185169" in r for r in gone)
        assert any("1791345316" in r for r in gone) and any("1370617918" in r for r in gone)
        assert any("1024131985" in r for r in gone)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print("self-test ok")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dumps", nargs="*")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return
    if not a.dumps:
        ap.error("pass one or more spreadsheet dumps")
    books, tabs = [], []
    seen = set()
    for path in a.dumps:
        doc = json.load(open(path, encoding="utf-8"))
        book, rows, hidden, excluded = one(doc)
        if book["spreadsheetId"] in seen: sys.exit("duplicate spreadsheet " + book["spreadsheetId"])
        seen.add(book["spreadsheetId"])
        books.append(book); tabs.extend(rows)
        extra = f", {excluded} excluded" if excluded else ""
        print(f"{book['name']}: {len(rows)} tabs ({hidden} hidden skipped{extra})")
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
    dropped_ex = prune_excluded_assets(a.repo)
    extra = f", {len(dropped_ex)} excluded assets removed" if dropped_ex else ""
    print(f"wrote {dest} ({len(grouped)} tabs, {n_thumb} thumbs kept, {dropped} orphan thumbs removed{extra})")

if __name__ == "__main__": main()
