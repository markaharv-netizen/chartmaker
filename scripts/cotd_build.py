#!/usr/bin/env python3
"""Build a "Charts of the day" edition for Chartmaker.  Runs on the box, not on Render.

Each edition targets 20 charts and accepts 1 through 20 items inclusive. A shorter
list is allowed so the edition is never padded with missing or inadequate charts.
More than 20 items is an error. Every item is published; the script does not
truncate the list (the old gate required exactly 10, then exactly 20).

Usage:  python3 scripts/cotd_build.py picks.json [--repo /workspace/chartmaker] [--keep-old N]

picks.json:
{
  "date": "2026-10-21",                      # edition date (YYYY-MM-DD)
  "theme": "one-line theme",
  "items": [                                  # 1 through 20 inclusive (target 20)
    {"spreadsheetId": "...", "sheet": "Bot experiments", "tab": "315 SpaceX revenue by segment",
     "gid": 399190070,                        # tab sheetId (for the 'open in sheet' link)
     "chart_n": 1,                            # optional. 1-based chart on that tab (catalog chartList
                                              # order; chart 1 is <gid>.png, chart 2 is <gid>_2.png).
                                              # Set it when the tab has more than one chart and you know
                                              # which panel the PNG is. Omitted when it cannot be known.
     "png": "/workspace/cotd_src/01.png",     # chart PNG already on the box (see COTD_README.md)
     "title": "...", "picked": "...", "matters": "...", "know": "..."}
  ]
}
Each PNG is checked (not blank / not a single colour), fitted onto a white 1600x900 canvas, and
written to static/cotd/<date>/NN.png; static/cotd/today.json is then rewritten.
"""
import argparse, json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_catalog

W, H = 1600, 900
# Target 20 charts. Accept 1 through 20 so a short, honest list is never padded.
EDITION_MIN = 1
EDITION_MAX = 20
EDITION_SIZE = EDITION_MAX
# Charts of the day only. These tabs stay in the sheet catalog, full PNGs, and thumbnails.
# ("bot2", 645072508) is BX2-069, tab "69 Mark MSTR red dot tracker", chart
# "Mark's Red Dot Tracker: MSTR". The tab still exists. It is retired from this edition only.
COTD_EXCLUDED = {
    ("bot2", 645072508),
}

def check(im, path):
    from PIL import ImageStat
    g = im.convert("L"); sd = ImageStat.Stat(g).stddev[0]
    cols = im.convert("RGB").getcolors(1 << 22) or []
    if sd < 5 or len(cols) < 12:
        raise SystemExit(f"BLANK/empty-looking chart image: {path} (stddev {sd:.1f}, {len(cols)} colours). "
                         "Combo charts often export blank from Apps Script; use the fallback PNG (see README).")

def fit(src, dst):
    from PIL import Image
    im = Image.open(src); im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA"); bg = Image.new("RGBA", im.size, "white"); bg.alpha_composite(im); im = bg
    im = im.convert("RGB"); check(im, src)
    s = min(W / im.width, H / im.height)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    c = Image.new("RGB", (W, H), "white"); c.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    c.save(dst, optimize=True)
    return os.path.getsize(dst)

def pick_excluded(item):
    """True when this Charts of the day pick must not be published."""
    sid = str(item.get("spreadsheetId") or "").strip()
    gid = item.get("gid")
    if gid in (None, ""):
        return False
    pair = sheets_catalog.BY_ID.get(sid)
    if not pair:
        return False
    book = pair[0]
    if sheets_catalog.tab_excluded(book, gid):
        return True
    try:
        return (book, int(gid)) in COTD_EXCLUDED
    except (TypeError, ValueError):
        return False

def norm_title(s):
    return " ".join(str(s or "").split())

