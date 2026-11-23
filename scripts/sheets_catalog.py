#!/usr/bin/env python3
"""Build Chartmaker's offline Sheets library catalog. Runs on a box with workbook metadata, not on Render.

The Sheets tab reads static/sheets/catalog.json for the tab list. Full-size PNGs live at
static/sheets/full/<book>/<gid>.png, written by scripts/import_full_pngs.py.

Usage:
  python3 scripts/sheets_catalog.py bot1.json bot2.json bot3.json
  python3 scripts/sheets_catalog.py --repo /workspace/chartmaker dump.json [...]
  python3 scripts/sheets_catalog.py --self-test

Each JSON file is one spreadsheets.get response (the Google Sheets `get_spreadsheet` tool returns this shape):
  spreadsheetId, properties.title, sheets[].properties.{sheetId,title,index,hidden},
  sheets[].charts[].{chartId,spec.title,position}

Monday's part 3 dump has to include chart position. A title-only get cannot number a tab
that has more than one chart. Field mask:

  sheets(properties,charts(chartId,spec.title,position))

chartList n follows position.overlayPosition.anchorCell (rowIndex, then columnIndex,
then the original charts[] index), the same order as the Monday exporter's
pngChartsSorted_. Google's JSON omits a zero index; an anchorCell object still counts,
and the missing index is 0. A tab with one chart does not need position. A multi-chart
tab that lacks an anchor on any chart is not renumbered: this script keeps that tab's
existing catalog entry and prints a warning. A new multi-chart tab with no anchors is
left out until the dump includes position.

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
# Permanently retired and excluded tabs live in config/retired_charts.json.
# EXCLUDED_TABS is the library-scope view (reason "retired" or "excluded").
# A Charts-of-the-day-only row (scope "cotd") stays in the library and is not
# in this set. Add a retirement with scripts/retire_chart.py.
import retired_charts
EXCLUDED_TABS = retired_charts.library_pairs()
# Tabs that must stay in the library even when a workbook dump omits them
# (a truncated spreadsheets.get, or a rebuild that never listed them).
# ("bot3", 874288811) is "Central Bank Ranking" in My Bot's Sheets: no native
# chart, PNG from Steve's twice-monthly render. Monday's exporter skips it.
# ("bot3", 797114604) is "NASDAQ Returns by Year" in My Bot's Sheets: no native
# chart, PNG from Steve's Python render of the C7:F25 table (navy header,
# alternating rows, green/red % change, @thepowerfulHRV). Monday's exporter
# skips it because the tab is chartless. Script: /workspace/nasdaq_returns/render.py.
# ("bot3", 819961031) is "BTC Returns by Year" in My Bot's Sheets: no native
# chart, PNG from the same C7:F25 sheet-style renderer as NASDAQ Returns
# (navy header, alternating rows, displayed $ closes and % change,
# @thepowerfulHRV). Monday's exporter skips it because the tab is chartless.
# Script: /workspace/returns_tables/render.py.
# ("bot3", 197035552) is "Waymo vs Tesla Robotaxi Fleets": it has a native
# Sheets chart, but the library PNG is Steve's weekly Python render.
# import_full_pngs.KEEP_MANUAL_PNGS blocks Monday from overwriting that PNG
# with the native-chart export.
# ("bot2", 855902063) is "94 BTC treasury monthly flows" in bot experiments 2:
# native chart "Monthly net bitcoin flows of public treasury companies" is
# unstyled, so the library PNG is Steve's Python render (re-rendered on the
# 3rd of each month). KEEP_MANUAL_PNGS blocks the native export here too.
# ("bot3", 1408701012) is "Waymo vs Tesla Texas" in My Bot's Sheets: native
# chart "Waymo vs Tesla in Texas" is plain, so the library PNG is Steve's
# Python render (Texas DMV driverless registrations). KEEP_MANUAL_PNGS blocks
# the native export here too.
# ("bot3", 9000000001) is "Tesla Robotaxi service area (sq mi)": Steve's weekly
# Python render of Tesla Robotaxi geofence size. There is no Google Sheet tab.
# The gid is reserved above Google's int32 sheetId max (SYNTHETIC_GID_MIN), so
# it cannot collide with a real tab. KEEP_MANUAL_PNGS blocks any import from
# overwriting the PNG. The catalog row is sheetless so Open in Sheets is hidden.
# ("bot3", 9000000002) is "Strategy's accretive swaps, week by week": Steve's
# weekly Python render of Strategy's Net BTC per Share accretion from 8-K-
# reported swaps. There is no Google Sheet tab. The gid is reserved above
# Google's int32 sheetId max (SYNTHETIC_GID_MIN), so it cannot collide with a
# real tab. KEEP_MANUAL_PNGS blocks any import from overwriting the PNG. The
# catalog row is sheetless so Open in Sheets is hidden.
# ("bot3", 9000000003) is "Hyperscaler free cash flow (incl. SpaceX)": Steve's
# quarterly Python render of Alphabet, Meta, Microsoft, Amazon and SpaceX
# free cash flow, 2014–2030E. There is no Google Sheet tab. The gid is
# reserved above Google's int32 sheetId max (SYNTHETIC_GID_MIN), so it cannot
# collide with a real tab. KEEP_MANUAL_PNGS blocks any import from
# overwriting the PNG. The catalog row is sheetless so Open in Sheets is hidden.
# ("bot3", 318084443) is "SpaceX Free Cash Flow" in My Bot's Sheets: it has a
# native column chart, but the library PNG is Steve's quarterly Python render
# (cream background, purple bars, shaded estimates band). KEEP_MANUAL_PNGS
# blocks Monday's native export from overwriting that PNG. The tab is real, so
# the catalog row is not sheetless and Open in Sheets stays on.
# Script: /workspace/fcf/plot_spcx_only.py.
# ("bot3", 1027111563) is "SpaceX Capex" in My Bot's Sheets: it has a native
# column chart, but the library PNG is Steve's quarterly Python render (cream
# background, purple bars, shaded estimates band, no subtitle). KEEP_MANUAL_PNGS
# blocks Monday's native export from overwriting that PNG. The tab is real, so
# the catalog row is not sheetless and Open in Sheets stays on.
# Script: /workspace/fcf/plot_spcx_capex.py.
# ("bot3", 1921102868) is "Share Count Change" in My Bot's Sheets: it has two
# native line charts, but the library PNG is Steve's Python render of the full
# comparison (title only, no subtitle). KEEP_MANUAL_PNGS blocks Monday's native
# export from overwriting that PNG. The tab is real, so the catalog row is not
# sheetless and Open in Sheets stays on. Script: /workspace/buybacks/plot_shares.py.
# ("bot3", 970035917) is "Buybacks TTM" in My Bot's Sheets: it has a native
# column chart, but the library PNG is Steve's Python render (title only, no
# subtitle). KEEP_MANUAL_PNGS blocks Monday's native export from overwriting
# that PNG. The tab is real, so the catalog row is not sheetless and Open in
# Sheets stays on. Script: /workspace/buybacks/plot_buybacks.py.
# A later catalog rebuild must not drop these.
PINNED_TABS = {
    ("bot3", 874288811): "Central Bank Ranking",
    ("bot3", 797114604): "NASDAQ Returns by Year",
    ("bot3", 819961031): "BTC Returns by Year",
    ("bot3", 197035552): "Waymo vs Tesla Robotaxi Fleets",
    ("bot2", 855902063): "94 BTC treasury monthly flows",
    ("bot3", 1408701012): "Waymo vs Tesla Texas",
    ("bot3", 9000000001): "Tesla Robotaxi service area (sq mi)",
    ("bot3", 9000000002): "Strategy's accretive swaps, week by week",
    ("bot3", 9000000003): "Hyperscaler free cash flow (incl. SpaceX)",
    ("bot3", 318084443): "SpaceX Free Cash Flow",
    ("bot3", 1027111563): "SpaceX Capex",
    ("bot3", 1921102868): "Share Count Change",
    ("bot3", 970035917): "Buybacks TTM",
}
# Google Sheets sheetId is a non-negative int32 (max 2,147,483,647). A library
# gid at or above this value is synthetic and can never collide with a real tab.
SYNTHETIC_GID_MIN = 2147483648
# PNG-only pins with no Sheet tab. restore_pinned_tabs and apply_pinned_flags
# attach a chartList (so Charts of the day treats the row as a chart) and a
# sheetless flag (so Open in Sheets is hidden). The value is the chart title.
SHEETLESS_TABS = {
    ("bot3", 9000000001): "Tesla Robotaxi service area (sq mi)",
    ("bot3", 9000000002): "Strategy's accretive swaps, week by week",
    ("bot3", 9000000003): "Hyperscaler free cash flow (incl. SpaceX)",
}
# Google's default name for a chart with no real title: "Chart 1", "Chart 2", ...
# "Chart 01" is a real title some tests use and is not this placeholder.
UNTITLED_CHART = re.compile(r"^Chart\s+[1-9]\d?$")

def untitled_chart_title(title):
    """True when a chart has no real title. Empty, or Google's "Chart N" default."""
    text = " ".join(str(title or "").split())
    return (not text) or bool(UNTITLED_CHART.fullmatch(text))

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

def _anchor_index(anchor, key):
    """Integer anchor index. A missing key is 0 (protobuf JSON drops zeros). None if the value is unusable."""
    if key not in anchor:
        return 0
    value = anchor[key]
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value

def chart_anchor(chart):
    """(rowIndex, columnIndex) from overlayPosition.anchorCell, or None when this chart has no anchor."""
    if not isinstance(chart, dict):
        return None
    pos = chart.get("position")
    if not isinstance(pos, dict):
        return None
    overlay = pos.get("overlayPosition")
    if not isinstance(overlay, dict):
        return None
    anchor = overlay.get("anchorCell")
    if not isinstance(anchor, dict):
        return None
    row = _anchor_index(anchor, "rowIndex")
    col = _anchor_index(anchor, "columnIndex")
    if row is None or col is None:
        return None
    return (row, col)

