#!/usr/bin/env python3
"""Build a "Charts of the day" edition for Chartmaker. Runs on the box, not on Render.

Each edition has two sections. "Today's picks" is 5 charts or table tabs supplied
by the curator in a picks file, each with a description and a reason. "Random from
the library" is a uniform random sample of 20 items from the sheet catalog, and
those 20 never repeat one of the 5 picks. An item is one chart (a catalog chartList
entry) or one chartless table tab. When fewer than 20 items are eligible, the
random section contains however many remain. More than 20 random items is an error.
With no picks file, the edition is the random section only, as before.

Usage:  python3 scripts/cotd_build.py [--picks picks.json] [--repo /workspace/chartmaker] [--date YYYY-MM-DD] [--keep-old N]

The edition date defaults to today in America/New_York. The draw is seeded with
that date, so a second run on the same day, with the same catalog, the same picks,
and the same three previous editions, writes the same 20 random items. Pass --seed
only to reproduce a draw in a test.

Each chosen chart or table uses a library preview when one exists:
static/sheets/full/<book>/<gid>.png (chart n uses <gid>_n.png), otherwise the
tab thumbnail for chart 1 or a table. The image is fitted onto a white 1600x900
canvas and written to static/cotd/<date>/NN.png. A table with no preview is still
a card, with a link to the tab. static/cotd/today.json is then rewritten.
The same run writes static/cotd/<date>/cards.json (book, gid, chart_n, tab, title).
Picks are in that list too, with section "picks", description, and reason, and the
file also carries picks and random arrays. An older cards.json that has only items
still counts. When the edition date is D, an item that appears in D-1, D-2, or D-3
is left out of the pool, including those days' picks. A missing cards.json for one
of those days excludes nothing.
Identity is book + gid + chart_n when both sides record chart_n, and book + gid
when either side does not.
"""
import argparse, datetime, hashlib, json, os, random, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_catalog

W, H = 1600, 900
# Draw this many random items. Publish fewer when the eligible pool is smaller.
EDITION_MAX = 20
EDITION_SIZE = EDITION_MAX
# Curated section. A picks file must contain exactly this many, or the build stops.
PICKS_SIZE = 5
# Charts of the day only. These tabs stay in the sheet catalog, full PNGs, and thumbnails.
# ("bot2", 645072508) is BX2-069, tab "69 Mark MSTR red dot tracker", chart
# "Mark's Red Dot Tracker: MSTR". The tab still exists. It is retired from this edition only.
COTD_EXCLUDED = {
    ("bot2", 645072508),
}

def check(im, path):
    import sheets_thumbs
    if sheets_thumbs.blank(im):
        raise SystemExit(f"BLANK/empty-looking chart image: {path}. "
                         "Combo charts often export blank from Apps Script; use the fallback PNG (see README).")

def file_is_blank(path):
    """True when this library image is near-uniform or an empty Google chart."""
    if not path or not os.path.isfile(path):
        return False
    try:
        from PIL import Image
        im = Image.open(path)
        im.load()
    except (OSError, ValueError):
        return False
    import sheets_thumbs
    return sheets_thumbs.blank(im)

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
    """(book, gid, chart_n or None), or None when the workbook tab is unknown.

    A catalog row already carries book. A hand-built row is resolved from its
    spreadsheet id through the known workbooks.
    """
    n = parse_chart_n(item.get("chart_n"))
    n = None if n in (None, "bad") else n
    book = str(item.get("book") or "").strip()
    if book and item.get("gid") not in (None, ""):
        try:
            return book, int(item.get("gid")), n
        except (TypeError, ValueError):
            pass
    base = book_gid_of(item)
    if not base:
        return None
    return base[0], base[1], n

def cards_path(repo, date):
    return os.path.join(repo, "static", "cotd", date, "cards.json")

def edition_card_rows(doc):
    """Cards in one edition file.

    `items` is the whole edition when it is present, including picks. A file that
    has no items list is read from `picks` and then `random`, so either shape counts
    toward the 3-day history. An items list wins, and the arrays are not added again.
    """
    if not isinstance(doc, dict):
        return []
    items = doc.get("items")
    if isinstance(items, list):
        return items
    rows = []
    for key in ("picks", "random"):
        block = doc.get(key)
        if isinstance(block, list):
            rows.extend(block)
    return rows

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
    out = []
    for it in edition_card_rows(doc):
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
    """One cards.json row. chart_n is omitted when this pick does not know it.

    section, description, and reason are recorded only when this edition has picks.
    A random-only card stays book, gid, chart_n, tab, title, and kind.
    """
    card = {}
    key = pick_chart_key(item)
    if key:
        card["book"] = key[0]
        card["gid"] = key[1]
        if key[2] is not None:
            card["chart_n"] = key[2]
    card["tab"] = item.get("tab") or ""
    card["title"] = item.get("title") or ""
    if item.get("kind") in ("chart", "table"):
        card["kind"] = item["kind"]
    if item.get("section") in ("picks", "random"):
        card["section"] = item["section"]
    if item.get("section") == "picks":
        card["description"] = str(item.get("description") or "").strip()
        card["reason"] = str(item.get("reason") or "").strip()
    return card

def as_recent(item, date):
    """A history row for one item, used to block the same chart later in the run."""
    key = pick_chart_key(item)
    if not key:
        return None
    return {"date": date, "book": key[0], "gid": key[1], "chart_n": key[2], "tab": item.get("tab") or ""}