def load_catalog(repo):
    """Book id by spreadsheet id, and tabs by (book, gid). None when this repo has no catalog."""
    path = os.path.join(repo, "static", "sheets", "catalog.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    books = {}
    for b in doc.get("books") or []:
        if isinstance(b, dict) and b.get("spreadsheetId") and b.get("id"):
            books[str(b["spreadsheetId"])] = str(b["id"])
    tabs = {}
    for t in doc.get("tabs") or []:
        if not isinstance(t, dict):
            continue
        try:
            gid = int(t.get("gid"))
        except (TypeError, ValueError):
            continue
        tabs[(str(t.get("book")), gid)] = t
    return {"books": books, "tabs": tabs}

def chart_rows(tab):
    """Catalog charts in chartList order, with the same labels as the Sheets library.

    A chartless tab is one synthetic row, n=1, titled with the tab name. That is the
    favorite key book/gid/1 and the <gid>.png / thumbnail already used for the tab.
    """
    raw = tab.get("chartList")
    source = []
    if isinstance(raw, list) and raw:
        for i, c in enumerate(raw, 1):
            if not isinstance(c, dict):
                continue
            n = c.get("n", i)
            if isinstance(n, bool) or not isinstance(n, (int, float)) or (isinstance(n, float) and not n.is_integer()):
                n = i
            n = int(n)
            if not (1 <= n <= 99):
                continue
            source.append((n, norm_title(c.get("title"))))
    else:
        charts = tab.get("charts") if isinstance(tab.get("charts"), list) else []
        for i, title in enumerate(charts, 1):
            if i > 99:
                break
            source.append((i, norm_title(title)))
    seen = {}
    for _n, title in source:
        if title:
            seen[title] = seen.get(title, 0) + 1
    out = []
    for n, title in source:
        label = title if title and seen.get(title) == 1 else "Chart %d" % n
        out.append({"n": n, "title": title, "label": label})
    if not out:
        title = norm_title(tab.get("tab"))
        if title:
            out.append({"n": 1, "title": title, "label": title, "synthetic": True})
    return out

def parse_chart_n(value):
    """Integer 1..99, None when absent, or 'bad' when the pick stated something else."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "bad"
    if isinstance(value, float):
        if not value.is_integer():
            return "bad"
        value = int(value)
    if not (1 <= value <= 99):
        return "bad"
    return value

def resolve_chart_n(item, catalog):
    """1-based chart position to store, or None when it is not known.

    An explicit chart_n is kept when that chart is on the tab (or when the tab is
    not in the catalog, so the picker's number is what is known). Otherwise a single
    chart or a chartless table records its one position. A multi-chart tab records a
    position only when the card title matches exactly one chart. This never guesses
    chart 1 for a multi-chart tab.
    """
    explicit = parse_chart_n(item.get("chart_n"))
    if explicit == "bad":
        raise SystemExit("chart_n must be an integer from 1 to 99")
    tab = None
    if catalog:
        sid = str(item.get("spreadsheetId") or "").strip()
        book = catalog["books"].get(sid)
        try:
            gid = int(item.get("gid"))
        except (TypeError, ValueError):
            gid = None
        if book is not None and gid is not None:
            tab = catalog["tabs"].get((book, gid))
    if tab is None:
        return explicit
    charts = chart_rows(tab)
    if explicit is not None:
        if any(c["n"] == explicit for c in charts):
            return explicit
        raise SystemExit("chart_n %s is not on %s (gid %s)" % (explicit, tab.get("tab"), tab.get("gid")))
    if len(charts) == 1:
        return charts[0]["n"]
    want = norm_title(item.get("title")).lower()
    if not want or len(charts) < 2:
        return None
    hits = [c for c in charts if c["title"].lower() == want or c["label"].lower() == want]
    return hits[0]["n"] if len(hits) == 1 else None

def assign_chart_ns(items, catalog):
    for i, x in enumerate(items, 1):
        try:
            n = resolve_chart_n(x, catalog)
        except SystemExit as e:
            raise SystemExit("item %d: %s" % (i, e))
        if n is None:
            x.pop("chart_n", None)
        else:
            x["chart_n"] = n

def validate(items):
    """Accept 1 through 20 items; reject an empty list or anything over the cap."""
    n = len(items)
    if n < EDITION_MIN or n > EDITION_MAX:
        sys.exit(f"need 1 to {EDITION_MAX} items, got {n}")
    req = ["spreadsheetId", "sheet", "tab", "png", "title", "picked", "matters", "know"]
    for i, x in enumerate(items, 1):
        miss = [k for k in req if not str(x.get(k, "")).strip()]
        if miss: sys.exit(f"item {i} missing {miss}")
        if pick_excluded(x):
            sys.exit(f"item {i} is an excluded tab ({x.get('tab')} gid {x.get('gid')}) and cannot be published")

def publish(P, repo, keep_old):
    d = P["date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d): sys.exit("date must be YYYY-MM-DD")
    items = P["items"]
    validate(items)
    assign_chart_ns(items, load_catalog(repo))
    root = os.path.join(repo, "static", "cotd"); out = os.path.join(root, d)
    tmp = out + ".tmp"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    rows = []
    for i, x in enumerate(items, 1):
        fn = f"{i:02d}.png"; n = fit(x["png"], os.path.join(tmp, fn))
        url = f"https://docs.google.com/spreadsheets/d/{x['spreadsheetId']}/edit" + (f"#gid={x['gid']}" if x.get("gid") not in (None, "") else "")
        row = {"sheet": x["sheet"], "tab": x["tab"], "title": x["title"], "image": f"cotd/{d}/{fn}",
               "picked": x["picked"].strip(), "matters": x["matters"].strip(), "know": x["know"].strip(), "sheet_url": x.get("sheet_url") or url}
        if x.get("chart_n"):
            row["chart_n"] = x["chart_n"]
        rows.append(row)
        print(f"{fn}  {n//1024} KB  {x['sheet']} / {x['tab']}")
    shutil.rmtree(out, ignore_errors=True); os.rename(tmp, out)
    doc = {"date": d, "theme": P["theme"].strip(), "items": rows}
    t = os.path.join(root, "today.json")
    with open(t + ".tmp", "w") as f: json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(t + ".tmp", t)
    if keep_old:
        ed = sorted(x for x in os.listdir(root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x))
        for old in ed[:-keep_old]: shutil.rmtree(os.path.join(root, old)); print("removed old edition", old)
    print("wrote", t)
    return doc

def self_test():
    import tempfile
    bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
    bot2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
    assert EDITION_MIN == 1 and EDITION_MAX == 20
    assert ("bot2", 645072508) in COTD_EXCLUDED
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1547332232})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 643299192})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 333185169})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1791345316})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1370617918})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1024131985})
    assert pick_excluded({"spreadsheetId": bot2, "gid": 645072508})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1476428159})
    # Neighbor of the deleted Korea/Taiwan tab: "160 China vs Mexico vs Taiwan".
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 171599627})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": ""})
    live = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Retired charts stay out of the published edition and the sheet catalog.
    # 1024131985 is Strategy tab 99 (MSTR BTC per share), removed in PR #34.
    # 1791345316 and 1370617918 were deleted on 2026-11-09.
    # 645072508 stays in the catalog (Charts of the day only) and is not in this list.
    for rel in ("static/cotd/today.json", "static/sheets/catalog.json"):
        with open(os.path.join(live, rel), encoding="utf-8") as f:
            blob = f.read()
        for gid in (1547332232, 643299192, 333185169, 1024131985, 1791345316, 1370617918):
            assert str(gid) not in blob, (rel, gid)
    with open(os.path.join(live, "static/sheets/catalog.json"), encoding="utf-8") as f:
        catalog_blob = f.read()
    with open(os.path.join(live, "static/cotd/today.json"), encoding="utf-8") as f:
        today_blob = f.read()
    assert "645072508" in catalog_blob
    assert "645072508" not in today_blob
    bot3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
    live_cat = load_catalog(live)
    assert live_cat, "live catalog"

    def resolved(**kw):
        row = {"spreadsheetId": bot2, "gid": 0, "title": "t", "tab": "t"}
        row.update(kw)
        return resolve_chart_n(row, live_cat)

    # Tables are one synthetic chart. A multi-chart tab is recorded only from chart_n or a unique title.
    assert resolved(gid=1670158709, title="anything") == 1
    assert resolved(gid=746117712, title="anything") == 1
    assert resolved(gid=1078237833, title="Bitcoin's price vs what ETFs, companies and Strategy paid") is None
    assert resolved(gid=1078237833, title="Average acquisition cost: treasury companies vs. miners") == 2
    assert resolved(gid=1078237833, title="editorial", chart_n=1) == 1
    assert resolved(gid=1078237833, title="Average acquisition cost: spot ETFs vs. companies", chart_n=2) == 2
    assert resolved(spreadsheetId=bot1, gid=1186940001, title="editorial title") == 1
    assert resolved(spreadsheetId=bot3, gid=978356289, title="Anthropic") is None
    assert resolved(spreadsheetId=bot3, gid=1045685066, title="Strive denominated in BTC (ASST/BTC)") is None
    assert resolved(spreadsheetId=bot3, gid=1045685066, title="Chart 2") == 2
    assert resolve_chart_n({"spreadsheetId": bot2, "gid": 1078237833, "title": "editorial"}, None) is None
    assert resolve_chart_n({"spreadsheetId": bot2, "gid": 1078237833, "title": "editorial", "chart_n": 2}, None) == 2
    try:
        resolved(gid=1078237833, title="x", chart_n=3)
    except SystemExit as e:
        assert "chart_n 3" in str(e), e
    else:
        raise SystemExit("chart_n 3 was accepted")
    try:
        resolved(gid=1078237833, chart_n="1")
    except SystemExit as e:
        assert "integer" in str(e), e
    else:
        raise SystemExit("string chart_n was accepted")

    def item(i, **kw):
        row = {"spreadsheetId": bot1, "sheet": "Bot experiments", "tab": f"sample tab {i}",
               "gid": 900000 + i, "png": f"/tmp/cotd-sample-{i}.png", "title": f"Chart {i:02d}",
               "picked": f"picked note {i} " + ("quality " * 12),
               "matters": f"matters note {i}",
               "know": f"know note {i} with a figure 12.5 and date 2026-11-01"}
        row.update(kw)
        return row

    def rejected(items, needle):
        try:
            validate(items)
        except SystemExit as e:
            if needle not in str(e):
                raise SystemExit(f"expected {needle!r} in {e}")
            return
        raise SystemExit("expected rejection, validation passed")

    for n in (0, 21):
        rejected([item(i) for i in range(1, n + 1)], f"need 1 to {EDITION_MAX} items, got {n}")
    validate([item(i) for i in range(1, 11)])
    validate([item(i) for i in range(1, 20)])
    for gid, tab, sid in (
        (1547332232, "161 Nvidia P-S and P-E", bot1),
        (643299192, "202 Nvidia revenue by geography", bot1),
        (333185169, "87 TSLA beta to Nasdaq", bot1),
        (1791345316, "158 Korea & Taiwan vs S&P", bot1),
        (1370617918, "163 Supercore vs core CPI", bot1),
        (1024131985, "99 MSTR BTC per share", bot1),
        (645072508, "69 Mark MSTR red dot tracker", bot2),
    ):
        items = [item(i) for i in range(1, EDITION_MAX + 1)]
        items[4] = item(5, gid=gid, tab=tab, spreadsheetId=sid)
        rejected(items, f"gid {gid}")
    neighbor = [item(i) for i in range(1, 4)]
    neighbor[1] = item(2, gid=171599627, tab="160 China vs Mexico vs Taiwan", spreadsheetId=bot1)
    validate(neighbor)

    src = os.path.join(tempfile.mkdtemp(prefix="cotd-src-"), "chart.png")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    from PIL import Image
    im = Image.new("RGB", (64, 36))
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            px[x, y] = ((x * 17 + y * 3) % 256, (x * 5 + y * 13) % 256, (x * y + 40) % 256)
    im.save(src)
    repo = tempfile.mkdtemp(prefix="cotd-repo-")
    try:
        blocked = [item(i, png=src) for i in range(1, 6)]
        blocked[2] = item(3, png=src, gid=643299192, tab="202 Nvidia revenue by geography")
        try:
            publish({"date": "2099-01-15", "theme": "excluded", "items": blocked}, repo, 0)
        except SystemExit as e:
            assert "gid 643299192" in str(e), e
        else:
            raise SystemExit("an excluded gid was published")
        assert not os.path.exists(os.path.join(repo, "static", "cotd", "today.json"))
        red_dot = [item(i, png=src) for i in range(1, 6)]
        red_dot[1] = item(2, png=src, gid=645072508, tab="69 Mark MSTR red dot tracker", spreadsheetId=bot2)
        try:
            publish({"date": "2099-01-15", "theme": "cotd only", "items": red_dot}, repo, 0)
        except SystemExit as e:
            assert "gid 645072508" in str(e), e
        else:
            raise SystemExit("a Charts-of-the-day-only gid was published")
        assert not os.path.exists(os.path.join(repo, "static", "cotd", "today.json"))
        extra = {"date": "2099-01-15", "theme": "too many", "items": [item(i, png=src) for i in range(1, 22)]}
        try:
            publish(extra, repo, 0)
        except SystemExit as e:
            assert "need 1 to 20 items, got 21" in str(e), e
        else:
            raise SystemExit("21 items were published")
        assert not os.path.exists(os.path.join(repo, "static", "cotd", "today.json"))
        assert not os.path.isdir(os.path.join(repo, "static", "cotd", "2099-01-15"))
        short = {"date": "2099-01-15", "theme": " ten is allowed ", "items": [item(i, png=src) for i in range(1, 11)]}
        doc10 = publish(short, repo, 0)
        assert len(doc10["items"]) == 10, len(doc10["items"])
        picks = [item(i, png=src) for i in range(1, EDITION_MAX + 1)]
        doc = publish({"date": "2099-01-16", "theme": " twenty charts ", "items": picks}, repo, 0)
        assert doc["theme"] == "twenty charts"
        assert len(doc["items"]) == 20, len(doc["items"])
        titles = [row["title"] for row in doc["items"]]
        assert titles == [f"Chart {i:02d}" for i in range(1, 21)], titles
        assert doc["items"][19]["know"].startswith("know note 20 ")
        assert "quality" in doc["items"][0]["picked"]
        folder = os.path.join(repo, "static", "cotd", "2099-01-16")
        names = sorted(os.listdir(folder))
        assert names == [f"{i:02d}.png" for i in range(1, 21)], names
        with open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8") as f:
            saved = json.load(f)
        assert [row["image"] for row in saved["items"]] == [f"cotd/2099-01-16/{i:02d}.png" for i in range(1, 21)]
        assert all("chart_n" not in row for row in doc["items"])
        stated = publish({"date": "2099-01-17", "theme": "stated position", "items": [item(1, png=src, chart_n=2)]}, repo, 0)
        assert stated["items"][0]["chart_n"] == 2
        cat_dir = os.path.join(repo, "static", "sheets")
        os.makedirs(cat_dir, exist_ok=True)
        fixture_cat = {
            "books": [{"id": "bot1", "spreadsheetId": bot1, "name": "Bot experiments"}],
            "tabs": [
                {"book": "bot1", "tab": "only one", "gid": 910001, "charts": ["Only"], "chartList": [{"n": 1, "title": "Only"}]},
                {"book": "bot1", "tab": "a table", "gid": 910002, "charts": [], "chartList": []},
                {"book": "bot1", "tab": "two", "gid": 910003, "charts": ["Alpha", "Beta"],
                 "chartList": [{"n": 1, "title": "Alpha"}, {"n": 2, "title": "Beta"}]},
                {"book": "bot1", "tab": "dup", "gid": 910004, "charts": ["Same"],
                 "chartList": [{"n": 1, "title": "Same"}, {"n": 2, "title": "Same"}]},
            ],
        }
        with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(fixture_cat, f)
        placed = [
            item(1, png=src, gid=910001, tab="only one", title="editorial one"),
            item(2, png=src, gid=910002, tab="a table", title="table card"),
            item(3, png=src, gid=910003, tab="two", title="Beta"),
            item(4, png=src, gid=910003, tab="two", title="neither name"),
            item(5, png=src, gid=910003, tab="two", title="neither name", chart_n=1),
            item(6, png=src, gid=910004, tab="dup", title="Same"),
            item(7, png=src, gid=910004, tab="dup", title="Chart 2"),
            item(8, png=src, gid=910099, tab="missing tab", title="not in catalog"),
            item(9, png=src, gid=910099, tab="missing tab", title="not in catalog", chart_n=4),
        ]
        placed_doc = publish({"date": "2099-01-18", "theme": "positions", "items": placed}, repo, 0)
        got = [row.get("chart_n") for row in placed_doc["items"]]
        assert got == [1, 1, 2, None, 1, None, 2, None, 4], got
        for idx in (3, 5, 7):
            assert "chart_n" not in placed_doc["items"][idx]
        try:
            publish({"date": "2099-01-19", "theme": "bad", "items": [
                item(1, png=src, gid=910003, tab="two", title="nope", chart_n=9)]}, repo, 0)
        except SystemExit as e:
            assert "item 1" in str(e) and "chart_n 9" in str(e), e
        else:
            raise SystemExit("a chart_n that is not on the tab was published")
        with open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8") as f:
            still = json.load(f)
        assert still["date"] == "2099-01-18", still["date"]
        try:
            publish({"date": "2099-01-19", "theme": "bad table", "items": [
                item(1, png=src, gid=910002, tab="a table", title="table", chart_n=2)]}, repo, 0)
        except SystemExit as e:
            assert "chart_n 2" in str(e), e
        else:
            raise SystemExit("chart 2 was accepted on a table")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(os.path.dirname(src), ignore_errors=True)
    print("self-test ok")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("picks", nargs="?"); ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--keep-old", type=int, default=7, help="keep this many dated edition folders (0 = keep all)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test(); return
    if not a.picks: ap.error("pass picks.json")
    P = json.load(open(a.picks))
    publish(P, a.repo, a.keep_old)

if __name__ == "__main__": main()