def charts_sorted(sheet):
    """Charts in pngChartsSorted_ order, or None when a multi-chart tab lacks an anchor.

    Sort key is overlayPosition.anchorCell rowIndex, then columnIndex, then the
    original index in sheets[].charts[]. A tab with one chart, or none, does not
    need position and is returned as given.
    """
    charts = list(sheet.get("charts") or [])
    if len(charts) <= 1:
        return charts
    keyed = []
    for i, ch in enumerate(charts):
        anchor = chart_anchor(ch)
        if anchor is None:
            return None
        keyed.append((anchor[0], anchor[1], i, ch))
    keyed.sort()
    return [row[3] for row in keyed]

def chart_text(ch):
    spec = ch.get("spec") or {} if isinstance(ch, dict) else {}
    title = spec.get("title") if isinstance(spec, dict) else ""
    if isinstance(title, dict):
        title = title.get("text") or ""
    return " ".join(str(title or "").split())

def chart_titles(sheet):
    """Unique titles in the same order as chart_list, capped at 6. None when chart_list is None."""
    charts = charts_sorted(sheet)
    if charts is None:
        return None
    out = []
    for ch in charts:
        title = chart_text(ch)
        if title and title not in out:
            out.append(title)
        if len(out) >= 6:
            break
    return out

def chart_list(sheet):
    """Every chart in on-sheet position order: [{"n": 1, "title": ...}].

    n matches the PNG export's (n) suffix (pngChartsSorted_: anchor row, then
    column, then the original charts[] index). None when a multi-chart tab has
    no anchor on every chart, so the caller must not invent numbers from API order.
    A single chart does not need a position.
    """
    charts = charts_sorted(sheet)
    if charts is None:
        return None
    return [{"n": i, "title": chart_text(ch)} for i, ch in enumerate(charts, 1)]

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
        listed = chart_list(s)
        if listed is None:
            # Multi-chart tab with no anchors. Do not number it from API order.
            tabs.append({
                "book": bid, "tab": title, "gid": int(gid),
                "_unpositioned": True, "_ncharts": len(s.get("charts") or []),
            })
            continue
        row = {"book": bid, "tab": title, "gid": int(gid), "charts": chart_titles(s), "chartList": listed}
        tabs.append(row)
    return {"id": bid, "name": name, "spreadsheetId": sid,
            "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"}, tabs, hidden, excluded