def _published_card(it, section=None):
    """One cards.json row rebuilt from a today.json card. sheet_url carries the gid."""
    if not isinstance(it, dict):
        return None
    url = str(it.get("sheet_url") or "")
    m = re.search(r"/spreadsheets/d/([^/]+)/[^#]*#gid=(\d+)", url)
    row = {"spreadsheetId": m.group(1) if m else "", "gid": int(m.group(2)) if m else "",
           "chart_n": it.get("chart_n"), "tab": it.get("tab") or "", "title": it.get("title") or ""}
    if it.get("kind") in ("chart", "table"):
        row["kind"] = it["kind"]
    if section in ("picks", "random"):
        row["section"] = section
    if section == "picks":
        row["description"] = it.get("description") or ""
        row["reason"] = it.get("reason") or ""
    return card_record(row)

def cards_from_published(doc):
    """Card list for an edition already stored as today.json (sheet_url carries the gid).

    A today.json with no picks array is items only, the same document older editions
    wrote. A split edition puts the picks back into items so the 3-day history sees them.
    """
    picks = doc.get("picks") if isinstance(doc, dict) and isinstance(doc.get("picks"), list) else None
    if picks:
        pick_cards = [c for c in (_published_card(it, "picks") for it in picks) if c]
        random_raw = [it for it in (doc.get("items") or []) if not (isinstance(it, dict) and it.get("section") == "picks")]
        random_cards = [c for c in (_published_card(it, "random") for it in random_raw) if c]
        return {"date": doc.get("date"), "items": pick_cards + random_cards, "picks": pick_cards, "random": random_cards}
    items = []
    for it in doc.get("items") or []:
        card = _published_card(it)
        if card:
            items.append(card)
    return {"date": doc.get("date"), "items": items}

def norm_title(s):
    return " ".join(str(s or "").split())

