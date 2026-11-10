#!/usr/bin/env python3
"""Build a "Charts of the day" edition for Chartmaker. Runs on the box, not on Render.

The daily refresh draws charts uniformly at random from the sheet catalog. It does not
rank them, and it does not write notes. No picks file and no curation step.

Usage:  python3 scripts/cotd_build.py [--repo /workspace/chartmaker] [--date YYYY-MM-DD] [--keep-old N]

The edition date defaults to today in America/New_York. The draw is seeded by that date,
so running the command again the same day, with the same catalog and the same history,
writes the same charts. Each chart's PNG is the committed library file
static/sheets/full/<book>/<gid>.png (chart 2+ is <gid>_n.png). It is checked (not blank /
not a single colour), fitted onto a white 1600x900 canvas, and written to
static/cotd/<date>/NN.png. static/cotd/today.json is then rewritten. Cards record the
title, source workbook, and chart_n. They do not carry picked / matters / know text.

The draw is 20 charts, or every eligible chart when fewer than 20 remain. Nothing is
added to fill a gap. Zero eligible charts is an error and leaves today.json in place.
A chart is eligible when it is a real catalog chart (not a chartless table), its full
PNG is on disk, it is not in sheets_catalog.EXCLUDED_TABS or COTD_EXCLUDED, and it did
not appear in D-1, D-2, or D-3. A missing or unreadable cards.json for one of those
days excludes nothing. Identity is book + gid + chart_n when both sides record chart_n,
and book + gid when either side does not.
"""
import argparse, datetime, json, os, random, re, shutil, sys, zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_catalog

W, H = 1600, 900
# Draw this many charts. Fewer eligible charts publishes that shorter list, unpadded.
EDITION_MIN = 1
EDITION_MAX = 20
EDITION_SIZE = EDITION_MAX
BOOK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,24}$")
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

def book_gid_of(item):
    """(book, gid) from a pick's spreadsheetId, or None when this is not a known workbook tab."""
    sid = str(item.get("spreadsheetId") or "").strip()
    pair = sheets_catalog.BY_ID.get(sid)
    if not pair or item.get("gid") in (None, ""):
        return None
    try:
        return pair[0], int(item.get("gid"))
    except (TypeError, ValueError):
        return None

def pick_chart_key(item):
    """(book, gid, chart_n or None), or None when the workbook tab is unknown."""
    base = book_gid_of(item)
    if not base:
        return None
    n = parse_chart_n(item.get("chart_n"))
    return base[0], base[1], None if n in (None, "bad") else n

def cards_path(repo, date):
    return os.path.join(repo, "static", "cotd", date, "cards.json")

def load_cards(repo, date):
    """Cards recorded for one edition. A missing or unreadable file excludes nothing."""
    path = cards_path(repo, date)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return []
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        book = str(it.get("book") or "").strip()
        try:
            gid = int(it.get("gid"))
        except (TypeError, ValueError):
            continue
        if not book:
            continue
        n = parse_chart_n(it.get("chart_n"))
        out.append({"date": date, "book": book, "gid": gid, "chart_n": None if n in (None, "bad") else n,
                     "tab": it.get("tab") or ""})
    return out

def recent_charts(repo, date):
    """Charts published on D-1, D-2, and D-3. A day with no cards.json contributes nothing."""
    day = datetime.date.fromisoformat(date)
    found = []
    for delta in (1, 2, 3):
        found.extend(load_cards(repo, (day - datetime.timedelta(days=delta)).isoformat()))
    return found

def repeated_recent(item, recent):
    """The prior card when this pick repeats one from the previous 3 editions."""
    key = pick_chart_key(item)
    if not key:
        return None
    book, gid, n = key
    for prev in recent or []:
        if prev["book"] != book or prev["gid"] != gid:
            continue
        if n is None or prev["chart_n"] is None or prev["chart_n"] == n:
            return prev
    return None

def card_record(item):
    """One cards.json row. chart_n is omitted when this pick does not know it."""
    card = {}
    key = pick_chart_key(item)
    if key:
        card["book"] = key[0]
        card["gid"] = key[1]
        if key[2] is not None:
            card["chart_n"] = key[2]
    card["tab"] = item.get("tab") or ""
    card["title"] = item.get("title") or ""
    return card

def cards_from_published(doc):
    """Card list for an edition already stored as today.json (sheet_url carries the gid)."""
    items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        url = str(it.get("sheet_url") or "")
        m = re.search(r"/spreadsheets/d/([^/]+)/[^#]*#gid=(\d+)", url)
        row = {"spreadsheetId": m.group(1) if m else "", "gid": int(m.group(2)) if m else "",
               "chart_n": it.get("chart_n"), "tab": it.get("tab") or "", "title": it.get("title") or ""}
        items.append(card_record(row))
    return {"date": doc.get("date"), "items": items}

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