def load_previous_tabs(repo):
    """(book, gid) -> catalog tab from the last catalog.json. Missing file is empty."""
    dest = os.path.join(repo, "static", "sheets", "catalog.json")
    try:
        old = json.load(open(dest, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for t in old.get("tabs") or []:
        if not isinstance(t, dict):
            continue
        book, gid = t.get("book"), t.get("gid")
        if book is None or gid is None:
            continue
        try:
            out[(str(book), int(gid))] = t
        except (TypeError, ValueError):
            continue
    return out

def resolve_unpositioned(tabs, previous):
    """Keep the prior catalog row when a multi-chart tab has no anchors.

    A row marked _unpositioned is not given API-order numbers. When (book, gid)
    is already in the catalog, that entry is kept (thumbnail is reattached later).
    When it is not, the tab is left out. Returns (tabs, warnings).
    """
    out, warnings = [], []
    for t in tabs:
        if not t.get("_unpositioned"):
            out.append(t)
            continue
        try:
            key = (str(t.get("book")), int(t.get("gid")))
        except (TypeError, ValueError):
            warnings.append("multi-chart tab has no position data and no gid; left out")
            continue
        label = "%s gid %s (%s)" % (key[0], key[1], t.get("tab") or "")
        ncharts = t.get("_ncharts")
        prev = previous.get(key)
        if prev:
            row = dict(prev)
            row.pop("thumb", None)
            out.append(row)
            warnings.append(
                "%s: %s charts and no position data; kept the existing catalog entry" % (label, ncharts)
            )
        else:
            warnings.append(
                "%s: %s charts and no position data; no existing catalog entry, tab left out" % (label, ncharts)
            )
    return out, warnings

def restore_pinned_tabs(tabs, previous):
    """Reattach pinned table tabs omitted from a workbook dump.

    Returns (tabs, warnings). A pinned gid already in tabs is left alone.
    An excluded gid is never restored. When the previous catalog has the
    row, that entry is kept (thumbnail is reattached later). Otherwise a
    bare chartless catalog row is added so the tab is not left out.
    """
    have = set()
    for t in tabs or []:
        try:
            have.add((str(t.get("book")), int(t.get("gid"))))
        except (TypeError, ValueError):
            continue
    out = list(tabs or [])
    warnings = []
    for (book, gid), title in sorted(PINNED_TABS.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        key = (str(book), int(gid))
        if key in have or tab_excluded(book, gid):
            continue
        prev = previous.get(key) if isinstance(previous, dict) else None
        if prev:
            row = dict(prev)
            row.pop("thumb", None)
            if key in SHEETLESS_TABS and not tab_excluded(*key):
                chart_title = SHEETLESS_TABS[key]
                row["sheetless"] = True
                if not row.get("tab"):
                    row["tab"] = title
                if not row.get("charts"):
                    row["charts"] = [chart_title]
                if not row.get("chartList"):
                    row["chartList"] = [{"n": 1, "title": chart_title}]
            out.append(row)
            warnings.append(
                "%s gid %s (%s): missing from dump; kept the existing catalog entry"
                % (key[0], key[1], row.get("tab") or title)
            )
        else:
            row = {"book": key[0], "tab": title, "gid": key[1], "charts": [], "chartList": []}
            if key in SHEETLESS_TABS:
                chart_title = SHEETLESS_TABS[key]
                row["charts"] = [chart_title]
                row["chartList"] = [{"n": 1, "title": chart_title}]
                row["sheetless"] = True
            out.append(row)
            warnings.append(
                "%s gid %s (%s): missing from dump and no existing catalog entry; added the pinned table tab"
                % (key[0], key[1], title)
            )
    return out, warnings

def apply_pinned_flags(tabs):
    """Mark pinned table tabs so the Sheets 'Has a chart' filter still lists them."""
    for t in tabs or []:
        try:
            key = (str(t.get("book")), int(t.get("gid")))
        except (TypeError, ValueError):
            continue
        if key in PINNED_TABS and not tab_excluded(*key):
            t["pin"] = True
        if key in SHEETLESS_TABS and not tab_excluded(*key):
            t["sheetless"] = True
            chart_title = SHEETLESS_TABS[key]
            if not t.get("charts"):
                t["charts"] = [chart_title]
            if not t.get("chartList"):
                t["chartList"] = [{"n": 1, "title": chart_title}]
    return tabs

def build_catalog(dumps, repo, allow_partial):
    """Rewrite catalog.json from spreadsheet dumps. Returns (doc, warnings)."""
    books, tabs = [], []
    seen = set()
    for path in dumps:
        doc = json.load(open(path, encoding="utf-8"))
        book, rows, hidden, excluded = one(doc)
        if book["spreadsheetId"] in seen:
            sys.exit("duplicate spreadsheet " + book["spreadsheetId"])
        seen.add(book["spreadsheetId"])
        books.append(book)
        tabs.extend(rows)
        extra = f", {excluded} excluded" if excluded else ""
        n_hold = sum(1 for t in rows if t.get("_unpositioned"))
        hold_s = f", {n_hold} multi-chart without position" if n_hold else ""
        print(f"{book['name']}: {len(rows)} tabs ({hidden} hidden skipped{extra}{hold_s})")
    missing = [name for bid, name, sid in BOOKS if sid not in seen]
    if missing and not allow_partial:
        sys.exit("missing workbook dumps: " + ", ".join(missing))
    order = {bid: i for i, (bid, _, _) in enumerate(BOOKS)}
    books.sort(key=lambda b: (order.get(b["id"], 99), b["name"]))
    # Within each dump, tabs are already in sheet-index order. Regroup into book order.
    grouped = []
    for b in books:
        grouped.extend(t for t in tabs if t["book"] == b["id"])
    previous = load_previous_tabs(repo)
    grouped, warnings = resolve_unpositioned(grouped, previous)
    grouped, pin_warns = restore_pinned_tabs(grouped, previous)
    warnings.extend(pin_warns)
    grouped = apply_pinned_flags(grouped)
    for w in warnings:
        print("warning: " + w)
    kept, stamp = load_thumb_index(repo)
    n_thumb = 0
    for t in grouped:
        prev = kept.get((t["book"], t["gid"]))
        if prev:
            t["thumb"] = prev
            n_thumb += 1
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"generated": generated, "note": NOTE}
    if stamp and n_thumb:
        doc["thumbsGenerated"] = stamp
    doc["books"] = books
    doc["tabs"] = grouped
    dest = os.path.join(repo, "static", "sheets", "catalog.json")
    write_catalog(dest, doc)
    dropped = prune_thumbs(repo, grouped)
    dropped_ex = prune_excluded_assets(repo)
    extra = f", {len(dropped_ex)} excluded assets removed" if dropped_ex else ""
    print(f"wrote {dest} ({len(grouped)} tabs, {n_thumb} thumbs kept, {dropped} orphan thumbs removed{extra})")
    return doc, warnings

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
            sheet("98 MSTR vs BTC drawdowns", 292208014,
                  "Strategy vs bitcoin: drawdown from record close"),
            sheet("151 Big Tech capex vs Nvidia", 2119398138,
                  "AI spending loop: Microsoft+Alphabet+Amazon+Meta capex vs Nvidia revenue (trailing 4 quarters)"),
            sheet("223 Micron revenue & margin", 1584345646,
                  "The AI memory boom (HBM): Micron quarterly revenue and gross margin"),
            sheet("226 OpenAI vs Anthropic revenue", 1186940001,
                  "Reported revenue run-rates: OpenAI vs Anthropic (company statements & media; log scale)"),
            sheet("263 Tariff pass-through", 501919598,
                  "Are tariffs showing up in prices? Import-heavy goods CPI vs core CPI (Jan 2025 = 100)"),
            sheet("12 Breakeven vs CPI", 579853621,
                  "Inflation 3.35% vs market's 10-year expectation 2.29%"),
            sheet("122 Bond vol vs stock vol", 1308503061,
                  "Bond market fear vs stock market fear: MOVE ÷ VIX"),
            sheet("232 Quits vs wage growth", 748431671,
                  "Job-hopping predicts pay raises: quits rate vs wage growth (Atlanta Fed tracker)"),
            sheet("162 Factory construction boom", 254001772,
                  "Chips Act & AI fabs: real US manufacturing construction spending"),
            sheet("153 Big Tech capex intensity", 1238930141,
                  "Capex intensity: Big Tech capital spending as % of revenue (trailing 4Q)"),
            sheet("43 Nasdaq vs S&P ratio", 1835714359,
                  "Tech leadership: Nasdaq Composite ÷ S&P 500"),
            sheet("109 Equal vs cap-weight S&P", 492285771,
                  "Equal-weight vs cap-weight S&P 500: relative total return (RSP ÷ SPY)"),
            sheet("135 Real coffee & cocoa", 220064271,
                  "Coffee and cocoa prices adjusted for inflation (Aug 2026 dollars)"),
            sheet("173 Continuing/initial claims", 79264442,
                  "Hard to find a new job: continuing claims per initial claim (4-week averages)"),
            sheet("239 Foreign vs native-born jobs", 1755343475,
                  "Immigration and jobs: employment of foreign-born vs native-born workers (12-month average, Jan 2020 = 100)"),
            sheet("233 Older workers participation", 1067583608,
                  "Retirement wave: labor force participation of Americans 55+ vs prime-age (25–54)"),
            sheet("97 MSTR vol vs BTC vol", 1027143376,
                  "Strategy vs bitcoin: 30-day realized volatility"),
            sheet("54 Trimmed vs core PCE", 1718545259,
                  "Underlying inflation: Dallas Fed trimmed-mean PCE vs core PCE"),
            sheet("68 Trade balance % GDP", 1892662496,
                  "US trade balance as % of GDP (goods & services)"),
            sheet("227 Private AI lab valuations", 1459587255,
                  "Private AI lab valuations from funding rounds: OpenAI, Anthropic and xAI (post-money, as reported; log scale)"),
            sheet("119 Emerging markets vs US", 687897000,
                  "Emerging markets vs US stocks (EEM ÷ SPY, total return)"),
            sheet("86 TSLA vs Toyota+GM+Ford", 546391999,
                  "Tesla market cap vs Toyota, GM and Ford combined"),
            sheet("22 LTC & BCH in BTC", 195161343,
                  "Old altcoins priced in Bitcoin: LTC/BTC and BCH/BTC"),
            sheet("56 Food home vs away", 321339250,
                  "Groceries vs restaurants: food inflation YoY"),
            sheet("111 Value vs growth", 2128724732,
                  "Value vs growth: Russell 1000 Value ÷ Russell 1000 Growth (total return)"),
            sheet("191 Real gold price", 189736118,
                  "Gold in today's dollars: inflation-adjusted price vs the 1980 peak (Aug 2026 USD)"),
            sheet("295 Tesla vs GM vs Ford margins", 1981719475,
                  "Tesla's margin edge is gone: operating margin vs GM and Ford (trailing 4 quarters)"),
            sheet("159 Uranium & nuclear vs S&P", 519508812,
                  "Nuclear for AI power: uranium stocks vs the S&P 500 (Jan 2020 = 100)"),
            sheet("45 Euro spreads vs Bunds", 45161413,
                  "Euro-area 10-year spreads over Germany: Italy, Spain, France"),
            sheet("126 CCC vs BB spread", 567489150,
                  "Junk-bond quality gap: CCC spread minus BB spread"),
            sheet("204 Cloud operating margins", 601460115,
                  "Cloud profitability: operating margin of AWS vs Google Cloud (trailing 4 quarters)"),
            sheet("hidden junk", 5, "nope", hidden=True),
        ],
    }
    _book, tabs, hidden, excluded = one(doc)
    gids = [t["gid"] for t in tabs]
    assert gids == [999, 171599627, 1027143376, 1718545259, 1892662496], gids
    assert hidden == 1 and excluded == 35, (hidden, excluded)
    assert ("bot1", 333185169) in EXCLUDED_TABS
    assert tab_excluded("bot1", 1547332232) and tab_excluded("bot1", 643299192)
    assert tab_excluded("bot1", 1791345316) and tab_excluded("bot1", 1370617918)
    assert tab_excluded("bot1", 1024131985)
    for gid in (292208014, 2119398138, 1584345646, 1186940001, 501919598, 579853621,
                1308503061, 748431671, 254001772,
                1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, 1067583608,
                1924976898, 1978525586, 1255387315, 1522766504, 1163669375, 1576800449,
                1192979444, 1912487083, 664060481, 1778640335,
                1476428159, 1459587255, 687897000, 546391999, 195161343, 321339250, 2128724732, 189736118, 1981719475, 519508812, 45161413, 567489150, 601460115, 1024470894, 1771590089, 1566394978, 780786735, 248629477, 1965360503, 583946273, 327679061, 909478550, 1828414017, 902803117, 949971563, 1234832613, 727565635, 626951791, 673256883, 711603553, 254194955, 1328859831, 125248111, 208477863, 1834386145, 1635736949, 747711957, 378380409, 847095656, 1694432360, 1421725187, 1607076800, 1924940296, 1966044875, 1161030254):
        assert tab_excluded("bot1", gid), gid
    assert tab_excluded("bot2", 418894352)
    assert tab_excluded("bot2", 1794999542)
    assert tab_excluded("bot2", 971499761)
    assert tab_excluded("bot2", 1149583871)
    assert tab_excluded("bot2", 2047295555)
    assert tab_excluded("bot2", 84709656)
    assert untitled_chart_title("") and untitled_chart_title("Chart 2") and untitled_chart_title("Chart 14")
    assert not untitled_chart_title("Chart 01")
    assert not untitled_chart_title("Bitcoin denominated in Gold (BTC/Gold price per oz)")
    assert tab_excluded("bot1", 1476428159)  # 201 Nvidia data center revenue
    assert tab_excluded("bot1", 189736118)  # 191 Real gold price
    assert tab_excluded("bot1", 1981719475)  # 295 Tesla vs GM vs Ford margins
    assert tab_excluded("bot1", 519508812)  # 159 Uranium & nuclear vs S&P
    assert tab_excluded("bot1", 45161413)  # 45 Euro spreads vs Bunds
    assert tab_excluded("bot2", 1794999542)  # 56 ROIC Tesla vs legacy and EV
    assert tab_excluded("bot1", 567489150)  # 126 CCC vs BB spread
    assert tab_excluded("bot1", 601460115)  # 204 Cloud operating margins
    assert tab_excluded("bot1", 1024470894)  # 206 Data center chip revenue
    assert tab_excluded("bot1", 1771590089)  # 198 Musk Inc vs mega caps
    assert tab_excluded("bot1", 1566394978)  # 256 Uncle Sam's borrowing cost
    assert tab_excluded("bot1", 780786735)  # 35 S&P 500 in gold
    assert tab_excluded("bot1", 248629477)  # 73 Bank loan-to-deposit
    assert tab_excluded("bot2", 971499761)  # 74 Neocloud quarterly revenue
    assert tab_excluded("bot1", 1965360503)  # 337 US power sales by sector
    assert tab_excluded("bot1", 583946273)  # 184 Buffett indicator
    assert tab_excluded("bot1", 327679061)  # 131 Refining crack spread
    assert tab_excluded("bot1", 909478550)  # 215 Most profitable companies
    assert tab_excluded("bot1", 1828414017)  # 185 r minus g
    assert tab_excluded("bot1", 902803117)  # 182 Real fed funds rate
    assert tab_excluded("bot1", 949971563)  # 209 Oracle cash burn
    assert tab_excluded("bot1", 1234832613)  # 113 Discretionary vs Staples
    assert tab_excluded("bot1", 727565635)  # 125 Junk vs IG spread ratio
    assert tab_excluded("bot1", 626951791)  # 199 Margin debt vs S&P
    assert tab_excluded("bot1", 673256883)  # 38 Copper-gold vs 10Y
    assert tab_excluded("bot1", 711603553)  # 323 Alphabet quarterly FCF
    assert tab_excluded("bot1", 254194955)  # 177 Real GDP US vs EU vs Japan
    assert tab_excluded("bot2", 1149583871)  # 53 Dividends and buybacks vs legacy
    assert tab_excluded("bot2", 2047295555)  # 23 Nvidia commitments table
    assert tab_excluded("bot2", 84709656)  # 50 BEV share H1 2026
    assert tab_excluded("bot1", 1328859831)  # 213 Chip gross margins
    assert tab_excluded("bot1", 125248111)  # 326 Big Tech capex vs FCF
    assert tab_excluded("bot1", 208477863)  # 258 Trade policy uncertainty
    assert tab_excluded("bot1", 1834386145)  # 293 Home equity share
    assert tab_excluded("bot1", 1635736949)  # 311 Tesla cash vs debt
    assert tab_excluded("bot1", 747711957)  # 16 USDJPY vs US-JP spread
    assert tab_excluded("bot1", 378380409)  # 74 Real GDP vs GDI
    assert tab_excluded("bot2", 4940912)  # 79 Neocloud valuation table
    assert tab_excluded("bot2", 1188646727)  # 45 EV free cash flow Q2 2026
    assert tab_excluded("bot1", 847095656)  # 42 VIX vs realized vol
    assert tab_excluded("bot1", 1694432360)  # 275 Tech vs financials
    assert tab_excluded("bot1", 1421725187)  # 100 SPCX vs TSLA & Nasdaq (retired 2026-11-20)
    assert tab_excluded("bot1", 1607076800)  # 194 Nvidia vs Cisco 2000 (retired 2026-11-20)
    assert tab_excluded("bot1", 1924940296)  # 222 TSMC monthly revenue (retired 2026-11-20)
    assert tab_excluded("bot1", 1966044875)  # 179 Productivity vs pay gap (retired 2026-11-20)
    assert tab_excluded("bot1", 1161030254)  # 304 Tesla quarterly capex (retired 2026-11-20)
    assert tab_excluded("bot2", 841222865)  # 18 Big Tech unbooked DC leases (retired 2026-11-20)
    assert tab_excluded("bot1", 1540677114)  # 09 Real S&P 500 (retired 2026-11-21)
    assert tab_excluded("bot1", 100750083)  # 41 VIX term structure (retired 2026-11-21)
    assert tab_excluded("bot1", 1266156625)  # 46 UK-US spread vs GBP (retired 2026-11-21)
    assert tab_excluded("bot1", 2132226252)  # 63 Auto loans vs sales (retired 2026-11-21)
    assert tab_excluded("bot1", 722028700)  # 84 TSLA volatility vs peers (retired 2026-11-21)
    assert tab_excluded("bot1", 1483938898)  # 112 Transports vs Industrials (retired 2026-11-21)
    assert tab_excluded("bot1", 965739722)  # 205 Meta Reality Labs losses (retired 2026-11-21)
    assert tab_excluded("bot1", 1708270675)  # 218 AI stocks price-to-sales (retired 2026-11-21)
    assert tab_excluded("bot1", 995219650)  # 248 Low-hire, low-fire (retired 2026-11-21)
    assert tab_excluded("bot1", 1173797512)  # 333 Big Tech net PP&E (retired 2026-11-21)
    assert tab_excluded("bot1", 2026015579)  # 214 Big Tech operating margins (retired 2026-11-22)
    assert tab_excluded("bot1", 1254747964)  # 152 Capex as % of cash flow (retired 2026-11-22)
    assert tab_excluded("bot2", 1714490525)  # 9 MSTR preferreds table (retired 2026-11-22)
    assert tab_excluded("bot1", 440691485)  # 327 Cloud revenue vs capex (retired 2026-11-22)
    assert tab_excluded("bot1", 200447618)  # 52 Core goods vs services (retired 2026-11-22)
    assert tab_excluded("bot1", 895253454)  # 61 Real income vs saving (retired 2026-11-22)
    assert tab_excluded("bot1", 441584127)  # 305 Tesla free cash flow (retired 2026-11-22)
    assert tab_excluded("bot1", 1770579175)  # 217 Stock comp % of revenue (retired 2026-11-22)
    assert tab_excluded("bot1", 117148574)  # 53 Sticky vs flexible CPI (retired 2026-11-22)
    assert tab_excluded("bot2", 1152913412)  # 48 EV Q2 2026 scoreboard (retired 2026-11-22)
    assert tab_excluded("bot1", 730955080)  # 51 ECB real rate (retired 2026-11-22)
    assert tab_excluded("bot1", 700703307)  # 44 Baa-Aaa quality spread (retired 2026-11-22)
    assert tab_excluded("bot1", 992343092)  # 330 D&A vs revenue growth (retired 2026-11-23)
    assert tab_excluded("bot1", 609140004)  # 110 Small caps vs S&P 500 (retired 2026-11-23)
    assert tab_excluded("bot1", 1809420114)  # 11 HY spread vs VIX (retired 2026-11-23)
    assert tab_excluded("bot1", 829703707)  # 18 Electricity vs CPI (retired 2026-11-23)
    assert tab_excluded("bot2", 474108036)  # 20 Microsoft lease liabilities (retired 2026-11-23)
    assert tab_excluded("bot2", 1901004927)  # 58 Profitable quarters since 2021 (retired 2026-11-23)
    assert tab_excluded("bot1", 557813776)  # 129 60-40 rolling returns (retired 2026-11-23)
    assert tab_excluded("bot1", 1444647589)  # 289 Dogecoin vs Tesla (retired 2026-11-23)
    assert tab_excluded("bot1", 1754119588)  # 271 US oil output vs price (retired 2026-11-23)
    assert not tab_excluded("bot1", 1718545259)  # 54 Trimmed vs core PCE (kept)
    assert not tab_excluded("bot1", 1363241271)  # 321 Capex % of cash flow, qtrly (kept)
    assert not tab_excluded("bot1", 1085148301)  # 328 Cloud revenue per capex $ (kept)
    assert not tab_excluded("bot1", 978859168)  # 329 Cloud profit vs capex (kept)
    assert not tab_excluded("bot1", 577053605)  # 207 Hyperscaler free cash flow (kept)
    assert not tab_excluded("bot2", 1172553519)  # 4 MSTR preferred stack (kept)
    assert not tab_excluded("bot2", 1268488619)  # 7 MSTR preferred yields (kept)
    assert not tab_excluded("bot2", 1132545646)  # 10 MSTR convertibles table (kept)
    assert not tab_excluded("bot2", 2073270825)  # 44 EV deliveries Q3 2026 (kept)
    assert not tab_excluded("bot1", 733691549)  # 80 Productivity vs labor costs (kept)
    assert not tab_excluded("bot1", 1798867607)  # 08 Real home prices (kept)
    assert not tab_excluded("bot1", 639374194)  # 13 Real oil price (kept)
    assert not tab_excluded("bot1", 1220281045)  # 192 S&P 10-yr real returns (kept)
    assert not tab_excluded("bot1", 762190162)  # 82 TSLA vs Mag 7 (kept)
    assert not tab_excluded("bot1", 450495048)  # 83 TSLA drawdown (kept)
    assert not tab_excluded("bot1", 477052168)  # 195 BTC vs NVDA vs TSLA vol (kept)
    assert not tab_excluded("bot1", 62435828)  # 310 Tesla price-to-sales (kept)
    assert not tab_excluded("bot1", 1554167260)  # 335 Price-to-sales vs Cisco (kept)
    assert not tab_excluded("bot1", 1692929819)  # 325 Meta quarterly FCF (kept)
    assert not tab_excluded("bot3", 1828716508)  # Meta Active Users (kept)
    assert not tab_excluded("bot1", 868062827)  # 172 Job openings per jobless (kept)
    assert not tab_excluded("bot1", 1597166284)  # 249 Youth unemployment (kept)
    assert not tab_excluded("bot2", 2055282175)  # 22 Big Tech unbooked leases by co (kept)
    assert not tab_excluded("bot1", 171599627)
    assert not tab_excluded("bot1", 1027143376)
    assert not tab_excluded("bot1", 1718545259)  # 54 Trimmed vs core PCE
    assert not tab_excluded("bot1", 1892662496)
    assert not tab_excluded("bot1", 1167074374)  # 299 Real yield vs breakeven
    assert not tab_excluded("bot1", 1363241271)  # 321 Capex % of cash flow, qtrly
    assert tab_excluded("bot1", 1328859831)  # 213 Chip gross margins (retired 2026-11-18)
    assert tab_excluded("bot1", 1459587255)  # 227 Private AI lab valuations
    assert tab_excluded("bot1", 727565635)  # 125 Junk vs IG spread ratio
    assert not tab_excluded("bot1", 134436208)   # 203 Cloud growth race
    assert tab_excluded("bot1", 965739722)  # 205 Meta Reality Labs losses (retired 2026-11-21)
    assert not tab_excluded("bot1", 2092406374)  # 231 Prime-age employment rate
    assert not tab_excluded("bot3", 1536200109)  # Gold Priced in BTC (blank chart removed, tab stays)
    assert not tab_excluded("bot2", 645072508)
    assert not tab_excluded("bot2", 530554790)   # 75 CoreWeave backlog vs revenue
    assert not tab_excluded("bot2", 817552047)   # 73 US ETF BTC holdings
    assert not tab_excluded("bot2", 1793142140)  # 76 Neocloud capex vs revenue
    assert not tab_excluded("bot1", 1672892616)  # 34 BTC supply inflation
    assert not tab_excluded("bot1", 1415455632)  # 36 Gold vs real yield
    assert not tab_excluded("bot1", 364521704)   # 72 Corporate debt % GDP
    assert tab_excluded("bot1", 378380409)  # 74 Real GDP vs GDI (retired 2026-11-18)
    assert not tab_excluded("bot1", 2090456245)  # 130 Brent-WTI spread
    assert not tab_excluded("bot1", 1164178367)  # 132 Energy stocks vs oil
    assert not tab_excluded("bot1", 595180551)   # 183 Fed net liquidity vs S&P
    assert tab_excluded("bot1", 1828414017)  # 185 r minus g
    assert not tab_excluded("bot1", 345236488)   # 197 BTC 4-year rolling return
    assert tab_excluded("bot1", 626951791)  # 199 Margin debt vs S&P
    assert not tab_excluded("bot1", 577053605)   # 207 Hyperscaler free cash flow
    assert not tab_excluded("bot1", 1934181206)  # 255 2Y yield minus fed funds
    assert not tab_excluded("bot1", 900897940)   # 257 Federal debt % of GDP
    assert not tab_excluded("bot1", 1554167260)  # 335 Price-to-sales vs Cisco
    assert not tab_excluded("bot1", 310431964)   # 338 Utility capex boom
    assert not tab_excluded("bot2", 1690294676)  # 6 MSTR vs ASST leverage (kept; CotD card refreshed)
    assert not tab_excluded("bot2", 1713824403)  # 55 Capex intensity vs legacy
    assert not tab_excluded("bot2", 82966268)    # 57 EBITDA margin Tesla vs peers
    assert not tab_excluded("bot3", 978356289)   # Anthropic Revenue vs Compute
    titles = [c for t in tabs for c in t["charts"]]
    assert not any("trailing P/E" in c or "billing location" in c for c in titles)
    assert not any("data-center company" in c for c in titles)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cat = json.load(open(os.path.join(repo, "static", "sheets", "catalog.json"), encoding="utf-8"))
    live = {(t["book"], t["gid"]) for t in cat["tabs"]}
    for book, gid in EXCLUDED_TABS:
        assert (book, gid) not in live, (book, gid)
    for gid in (171599627, 1027143376, 1718545259, 397488583, 1892662496, 1167074374,
                1363241271, 2092406374):
        assert ("bot1", gid) in live, gid
    assert ("bot2", 1713824403) in live  # 55 Capex intensity vs legacy
    assert ("bot2", 645072508) in live
    assert ("bot2", 530554790) in live
    assert ("bot2", 817552047) in live  # 73 US ETF BTC holdings
    assert ("bot2", 1793142140) in live  # 76 Neocloud capex vs revenue
    assert ("bot2", 1690294676) in live  # 6 MSTR vs ASST leverage
    assert ("bot3", 978356289) in live
    assert ("bot3", 1618426207) in live  # ASST 30 Day Avg Volume
    assert ("bot3", 197035552) in live  # Waymo vs Tesla Robotaxi Fleets
    assert ("bot2", 855902063) in live  # 94 BTC treasury monthly flows
    assert ("bot3", 1408701012) in live  # Waymo vs Tesla Texas
    assert ("bot3", 9000000001) in live  # Tesla Robotaxi service area (sq mi)
    assert ("bot3", 9000000002) in live  # Strategy's accretive swaps, week by week
    assert ("bot3", 9000000003) in live  # Hyperscaler free cash flow (incl. SpaceX)
    assert ("bot3", 797114604) in live  # NASDAQ Returns by Year
    assert ("bot3", 819961031) in live  # BTC Returns by Year
    assert ("bot3", 318084443) in live  # SpaceX Free Cash Flow
    assert ("bot3", 1027111563) in live  # SpaceX Capex
    assert ("bot3", 1921102868) in live  # Share Count Change
    assert ("bot3", 970035917) in live  # Buybacks TTM
    assert ("bot3", 1608741572) not in live  # Bitcoin ETFs Weekly Flows (Python ETF draft)
    assert len(cat["tabs"]) == 421, len(cat["tabs"])
    assert sum(1 for t in cat["tabs"] if t.get("book") == "bot3") == 96
    assert sum(1 for t in cat["tabs"] if t.get("book") == "bot2") == 85
    blob = json.dumps(cat)
    assert "09 Real S&P 500" not in blob
    assert "41 VIX term structure" not in blob
    assert "46 UK-US spread vs GBP" not in blob
    assert "63 Auto loans vs sales" not in blob
    assert "84 TSLA volatility vs peers" not in blob
    assert "112 Transports vs Industrials" not in blob
    assert "205 Meta Reality Labs losses" not in blob
    assert "218 AI stocks price-to-sales" not in blob
    assert "248 Low-hire, low-fire" not in blob
    assert "333 Big Tech net PP&E" not in blob
    assert "201 Nvidia data center revenue" not in blob
    assert "Nvidia became a data-center company" not in blob
    assert "227 Private AI lab valuations" not in blob
    assert "Private AI lab valuations from funding rounds" not in blob
    assert "119 Emerging markets vs US" not in blob
    assert "Emerging markets vs US stocks" not in blob
    assert "86 TSLA vs Toyota+GM+Ford" not in blob
    assert "Tesla market cap vs Toyota, GM and Ford combined" not in blob
    assert "22 LTC & BCH in BTC" not in blob
    assert "Old altcoins priced in Bitcoin" not in blob
    assert "56 Food home vs away" not in blob
    assert "Groceries vs restaurants" not in blob
    assert "111 Value vs growth" not in blob
    assert "Russell 1000 Value" not in blob
    assert "191 Real gold price" not in blob
    assert "inflation-adjusted price vs the 1980 peak" not in blob
    assert "295 Tesla vs GM vs Ford margins" not in blob
    assert "Tesla's margin edge is gone" not in blob
    assert "159 Uranium & nuclear vs S&P" not in blob
    assert "Nuclear for AI power: uranium stocks vs the S&P 500" not in blob
    assert "45 Euro spreads vs Bunds" not in blob
    assert "Euro-area 10-year spreads over Germany" not in blob
    assert "56 ROIC Tesla vs legacy and EV" not in blob
    assert "Return on invested capital: BYD 12.4%" not in blob
    assert "126 CCC vs BB spread" not in blob
    assert "Junk-bond quality gap: CCC spread minus BB spread" not in blob
    assert "204 Cloud operating margins" not in blob
    assert "Cloud profitability: operating margin of AWS vs Google Cloud" not in blob
    assert "206 Data center chip revenue" not in blob
    assert "The AI chip scoreboard: quarterly data-center revenue" not in blob
    assert "198 Musk Inc vs mega caps" not in blob
    assert "market value since the SpaceX IPO" not in blob
    assert "256 Uncle Sam's borrowing cost" not in blob
    assert "average interest rate paid on federal debt vs the 10-year yield" not in blob
    assert "35 S&P 500 in gold" not in blob
    assert "The S&P 500 priced in gold (ounces per index unit)" not in blob
    assert "73 Bank loan-to-deposit" not in blob
    assert "US banks: loan-to-deposit ratio and loan growth" not in blob
    assert "74 Neocloud quarterly revenue" not in blob
    assert "Neocloud quarterly revenue ($M)" not in blob
    assert "337 US power sales by sector" not in blob
    assert "US electricity sales growth by sector: commercial leads" not in blob
    assert "184 Buffett indicator" not in blob
    assert "US stock market value ÷ GDP" not in blob
    assert "131 Refining crack spread" not in blob
    assert "3-2-1 crack spread" not in blob
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
    assert "81 CoreWeave margins" not in blob
    assert "CoreWeave margins (%)" not in blob
    assert "98 MSTR vs BTC drawdowns" not in blob
    assert "Strategy vs bitcoin: drawdown from record close" not in blob
    assert "151 Big Tech capex vs Nvidia" not in blob
    assert "223 Micron revenue & margin" not in blob
    assert "226 OpenAI vs Anthropic revenue" not in blob
    assert "263 Tariff pass-through" not in blob
    assert "Are tariffs showing up in prices? Import-heavy goods CPI vs core CPI (Jan 2025 = 100)" not in blob
    assert "213 Chip gross margins" not in blob
    assert "Who captures AI chip profits?" not in blob
    assert "326 Big Tech capex vs FCF" not in blob
    assert "Big Tech 4: quarterly capex has overtaken free cash flow" not in blob
    assert "258 Trade policy uncertainty" not in blob
    assert "Tariff shock in the news" not in blob
    assert "293 Home equity share" not in blob
    assert "Homeowners' equity as % of household real estate value" not in blob
    assert "311 Tesla cash vs debt" not in blob
    assert "Tesla cash & short-term investments vs debt, quarter ends" not in blob
    assert "16 USDJPY vs US-JP spread" not in blob
    assert "The yen has stopped following rate spreads" not in blob
    assert "74 Real GDP vs GDI" not in blob
    assert "Two measures of growth: real GDP vs real GDI" not in blob
    assert "79 Neocloud valuation table" not in blob
    assert "Neocloud valuation vs revenue run-rate" not in blob
    assert "45 EV free cash flow Q2 2026" not in blob
    assert "Q2 2026 free cash flow: Lucid" not in blob
    assert "42 VIX vs realized vol" not in blob
    assert "Fear premium: VIX vs actual S&P 500 volatility" not in blob
    assert "275 Tech vs financials" not in blob
    assert "Tech vs banks: Technology Select Sector" not in blob
    assert "12 Breakeven vs CPI" not in blob
    assert "122 Bond vol vs stock vol" not in blob
    assert "Bond market fear vs stock market fear: MOVE ÷ VIX" not in blob
    assert "232 Quits vs wage growth" not in blob
    assert "Job-hopping predicts pay raises: quits rate vs wage growth (Atlanta Fed tracker)" not in blob
    assert "162 Factory construction boom" not in blob
    assert "Chips Act & AI fabs: real US manufacturing construction spending" not in blob
    assert "153 Big Tech capex intensity" not in blob
    assert "Capex intensity: Big Tech capital spending as % of revenue (trailing 4Q)" not in blob
    assert "43 Nasdaq vs S&P ratio" not in blob
    assert "Tech leadership: Nasdaq Composite ÷ S&P 500" not in blob
    assert "109 Equal vs cap-weight S&P" not in blob
    assert "Equal-weight vs cap-weight S&P 500: relative total return (RSP ÷ SPY)" not in blob
    assert "135 Real coffee & cocoa" not in blob
    assert "Coffee and cocoa prices adjusted for inflation (Aug 2026 dollars)" not in blob
    assert "173 Continuing/initial claims" not in blob
    assert "Hard to find a new job: continuing claims per initial claim (4-week averages)" not in blob
    assert "239 Foreign vs native-born jobs" not in blob
    assert "employment of foreign-born vs native-born workers" not in blob
    assert "233 Older workers participation" not in blob
    assert "labor force participation of Americans 55+" not in blob
    assert "57 PPI vs CPI" not in blob
    assert "Pipeline inflation: producer prices vs consumer prices (YoY)" not in blob
    assert "55 Shelter vs home prices" not in blob
    assert "Home prices lead shelter inflation by ~1 year" not in blob
    assert "115 Regional banks vs S&P" not in blob
    assert "Regional banks vs the S&P 500 (KRE ÷ SPY, total return)" not in blob
    assert "128 Long bonds vs stocks DD" not in blob
    assert "The long-bond bear market: TLT vs SPY drawdowns" not in blob
    assert "186 Sahm rule recession gauge" not in blob
    assert "Sahm rule recession indicator" not in blob
    assert "225 Nvidia inventory & DSO" not in blob
    assert "Is Nvidia stuffing the channel?" not in blob
    assert "259 Financial conditions" not in blob
    assert "Financial conditions are loose:" not in blob
    assert "285 Ethereum L2s: Base leads" not in blob
    assert "Ethereum layer-2 race:" not in blob
    assert "322 Microsoft quarterly FCF" not in blob
    assert "Microsoft: operating cash flow, capex and free cash flow per quarter (USD bn)" not in blob
    assert "324 Amazon quarterly FCF" not in blob
    assert "Amazon: operating cash flow, capex and free cash flow per quarter (USD bn)" not in blob
    assert "Bitcoin correlation with gold" not in blob
    gold = next(t for t in cat["tabs"] if t["book"] == "bot3" and t["gid"] == 1536200109)
    assert [c["n"] for c in gold["chartList"]] == [1, 2]
    assert [c["title"] for c in gold["chartList"]] == [
        "Bitcoin denominated in Gold (BTC/Gold price per oz)",
        "Bitcoin denominated in Gold (BTC/Gold price per oz)",
    ]
    assert gold["charts"] == ["Bitcoin denominated in Gold (BTC/Gold price per oz)"]
    assert gold.get("thumb") == "sheets/thumbs/bot3/1536200109.jpg"
    names = {t["tab"] for t in cat["tabs"]}
    for keep in (
        "156 Nasdaq dot-com vs AI era",
        "229 Nvidia vs country GDPs",
        "334 Nvidia top-customer share",
        "75 CoreWeave backlog vs revenue",
        "97 MSTR vol vs BTC vol",
        "92 MSTR priced in BTC",
        "54 Trimmed vs core PCE",
        "68 Trade balance % GDP",
        "299 Real yield vs breakeven",
        "190 Work minutes gas & eggs",
        "192 S&P 10-yr real returns",
        "294 Median home in BTC & gold",
        "296 Stocks vs bonds, 10-yr",
        "157 Real chip prices",
        "160 China vs Mexico vs Taiwan",
        "321 Capex % of cash flow, qtrly",
        "55 Capex intensity vs legacy",
        "57 EBITDA margin Tesla vs peers",
        "203 Cloud growth race",
        "Anthropic Revenue vs Compute",
        "34 BTC supply inflation",
        "36 Gold vs real yield",
        "72 Corporate debt % GDP",
        "130 Brent-WTI spread",
        "132 Energy stocks vs oil",
        "183 Fed net liquidity vs S&P",
        "197 BTC 4-year rolling return",
        "207 Hyperscaler free cash flow",
        "255 2Y yield minus fed funds",
        "257 Federal debt % of GDP",
        "335 Price-to-sales vs Cisco",
        "338 Utility capex boom",
        "73 US ETF BTC holdings",
        "76 Neocloud capex vs revenue",
        "6 MSTR vs ASST leverage",
    ):
        assert keep in names, keep
    assert tab_excluded("bot2", 841222865)  # 18 Big Tech unbooked DC leases (retired 2026-11-20)
    today = json.load(open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8"))
    today_blob = json.dumps(today)
    # today.json is whatever edition is committed, not a frozen calendar date.
    edition = str(today.get("date") or "")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", edition), edition
    assert isinstance(today.get("picks"), list) and isinstance(today.get("items"), list)
    import cotd_build
    assert today.get("theme") == cotd_build.theme_line(len(today["items"]), len(today["picks"])), today.get("theme")
    assert "1547332232" not in today_blob and "643299192" not in today_blob
    assert "1791345316" not in today_blob and "1370617918" not in today_blob
    for gid in (292208014, 418894352, 2119398138, 1584345646, 1186940001, 501919598, 579853621,
                1308503061, 748431671, 254001772, 1536200109,
                1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, 1067583608,
                1924976898, 1978525586, 1255387315, 1522766504, 1163669375, 1576800449,
                1192979444, 1912487083, 664060481, 1778640335,
                1476428159, 1459587255, 687897000, 546391999, 195161343, 321339250, 2128724732, 189736118, 1981719475, 519508812, 45161413, 567489150, 601460115, 1024470894, 1771590089, 1566394978, 780786735, 248629477, 1965360503, 583946273, 327679061, 971499761, 909478550, 1828414017, 902803117, 949971563, 1234832613, 727565635, 626951791, 673256883, 711603553, 254194955, 1149583871, 2047295555, 84709656, 1328859831, 125248111, 208477863, 1834386145, 1635736949, 747711957, 378380409, 4940912, 1188646727, 847095656, 1694432360, 1421725187, 1607076800, 1924940296, 1966044875, 1161030254, 841222865):
        assert str(gid) not in today_blob, gid
    assert "161 Nvidia P-S and P-E" not in today_blob
    assert "202 Nvidia revenue by geography" not in today_blob
    assert "158 Korea & Taiwan vs S&P" not in today_blob
    assert "163 Supercore vs core CPI" not in today_blob
    assert "81 CoreWeave margins" not in today_blob
    assert "98 MSTR vs BTC drawdowns" not in today_blob
    assert "151 Big Tech capex vs Nvidia" not in today_blob
    assert "223 Micron revenue & margin" not in today_blob
    assert "226 OpenAI vs Anthropic revenue" not in today_blob
    assert "263 Tariff pass-through" not in today_blob
    assert "12 Breakeven vs CPI" not in today_blob
    assert "122 Bond vol vs stock vol" not in today_blob
    assert "232 Quits vs wage growth" not in today_blob
    assert "162 Factory construction boom" not in today_blob
    assert "153 Big Tech capex intensity" not in today_blob
    assert "43 Nasdaq vs S&P ratio" not in today_blob
    assert "109 Equal vs cap-weight S&P" not in today_blob
    assert "135 Real coffee & cocoa" not in today_blob
    assert "173 Continuing/initial claims" not in today_blob
    assert "239 Foreign vs native-born jobs" not in today_blob
    assert "233 Older workers participation" not in today_blob
    assert "57 PPI vs CPI" not in today_blob
    assert "Pipeline inflation: producer prices vs consumer prices" not in today_blob
    assert "55 Shelter vs home prices" not in today_blob
    assert "Home prices lead shelter inflation" not in today_blob
    assert "115 Regional banks vs S&P" not in today_blob
    assert "128 Long bonds vs stocks DD" not in today_blob
    assert "186 Sahm rule recession gauge" not in today_blob
    assert "225 Nvidia inventory & DSO" not in today_blob
    assert "Is Nvidia stuffing the channel?" not in today_blob
    assert "259 Financial conditions" not in today_blob
    assert "285 Ethereum L2s: Base leads" not in today_blob
    assert "322 Microsoft quarterly FCF" not in today_blob
    assert "324 Amazon quarterly FCF" not in today_blob
    assert "201 Nvidia data center revenue" not in today_blob
    assert "Nvidia became a data-center company" not in today_blob
    assert "227 Private AI lab valuations" not in today_blob
    assert "Private AI lab valuations from funding rounds" not in today_blob
    assert "119 Emerging markets vs US" not in today_blob
    assert "86 TSLA vs Toyota+GM+Ford" not in today_blob
    assert "22 LTC & BCH in BTC" not in today_blob
    assert "56 Food home vs away" not in today_blob
    assert "Groceries vs restaurants" not in today_blob
    assert "111 Value vs growth" not in today_blob
    assert "191 Real gold price" not in today_blob
    assert "295 Tesla vs GM vs Ford margins" not in today_blob
    assert "159 Uranium & nuclear vs S&P" not in today_blob
    assert "45 Euro spreads vs Bunds" not in today_blob
    assert "56 ROIC Tesla vs legacy and EV" not in today_blob
    assert "126 CCC vs BB spread" not in today_blob
    assert "204 Cloud operating margins" not in today_blob
    assert "206 Data center chip revenue" not in today_blob
    assert "The AI chip scoreboard" not in today_blob
    assert "198 Musk Inc vs mega caps" not in today_blob
    assert "256 Uncle Sam's borrowing cost" not in today_blob
    assert "35 S&P 500 in gold" not in today_blob
    assert "The S&P 500 priced in gold" not in today_blob
    assert "73 Bank loan-to-deposit" not in today_blob
    assert "74 Neocloud quarterly revenue" not in today_blob
    assert "337 US power sales by sector" not in today_blob
    assert "184 Buffett indicator" not in today_blob
    assert "131 Refining crack spread" not in today_blob
    assert "215 Most profitable companies" not in today_blob
    assert "185 r minus g" not in today_blob
    assert "182 Real fed funds rate" not in today_blob
    assert "209 Oracle cash burn" not in today_blob
    assert "113 Discretionary vs Staples" not in today_blob
    assert "125 Junk vs IG spread ratio" not in today_blob
    assert "199 Margin debt vs S&P" not in today_blob
    assert "38 Copper-gold vs 10Y" not in today_blob
    assert "323 Alphabet quarterly FCF" not in today_blob
    assert "177 Real GDP US vs EU vs Japan" not in today_blob
    assert "53 Dividends and buybacks vs legacy" not in today_blob
    assert "23 Nvidia commitments table" not in today_blob
    assert "50 BEV share H1 2026" not in today_blob
    assert "6 MSTR leverage" not in today_blob
    assert "Strategy's leverage, Jan 2025" not in today_blob
    assert not any(it.get("tab") == "6 MSTR vs ASST leverage" for it in today["items"])
    assert not any(it.get("title") == "Strategy vs Strive leverage: Strategy 31% of its bitcoin, Strive 65%" for it in today["items"])
    assert "Bitcoin correlation with gold" not in today_blob
    assert "Taiwan's chip stocks surge" not in today["theme"]
    assert "October CPI is due Tuesday" not in today["theme"]
    assert "Anthropic's IPO roadshow" not in today["theme"]
    assert "Nvidia and Micron drop off BofA" not in today["theme"]
    images = [it["image"] for it in today["items"] if it.get("image")]
    pick_images = [it["image"] for it in today["picks"] if it.get("image")]
    prefix = "cotd/%s/" % edition
    for name in images + pick_images:
        assert str(name).startswith(prefix), name
        assert os.path.isfile(os.path.join(repo, "static", name)), name
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
    def edition_pngs(date, absent, present):
        # Older editions are deleted as new days are published. Check a day only while its folder is still committed.
        folder = os.path.join(repo, "static", "cotd", date)
        if not os.path.isdir(folder):
            return
        for name in absent:
            assert not os.path.isfile(os.path.join(folder, name)), (date, name)
        for name in present:
            assert os.path.isfile(os.path.join(folder, name)), (date, name)
    edition_pngs("2026-11-10",
                 ("02.png", "03.png", "10.png", "12.png", "16.png", "17.png", "18.png"),
                 ("01.png", "04.png", "11.png", "13.png", "15.png", "19.png", "20.png"))
    edition_pngs("2026-11-11",
                 ("01.png", "07.png", "14.png", "15.png"),
                 ("02.png", "04.png", "08.png", "13.png", "16.png", "18.png", "20.png"))
    edition_pngs("2026-11-12",
                 ("06.png", "08.png", "10.png", "11.png", "12.png", "13.png", "18.png"),
                 ("01.png", "05.png", "07.png", "09.png", "14.png", "16.png", "19.png", "22.png", "25.png"))
    edition_pngs("2026-11-13",
                 ("01.png", "07.png", "08.png", "10.png", "13.png", "14.png", "16.png", "18.png", "21.png", "22.png"),
                 ("02.png", "03.png", "04.png", "05.png", "06.png", "09.png", "11.png", "12.png", "15.png", "17.png", "19.png", "20.png", "23.png", "24.png", "25.png"))
    edition_pngs("2026-11-14",
                 ("04.png", "05.png", "10.png", "15.png", "20.png", "21.png", "22.png"),
                 ("01.png", "02.png", "03.png", "06.png", "07.png", "08.png", "11.png", "12.png", "13.png", "14.png", "16.png", "17.png", "18.png", "19.png", "23.png", "24.png", "25.png"))
    edition_pngs("2026-11-15",
                 ("06.png", "07.png", "12.png", "14.png", "18.png", "19.png", "25.png"),
                 ("01.png", "02.png", "03.png", "04.png", "05.png", "08.png", "09.png", "10.png", "11.png", "13.png", "15.png", "16.png", "17.png", "20.png", "21.png", "22.png", "23.png", "24.png"))
    edition_pngs("2026-11-16",
                 ("02.png", "03.png", "04.png", "07.png", "09.png", "12.png", "17.png", "18.png", "19.png"),
                 ("01.png", "05.png", "06.png", "08.png", "10.png", "11.png", "13.png", "14.png", "15.png", "16.png", "20.png", "21.png", "22.png", "23.png", "24.png", "25.png"))
    edition_pngs("2026-11-17",
                 ("02.png", "06.png", "07.png", "09.png", "10.png", "12.png", "14.png", "16.png", "18.png", "20.png", "22.png", "23.png", "24.png"),
                 ("01.png", "03.png", "04.png", "05.png", "08.png", "11.png", "13.png", "15.png", "17.png", "19.png", "21.png", "25.png"))
    for rel in (
        "static/sheets/full/bot3/1536200109_2.png",
        "static/sheets/full/bot3/1536200109_3.png",
    ):
        assert not os.path.isfile(os.path.join(repo, rel)), rel
    for rel in (
        "static/sheets/full/bot3/1536200109.png",
        "static/sheets/thumbs/bot3/1536200109.jpg",
    ):
        assert os.path.isfile(os.path.join(repo, rel)), rel
    import sheets_thumbs
    from PIL import Image
    with Image.open(os.path.join(repo, "static/sheets/full/bot3/1536200109.png")) as gold_im:
        assert not sheets_thumbs.blank(gold_im)
    with Image.open(os.path.join(repo, "static/sheets/thumbs/bot3/1536200109.jpg")) as gold_thumb:
        assert not sheets_thumbs.blank(gold_thumb)
    for book, gid in EXCLUDED_TABS:
        for rel in (
            "static/sheets/full/%s/%d.png" % (book, gid),
            "static/sheets/thumbs/%s/%d.jpg" % (book, gid),
        ):
            assert not os.path.isfile(os.path.join(repo, rel)), rel
        full_dir = os.path.join(repo, "static", "sheets", "full", book)
        if os.path.isdir(full_dir):
            prefix = "%d" % gid
            for fn in os.listdir(full_dir):
                stem = fn[:-4] if fn.lower().endswith(".png") else ""
                assert stem != prefix and not stem.startswith(prefix + "_"), (book, fn)
    for rel in (
        "static/sheets/full/bot2/645072508.png",
        "static/sheets/thumbs/bot2/645072508.jpg",
        "static/sheets/full/bot2/530554790.png",
        "static/sheets/thumbs/bot2/530554790.jpg",
        "static/sheets/full/bot1/171599627.png",
        "static/sheets/full/bot1/1027143376.png",
        "static/sheets/full/bot1/1718545259.png",
        "static/sheets/full/bot1/397488583.png",
        "static/sheets/full/bot1/1892662496.png",
        "static/sheets/full/bot1/1167074374.png",
        "static/sheets/full/bot3/978356289.png",
    ):
        assert os.path.isfile(os.path.join(repo, rel)), rel
    scratch = tempfile.mkdtemp(prefix="excluded-assets-")
    try:
        keep_rels = [
            "static/sheets/full/bot1/1796631140.png",
            "static/sheets/full/bot1/171599627.png",
            "static/sheets/full/bot1/1892662496.png",
            "static/sheets/full/bot1/1167074374.png",
            "static/sheets/full/bot2/645072508.png",
            "static/sheets/full/bot2/530554790.png",
        ]
        drop_rels = []
        for book, gid in sorted(EXCLUDED_TABS):
            drop_rels.append("static/sheets/full/%s/%d.png" % (book, gid))
            drop_rels.append("static/sheets/full/%s/%d_2.png" % (book, gid))
            drop_rels.append("static/sheets/thumbs/%s/%d.jpg" % (book, gid))
        for rel in keep_rels + drop_rels:
            path = os.path.join(scratch, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x")
        gone = prune_excluded_assets(scratch)
        for rel in keep_rels:
            assert os.path.isfile(os.path.join(scratch, rel)), rel
        for rel in drop_rels:
            assert not os.path.exists(os.path.join(scratch, rel)), rel
        for book, gid in EXCLUDED_TABS:
            assert any(str(gid) in r for r in gone), gid
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    debt = next(t for t in cat["tabs"] if t["book"] == "bot3" and t["gid"] == 1145735379)
    assert [c["n"] for c in debt["chartList"]] == [1, 2, 3, 4, 5]
    assert [c["title"] for c in debt["chartList"]] == [
        "Strategy bitcoin on balance sheet",
        "Strategy BTC Holdings Q/Q Change",
        "Strategy BTC/Share (in Sats)",
        "MSTR mNAV",
        "MSTR Market Cap vs. BTC Reserve Value",
    ]
    assert debt["charts"] == [c["title"] for c in debt["chartList"]]
    rec = next(t for t in cat["tabs"] if t["book"] == "bot3" and t["gid"] == 729377719)
    assert [c["title"] for c in rec["chartList"]] == [
        "MSTR Amplification",
        "MSTR Net BTC per share (sats)",
        "mNAV = MSTR market price / Net BTC per share ($)",
    ]
    assert rec["charts"] == [c["title"] for c in rec["chartList"]]
    # That Q/Q chart was in the 2026-11-12 edition. Skip once that edition has been pruned.
    cards_path = os.path.join(repo, "static", "cotd", "2026-11-12", "cards.json")
    if os.path.isfile(cards_path):
        cards = json.load(open(cards_path, encoding="utf-8"))
        qq = [it for it in (cards.get("items") or []) if it.get("title") == "Strategy BTC Holdings Q/Q Change"]
        assert [it.get("chart_n") for it in qq] == [2], qq
        card_ns = []
        for key in ("items", "random", "picks"):
            for it in cards.get(key) or []:
                if isinstance(it, dict) and it.get("gid") == 1145735379 and it.get("title") == "Strategy BTC Holdings Q/Q Change":
                    card_ns.append((key, it.get("chart_n")))
        assert card_ns == [("items", 2), ("random", 2)], card_ns
    # Position order, not spreadsheets.get charts[] order. pngChartsSorted_ is
    # anchor row, then column, then the original index.
    def placed(title, row, col):
        anchor = {}
        if row:
            anchor["rowIndex"] = row
        if col:
            anchor["columnIndex"] = col
        return {"spec": {"title": title}, "position": {"overlayPosition": {"anchorCell": anchor}}}
    cab = {"charts": [placed("C", 10, 0), placed("A", 1, 0), placed("B", 2, 0)]}
    assert [c["title"] for c in chart_list(cab)] == ["A", "B", "C"]
    assert [c["n"] for c in chart_list(cab)] == [1, 2, 3]
    assert chart_titles(cab) == ["A", "B", "C"]
    # Same row: column order, and a same-cell tie keeps the original index.
    tied = {"charts": [placed("Right", 5, 4), placed("Left", 5, 0), placed("RightLater", 5, 4)]}
    assert [c["title"] for c in chart_list(tied)] == ["Left", "Right", "RightLater"]
    # MSTR Debt shape: the first inserted chart sits last on the sheet.
    mstr = {"charts": [
        placed("MSTR mNAV", 60, 0),
        placed("Strategy bitcoin on balance sheet", 0, 0),
        placed("Strategy BTC Holdings Q/Q Change", 20, 0),
        placed("Strategy BTC/Share (in Sats)", 40, 0),
    ]}
    assert [c["title"] for c in chart_list(mstr)] == [
        "Strategy bitcoin on balance sheet",
        "Strategy BTC Holdings Q/Q Change",
        "Strategy BTC/Share (in Sats)",
        "MSTR mNAV",
    ]
    assert chart_titles(mstr) == [c["title"] for c in chart_list(mstr)]
    assert chart_list({"charts": [{"spec": {"title": "Only"}}]}) == [{"n": 1, "title": "Only"}]
    assert chart_list({"charts": []}) == []
    missing = {"charts": [{"spec": {"title": "First"}}, {"spec": {"title": "Second"}}]}
    assert chart_list(missing) is None and chart_titles(missing) is None
    partial = {"charts": [placed("Has", 1, 0), {"spec": {"title": "Missing"}}]}
    assert chart_list(partial) is None
    # An excluded multi-chart tab is still excluded when it has no position.
    _b, rows, _hidden, excluded_n = one({
        "spreadsheetId": sid,
        "properties": {"title": "Bot experiments"},
        "sheets": [
            {"properties": {"title": "87 TSLA beta to Nasdaq", "sheetId": 333185169, "index": 0},
             "charts": [{"spec": {"title": "A"}}, {"spec": {"title": "B"}}]},
            {"properties": {"title": "kept", "sheetId": 999, "index": 1},
             "charts": [{"spec": {"title": "Only"}}]},
        ],
    })
    assert excluded_n == 1 and [t["gid"] for t in rows] == [999], rows
    assert rows[0]["chartList"] == [{"n": 1, "title": "Only"}]
    assert not any(t.get("_unpositioned") for t in rows)
    # Importer still skips by catalog n, which is the PNG (n) suffix. No importer change.
    import import_full_pngs
    listed = chart_list(mstr)
    skip_tabs = [{"book": "bot3", "gid": 1145735379, "chartList": listed}]
    for n in (1, 2, 3, 4):
        assert not import_full_pngs.skip_chart_image(skip_tabs, "bot3", 1145735379, n), n
    assert import_full_pngs.skip_chart_image(skip_tabs, "bot3", 1145735379, 5)
    untitled = {"charts": [placed("Chart 2", 1, 0), placed("Kept", 3, 0)]}
    utabs = [{"book": "bot3", "gid": 9, "chartList": chart_list(untitled)}]
    assert import_full_pngs.skip_chart_image(utabs, "bot3", 9, 1)
    assert not import_full_pngs.skip_chart_image(utabs, "bot3", 9, 2)
    # A title-only dump must not undo a position-order catalog. A dump that
    # carries anchors must number a wrong catalog into position order.
    bot3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
    debt_titles = [c["title"] for c in listed]
    debt_list = [{"n": i, "title": title} for i, title in enumerate(debt_titles, 1)]
    api_titles = [
        "MSTR mNAV",
        "Strategy bitcoin on balance sheet",
        "Strategy BTC Holdings Q/Q Change",
        "Strategy BTC/Share (in Sats)",
    ]
    api_list = [{"n": i, "title": title} for i, title in enumerate(api_titles, 1)]
    rec_titles = [
        "MSTR Amplification",
        "MSTR Net BTC per share (sats)",
        "mNAV = MSTR market price / Net BTC per share ($)",
    ]
    rec_list = [{"n": i, "title": title} for i, title in enumerate(rec_titles, 1)]
    rec_api = [
        "mNAV = MSTR market price / Net BTC per share ($)",
        "MSTR Net BTC per share (sats)",
        "MSTR Amplification",
    ]
    def sheet_row(title, gid, charts):
        return {"properties": {"title": title, "sheetId": gid, "index": gid}, "charts": charts}
    bare = [
        sheet_row("MSTR Debt", 1145735379, [{"spec": {"title": t}} for t in api_titles]),
        sheet_row("MSTR Debt recording", 729377719, [{"spec": {"title": t}} for t in rec_api]),
        sheet_row("Solo", 42, [{"spec": {"title": "Only chart"}}]),
        sheet_row("New Pair", 77, [{"spec": {"title": "Right"}}, {"spec": {"title": "Left"}}]),
    ]
    positioned = [
        sheet_row("MSTR Debt", 1145735379, mstr["charts"]),
        sheet_row("MSTR Debt recording", 729377719, [
            placed(rec_api[0], 40, 0),
            placed(rec_api[1], 20, 0),
            placed(rec_api[2], 0, 0),
        ]),
        sheet_row("Solo", 42, [{"spec": {"title": "Only chart"}}]),
        sheet_row("New Pair", 77, [placed("Right", 1, 5), placed("Left", 1, 0)]),
    ]
    order_repo = tempfile.mkdtemp(prefix="chart-order-")
    try:
        cat_dir = os.path.join(order_repo, "static", "sheets")
        os.makedirs(cat_dir)
        thumb_rel = "sheets/thumbs/bot3/1145735379.jpg"
        thumb_path = os.path.join(order_repo, "static", thumb_rel)
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        with open(thumb_path, "wb") as f:
            f.write(b"x")
        def write_prev(debt_rows, debt_charts, rec_rows=None, rec_charts=None):
            prev = {
                "books": [{"id": "bot3", "name": "My Bot's Sheets", "spreadsheetId": bot3}],
                "tabs": [
                    {"book": "bot3", "tab": "MSTR Debt", "gid": 1145735379,
                     "charts": debt_charts, "chartList": debt_rows, "thumb": thumb_rel},
                    {"book": "bot3", "tab": "MSTR Debt recording", "gid": 729377719,
                     "charts": rec_titles if rec_charts is None else rec_charts,
                     "chartList": rec_list if rec_rows is None else rec_rows},
                    {"book": "bot3", "tab": "Solo", "gid": 42,
                     "charts": ["Old"], "chartList": [{"n": 1, "title": "Old"}]},
                ],
            }
            with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump(prev, f)
        def write_dump(name, sheets):
            path = os.path.join(order_repo, name)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"spreadsheetId": bot3, "properties": {"title": "My Bot's Sheets"}, "sheets": sheets}, f)
            return path
        write_prev(debt_list, debt_titles)
        bare_path = write_dump("bare.json", bare)
        built, warns = build_catalog([bare_path], order_repo, allow_partial=True)
        by_gid = {t["gid"]: t for t in built["tabs"]}
        assert [c["title"] for c in by_gid[1145735379]["chartList"]] == debt_titles
        assert [c["title"] for c in by_gid[729377719]["chartList"]] == rec_titles
        assert by_gid[1145735379].get("thumb") == thumb_rel
        assert by_gid[42]["tab"] == "Solo"
        assert by_gid[42]["chartList"] == [{"n": 1, "title": "Only chart"}]
        assert 77 not in by_gid
        assert not any("_unpositioned" in t for t in built["tabs"])
        assert any("1145735379" in w and "kept the existing catalog entry" in w for w in warns), warns
        assert any("729377719" in w and "kept the existing catalog entry" in w for w in warns), warns
        assert any("77" in w and "left out" in w for w in warns), warns
        rec_api_list = [{"n": i, "title": title} for i, title in enumerate(rec_api, 1)]
        write_prev(api_list, api_titles, rec_api_list, rec_api)
        pos_path = write_dump("positioned.json", positioned)
        built2, warns2 = build_catalog([pos_path], order_repo, allow_partial=True)
        by_gid2 = {t["gid"]: t for t in built2["tabs"]}
        assert [c["title"] for c in by_gid2[1145735379]["chartList"]] == debt_titles
        assert [c["n"] for c in by_gid2[1145735379]["chartList"]] == [1, 2, 3, 4]
        assert by_gid2[1145735379]["charts"] == debt_titles
        assert [c["title"] for c in by_gid2[729377719]["chartList"]] == rec_titles
        assert [c["title"] for c in by_gid2[77]["chartList"]] == ["Left", "Right"]
        # The fixture dump is bot3-only and does not include the pinned tabs.
        # Restoring them warns; that is the pinned-tab behavior, not a bad rebuild.
        pinned_warns = {
            "bot2 gid 855902063 (94 BTC treasury monthly flows): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 197035552 (Waymo vs Tesla Robotaxi Fleets): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 1408701012 (Waymo vs Tesla Texas): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 874288811 (Central Bank Ranking): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 797114604 (NASDAQ Returns by Year): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 819961031 (BTC Returns by Year): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 9000000001 (Tesla Robotaxi service area (sq mi)): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 9000000002 (Strategy's accretive swaps, week by week): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 9000000003 (Hyperscaler free cash flow (incl. SpaceX)): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 318084443 (SpaceX Free Cash Flow): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 1027111563 (SpaceX Capex): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 1921102868 (Share Count Change): missing from dump and no existing catalog entry; added the pinned table tab",
            "bot3 gid 970035917 (Buybacks TTM): missing from dump and no existing catalog entry; added the pinned table tab",
        }
        assert set(warns2) == pinned_warns, warns2
        assert by_gid2[1145735379].get("thumb") == thumb_rel
    finally:
        shutil.rmtree(order_repo, ignore_errors=True)
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
    build_catalog(a.dumps, a.repo, a.allow_partial)

if __name__ == "__main__": main()