def load_catalog_doc(repo):
    """The catalog file as written. None when this repo has no readable catalog."""
    path = os.path.join(repo, "static", "sheets", "catalog.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None

def load_catalog(repo):
    """Book id by spreadsheet id, and tabs by (book, gid). None when this repo has no catalog."""
    doc = load_catalog_doc(repo)
    if not doc:
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

def library_preview(repo, book, gid, chart_n):
    """Full PNG for this chart, else the tab thumbnail for chart 1 or a table.

    Chart 2 and later never borrow chart 1's thumbnail. A missing file is no preview.
    """
    n = chart_n or 1
    full_name = "%d.png" % gid if n == 1 else "%d_%d.png" % (gid, n)
    full = os.path.join(repo, "static", "sheets", "full", str(book), full_name)
    if os.path.isfile(full) and os.path.getsize(full) > 0:
        return full
    if n == 1:
        thumb = os.path.join(repo, "static", "sheets", "thumbs", str(book), "%d.jpg" % gid)
        if os.path.isfile(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    return None

def try_fit(src, dst):
    """Fit one library preview. A blank or unreadable file is skipped, not fatal."""
    try:
        fit(src, dst)
    except (SystemExit, OSError, ValueError):
        if os.path.isfile(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        return False
    return True

def pool_items(catalog, repo=None):
    """Every eligible chart, plus one item per chartless table tab.

    Order is (book, gid, chart_n) so the dated draw does not depend on catalog order.
    EXCLUDED_TABS and COTD_EXCLUDED are not in the pool. A tab with several charts
    contributes one item per chart, each with its own chart_n. A chart titled
    "Chart N" (or with no title) is left out. So is a chart whose own PNG is blank,
    and a chart the card would title "Chart N" when that chart has no image of its own.
    """
    if not catalog:
        return []
    books = {}
    for b in catalog.get("books") or []:
        if isinstance(b, dict) and b.get("id"):
            books[str(b["id"])] = b
    out = []
    for tab in catalog.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        book = str(tab.get("book") or "").strip()
        try:
            gid = int(tab.get("gid"))
        except (TypeError, ValueError):
            continue
        if not book or sheets_catalog.tab_excluded(book, gid) or (book, gid) in COTD_EXCLUDED:
            continue
        meta = books.get(book) or {}
        charts = chart_rows(tab)
        if not charts:
            continue
        for c in charts:
            kind = "table" if c.get("synthetic") else "chart"
            title = norm_title(tab.get("tab")) if kind == "table" else c["label"]
            if not title:
                continue
            if kind == "chart" and sheets_catalog.untitled_chart_title(c.get("title")):
                continue
            src = library_preview(repo, book, gid, c["n"]) if repo and kind == "chart" else None
            if kind == "chart" and src and file_is_blank(src):
                continue
            # Duplicate real titles are labeled "Chart N". With no image of its own,
            # that card is the blank or untitled chart and is not drawn.
            if kind == "chart" and repo and sheets_catalog.untitled_chart_title(c.get("label")) and not src:
                continue
            out.append({
                "book": book,
                "spreadsheetId": str(meta.get("spreadsheetId") or "").strip(),
                "sheet": meta.get("name") or book,
                "tab": tab.get("tab") or "",
                "gid": gid,
                "chart_n": c["n"],
                "title": title,
                "kind": kind,
            })
    out.sort(key=lambda it: (it["book"], it["gid"], it["chart_n"], it["kind"], it["title"]))
    return out

def draw_seed(date, seed=None):
    """Stable integer seed. The default is the edition date."""
    text = str(date if seed is None else seed)
    digest = hashlib.sha256(("cotd-v1\n" + text).encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")

def today_et():
    """Calendar date of an 8 AM ET refresh. America/New_York, not the host's UTC date."""
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()

def _find_tab(catalog, book, gid):
    for tab in catalog.get("tabs") or []:
        if not isinstance(tab, dict) or str(tab.get("book") or "").strip() != book:
            continue
        try:
            if int(tab.get("gid")) == int(gid):
                return tab
        except (TypeError, ValueError):
            continue
    return None

def explain_ineligible(book, gid, chart_n, catalog, repo):
    """Why this chart is outside the pool, or None when a pick of it is allowed.

    The checks match pool_items: excluded tabs, an unknown book or gid, a chart
    index that is not on the tab, an untitled chart, and a blank library image.
    """
    books = {}
    for b in catalog.get("books") or []:
        if isinstance(b, dict) and b.get("id"):
            books[str(b["id"])] = b
    if book not in books:
        return "unknown book %s" % book
    tab = _find_tab(catalog, book, gid)
    if tab is None:
        return "unknown tab %s gid %s" % (book, gid)
    name = tab.get("tab") or book
    if sheets_catalog.tab_excluded(book, gid) or (book, gid) in COTD_EXCLUDED:
        return "excluded tab (%s gid %s) and cannot be picked" % (name, gid)
    charts = chart_rows(tab)
    chosen = chart_n
    if chosen is None:
        if len(charts) == 1:
            chosen = charts[0]["n"]
        elif not charts:
            return "tab %s (gid %s) has nothing to pick" % (name, gid)
        else:
            listed = ", ".join("%s (%s)" % (c["n"], c["label"]) for c in charts)
            return "tab %s (gid %s) has more than one chart; set chart to one of: %s" % (name, gid, listed)
    match = next((c for c in charts if c["n"] == chosen), None)
    if match is None:
        return "chart %s is not on %s (gid %s)" % (chosen, name, gid)
    kind = "table" if match.get("synthetic") else "chart"
    if kind == "chart" and sheets_catalog.untitled_chart_title(match.get("title")):
        return "chart %s on %s (gid %s) has no title and cannot be picked" % (chosen, name, gid)
    src = library_preview(repo, book, gid, chosen) if repo and kind == "chart" else None
    if kind == "chart" and src and file_is_blank(src):
        return "chart %s on %s (gid %s) is blank and cannot be picked" % (chosen, name, gid)
    if kind == "chart" and repo and sheets_catalog.untitled_chart_title(match.get("label")) and not src:
        return "chart %s on %s (gid %s) has no title of its own and cannot be picked" % (chosen, name, gid)
    return None

def parse_pick_gid(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value.strip())
    return None

def parse_pick_chart(raw, index):
    """The optional chart index, or None when the curator left it out."""
    if "chart" not in raw or raw.get("chart") in (None, ""):
        return None
    n = parse_chart_n(raw.get("chart"))
    if n in (None, "bad"):
        sys.exit("pick %d: chart must be an integer from 1 to 99" % index)
    return n

def resolve_pick(raw, index, catalog, pool, repo):
    """One curator pick turned into a pool item, or SystemExit with a pick-numbered reason."""
    if not isinstance(raw, dict):
        sys.exit("pick %d: must be an object with book, gid, description, and reason" % index)
    book = str(raw.get("book") or "").strip()
    if not book:
        sys.exit("pick %d: missing book" % index)
    gid = parse_pick_gid(raw.get("gid"))
    if gid is None:
        sys.exit("pick %d: gid must be an integer" % index)
    description = raw.get("description")
    reason = raw.get("reason")
    if not isinstance(description, str) or not description.strip():
        sys.exit("pick %d: description must be a non-empty string" % index)
    if not isinstance(reason, str) or not reason.strip():
        sys.exit("pick %d: reason must be a non-empty string" % index)
    chart_n = parse_pick_chart(raw, index)
    why = explain_ineligible(book, gid, chart_n, catalog, repo)
    if why:
        sys.exit("pick %d: %s" % (index, why))
    hits = [it for it in pool if it["book"] == book and it["gid"] == gid]
    if chart_n is not None:
        hits = [it for it in hits if it["chart_n"] == chart_n]
    if len(hits) != 1:
        sys.exit("pick %d: %s gid %s is not eligible" % (index, book, gid))
    item = dict(hits[0])
    item["description"] = description.strip()
    item["reason"] = reason.strip()
    item["section"] = "picks"
    return item

def load_picks(path, repo, date):
    """Read exactly 5 picks. Reject excluded, blank, unknown, and recent repeats.

    The file is a JSON array of {book, gid, chart, description, reason}. chart is
    the catalog chart number and may be omitted when the tab has one chart or is
    a table. An object {"picks": [...]} is accepted too.
    """
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        sys.exit("could not read picks file %s: %s" % (path, e))
    except ValueError as e:
        sys.exit("could not read picks file %s: %s" % (path, e))
    if isinstance(doc, dict) and isinstance(doc.get("picks"), list):
        rows = doc["picks"]
    elif isinstance(doc, list):
        rows = doc
    else:
        sys.exit("picks file must be a JSON array of {book, gid, chart, description, reason}")
    if len(rows) != PICKS_SIZE:
        sys.exit("picks file must contain exactly %d picks, got %d" % (PICKS_SIZE, len(rows)))
    catalog = load_catalog_doc(repo)
    if not catalog:
        sys.exit("missing static/sheets/catalog.json")
    pool = pool_items(catalog, repo)
    recent = recent_charts(repo, date)
    resolved = []
    for i, raw in enumerate(rows, 1):
        item = resolve_pick(raw, i, catalog, pool, repo)
        prev = repeated_recent(item, recent)
        if prev:
            chart = item.get("chart_n")
            extra = (" chart %s" % chart) if chart else ""
            sys.exit("pick %d repeats a chart from %s (%s gid %s%s) and cannot be picked"
                     % (i, prev["date"], item.get("tab"), item.get("gid"), extra))
        for j, earlier in enumerate(resolved, 1):
            if repeated_recent(item, [as_recent(earlier, date)]):
                sys.exit("pick %d repeats pick %d (%s gid %s chart %s)"
                         % (i, j, item.get("tab"), item.get("gid"), item.get("chart_n")))
        resolved.append(item)
    return resolved

def theme_line(n, picks=0):
    noun = "item" if n == 1 else "items"
    if picks:
        pick_noun = "pick" if picks == 1 else "picks"
        return "%d %s, plus %d %s drawn at random from the sheet library." % (picks, pick_noun, n, noun)
    return "%d %s drawn at random from the sheet library." % (n, noun)

def select_edition(repo, date, seed=None, size=None, block=None):
    """Uniform sample of up to `size` eligible items. The list order is the draw order.

    block is today's picks. Those charts leave the random pool, on top of the
    previous three editions. Omitting block keeps the draw the random-only edition uses.
    """
    catalog = load_catalog_doc(repo)
    if not catalog:
        sys.exit("missing static/sheets/catalog.json")
    recent = recent_charts(repo, date)
    for extra in block or []:
        row = as_recent(extra, date)
        if row:
            recent.append(row)
    eligible = [it for it in pool_items(catalog, repo) if not repeated_recent(it, recent)]
    k = EDITION_SIZE if size is None else int(size)
    k = max(0, min(k, len(eligible)))
    if k < 1:
        return []
    rng = random.Random(draw_seed(date, seed))
    return rng.sample(eligible, k)

def validate(items, recent=None):
    """Accept 0 through 20 items. Reject anything over the cap, an excluded tab, or a recent repeat."""
    n = len(items)
    if n > EDITION_MAX:
        sys.exit("need at most %d items, got %d" % (EDITION_MAX, n))
    req = ["spreadsheetId", "sheet", "tab", "title"]
    for i, x in enumerate(items, 1):
        miss = [k for k in req if not str(x.get(k, "")).strip()]
        if miss: sys.exit(f"item {i} missing {miss}")
        if pick_excluded(x):
            sys.exit(f"item {i} is an excluded tab ({x.get('tab')} gid {x.get('gid')}) and cannot be published")
        prev = repeated_recent(x, recent)
        if prev:
            chart = x.get("chart_n")
            extra = (" chart %s" % chart) if chart else ""
            sys.exit("item %d repeats a chart from %s (%s gid %s%s) and cannot be published"
                     % (i, prev["date"], x.get("tab"), x.get("gid"), extra))

def publish(P, repo, keep_old):
    d = P["date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d): sys.exit("date must be YYYY-MM-DD")
    picks = [dict(x) for x in (P.get("picks") or [])]
    items = [dict(x) for x in P["items"]]
    # Resolve chart_n before the repeat check so a single-chart tab matches chart 1 in history.
    catalog = load_catalog(repo)
    assign_chart_ns(picks, catalog)
    assign_chart_ns(items, catalog)
    recent = recent_charts(repo, d)
    if picks and len(picks) != PICKS_SIZE:
        sys.exit("need exactly %d picks, got %d" % (PICKS_SIZE, len(picks)))
    for i, pick in enumerate(picks, 1):
        if not str(pick.get("description") or "").strip() or not str(pick.get("reason") or "").strip():
            sys.exit("pick %d needs a description and a reason" % i)
        pick["description"] = str(pick["description"]).strip()
        pick["reason"] = str(pick["reason"]).strip()
        pick["section"] = "picks"
    if picks:
        for it in items:
            it["section"] = "random"
    validate(items, recent)
    if picks:
        validate(picks, recent)
        seen = []
        for i, pick in enumerate(picks, 1):
            if repeated_recent(pick, seen):
                sys.exit("pick %d repeats an earlier pick (%s gid %s)" % (i, pick.get("tab"), pick.get("gid")))
            row = as_recent(pick, d)
            if row:
                seen.append(row)
        blocked = [row for row in (as_recent(p, d) for p in picks) if row]
        for i, it in enumerate(items, 1):
            if repeated_recent(it, blocked):
                sys.exit("item %d repeats one of today's picks (%s gid %s) and cannot be published"
                         % (i, it.get("tab"), it.get("gid")))
    ordered = picks + items
    root = os.path.join(repo, "static", "cotd"); out = os.path.join(root, d)
    tmp = out + ".tmp"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    rows, card_items = [], []
    for x in ordered:
        key = pick_chart_key(x)
        src = str(x.get("png") or "").strip()
        from_library = False
        if not src and key:
            src = library_preview(repo, key[0], key[1], key[2] or 1) or ""
            from_library = bool(src)
        if from_library and src and file_is_blank(src):
            if x.get("section") == "picks":
                shutil.rmtree(tmp, ignore_errors=True)
                sys.exit("pick image is blank (%s gid %s) and cannot be published" % (x.get("tab"), x.get("gid")))
            print(f"--  blank image, skipped  {x['sheet']} / {x['tab']}")
            continue
        fn = f"{len(rows) + 1:02d}.png"
        image = ""
        nbytes = 0
        if src:
            dest = os.path.join(tmp, fn)
            if from_library:
                if try_fit(src, dest):
                    image = f"cotd/{d}/{fn}"
                    nbytes = os.path.getsize(dest)
            else:
                nbytes = fit(src, dest)
                image = f"cotd/{d}/{fn}"
        sid = str(x.get("spreadsheetId") or "").strip()
        url = ""
        if sid:
            url = f"https://docs.google.com/spreadsheets/d/{sid}/edit" + (f"#gid={x['gid']}" if x.get("gid") not in (None, "") else "")
        row = {"sheet": x["sheet"], "tab": x["tab"], "title": x["title"],
               "sheet_url": x.get("sheet_url") or url}
        if image:
            row["image"] = image
        if x.get("kind") in ("chart", "table"):
            row["kind"] = x["kind"]
        if x.get("chart_n"):
            row["chart_n"] = x["chart_n"]
        if x.get("section") == "picks":
            row["section"] = "picks"
            row["description"] = x.get("description") or ""
            row["reason"] = x.get("reason") or ""
        elif x.get("section") == "random":
            row["section"] = "random"
        rows.append(row)
        card_items.append(card_record(x))
        if image:
            print(f"{fn}  {nbytes//1024} KB  {x['sheet']} / {x['tab']}")
        else:
            print(f"--  no preview  {x['sheet']} / {x['tab']}")
    pick_rows = [r for r in rows if r.get("section") == "picks"]
    random_rows = [r for r in rows if r.get("section") != "picks"]
    cards_doc = {"date": d, "items": card_items}
    if picks:
        cards_doc["picks"] = [c for c in card_items if c.get("section") == "picks"]
        cards_doc["random"] = [c for c in card_items if c.get("section") == "random"]
    with open(os.path.join(tmp, "cards.json"), "w", encoding="utf-8") as f:
        json.dump(cards_doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    shutil.rmtree(out, ignore_errors=True); os.rename(tmp, out)
    # items stays the random list, or the whole edition when there are no picks.
    # Older editions have no picks array and the page renders items as one list.
    doc = {"date": d, "theme": (P.get("theme") or "").strip(), "items": random_rows if picks else rows}
    if pick_rows:
        doc["picks"] = pick_rows
    if P.get("random"):
        doc["random"] = True
    t = os.path.join(root, "today.json")
    with open(t + ".tmp", "w") as f: json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(t + ".tmp", t)
    if keep_old:
        ed = sorted(x for x in os.listdir(root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x))
        for old in ed[:-keep_old]: shutil.rmtree(os.path.join(root, old)); print("removed old edition", old)
    if picks:
        print("wrote", t, "(%d picks, %d random)" % (len(pick_rows), len(random_rows)))
    else:
        print("wrote", t, "(%d items)" % len(rows))
    return doc

def publish_random(repo, date, keep_old, seed=None, picks=None):
    """Draw the edition and write it. This is the daily path.

    picks is the curator's 5 charts, already checked against the catalog. They
    are published first and removed from the random draw. No picks file means
    a random-only edition, with no picks section.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        sys.exit("date must be YYYY-MM-DD")
    picks = list(picks or [])
    items = select_edition(repo, date, seed, block=picks)
    return publish({
        "date": date,
        "theme": theme_line(len(items), len(picks)),
        "random": True,
        "items": items,
        "picks": picks,
    }, repo, keep_old)

def self_test():
    import tempfile
    bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
    bot2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
    assert EDITION_MAX == 20 and EDITION_SIZE == 20 and PICKS_SIZE == 5
    assert ("bot2", 645072508) in COTD_EXCLUDED
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1547332232})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 643299192})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 333185169})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1791345316})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1370617918})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1024131985})
    assert pick_excluded({"spreadsheetId": bot2, "gid": 645072508})
    # Retired by Mark on 2026-11-10, 2026-11-11, 2026-11-12, 2026-11-13, and 2026-11-14. Adding a gid to EXCLUDED_TABS is enough.
    for gid in (292208014, 2119398138, 1584345646, 1186940001, 501919598, 579853621,
                1308503061, 748431671, 254001772,
                1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, 1067583608,
                1924976898, 1978525586, 1255387315, 1522766504, 1163669375, 1576800449,
                1192979444, 1912487083, 664060481, 1778640335,
                1476428159, 1459587255, 687897000, 546391999, 195161343, 321339250, 2128724732):
        assert pick_excluded({"spreadsheetId": bot1, "gid": gid}), gid
    assert pick_excluded({"spreadsheetId": bot2, "gid": 418894352})
    assert not pick_excluded({"spreadsheetId": "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU", "gid": 1536200109})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1476428159})
    # Neighbors that stay: Korea/Taiwan's neighbor, CoreWeave backlog, CPI charts, tariff rate, real yield.
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 171599627})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 200447618})  # 52 Core goods vs services
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 829703707})  # 18 Electricity vs CPI
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 972156540})  # 67 Effective tariff rate
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1167074374})  # 299 Real yield vs breakeven
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1027143376})  # 97 MSTR vol vs BTC vol
    assert not pick_excluded({"spreadsheetId": bot2, "gid": 530554790})  # 75 CoreWeave backlog vs revenue
    assert not pick_excluded({"spreadsheetId": bot2, "gid": 1713824403})  # 55 Capex intensity vs legacy
    assert not pick_excluded({"spreadsheetId": bot1, "gid": ""})
    live = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Retired charts stay out of the published edition and the sheet catalog.
    # 1024131985 is Strategy tab 99 (MSTR BTC per share), removed in PR #34.
    # 1791345316 and 1370617918 were deleted on 2026-11-09.
    # 645072508 stays in the catalog (Charts of the day only) and is not in this list.
    # The 2026-11-10, 2026-11-11, 2026-11-12, 2026-11-13, and 2026-11-14 gids are the charts Mark retired on those days.
    # 1536200109 (Gold Priced in BTC) stays in the catalog. Its blank chart image does not.
    retired_live = (1547332232, 643299192, 333185169, 1024131985, 1791345316, 1370617918,
                    292208014, 2119398138, 1584345646, 1186940001, 501919598, 579853621, 418894352,
                    1308503061, 748431671, 254001772,
                    1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, 1067583608,
                    1924976898, 1978525586, 1255387315, 1522766504, 1163669375, 1576800449,
                    1192979444, 1912487083, 664060481, 1778640335,
                    1476428159, 1459587255, 687897000, 546391999, 195161343, 321339250, 2128724732)
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
    for gid in (530554790, 200447618, 829703707, 972156540, 1167074374, 978356289):
        assert str(gid) in catalog_blob, gid
    assert today_doc["date"] == "2026-11-14"
    assert len(today_doc["picks"]) == 3, len(today_doc["picks"])
    assert len(today_doc["items"]) == 15, len(today_doc["items"])
    assert "Chart 2" not in today_blob and "1536200109" not in today_blob
    cards_live = json.load(open(os.path.join(live, "static/cotd/2026-11-14/cards.json"), encoding="utf-8"))
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

    rejected([item(i) for i in range(1, 22)], "need at most %d items, got 21" % EDITION_MAX)
    validate([])
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
        (292208014, "98 MSTR vs BTC drawdowns", bot1),
        (418894352, "81 CoreWeave margins", bot2),
        (2119398138, "151 Big Tech capex vs Nvidia", bot1),
        (1584345646, "223 Micron revenue & margin", bot1),
        (1186940001, "226 OpenAI vs Anthropic revenue", bot1),
        (501919598, "263 Tariff pass-through", bot1),
        (579853621, "12 Breakeven vs CPI", bot1),
        (1308503061, "122 Bond vol vs stock vol", bot1),
        (748431671, "232 Quits vs wage growth", bot1),
        (254001772, "162 Factory construction boom", bot1),
        (1238930141, "153 Big Tech capex intensity", bot1),
        (1835714359, "43 Nasdaq vs S&P ratio", bot1),
        (492285771, "109 Equal vs cap-weight S&P", bot1),
        (220064271, "135 Real coffee & cocoa", bot1),
        (79264442, "173 Continuing/initial claims", bot1),
        (1755343475, "239 Foreign vs native-born jobs", bot1),
        (1067583608, "233 Older workers participation", bot1),
    ):
        items = [item(i) for i in range(1, EDITION_MAX + 1)]
        items[4] = item(5, gid=gid, tab=tab, spreadsheetId=sid)
        rejected(items, f"gid {gid}")
    neighbor = [item(i) for i in range(1, 4)]
    neighbor[1] = item(2, gid=171599627, tab="160 China vs Mexico vs Taiwan", spreadsheetId=bot1)
    validate(neighbor)
    kept_cpi = [item(i) for i in range(1, 4)]
    kept_cpi[0] = item(1, gid=200447618, tab="52 Core goods vs services", spreadsheetId=bot1)
    kept_cpi[1] = item(2, gid=972156540, tab="67 Effective tariff rate", spreadsheetId=bot1)
    kept_cpi[2] = item(3, gid=1167074374, tab="299 Real yield vs breakeven", spreadsheetId=bot1)
    validate(kept_cpi)

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
            assert "need at most 20 items, got 21" in str(e), e
        else:
            raise SystemExit("21 items were published")
        assert not os.path.exists(os.path.join(repo, "static", "cotd", "today.json"))
        assert not os.path.isdir(os.path.join(repo, "static", "cotd", "2099-01-15"))
        short = {"date": "2099-01-15", "theme": " ten is allowed ", "items": [item(i, png=src) for i in range(1, 11)]}
        doc10 = publish(short, repo, 0)
        assert len(doc10["items"]) == 10, len(doc10["items"])
        picks = [item(i, png=src) for i in range(1, EDITION_MAX + 1)]
        doc = publish({"date": "2099-01-20", "theme": " twenty charts ", "items": picks}, repo, 0)
        assert doc["theme"] == "twenty charts"
        assert len(doc["items"]) == 20, len(doc["items"])
        titles = [row["title"] for row in doc["items"]]
        assert titles == [f"Chart {i:02d}" for i in range(1, 21)], titles
        assert "picked" not in doc["items"][0] and "matters" not in doc["items"][0] and "know" not in doc["items"][19]
        assert "picked" not in doc["items"][19] and "random" not in doc
        folder = os.path.join(repo, "static", "cotd", "2099-01-20")
        names = sorted(os.listdir(folder))
        assert names == [f"{i:02d}.png" for i in range(1, 21)] + ["cards.json"], names
        with open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8") as f:
            saved = json.load(f)
        assert [row["image"] for row in saved["items"]] == [f"cotd/2099-01-20/{i:02d}.png" for i in range(1, 21)]
        with open(os.path.join(folder, "cards.json"), encoding="utf-8") as f:
            saved_cards = json.load(f)
        assert saved_cards["date"] == "2099-01-20"
        assert saved_cards["items"][0]["book"] == "bot1" and saved_cards["items"][0]["gid"] == 900001
        assert "chart_n" not in saved_cards["items"][0]
        assert all("chart_n" not in row for row in doc["items"])
        stated = publish({"date": "2099-01-24", "theme": "stated position", "items": [item(1, png=src, chart_n=2)]}, repo, 0)
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
        placed_doc = publish({"date": "2099-01-28", "theme": "positions", "items": placed}, repo, 0)
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
        assert still["date"] == "2099-01-28", still["date"]
        try:
            publish({"date": "2099-01-19", "theme": "bad table", "items": [
                item(1, png=src, gid=910002, tab="a table", title="table", chart_n=2)]}, repo, 0)
        except SystemExit as e:
            assert "chart_n 2" in str(e), e
        else:
            raise SystemExit("chart 2 was accepted on a table")
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
            blocked_recent = [item(i, png=src) for i in range(1, 4)]
            blocked_recent[1] = item(2, png=src, gid=222, tab="d3", spreadsheetId=bot1, chart_n=1)
            try:
                publish({"date": "2099-03-05", "theme": "repeat", "items": blocked_recent}, hist, 0)
            except SystemExit as e:
                assert "2099-03-02" in str(e) and "gid 222" in str(e), e
            else:
                raise SystemExit("a chart from the previous 3 days was published")
            assert not os.path.exists(os.path.join(hist, "static", "cotd", "today.json"))
            fresh = publish({"date": "2099-03-05", "theme": "fresh", "items": [
                item(1, png=src, gid=333, tab="other chart", spreadsheetId=bot2, chart_n=1)]}, hist, 0)
            assert fresh["items"][0]["chart_n"] == 1
            saved_recent = json.load(open(os.path.join(hist, "static", "cotd", "2099-03-05", "cards.json"), encoding="utf-8"))
            assert saved_recent["items"][0]["book"] == "bot2" and saved_recent["items"][0]["gid"] == 333
            assert saved_recent["items"][0]["chart_n"] == 1
        finally:
            shutil.rmtree(hist, ignore_errors=True)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(os.path.dirname(src), ignore_errors=True)
    random_self_test()
    print("self-test ok")

def random_self_test():
    """Exclusions, table eligibility, a draw of 20, and the 3-day no-repeat rule."""
    import tempfile
    bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
    bot2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
    catalog = {
        "books": [
            {"id": "bot1", "name": "Bot experiments", "spreadsheetId": bot1},
            {"id": "bot2", "name": "bot experiments 2", "spreadsheetId": bot2},
        ],
        "tabs": [
            {"book": "bot1", "tab": "161 Nvidia P-S and P-E", "gid": 1547332232, "charts": ["retired"], "chartList": [{"n": 1, "title": "retired"}]},
            {"book": "bot2", "tab": "69 Mark MSTR red dot tracker", "gid": 645072508, "charts": ["red"], "chartList": [{"n": 1, "title": "red"}]},
            {"book": "bot1", "tab": "a table", "gid": 910002, "charts": [], "chartList": []},
            {"book": "bot1", "tab": "two", "gid": 910003, "charts": ["Alpha", "Beta"],
             "chartList": [{"n": 1, "title": "Alpha"}, {"n": 2, "title": "Beta"}]},
        ],
    }
    for i in range(1, 21):
        catalog["tabs"].append({
            "book": "bot1", "tab": "chart %02d" % i, "gid": 920000 + i,
            "charts": ["Sample %02d" % i], "chartList": [{"n": 1, "title": "Sample %02d" % i}],
        })
    pool = pool_items(catalog)
    keys = {(it["book"], it["gid"], it["chart_n"]) for it in pool}
    assert ("bot1", 1547332232, 1) not in keys
    assert ("bot2", 645072508, 1) not in keys
    tables = [it for it in pool if it["kind"] == "table"]
    assert len(tables) == 1 and tables[0]["gid"] == 910002 and tables[0]["chart_n"] == 1, tables
    assert ("bot1", 910003, 1) in keys and ("bot1", 910003, 2) in keys
    assert len(pool) == 23, len(pool)  # 20 single charts + 2 panels + 1 table
    untitled = {
        "books": catalog["books"],
        "tabs": [
            {"book": "bot1", "tab": "blank chart", "gid": 940001,
             "charts": ["Chart 2"], "chartList": [{"n": 1, "title": "Chart 2"}]},
            {"book": "bot1", "tab": "real", "gid": 940002,
             "charts": ["A real title"], "chartList": [{"n": 1, "title": "A real title"}]},
            {"book": "bot1", "tab": "padded", "gid": 940003,
             "charts": ["Chart 01"], "chartList": [{"n": 1, "title": "Chart 01"}]},
        ],
    }
    untitled_gids = {it["gid"] for it in pool_items(untitled)}
    assert 940001 not in untitled_gids and 940002 in untitled_gids and 940003 in untitled_gids
    from PIL import Image as _Image
    blank_repo = tempfile.mkdtemp(prefix="cotd-blank-")
    try:
        full = os.path.join(blank_repo, "static", "sheets", "full", "bot1")
        os.makedirs(full)
        empty = _Image.new("RGB", (100, 60), "white")
        epx = empty.load()
        for y in range(24, 40):
            for x in range(10, 90):
                shade = 150 + ((x + y) % 20)
                epx[x, y] = (shade, shade, shade)
        for i in range(20):
            epx[i, 0] = (230 - i, 230 - i, 230 - i)
        empty.save(os.path.join(full, "940004.png"))
        kept = _Image.new("RGB", (80, 40), "white")
        for x in range(80):
            kept.putpixel((x, 8 + (x % 17)), (66, 133, 244))
            kept.putpixel((x, 36), (90 + (x % 50), 40, 40))
        for y in range(40):
            kept.putpixel((2, y), (20, 20, 20))
        kept.save(os.path.join(full, "940005.png"))
        kept.save(os.path.join(full, "940006.png"))
        assert file_is_blank(os.path.join(full, "940004.png"))
        assert not file_is_blank(os.path.join(full, "940005.png"))
        blank_cat = {
            "books": catalog["books"],
            "tabs": [
                {"book": "bot1", "tab": "empty google", "gid": 940004,
                 "charts": ["Has a title"], "chartList": [{"n": 1, "title": "Has a title"}]},
                {"book": "bot1", "tab": "kept line", "gid": 940005,
                 "charts": ["Kept line"], "chartList": [{"n": 1, "title": "Kept line"}]},
                {"book": "bot1", "tab": "dup gold", "gid": 940006, "charts": ["Same title"],
                 "chartList": [{"n": 1, "title": "Same title"}, {"n": 2, "title": "Same title"}]},
            ],
        }
        got = {(it["gid"], it["chart_n"]) for it in pool_items(blank_cat, blank_repo)}
        assert (940004, 1) not in got and (940005, 1) in got, got
        assert (940006, 1) in got and (940006, 2) not in got, got
    finally:
        shutil.rmtree(blank_repo, ignore_errors=True)
    repo = tempfile.mkdtemp(prefix="cotd-random-")
    try:
        cat_dir = os.path.join(repo, "static", "sheets")
        os.makedirs(cat_dir)
        with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        # 2099-05-05 looks at 05-04, 05-03, 05-02. Block the table, chart 01, and only panel 2.
        for date, rows in (
            ("2099-05-02", [{"book": "bot1", "gid": 920001, "chart_n": 1, "tab": "chart 01", "title": "Chart 01"}]),
            ("2099-05-03", [{"book": "bot1", "gid": 910003, "chart_n": 2, "tab": "two", "title": "Beta"}]),
            ("2099-05-04", [{"book": "bot1", "gid": 910002, "chart_n": 1, "tab": "a table", "title": "a table", "kind": "table"}]),
        ):
            folder = os.path.join(repo, "static", "cotd", date)
            os.makedirs(folder)
            with open(os.path.join(folder, "cards.json"), "w", encoding="utf-8") as f:
                json.dump({"date": date, "items": rows}, f)
        drawn = select_edition(repo, "2099-05-05")
        assert len(drawn) == 20, len(drawn)
        got = {(it["book"], it["gid"], it["chart_n"]) for it in drawn}
        assert ("bot1", 920001, 1) not in got
        assert ("bot1", 910003, 2) not in got
        assert ("bot1", 910002, 1) not in got
        assert ("bot1", 1547332232, 1) not in got
        assert ("bot2", 645072508, 1) not in got
        assert ("bot1", 910003, 1) in got  # the other panel on a repeated tab stays eligible
        assert drawn == select_edition(repo, "2099-05-05")
        assert select_edition(repo, "2099-05-05", seed="other") != drawn
        # Fewer than 20 eligible: publish that many, including a table, and do not invent notes.
        short_cat = {
            "books": catalog["books"],
            "tabs": [
                {"book": "bot1", "tab": "only", "gid": 930001, "charts": ["Only"], "chartList": [{"n": 1, "title": "Only"}]},
                {"book": "bot1", "tab": "plain table", "gid": 930002, "charts": [], "chartList": []},
                {"book": "bot1", "tab": "161 Nvidia P-S and P-E", "gid": 1547332232, "charts": [], "chartList": []},
            ],
        }
        with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(short_cat, f)
        short = select_edition(repo, "2099-06-01")
        assert len(short) == 2, short
        assert {it["kind"] for it in short} == {"chart", "table"}
        doc = publish_random(repo, "2099-06-01", 0)
        assert doc["random"] is True
        assert doc["theme"] == "2 items drawn at random from the sheet library."
        assert len(doc["items"]) == 2
        for row in doc["items"]:
            assert "picked" not in row and "matters" not in row and "know" not in row
            assert row.get("chart_n") == 1
        kinds = {row["kind"] for row in doc["items"]}
        assert kinds == {"chart", "table"}, kinds
        table = next(row for row in doc["items"] if row["kind"] == "table")
        assert table["title"] == "plain table" and "image" not in table
        again = publish_random(repo, "2099-06-01", 0)
        assert [row["title"] for row in again["items"]] == [row["title"] for row in doc["items"]]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Draw a Charts of the day edition from the sheet catalog.")
    ap.add_argument("--repo", default=here)
    ap.add_argument("--date", default=None, help="Edition date YYYY-MM-DD. Default: today in America/New_York.")
    ap.add_argument("--seed", default=None, help="Override the draw seed. Default: the edition date.")
    ap.add_argument("--picks", default=None, metavar="FILE",
                    help="JSON array of exactly 5 picks: [{book, gid, chart, description, reason}]. "
                         "Omit for a random-only edition.")
    ap.add_argument("--keep-old", type=int, default=7, help="keep this many dated edition folders (0 = keep all)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test(); return
    date = a.date or today_et()
    picks = load_picks(a.picks, a.repo, date) if a.picks else None
    publish_random(a.repo, date, a.keep_old, seed=a.seed, picks=picks)

if __name__ == "__main__": main()
