#!/usr/bin/env python3
"""Copy weekly chart exports into static/sheets/full/<book>/<gid>.png.

Render serves those files as static assets. This script does not call Google.
It does not commit anything. The weekly routine runs it, then commits
static/sheets/full/.

Usage:
  python3 scripts/import_full_pngs.py --from-dir /path/to/exported-pngs
  python3 scripts/import_full_pngs.py --from-dir /path/to/exported-pngs --repo /workspace/chartmaker
  python3 scripts/import_full_pngs.py --from-dir /path/bot1 --from-dir /path/bot2 --dry-run
  python3 scripts/import_full_pngs.py --from-dir /path/to/exported-pngs --no-optimize
  python3 scripts/import_full_pngs.py --self-test

--from-dir is repeatable. Point it at one folder of PNGs, or at one folder per
workbook. Naming matches scripts/sheets_thumbs.py:

  090 TSLA gross margin vs price 2026-10-21.png
  001 BTC vol vs VIX.png
  MSTR Volume.png

A trailing YYYY-MM-DD is not part of the tab name. The leading number may be
zero-padded (090 matches the tab "90 TSLA ..."). When several files match one
tab, the newest wins: later date in the name, then later file mtime.

A subfolder (or the --from-dir folder itself) named with the book id, the
spreadsheet id, or the workbook title limits matches to that book. A folder
whose name is not a book is searched across all tabs; tab names are unique
across the three books today.

catalog.json supplies the gid. Output is only
static/sheets/full/<book>/<gid>.png. Files that match no tab are counted and
left unwritten. Existing full PNGs this run did not replace are left in place,
so a partial folder does not wipe the library.

Optimization, when --no-optimize is not set, uses the first tool on PATH:
oxipng, then optipng (both lossless), then pngquant (skipped when the result
is larger). If none of those is installed and Pillow is, the PNG is rewritten
with Pillow's lossless optimize flag. Otherwise the bytes are copied unchanged.
"""
import argparse, io, os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_catalog
import sheets_thumbs

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FULL_REL = re.compile(r"^sheets/full/([A-Za-z0-9][A-Za-z0-9_-]{0,24})/(\d+)(?:_([2-9]|[1-9]\d))?\.png$")
# The export names extra charts on a tab "<tab> (2).png", "<tab> (3).png", ... (before any date).
CHART_SUFFIX = re.compile(r"\s+\((\d{1,2})\)(?=(?:\s+\d{4}-\d{2}-\d{2})?\.png$)", re.I)


def split_chart_n(name):
    """Return (name without ' (n)', n). n is 1 when there is no suffix."""
    m = CHART_SUFFIX.search(name)
    if not m or int(m.group(1)) < 2:
        return name, 1
    return name[:m.start()] + name[m.end():], int(m.group(1))


def dest_for(repo, book, gid, n=1):
    rel = "sheets/full/%s/%d%s.png" % (book, int(gid), "" if int(n) <= 1 else "_%d" % int(n))
    if not FULL_REL.fullmatch(rel):
        raise SystemExit("refusing unsafe path " + rel)
    root = os.path.realpath(os.path.join(repo, "static", "sheets", "full"))
    os.makedirs(root, exist_ok=True)
    path = os.path.realpath(os.path.join(repo, "static", rel))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    path = os.path.realpath(path)
    if os.path.commonpath([root, path]) != root:
        raise SystemExit("refusing path outside static/sheets/full")
    return path, rel