def theme_for(n):
    noun = "chart" if n == 1 else "charts"
    return "%d %s drawn at random from the catalog." % (n, noun)

def edition_rng(seed):
    """RNG for one edition. A str seed is stable across processes (not hashed)."""
    rng = random.Random()
    rng.seed(str(seed))
    return rng

def choose_charts(pool, k, rng):
    """Uniform sample of k charts. When fewer than k are eligible, return all of them, shuffled."""
    pool = list(pool)
    if len(pool) <= k:
        rng.shuffle(pool)
        return pool
    return rng.sample(pool, k)

def full_png(repo, book, gid, n):
    """Committed library PNG for this chart, or None when the file is missing or the path is unsafe."""
    if not BOOK_ID.fullmatch(str(book or "")):
        return None
    try:
        gid = int(gid)
        n = int(n)
    except (TypeError, ValueError):
        return None
    if gid < 0 or not (1 <= n <= 99):
        return None
    suffix = "" if n <= 1 else "_%d" % n
    rel = "sheets/full/%s/%d%s.png" % (book, gid, suffix)
    root = os.path.realpath(os.path.join(repo, "static", "sheets", "full"))
    path = os.path.realpath(os.path.join(repo, "static", rel))
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        return None
    return path

def chart_blocked(book, gid, spreadsheet_id):
    """True for a retired tab, a Charts-of-the-day exclusion, or a gid this script already refuses."""
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return True
    if sheets_catalog.tab_excluded(book, gid) or (str(book), gid) in COTD_EXCLUDED:
        return True
    return pick_excluded({"spreadsheetId": spreadsheet_id, "gid": gid})

