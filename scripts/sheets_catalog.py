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
# Mark deleted the following tabs on 2026-11-10. Add another ("book", gid) here
# when he retires a tab. Catalog rebuild, thumbnail refresh, full-PNG import,
# Charts of the day, and favorites all read this set.
# ("bot2", 418894352) "81 CoreWeave margins", chart "CoreWeave margins (%)"
# (chartId 1921376754).
# ("bot1", 292208014) "98 MSTR vs BTC drawdowns", chart
# "Strategy vs bitcoin: drawdown from record close" (chartId 1252176570).
# ("bot1", 2119398138) "151 Big Tech capex vs Nvidia", chart
# "AI spending loop: Microsoft+Alphabet+Amazon+Meta capex vs Nvidia revenue (trailing 4 quarters)"
# (chartId 140779756).
# ("bot1", 1584345646) "223 Micron revenue & margin", chart
# "The AI memory boom (HBM): Micron quarterly revenue and gross margin" (chartId 1400358015).
# ("bot1", 1186940001) "226 OpenAI vs Anthropic revenue", chart
# "Reported revenue run-rates: OpenAI vs Anthropic (company statements & media; log scale)"
# (chartId 118363695).
# ("bot1", 501919598) "263 Tariff pass-through", chart
# "Are tariffs showing up in prices? Import-heavy goods CPI vs core CPI (Jan 2025 = 100)"
# (chartId 1223359242).
# ("bot1", 579853621) "12 Breakeven vs CPI", chart
# "Inflation 3.35% vs market's 10-year expectation 2.29%" (chartId 905132998).
# Mark deleted the following tabs on 2026-11-11.
# ("bot1", 1308503061) "122 Bond vol vs stock vol", chart
# "Bond market fear vs stock market fear: MOVE ÷ VIX" (chartId 1366682353).
# ("bot1", 748431671) "232 Quits vs wage growth", chart
# "Job-hopping predicts pay raises: quits rate vs wage growth (Atlanta Fed tracker)"
# (chartId 938561994).
# ("bot1", 254001772) "162 Factory construction boom", chart
# "Chips Act & AI fabs: real US manufacturing construction spending" (chartId 1210694775).
# Mark retired the following tabs on 2026-11-12. They must not be rebuilt or shown.
# ("bot1", 1238930141) "153 Big Tech capex intensity", chart
# "Capex intensity: Big Tech capital spending as % of revenue (trailing 4Q)".
# ("bot1", 1835714359) "43 Nasdaq vs S&P ratio", chart
# "Tech leadership: Nasdaq Composite ÷ S&P 500".
# ("bot1", 492285771) "109 Equal vs cap-weight S&P", chart
# "Equal-weight vs cap-weight S&P 500: relative total return (RSP ÷ SPY)".
# ("bot1", 220064271) "135 Real coffee & cocoa", chart
# "Coffee and cocoa prices adjusted for inflation (Aug 2026 dollars)".
# ("bot1", 79264442) "173 Continuing/initial claims", chart
# "Hard to find a new job: continuing claims per initial claim (4-week averages)".
# ("bot1", 1755343475) "239 Foreign vs native-born jobs", chart
# "Immigration and jobs: employment of foreign-born vs native-born workers (12-month average, Jan 2020 = 100)".
# ("bot1", 1067583608) "233 Older workers participation", chart
# "Retirement wave: labor force participation of Americans 55+ vs prime-age (25–54)".
EXCLUDED_TABS = {
    ("bot1", 333185169),
    ("bot1", 1547332232),
    ("bot1", 643299192),
    ("bot1", 1791345316),
    ("bot1", 1370617918),
    ("bot1", 1024131985),
    ("bot2", 418894352),
    ("bot1", 292208014),
    ("bot1", 2119398138),
    ("bot1", 1584345646),
    ("bot1", 1186940001),
    ("bot1", 501919598),
    ("bot1", 579853621),
    ("bot1", 1308503061),
    ("bot1", 748431671),
    ("bot1", 254001772),
    ("bot1", 1238930141),
    ("bot1", 1835714359),
    ("bot1", 492285771),
    ("bot1", 220064271),
    ("bot1", 79264442),
    ("bot1", 1755343475),
    ("bot1", 1067583608),
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
            sheet("52 Core goods vs services", 200447618,
                  "Core goods vs core services inflation (CPI, YoY)"),
            sheet("67 Effective tariff rate", 972156540,
                  "US effective tariff rate: customs duties as % of goods imports"),
            sheet("hidden junk", 5, "nope", hidden=True),
        ],
    }
    _book, tabs, hidden, excluded = one(doc)
    gids = [t["gid"] for t in tabs]
    assert gids == [1476428159, 999, 171599627, 1027143376, 200447618, 972156540], gids
    assert hidden == 1 and excluded == 22, (hidden, excluded)
    assert ("bot1", 333185169) in EXCLUDED_TABS
    assert tab_excluded("bot1", 1547332232) and tab_excluded("bot1", 643299192)
    assert tab_excluded("bot1", 1791345316) and tab_excluded("bot1", 1370617918)
    assert tab_excluded("bot1", 1024131985)
    for gid in (292208014, 2119398138, 1584345646, 1186940001, 501919598, 579853621,
                1308503061, 748431671, 254001772,
                1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, 1067583608):
        assert tab_excluded("bot1", gid), gid
    assert tab_excluded("bot2", 418894352)
    assert untitled_chart_title("") and untitled_chart_title("Chart 2") and untitled_chart_title("Chart 14")
    assert not untitled_chart_title("Chart 01")
    assert not untitled_chart_title("Bitcoin denominated in Gold (BTC/Gold price per oz)")
    assert not tab_excluded("bot1", 1476428159)
    assert not tab_excluded("bot1", 171599627)
    assert not tab_excluded("bot1", 1027143376)
    assert not tab_excluded("bot1", 200447618)
    assert not tab_excluded("bot1", 829703707)   # 18 Electricity vs CPI
    assert not tab_excluded("bot1", 972156540)
    assert not tab_excluded("bot1", 1167074374)  # 299 Real yield vs breakeven
    assert not tab_excluded("bot1", 1254747964)  # 152 Capex as % of cash flow
    assert not tab_excluded("bot1", 1328859831)  # 213 Chip gross margins
    assert not tab_excluded("bot1", 1459587255)  # 227 Private AI lab valuations
    assert not tab_excluded("bot1", 727565635)   # 125 Junk vs IG spread ratio
    assert not tab_excluded("bot1", 2092406374)  # 231 Prime-age employment rate
    assert not tab_excluded("bot3", 1536200109)  # Gold Priced in BTC (blank chart removed, tab stays)
    assert not tab_excluded("bot2", 645072508)
    assert not tab_excluded("bot2", 530554790)   # 75 CoreWeave backlog vs revenue
    assert not tab_excluded("bot2", 1713824403)  # 55 Capex intensity vs legacy
    assert not tab_excluded("bot3", 978356289)   # Anthropic Revenue vs Compute
    titles = [c for t in tabs for c in t["charts"]]
    assert not any("trailing P/E" in c or "billing location" in c for c in titles)
    assert any("data-center company" in c for c in titles)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cat = json.load(open(os.path.join(repo, "static", "sheets", "catalog.json"), encoding="utf-8"))
    live = {(t["book"], t["gid"]) for t in cat["tabs"]}
    for book, gid in EXCLUDED_TABS:
        assert (book, gid) not in live, (book, gid)
    for gid in (171599627, 1027143376, 200447618, 829703707, 972156540, 1167074374,
                1254747964, 1328859831, 1459587255, 727565635, 2092406374):
        assert ("bot1", gid) in live, gid
    assert ("bot2", 1713824403) in live  # 55 Capex intensity vs legacy
    assert ("bot2", 645072508) in live
    assert ("bot2", 530554790) in live
    assert ("bot3", 978356289) in live
    assert len(cat["tabs"]) == 487, len(cat["tabs"])
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
    assert "81 CoreWeave margins" not in blob
    assert "CoreWeave margins (%)" not in blob
    assert "98 MSTR vs BTC drawdowns" not in blob
    assert "Strategy vs bitcoin: drawdown from record close" not in blob
    assert "151 Big Tech capex vs Nvidia" not in blob
    assert "223 Micron revenue & margin" not in blob
    assert "226 OpenAI vs Anthropic revenue" not in blob
    assert "263 Tariff pass-through" not in blob
    assert "Are tariffs showing up in prices? Import-heavy goods CPI vs core CPI (Jan 2025 = 100)" not in blob
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
        "201 Nvidia data center revenue",
        "194 Nvidia vs Cisco 2000",
        "225 Nvidia inventory & DSO",
        "229 Nvidia vs country GDPs",
        "334 Nvidia top-customer share",
        "19 Nvidia supply commitments",
        "23 Nvidia commitments table",
        "75 CoreWeave backlog vs revenue",
        "97 MSTR vol vs BTC vol",
        "92 MSTR priced in BTC",
        "52 Core goods vs services",
        "18 Electricity vs CPI",
        "67 Effective tariff rate",
        "299 Real yield vs breakeven",
        "152 Capex as % of cash flow",
        "55 Capex intensity vs legacy",
        "213 Chip gross margins",
        "227 Private AI lab valuations",
        "Anthropic Revenue vs Compute",
    ):
        assert keep in names, keep
    today = json.load(open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8"))
    today_blob = json.dumps(today)
    assert today["date"] == "2026-11-12"
    assert len(today["picks"]) == 5, len(today["picks"])
    assert len(today["items"]) == 13, len(today["items"])
    assert today["theme"] == "5 picks, plus 13 items drawn at random from the sheet library."
    assert "1547332232" not in today_blob and "643299192" not in today_blob
    assert "1791345316" not in today_blob and "1370617918" not in today_blob
    for gid in (292208014, 418894352, 2119398138, 1584345646, 1186940001, 501919598, 579853621,
                1308503061, 748431671, 254001772, 1536200109,
                1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, 1067583608):
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
    assert "Chart 2" not in today_blob
    assert "Bitcoin correlation with gold" not in today_blob
    assert "Taiwan's chip stocks surge" not in today["theme"]
    assert "October CPI is due Tuesday" not in today["theme"]
    assert "Anthropic's IPO roadshow" not in today["theme"]
    assert "Nvidia and Micron drop off BofA" not in today["theme"]
    images = [it["image"] for it in today["items"] if it.get("image")]
    for name in ("06.png", "08.png", "10.png", "11.png", "12.png", "13.png", "18.png"):
        assert "cotd/2026-11-12/" + name not in images, name
    for name in ("07.png", "09.png", "14.png", "15.png", "16.png", "19.png", "20.png", "22.png", "23.png", "24.png", "25.png"):
        assert "cotd/2026-11-12/" + name in images, name
    pick_images = [it["image"] for it in today["picks"] if it.get("image")]
    for name in ("01.png", "02.png", "03.png", "04.png", "05.png"):
        assert "cotd/2026-11-12/" + name in pick_images, name
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
    for name in ("02.png", "03.png", "10.png", "12.png", "16.png", "17.png", "18.png"):
        assert not os.path.isfile(os.path.join(repo, "static/cotd/2026-11-10", name)), name
    for name in ("01.png", "04.png", "11.png", "13.png", "15.png", "19.png", "20.png"):
        assert os.path.isfile(os.path.join(repo, "static/cotd/2026-11-10", name)), name
    for name in ("01.png", "07.png", "14.png", "15.png"):
        assert not os.path.isfile(os.path.join(repo, "static/cotd/2026-11-11", name)), name
    for name in ("02.png", "04.png", "08.png", "13.png", "16.png", "18.png", "20.png"):
        assert os.path.isfile(os.path.join(repo, "static/cotd/2026-11-11", name)), name
    for name in ("06.png", "08.png", "10.png", "11.png", "12.png", "13.png", "18.png"):
        assert not os.path.isfile(os.path.join(repo, "static/cotd/2026-11-12", name)), name
    for name in ("01.png", "05.png", "07.png", "09.png", "14.png", "16.png", "19.png", "22.png", "25.png"):
        assert os.path.isfile(os.path.join(repo, "static/cotd/2026-11-12", name)), name
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
        "static/sheets/full/bot1/200447618.png",
        "static/sheets/full/bot1/829703707.png",
        "static/sheets/full/bot1/972156540.png",
        "static/sheets/full/bot1/1167074374.png",
        "static/sheets/full/bot3/978356289.png",
    ):
        assert os.path.isfile(os.path.join(repo, rel)), rel
    scratch = tempfile.mkdtemp(prefix="excluded-assets-")
    try:
        keep_rels = [
            "static/sheets/full/bot1/1476428159.png",
            "static/sheets/full/bot1/171599627.png",
            "static/sheets/full/bot1/972156540.png",
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