def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def optimize(data):
    """Return (png_bytes, tool). tool is copied when nothing smaller is available."""
    if not data.startswith(PNG_MAGIC):
        raise ValueError("not a png")
    best, tool = data, "copied"
    fd, src = tempfile.mkstemp(prefix="fullpng-", suffix=".png")
    try:
        os.write(fd, data)
        os.close(fd)
        fd = None
        if shutil.which("oxipng"):
            r = _run(["oxipng", "-o", "2", "--strip", "safe", "-q", src])
            if r.returncode == 0:
                raw = open(src, "rb").read()
                if raw.startswith(PNG_MAGIC) and len(raw) <= len(best):
                    best, tool = raw, "oxipng"
            return best, tool
        if shutil.which("optipng"):
            r = _run(["optipng", "-o2", "-quiet", src])
            if r.returncode == 0:
                raw = open(src, "rb").read()
                if raw.startswith(PNG_MAGIC) and len(raw) <= len(best):
                    best, tool = raw, "optipng"
            return best, tool
        if shutil.which("pngquant"):
            out = src + ".q.png"
            r = _run(["pngquant", "--quality=90-100", "--skip-if-larger", "--force", "-o", out, src])
            try:
                if r.returncode == 0 and os.path.isfile(out):
                    raw = open(out, "rb").read()
                    if raw.startswith(PNG_MAGIC) and len(raw) <= len(best):
                        best, tool = raw, "pngquant"
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass
            return best, tool
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im.load()
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            raw = buf.getvalue()
            if raw.startswith(PNG_MAGIC) and len(raw) <= len(best):
                best, tool = raw, "pillow"
        except Exception:
            pass
        return best, tool
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.remove(src)
        except OSError:
            pass


def collect(dirs):
    files, skipped = [], []
    for d in dirs:
        if not os.path.isdir(d):
            raise SystemExit("not a folder: " + d)
        found, missing = sheets_thumbs.iter_dir(d)
        for m in missing:
            print("index.json entry missing on disk:", m)
        for f in found:
            if str(f.get("name") or "").lower().endswith(".png"):
                files.append(f)
            else:
                skipped.append(f["path"])
    return files, skipped


def chart_list_of(tabs, book, gid):
    """Non-empty chartList for this tab, or None when the catalog does not list charts."""
    for t in tabs or []:
        try:
            if str(t.get("book")) != str(book) or int(t.get("gid")) != int(gid):
                continue
        except (TypeError, ValueError):
            continue
        raw = t.get("chartList")
        if isinstance(raw, list) and raw:
            return raw
        return None
    return None

def skip_chart_image(tabs, book, gid, n):
    """True when this export must not be copied into the library.

    A chartList is the charts that still exist on the tab. An empty title, or
    Google's "Chart N", is an untitled chart. A tab with no chartList is unchanged.
    """
    listed = chart_list_of(tabs, book, gid)
    if not listed:
        return False
    found, title = False, ""
    for c in listed:
        if not isinstance(c, dict):
            continue
        try:
            cn = int(c.get("n"))
        except (TypeError, ValueError):
            continue
        if cn == int(n):
            found, title = True, c.get("title")
            break
    if not found:
        return True
    return sheets_catalog.untitled_chart_title(title)

def png_is_blank(data):
    """True when the bytes are a decodable image that is blank or near-uniform.

    Bytes that are not a real image (the self-test markers) are not blank.
    """
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return False
    return sheets_thumbs.blank(im)