def load_catalog_doc(repo):
    path = os.path.join(repo, "static", "sheets", "catalog.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None

def eligible_charts(repo, date):
    """Catalog charts that can be drawn for this edition date, in a stable order.

    Order is book, gid, chart_n, title, so the date-seeded sample does not depend on
    catalog file order. Chartless tabs (tables) are not charts. A chart with no
    library PNG is skipped. Repeats from the previous 3 days are skipped.
    """
    doc = load_catalog_doc(repo)
    if not doc:
        return []
    books = {}
    for b in doc.get("books") or []:
        if isinstance(b, dict) and b.get("id"):
            books[str(b["id"])] = b
    recent = recent_charts(repo, date)
    out, seen = [], set()
    for tab in doc.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        book_id = str(tab.get("book") or "").strip()
        book = books.get(book_id)
        if not book:
            continue
        try:
            gid = int(tab.get("gid"))
        except (TypeError, ValueError):
            continue
        sid = str(book.get("spreadsheetId") or "").strip()
        if not sheets_catalog.BY_ID.get(sid):
            continue
        if chart_blocked(book_id, gid, sid):
            continue
        for row in chart_rows(tab):
            if row.get("synthetic"):
                continue
            n = row["n"]
            key = (book_id, gid, n)
            if key in seen:
                continue
            item = {
                "book": book_id,
                "sheet": book.get("name") or "",
                "spreadsheetId": sid,
                "tab": tab.get("tab") or "",
                "gid": gid,
                "chart_n": n,
                "title": row["title"] or row["label"] or (tab.get("tab") or ""),
            }
            if repeated_recent(item, recent):
                continue
            png = full_png(repo, book_id, gid, n)
            if not png:
                continue
            item["png"] = png
            seen.add(key)
            out.append(item)
    out.sort(key=lambda c: (c["book"], c["gid"], c["chart_n"], c["title"]))
    return out

def write_edition(repo, date, chosen, keep_old):
    """Fit the chosen PNGs and publish today.json. Leaves today.json alone if a chart image fails."""
    if not chosen:
        sys.exit("no eligible charts")
    if len(chosen) > EDITION_MAX:
        sys.exit("draw is larger than %d" % EDITION_MAX)
    root = os.path.join(repo, "static", "cotd")
    out = os.path.join(root, date)
    tmp = out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    rows, card_items = [], []
    try:
        for i, x in enumerate(chosen, 1):
            fn = "%02d.png" % i
            nbytes = fit(x["png"], os.path.join(tmp, fn))
            url = "https://docs.google.com/spreadsheets/d/%s/edit#gid=%s" % (x["spreadsheetId"], x["gid"])
            rows.append({
                "sheet": x["sheet"],
                "tab": x["tab"],
                "title": x["title"],
                "image": "cotd/%s/%s" % (date, fn),
                "sheet_url": url,
                "chart_n": x["chart_n"],
            })
            card_items.append(card_record(x))
            print("%s  %d KB  %s / %s  chart %s" % (fn, nbytes // 1024, x["sheet"], x["tab"], x["chart_n"]))
        with open(os.path.join(tmp, "cards.json"), "w", encoding="utf-8") as f:
            json.dump({"date": date, "items": card_items}, f, indent=1, ensure_ascii=False)
            f.write("\n")
        shutil.rmtree(out, ignore_errors=True)
        os.rename(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    doc = {"date": date, "draw": "random", "theme": theme_for(len(rows)), "items": rows}
    dest = os.path.join(root, "today.json")
    with open(dest + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(dest + ".tmp", dest)
    if keep_old:
        editions = sorted(x for x in os.listdir(root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x))
        for old in editions[:-keep_old]:
            shutil.rmtree(os.path.join(root, old))
            print("removed old edition", old)
    print("wrote", dest)
    return doc

def publish_random(repo, date, keep_old):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        sys.exit("date must be YYYY-MM-DD")
    if load_catalog_doc(repo) is None:
        sys.exit("static/sheets/catalog.json is missing or unreadable")
    pool = eligible_charts(repo, date)
    if not pool:
        sys.exit("no eligible charts")
    chosen = choose_charts(pool, EDITION_SIZE, edition_rng(date))
    print("eligible %d, drawing %d for %s" % (len(pool), len(chosen), date))
    return write_edition(repo, date, chosen, keep_old)

def today_et():
    return datetime.datetime.now(zoneinfo.ZoneInfo("America/New_York")).date().isoformat()

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
    # Retired by Mark on 2026-11-10. Adding a gid to EXCLUDED_TABS is enough for this check.
    for gid in (292208014, 2119398138, 1584345646, 1186940001, 501919598, 579853621):
        assert pick_excluded({"spreadsheetId": bot1, "gid": gid}), gid
    assert pick_excluded({"spreadsheetId": bot2, "gid": 418894352})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1476428159})
    # Neighbors that stay: Korea/Taiwan's neighbor, CoreWeave backlog, CPI charts, tariff rate, real yield.
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 171599627})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 200447618})  # 52 Core goods vs services
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 829703707})  # 18 Electricity vs CPI
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 972156540})  # 67 Effective tariff rate
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1167074374})  # 299 Real yield vs breakeven
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1027143376})  # 97 MSTR vol vs BTC vol
    assert not pick_excluded({"spreadsheetId": bot2, "gid": 530554790})  # 75 CoreWeave backlog vs revenue
    assert not pick_excluded({"spreadsheetId": bot1, "gid": ""})
    live = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Retired charts stay out of the published edition and the sheet catalog.
    # 1024131985 is Strategy tab 99 (MSTR BTC per share), removed in PR #34.
    # 1791345316 and 1370617918 were deleted on 2026-11-09.
    # 645072508 stays in the catalog (Charts of the day only) and is not in this list.
    # The 2026-11-10 gids are the charts Mark deleted that day.
    retired_live = (1547332232, 643299192, 333185169, 1024131985, 1791345316, 1370617918,
                    292208014, 2119398138, 1584345646, 1186940001, 501919598, 579853621, 418894352)
    for rel in ("static/cotd/today.json", "static/sheets/catalog.json"):
        with open(os.path.join(live, rel), encoding="utf-8") as f:
            blob = f.read()
        for gid in retired_live:
            assert str(gid) not in blob, (rel, gid)
    with open(os.path.join(live, "static/sheets/catalog.json"), encoding="utf-8") as f:
        catalog_blob = f.read()
    with open(os.path.join(live, "static/cotd/today.json"), encoding="utf-8") as f:
        today_doc = json.load(f)
        today_blob = json.dumps(today_doc)
    assert "645072508" in catalog_blob
    assert "645072508" not in today_blob
    for gid in (530554790, 200447618, 829703707, 972156540, 1167074374, 978356289, 1459587255):
        assert str(gid) in catalog_blob, gid
    assert len(today_doc["items"]) == 13, len(today_doc["items"])
    cards_live = json.load(open(os.path.join(live, "static/cotd/2026-11-10/cards.json"), encoding="utf-8"))
    assert cards_live == cards_from_published(today_doc)
    assert all(it.get("book") and it.get("gid") for it in cards_live["items"])
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
    # 200447618 is "52 Core goods vs services", a single chart that stays in the catalog.
    assert resolved(spreadsheetId=bot1, gid=200447618, title="editorial title") == 1
    # 978356289 is one chart ("Anthropic Revenue vs Compute") and stays in the library.
    assert resolved(spreadsheetId=bot3, gid=978356289, title="Anthropic") == 1
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

    hist = tempfile.mkdtemp(prefix="cotd-recent-")
    try:
        def write_cards(date, rows):
            folder = os.path.join(hist, "static", "cotd", date)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "cards.json"), "w", encoding="utf-8") as f:
                json.dump({"date": date, "items": rows}, f)
        # 2099-03-05 looks back at 03-04, 03-03, and 03-02. 03-01 is four days back.
        write_cards("2099-03-01", [{"book": "bot1", "gid": 111, "chart_n": 1, "tab": "old", "title": "A"}])
        write_cards("2099-03-02", [{"book": "bot1", "gid": 222, "chart_n": 1, "tab": "d3", "title": "B"}])
        write_cards("2099-03-04", [
            {"book": "bot2", "gid": 333, "chart_n": 2, "tab": "d1", "title": "C"},
            {"book": "bot1", "gid": 444, "tab": "no-n", "title": "D"},
        ])
        recent = recent_charts(hist, "2099-03-05")
        assert {(r["book"], r["gid"]) for r in recent} == {("bot1", 222), ("bot2", 333), ("bot1", 444)}, recent
        assert repeated_recent({"spreadsheetId": bot1, "gid": 222, "chart_n": 1, "tab": "d3"}, recent)
        assert not repeated_recent({"spreadsheetId": bot1, "gid": 111, "chart_n": 1, "tab": "old"}, recent)
        assert repeated_recent({"spreadsheetId": bot2, "gid": 333, "chart_n": 2, "tab": "d1"}, recent)
        assert not repeated_recent({"spreadsheetId": bot2, "gid": 333, "chart_n": 1, "tab": "d1"}, recent)
        assert repeated_recent({"spreadsheetId": bot1, "gid": 444, "chart_n": 2, "tab": "no-n"}, recent)
        assert not repeated_recent({"spreadsheetId": bot1, "gid": 555, "chart_n": 1, "tab": "new"}, recent)
        broken = os.path.join(hist, "static", "cotd", "2099-03-03")
        os.makedirs(broken)
        with open(os.path.join(broken, "cards.json"), "w", encoding="utf-8") as f:
            f.write("{")
        assert len(recent_charts(hist, "2099-03-05")) == 3
    finally:
        shutil.rmtree(hist, ignore_errors=True)

    repo = tempfile.mkdtemp(prefix="cotd-random-")
    try:
        from PIL import Image
        def write_png(rel):
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            im = Image.new("RGB", (64, 36))
            px = im.load()
            for y in range(im.height):
                for x in range(im.width):
                    px[x, y] = ((x * 17 + y * 3) % 256, (x * 5 + y * 13) % 256, (x * y + 40) % 256)
            im.save(path)
        tabs = []
        for i in range(1, 23):
            gid = 810000 + i
            write_png("static/sheets/full/bot1/%d.png" % gid)
            tabs.append({"book": "bot1", "tab": "sample %02d" % i, "gid": gid,
                         "charts": ["Chart %02d" % i], "chartList": [{"n": 1, "title": "Chart %02d" % i}]})
        write_png("static/sheets/full/bot1/1547332232.png")
        tabs.append({"book": "bot1", "tab": "161 Nvidia P-S and P-E", "gid": 1547332232,
                     "charts": ["retired"], "chartList": [{"n": 1, "title": "retired"}]})
        write_png("static/sheets/full/bot2/645072508.png")
        tabs.append({"book": "bot2", "tab": "69 Mark MSTR red dot tracker", "gid": 645072508,
                     "charts": ["red dot"], "chartList": [{"n": 1, "title": "red dot"}]})
        tabs.append({"book": "bot1", "tab": "a table", "gid": 810100, "charts": [], "chartList": []})
        os.makedirs(os.path.join(repo, "static", "sheets"), exist_ok=True)
        catalog = {
            "books": [
                {"id": "bot1", "name": "Bot experiments", "spreadsheetId": bot1},
                {"id": "bot2", "name": "bot experiments 2", "spreadsheetId": bot2},
            ],
            "tabs": tabs,
        }
        with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        day = "2099-08-01"
        prev = "2099-07-31"
        folder = os.path.join(repo, "static", "cotd", prev)
        os.makedirs(folder)
        with open(os.path.join(folder, "cards.json"), "w", encoding="utf-8") as f:
            json.dump({"date": prev, "items": [
                {"book": "bot1", "gid": 810001, "chart_n": 1, "tab": "sample 01", "title": "Chart 01"}
            ]}, f)
        pool = eligible_charts(repo, day)
        ids = {(c["book"], c["gid"], c["chart_n"]) for c in pool}
        assert ("bot1", 1547332232, 1) not in ids
        assert ("bot2", 645072508, 1) not in ids
        assert ("bot1", 810001, 1) not in ids
        assert ("bot1", 810100, 1) not in ids
        assert len(pool) == 21, len(pool)
        drawn = choose_charts(pool, EDITION_SIZE, edition_rng(day))
        assert len(drawn) == 20, len(drawn)
        again = edition_rng(day)
        assert [c["gid"] for c in drawn] == [c["gid"] for c in again.sample(list(pool), 20)]
        short = choose_charts(pool[:4], EDITION_SIZE, edition_rng("short"))
        assert len(short) == 4 and {c["gid"] for c in short} == {c["gid"] for c in pool[:4]}
        doc = publish_random(repo, day, 0)
        assert doc["draw"] == "random"
        assert doc["theme"] == "20 charts drawn at random from the catalog."
        assert len(doc["items"]) == 20
        assert [row["title"] for row in doc["items"]] == [c["title"] for c in drawn]
        for row in doc["items"]:
            assert row["chart_n"] == 1
            assert "picked" not in row and "matters" not in row and "know" not in row
            assert "1547332232" not in row["sheet_url"] and "645072508" not in row["sheet_url"]
            assert "gid=810001" not in row["sheet_url"]
        cards = json.load(open(os.path.join(repo, "static", "cotd", day, "cards.json"), encoding="utf-8"))
        assert len(cards["items"]) == 20
        assert all(it["book"] == "bot1" and it["chart_n"] == 1 for it in cards["items"])
        assert not os.path.exists(os.path.join(repo, "static", "cotd", "today.json.tmp"))
        # A second run the same day draws the same charts and does not require a picks file.
        second = publish_random(repo, day, 0)
        assert [row["image"] for row in second["items"]] == [row["image"] for row in doc["items"]]
        try:
            publish_random(repo, "2099-08-02", 0)
        except SystemExit as e:
            # 2099-08-02 looks back at 08-01, which now holds 20 of the 21 eligible charts.
            # The one chart left is still eligible, so this must not be the failure.
            raise SystemExit("a one-chart remainder was refused: %s" % e)
        rest = json.load(open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8"))
        assert len(rest["items"]) == 1, len(rest["items"])
        assert rest["theme"] == "1 chart drawn at random from the catalog."
        blocked = rest["items"][0]
        assert blocked["chart_n"] == 1
        # The published card is the one chart from the 08-01 pool that 08-01 did not use.
        # 810001 was already ineligible on 08-01 and stays ineligible (it is in 07-31).
        used = {int(row["sheet_url"].rsplit("gid=", 1)[1]) for row in doc["items"]}
        left = int(blocked["sheet_url"].rsplit("gid=", 1)[1])
        assert left != 810001
        assert {c["gid"] for c in pool} - used == {left}
        empty = tempfile.mkdtemp(prefix="cotd-empty-")
        try:
            os.makedirs(os.path.join(empty, "static", "sheets"))
            with open(os.path.join(empty, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
                json.dump({"books": catalog["books"], "tabs": [tabs[-3], tabs[-2], tabs[-1]]}, f)
            try:
                publish_random(empty, day, 0)
            except SystemExit as e:
                assert "no eligible charts" in str(e), e
            else:
                raise SystemExit("an edition with no eligible charts was published")
            assert not os.path.exists(os.path.join(empty, "static", "cotd", "today.json"))
        finally:
            shutil.rmtree(empty, ignore_errors=True)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    print("self-test ok")

def main():
    ap = argparse.ArgumentParser(description="Draw a Charts of the day edition at random from the catalog.")
    ap.add_argument("picks", nargs="*", help=argparse.SUPPRESS)
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--date", help="edition date YYYY-MM-DD (default: today in America/New_York)")
    ap.add_argument("--keep-old", type=int, default=7, help="keep this many dated edition folders (0 = keep all)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return
    if a.picks:
        sys.exit("Charts of the day draws charts at random and does not read a picks file. "
                 "Run: python3 scripts/cotd_build.py")
    publish_random(a.repo, a.date or today_et(), a.keep_old)

if __name__ == "__main__":
    main()