def publish(repo, chosen, unmatched, ambiguous, optimize_png, dry):
    n = 0
    for key, cands in sorted(chosen.items(), key=lambda kv: kv[0]):
        book, gid = key[0], key[1]
        cn = key[2] if len(key) > 2 else 1
        src = cands[0]["path"]
        data = open(src, "rb").read()
        if not data.startswith(PNG_MAGIC):
            print("skip %s (not a png)" % src)
            unmatched.append(src)
            continue
        if png_is_blank(data):
            print("skip %s (blank chart image)" % os.path.basename(src))
            unmatched.append(src)
            continue
        tool = "copied"
        if optimize_png:
            try:
                data, tool = optimize(data)
            except ValueError as e:
                print("skip %s (%s)" % (src, e))
                unmatched.append(src)
                continue
        path, rel = dest_for(repo, book, gid, cn)
        if dry:
            print("match %s  %s  <= %s  (%s)" % (cands[0]["how"], rel, os.path.basename(src), tool))
        else:
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            print("wrote %s  %d KB  %s  <= %s" % (rel, (len(data) + 1023) // 1024, tool, os.path.basename(src)))
        n += 1
    summary = "matched %d, unmatched %d, ambiguous %d" % (n, len(unmatched), len(ambiguous))
    print(summary)
    if unmatched:
        print("unmatched, for example: " + ", ".join(os.path.basename(p) for p in unmatched[:8]))
    for path, why in ambiguous[:8]:
        print("ambiguous, skipped:", os.path.basename(path), "(", why, ")")
    return n, len(unmatched), len(ambiguous)


def run(repo, dirs, optimize_png, dry):
    doc, _path = sheets_thumbs.load_catalog(repo)
    books = doc.get("books") or []
    tabs = sheets_thumbs.visible_tabs(doc.get("tabs") or [])
    files, skipped = collect(dirs)
    groups = {}
    for f in files:
        base, n = split_chart_n(f["name"])
        groups.setdefault(n, []).append(dict(f, name=base))
    chosen, unmatched, ambiguous = {}, [], []
    for n, group in sorted(groups.items()):
        c, u, a = sheets_thumbs.match_files(group, books, tabs)
        for (book, gid), cands in c.items():
            if skip_chart_image(tabs, book, gid, n):
                unmatched.extend(cand["path"] for cand in cands)
                continue
            chosen[(book, gid, n)] = cands
        unmatched += list(u)
        ambiguous += list(a)
    unmatched = list(unmatched) + skipped
    return publish(repo, chosen, unmatched, ambiguous, optimize_png, dry)


def self_test():
    import json
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html"), encoding="utf-8").read()
    assert "sheets/full/" in html
    assert "/api/sheet-png" not in html
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" not in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "render.yaml"), encoding="utf-8").read()
    # Newest dated name wins. A file in the wrong book folder does not.
    # --no-optimize copies bytes after the PNG magic check, so the payload can be a marker.
    tiny = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0P\x0f\x00\x04\x85\x01\x80\x84\xa9\x8c!\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    old_bytes = PNG_MAGIC + b"OLD"
    new_bytes = PNG_MAGIC + b"NEW"
    repo = tempfile.mkdtemp(prefix="fullpng-repo-")
    src = tempfile.mkdtemp(prefix="fullpng-src-")
    try:
        os.makedirs(os.path.join(repo, "static", "sheets"))
        catalog = {
            "books": [
                {"id": "bot1", "name": "Bot experiments", "spreadsheetId": "sheetBOT1"},
                {"id": "bot2", "name": "bot experiments 2", "spreadsheetId": "sheetBOT2"},
            ],
            "tabs": [
                {"book": "bot1", "tab": "01 BTC vol vs VIX", "gid": 1546024641, "charts": []},
                {"book": "bot2", "tab": "MSTR Volume", "gid": 30, "charts": ["MSTR 30-day volume"]},
                {"book": "bot1", "tab": "90 TSLA gross margin vs price", "gid": 10, "charts": []},
                {"book": "bot1", "tab": "173 Continuing/initial claims", "gid": 173, "charts": []},
                {"book": "bot1", "tab": "285 Ethereum L2s: Base leads", "gid": 285, "charts": []},
            ],
        }
        with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        b1 = os.path.join(src, "bot1")
        os.makedirs(b1)
        older = os.path.join(b1, "001 BTC vol vs VIX.png")
        dated = os.path.join(b1, "001 BTC vol vs VIX 2026-11-02.png")
        noise = os.path.join(b1, "not a chart.png")
        tsla_old = os.path.join(b1, "090 TSLA gross margin vs price 2026-10-19.png")
        tsla_new = os.path.join(b1, "090 TSLA gross margin vs price 2026-10-21.png")
        slash = os.path.join(b1, "173 Continuing-initial claims.png")
        colon = os.path.join(b1, "285 Ethereum L2s- Base leads.png")
        for path, blob in ((slash, new_bytes), (colon, new_bytes), (older, old_bytes), (dated, new_bytes), (noise, old_bytes), (tsla_old, old_bytes), (tsla_new, new_bytes)):
            with open(path, "wb") as f:
                f.write(blob)
        os.utime(dated, (1_700_000_000, 1_700_000_000))  # older mtime, newer date in the name
        os.utime(older, (1_800_000_000, 1_800_000_000))
        mstr_dir = os.path.join(src, "bot2")
        os.makedirs(mstr_dir)
        mstr = os.path.join(mstr_dir, "MSTR Volume.png")
        with open(mstr, "wb") as f:
            f.write(old_bytes)
        # Same tab name dropped in the other book must not steal the match.
        stray = os.path.join(mstr_dir, "001 BTC vol vs VIX.png")
        with open(stray, "wb") as f:
            f.write(old_bytes)
        n, u, a = run(repo, [src], optimize_png=False, dry=False)
        assert n == 5, n
        assert u == 2, u  # noise.png and the stray in bot2
        assert a == 0, a
        got = open(os.path.join(repo, "static", "sheets", "full", "bot1", "1546024641.png"), "rb").read()
        assert got == new_bytes, "dated export should win over a newer mtime"
        assert open(os.path.join(repo, "static", "sheets", "full", "bot1", "10.png"), "rb").read() == new_bytes
        assert open(os.path.join(repo, "static", "sheets", "full", "bot1", "173.png"), "rb").read() == new_bytes
        assert open(os.path.join(repo, "static", "sheets", "full", "bot1", "285.png"), "rb").read() == new_bytes
        assert open(os.path.join(repo, "static", "sheets", "full", "bot2", "30.png"), "rb").read() == old_bytes
        # A catalog row for a deleted tab must not be refreshed from an export of the same name.
        catalog["tabs"].append({
            "book": "bot1", "tab": "161 Nvidia P-S and P-E", "gid": 1547332232,
            "charts": ["Nvidia valuation: price-to-sales and trailing P/E (month-end)"],
        })
        catalog["tabs"].append({
            "book": "bot1", "tab": "202 Nvidia revenue by geography", "gid": 643299192,
            "charts": ["Where Nvidia bills its sales: revenue share by customer billing location (quarterly)"],
        })
        with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        blocked = os.path.join(b1, "161 Nvidia P-S and P-E.png")
        blocked2 = os.path.join(b1, "202 Nvidia revenue by geography (2).png")
        kept = os.path.join(b1, "201 Nvidia data center revenue.png")
        korea = os.path.join(b1, "158 Korea & Taiwan vs S&P.png")
        korea2 = os.path.join(b1, "158 Korea & Taiwan vs S&P (2).png")
        supercore = os.path.join(b1, "163 Supercore vs core CPI.png")
        strategy = os.path.join(b1, "99 MSTR BTC per share.png")
        china = os.path.join(b1, "160 China vs Mexico vs Taiwan.png")
        red_dot = os.path.join(mstr_dir, "69 Mark MSTR red dot tracker.png")
        for path in (blocked, blocked2, korea, korea2, supercore, strategy):
            with open(path, "wb") as f:
                f.write(new_bytes)
        # Neighbor charts are not excluded; give them catalog rows and confirm they still import.
        # BX2-069 stays importable: it is Charts-of-the-day-only, not EXCLUDED_TABS.
        catalog["tabs"].extend([
            {"book": "bot1", "tab": "201 Nvidia data center revenue", "gid": 1476428159, "charts": []},
            {"book": "bot1", "tab": "158 Korea & Taiwan vs S&P", "gid": 1791345316,
             "charts": ["Asia's AI chip exporters: Korea (EWY) and Taiwan (EWT) vs the S&P 500"]},
            {"book": "bot1", "tab": "163 Supercore vs core CPI", "gid": 1370617918,
             "charts": ["The Fed's 'supercore': services ex-shelter inflation vs core and headline CPI"]},
            {"book": "bot1", "tab": "99 MSTR BTC per share", "gid": 1024131985,
             "charts": ["Strategy: bitcoin per diluted share vs share count (at each 8-K)"]},
            {"book": "bot1", "tab": "160 China vs Mexico vs Taiwan", "gid": 171599627, "charts": []},
            {"book": "bot2", "tab": "69 Mark MSTR red dot tracker", "gid": 645072508,
             "charts": ["Mark's Red Dot Tracker: MSTR"]},
            {"book": "bot2", "tab": "81 CoreWeave margins", "gid": 418894352,
             "charts": ["CoreWeave margins (%)"]},
            {"book": "bot1", "tab": "98 MSTR vs BTC drawdowns", "gid": 292208014,
             "charts": ["Strategy vs bitcoin: drawdown from record close"]},
            {"book": "bot1", "tab": "151 Big Tech capex vs Nvidia", "gid": 2119398138, "charts": []},
            {"book": "bot1", "tab": "223 Micron revenue & margin", "gid": 1584345646, "charts": []},
            {"book": "bot1", "tab": "226 OpenAI vs Anthropic revenue", "gid": 1186940001, "charts": []},
            {"book": "bot1", "tab": "263 Tariff pass-through", "gid": 501919598, "charts": []},
            {"book": "bot1", "tab": "12 Breakeven vs CPI", "gid": 579853621, "charts": []},
            {"book": "bot1", "tab": "122 Bond vol vs stock vol", "gid": 1308503061, "charts": []},
            {"book": "bot1", "tab": "232 Quits vs wage growth", "gid": 748431671, "charts": []},
            {"book": "bot1", "tab": "162 Factory construction boom", "gid": 254001772, "charts": []},
            {"book": "bot1", "tab": "153 Big Tech capex intensity", "gid": 1238930141, "charts": []},
            {"book": "bot1", "tab": "43 Nasdaq vs S&P ratio", "gid": 1835714359, "charts": []},
            {"book": "bot1", "tab": "109 Equal vs cap-weight S&P", "gid": 492285771, "charts": []},
            {"book": "bot1", "tab": "135 Real coffee & cocoa", "gid": 220064271, "charts": []},
            {"book": "bot1", "tab": "239 Foreign vs native-born jobs", "gid": 1755343475, "charts": []},
            {"book": "bot1", "tab": "233 Older workers participation", "gid": 1067583608, "charts": []},
            {"book": "bot1", "tab": "57 PPI vs CPI", "gid": 1924976898, "charts": []},
            {"book": "bot1", "tab": "55 Shelter vs home prices", "gid": 1978525586, "charts": []},
            {"book": "bot1", "tab": "115 Regional banks vs S&P", "gid": 1255387315, "charts": []},
            {"book": "bot1", "tab": "128 Long bonds vs stocks DD", "gid": 1522766504, "charts": []},
            {"book": "bot1", "tab": "186 Sahm rule recession gauge", "gid": 1163669375, "charts": []},
            {"book": "bot1", "tab": "225 Nvidia inventory & DSO", "gid": 1576800449, "charts": []},
            {"book": "bot1", "tab": "259 Financial conditions", "gid": 1192979444, "charts": []},
            {"book": "bot1", "tab": "285 Ethereum L2s: Base leads", "gid": 1912487083, "charts": []},
            {"book": "bot1", "tab": "322 Microsoft quarterly FCF", "gid": 664060481, "charts": []},
            {"book": "bot1", "tab": "324 Amazon quarterly FCF", "gid": 1778640335, "charts": []},
            {"book": "bot2", "tab": "75 CoreWeave backlog vs revenue", "gid": 530554790, "charts": []},
            {"book": "bot1", "tab": "97 MSTR vol vs BTC vol", "gid": 1027143376, "charts": []},
            {"book": "bot1", "tab": "52 Core goods vs services", "gid": 200447618, "charts": []},
            {"book": "bot1", "tab": "67 Effective tariff rate", "gid": 972156540, "charts": []},
            {"book": "bot1", "tab": "299 Real yield vs breakeven", "gid": 1167074374, "charts": []},
            {"book": "bot1", "tab": "18 Electricity vs CPI", "gid": 829703707, "charts": []},
        ])
        with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        blocked_new = [
            os.path.join(mstr_dir, "81 CoreWeave margins.png"),
            os.path.join(b1, "98 MSTR vs BTC drawdowns.png"),
            os.path.join(b1, "151 Big Tech capex vs Nvidia.png"),
            os.path.join(b1, "223 Micron revenue & margin.png"),
            os.path.join(b1, "226 OpenAI vs Anthropic revenue.png"),
            os.path.join(b1, "263 Tariff pass-through.png"),
            os.path.join(b1, "263 Tariff pass-through (2).png"),
            os.path.join(b1, "12 Breakeven vs CPI.png"),
            os.path.join(b1, "122 Bond vol vs stock vol.png"),
            os.path.join(b1, "232 Quits vs wage growth.png"),
            os.path.join(b1, "162 Factory construction boom.png"),
            os.path.join(b1, "153 Big Tech capex intensity.png"),
            os.path.join(b1, "43 Nasdaq vs S&P ratio.png"),
            os.path.join(b1, "109 Equal vs cap-weight S&P.png"),
            os.path.join(b1, "135 Real coffee & cocoa.png"),
            os.path.join(b1, "239 Foreign vs native-born jobs.png"),
            os.path.join(b1, "233 Older workers participation.png"),
            os.path.join(b1, "057 PPI vs CPI.png"),
            os.path.join(b1, "055 Shelter vs home prices.png"),
            os.path.join(b1, "115 Regional banks vs S&P.png"),
            os.path.join(b1, "128 Long bonds vs stocks DD.png"),
            os.path.join(b1, "186 Sahm rule recession gauge.png"),
            os.path.join(b1, "225 Nvidia inventory & DSO.png"),
            os.path.join(b1, "259 Financial conditions.png"),
            os.path.join(b1, "322 Microsoft quarterly FCF.png"),
            os.path.join(b1, "324 Amazon quarterly FCF.png"),
        ]
        kept_new = [
            (os.path.join(mstr_dir, "75 CoreWeave backlog vs revenue.png"), "bot2", "530554790.png"),
            (os.path.join(b1, "97 MSTR vol vs BTC vol.png"), "bot1", "1027143376.png"),
            (os.path.join(b1, "52 Core goods vs services.png"), "bot1", "200447618.png"),
            (os.path.join(b1, "67 Effective tariff rate.png"), "bot1", "972156540.png"),
            (os.path.join(b1, "299 Real yield vs breakeven.png"), "bot1", "1167074374.png"),
            (os.path.join(b1, "18 Electricity vs CPI.png"), "bot1", "829703707.png"),
        ]
        for path in (kept, china, red_dot):
            with open(path, "wb") as f:
                f.write(old_bytes)
        for path in blocked_new:
            with open(path, "wb") as f:
                f.write(new_bytes)
        for path, _book, _name in kept_new:
            with open(path, "wb") as f:
                f.write(old_bytes)
        n2, u2, a2 = run(repo, [src], optimize_png=False, dry=False)
        # original five, plus data center, China/Mexico/Taiwan, the red-dot tracker, and six neighbors
        assert n2 == 14, n2
        # prior eight unmatched, plus the retired exports and the tariff chart-2 file.
        # "173 Continuing/initial claims" stays in this fixture as gid 173, the slash-in-name
        # example. The retired sheet is gid 79264442, so that export is not added again.
        assert u2 == 34, u2
        assert a2 == 0, a2
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "1547332232.png"))
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "643299192.png"))
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "643299192_2.png"))
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "1791345316.png"))
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "1791345316_2.png"))
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "1370617918.png"))
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "1024131985.png"))
        assert open(os.path.join(repo, "static", "sheets", "full", "bot1", "1476428159.png"), "rb").read() == old_bytes
        assert open(os.path.join(repo, "static", "sheets", "full", "bot1", "171599627.png"), "rb").read() == old_bytes
        assert open(os.path.join(repo, "static", "sheets", "full", "bot2", "645072508.png"), "rb").read() == old_bytes
        for gid in (418894352,):
            assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot2", "%d.png" % gid))
        for gid in (292208014, 2119398138, 1584345646, 1186940001, 501919598, 579853621,
                    1308503061, 748431671, 254001772,
                    1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, 1067583608,
                    1924976898, 1978525586, 1255387315, 1522766504, 1163669375, 1576800449,
                    1192979444, 1912487083, 664060481, 1778640335):
            assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "%d.png" % gid)), gid
        assert not os.path.exists(os.path.join(repo, "static", "sheets", "full", "bot1", "501919598_2.png"))
        for _path, book, name in kept_new:
            assert open(os.path.join(repo, "static", "sheets", "full", book, name), "rb").read() == old_bytes, name
        # optimize() keeps a valid PNG and does not grow a tiny file into garbage.
        out, tool = optimize(tiny)
        assert out.startswith(PNG_MAGIC), tool
        assert tool in ("copied", "oxipng", "optipng", "pngquant", "pillow")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(src, ignore_errors=True)
    from PIL import Image
    repo = tempfile.mkdtemp(prefix="fullpng-blank-")
    src = tempfile.mkdtemp(prefix="fullpng-blank-src-")
    try:
        os.makedirs(os.path.join(repo, "static", "sheets"))
        catalog = {
            "books": [{"id": "bot3", "name": "My Bot's Sheets", "spreadsheetId": "sheetBOT3"}],
            "tabs": [{
                "book": "bot3", "tab": "Gold Priced in BTC", "gid": 1536200109,
                "charts": ["Bitcoin denominated in Gold (BTC/Gold price per oz)"],
                "chartList": [
                    {"n": 1, "title": "Bitcoin denominated in Gold (BTC/Gold price per oz)"},
                    {"n": 2, "title": "Bitcoin denominated in Gold (BTC/Gold price per oz)"},
                ],
            }],
        }
        with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        folder = os.path.join(src, "bot3")
        os.makedirs(folder)
        real = Image.new("RGB", (80, 40), "white")
        for x in range(80):
            real.putpixel((x, 8 + (x % 17)), (66, 133, 244))
            real.putpixel((x, 36), (90 + (x % 50), 40, 40))
        for y in range(40):
            real.putpixel((2, y), (20, 20, 20))
        real.save(os.path.join(folder, "Gold Priced in BTC.png"))
        empty = Image.new("RGB", (100, 60), "white")
        epx = empty.load()
        for y in range(24, 40):
            for x in range(10, 90):
                shade = 150 + ((x + y) % 20)
                epx[x, y] = (shade, shade, shade)
        for i in range(20):
            epx[i, 0] = (230 - i, 230 - i, 230 - i)
        empty.save(os.path.join(folder, "Gold Priced in BTC (2).png"))
        real.save(os.path.join(folder, "Gold Priced in BTC (3).png"))
        # A chart whose catalog title is "Chart 2" is untitled even when the PNG has data.
        catalog["tabs"].append({
            "book": "bot3", "tab": "Untitled chart", "gid": 1536200110,
            "charts": ["Chart 2"], "chartList": [{"n": 1, "title": "Chart 2"}],
        })
        with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        real.save(os.path.join(folder, "Untitled chart.png"))
        assert png_is_blank(open(os.path.join(folder, "Gold Priced in BTC (2).png"), "rb").read())
        assert not png_is_blank(open(os.path.join(folder, "Gold Priced in BTC.png"), "rb").read())
        assert not png_is_blank(PNG_MAGIC + b"OLD")
        n, u, a = run(repo, [src], optimize_png=False, dry=False)
        assert n == 1, (n, u, a)
        assert u == 3, (n, u, a)
        assert a == 0, a
        full = os.path.join(repo, "static", "sheets", "full", "bot3")
        assert os.path.isfile(os.path.join(full, "1536200109.png"))
        assert not os.path.exists(os.path.join(full, "1536200109_2.png"))
        assert not os.path.exists(os.path.join(full, "1536200109_3.png"))
        assert not os.path.exists(os.path.join(full, "1536200110.png"))
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(src, ignore_errors=True)
    assert split_chart_n("STRC Volume (3).png") == ("STRC Volume.png", 3)
    assert split_chart_n("013 BTC cost basis by holder (2) 2026-11-09.png") == ("013 BTC cost basis by holder 2026-11-09.png", 2)
    assert split_chart_n("STRC Volume.png") == ("STRC Volume.png", 1)
    assert FULL_REL.fullmatch("sheets/full/bot3/1770358701_4.png") and not FULL_REL.fullmatch("sheets/full/bot3/1_1.png")
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser(description="Import full-size Sheets chart PNGs into static/sheets/full/<book>/<gid>.png.")
    ap.add_argument("--from-dir", action="append", default=[], help="folder of exported chart PNGs; repeatable, one per book or one flat folder")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--dry-run", action="store_true", help="print matches and counts without writing")
    ap.add_argument("--no-optimize", action="store_true", help="copy PNG bytes unchanged")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return
    if not a.from_dir:
        ap.error("pass --from-dir")
    n, _u, _a = run(a.repo, a.from_dir, optimize_png=not a.no_optimize, dry=a.dry_run)
    if n == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
