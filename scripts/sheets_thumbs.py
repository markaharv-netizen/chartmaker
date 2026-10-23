#!/usr/bin/env python3
"""Build compressed Sheets-library search thumbnails. Runs beside the catalog refresh, not on Render.

Render only serves static files. It does not call Google. Full-size chart PNGs stay on Drive
(or in a local export folder) and are never written into the repo.

Usage:
  python3 scripts/sheets_thumbs.py --from-dir /path/to/exported-pngs
  python3 scripts/sheets_thumbs.py --from-cotd
  python3 scripts/sheets_thumbs.py --from-dir /path/to/pngs --repo /workspace/chartmaker
  python3 scripts/sheets_thumbs.py --self-test

--from-dir walks a folder of chart images, the weekly Drive export downloaded to disk.
The export names files after the tab, often with a trailing date:

  090 TSLA gross margin vs price 2026-10-21.png
  026 Tesla FSD subscriptions 2026-10-21.png
  MSTR Volume.png

The leading tab number may be zero-padded (090 matches the tab "90 TSLA ...").
A trailing YYYY-MM-DD is not part of the tab name. When several files match one tab,
the newest wins: later date in the name, then later file mtime (same rule as Drive
modifiedTime when names collide).

Put each workbook in its own subfolder if you want the match limited to that book.
The folder may be the book id (bot1), the spreadsheet id, or the workbook title
(Bot experiments, bot experiments 2, My Bot's Sheets). A flat folder is fine too:
tab names are unique across the three books today.

A file named "{gid}.png" or "{spreadsheetId}__{gid}.png" matches that tab directly.
Optional index.json in the folder overrides names:

  [{"file": "a.png", "gid": 1913607487},
   {"file": "b.png", "spreadsheetId": "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY", "tab": "01 BTC vol vs VIX"}]

--from-cotd seeds thumbnails from today's Charts of the day (static/cotd/today.json)
for tabs that --from-dir did not already match. That is the stand-in when the Drive
export is not on this machine. It does not remove thumbnails other runs already wrote.

Each thumbnail is a JPEG at most 480x270 under static/sheets/thumbs/<book>/<gid>.jpg.
The catalog gains an optional "thumb" field. sheets_catalog.py keeps those fields when
it rebuilds tabs. The Sheets page shows a thumbnail only while the search box has text.

The script refuses to publish if static/sheets/thumbs would exceed 12 MiB.
"""
import argparse, datetime, io, json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_catalog

MAX_W, MAX_H = 480, 270
MAX_FILE = 20_000
TOTAL_MAX = 12 * 1024 * 1024
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
DATE_SUFFIX = re.compile(r"\s+(\d{4}-\d{2}-\d{2})$")
GID_NAME = re.compile(r"^(?:(?P<pre>.+?)__)?(?P<gid>\d+)$")

def norm_tab(s):
    s = re.sub(r"\s+", " ", str(s).replace("\u00a0", " ").strip().lower())
    return re.sub(r"^0+(\d+)\b", r"\1", s)

def norm_book(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()

def book_lookups(books):
    exact, loose = {}, {}
    for b in books:
        for raw in (b.get("id") or "", b.get("spreadsheetId") or "", b.get("name") or ""):
            raw = str(raw).strip()
            if not raw: continue
            exact[raw.lower()] = b["id"]
            loose[norm_book(raw)] = b["id"]
    return exact, loose

def parent_book(name, exact, loose):
    if not name: return None
    hit = exact.get(str(name).strip().lower())
    if hit: return hit
    return loose.get(norm_book(name))

def name_keys(filename):
    stem = os.path.splitext(os.path.basename(filename))[0].strip()
    keys, date = [], ""
    def add(s):
        k = norm_tab(s)
        if k and k not in keys: keys.append(k)
    add(stem)
    m = DATE_SUFFIX.search(stem)
    if m:
        date = m.group(1)
        add(stem[:m.start()])
    return stem, keys, date

class Index:
    def __init__(self, books, tabs):
        self.books = books
        self.exact, self.loose = book_lookups(books)
        self.by_gid = {}
        self.by_key, self.by_book_key = {}, {}
        self.by_title, self.by_book_title = {}, {}
        for t in tabs:
            gid = int(t["gid"])
            self.by_gid[gid] = t
            k = norm_tab(t["tab"])
            self.by_key.setdefault(k, []).append(t)
            self.by_book_key[(t["book"], k)] = t
            for title in t.get("charts") or []:
                tk = norm_tab(title)
                self.by_title.setdefault(tk, []).append(t)
                self.by_book_title.setdefault((t["book"], tk), []).append(t)

    def _one(self, hits, book_id):
        if book_id:
            hits = [t for t in hits if t["book"] == book_id]
        if len(hits) == 1: return hits[0]
        if len(hits) > 1: return "ambiguous"
        return None

    def by_tab_key(self, key, book_id):
        if book_id:
            hit = self.by_book_key.get((book_id, key))
            return hit
        return self._one(self.by_key.get(key) or [], None)

    def by_chart_key(self, key, book_id):
        if book_id: return self._one(self.by_book_title.get((book_id, key)) or [], book_id)
        return self._one(self.by_title.get(key) or [], None)

    def by_gid_name(self, stem, book_id):
        m = GID_NAME.fullmatch(stem.strip())
        if not m: return None
        tab = self.by_gid.get(int(m.group("gid")))
        if not tab: return None
        pre = m.group("pre")
        if pre:
            bid = parent_book(pre, self.exact, self.loose)
            if not bid or bid != tab["book"]: return None
        elif book_id and tab["book"] != book_id:
            return None
        return tab

    def explicit(self, row):
        book = row.get("book") or row.get("spreadsheetId")
        bid = parent_book(book, self.exact, self.loose) if book else None
        if book and not bid: return None
        if row.get("gid") not in (None, ""):
            tab = self.by_gid.get(int(row["gid"]))
            if not tab or (bid and tab["book"] != bid): return None
            return tab
        if row.get("tab"):
            return self.by_tab_key(norm_tab(row["tab"]), bid)
        return None

def match_files(files, books, tabs):
    """files: {path, name, parent, mtime, explicit|None}. Newest candidate per tab wins."""
    idx = Index(books, tabs)
    chosen, unmatched, ambiguous = {}, [], []
    for f in files:
        book_id = parent_book(f.get("parent") or "", idx.exact, idx.loose)
        tab, how = None, ""
        if f.get("explicit"):
            tab = idx.explicit(f["explicit"])
            how = "index"
            if tab == "ambiguous":
                ambiguous.append((f["path"], "index matched more than one tab")); continue
            if not tab:
                unmatched.append(f["path"]); continue
        else:
            stem, keys, _date = name_keys(f["name"])
            tab = idx.by_gid_name(stem, book_id)
            if tab: how = "gid"
            if not tab:
                for key in keys:
                    hit = idx.by_tab_key(key, book_id)
                    if hit == "ambiguous":
                        ambiguous.append((f["path"], "tab name " + key)); tab = "ambiguous"; break
                    if hit:
                        tab, how = hit, "tab"; break
            if tab == "ambiguous": continue
            if not tab:
                for key in keys:
                    hit = idx.by_chart_key(key, book_id)
                    if hit == "ambiguous":
                        ambiguous.append((f["path"], "chart title " + key)); tab = "ambiguous"; break
                    if hit:
                        tab, how = hit, "chart"; break
            if tab == "ambiguous": continue
            if not tab:
                unmatched.append(f["path"]); continue
        _stem, _keys, date = name_keys(f["name"])
        rank = (date, float(f.get("mtime") or 0))
        key = (tab["book"], int(tab["gid"]))
        chosen.setdefault(key, []).append({"path": f["path"], "rank": rank, "how": how, "tab": tab["tab"]})
    for key in chosen:
        chosen[key].sort(key=lambda c: c["rank"], reverse=True)
    return chosen, unmatched, ambiguous

def iter_dir(root):
    index = {}
    manifest = os.path.join(root, "index.json")
    if os.path.isfile(manifest):
        doc = json.load(open(manifest, encoding="utf-8"))
        rows = doc.get("files") if isinstance(doc, dict) else doc
        if not isinstance(rows, list): raise SystemExit("index.json must be a list or {files: [...]}")
        for row in rows:
            if not isinstance(row, dict): continue
            rel = row.get("file") or row.get("png") or ""
            if not rel: continue
            path = rel if os.path.isabs(rel) else os.path.join(root, rel)
            index[os.path.normpath(path)] = row
    files = []
    seen = set()
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in names:
            if fn.startswith(".") or fn == "index.json": continue
            if os.path.splitext(fn)[1].lower() not in IMAGE_EXT: continue
            path = os.path.normpath(os.path.join(dirpath, fn))
            seen.add(path)
            parent = os.path.basename(dirpath) if os.path.normpath(dirpath) != os.path.normpath(root) else ""
            # A file sitting directly in a workbook folder still needs that folder name.
            if not parent: parent = os.path.basename(os.path.normpath(root))
            files.append({"path": path, "name": fn, "parent": parent, "mtime": os.path.getmtime(path),
                          "explicit": index.get(path)})
    missing = [p for p in index if p not in seen]
    return files, missing

def blank(im):
    from PIL import ImageStat
    g = im.convert("L")
    if ImageStat.Stat(g).stddev[0] < 5: return True
    cols = im.convert("RGB").getcolors(max(im.size[0] * im.size[1], 1))
    return cols is not None and len(cols) < 12

def compress(src, dest):
    from PIL import Image
    im = Image.open(src)
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if blank(im): raise ValueError("blank")
    w, h = MAX_W, MAX_H
    best = None
    for _ in range(4):
        thumb = im.copy()
        thumb.thumbnail((max(1, w), max(1, h)), Image.LANCZOS)
        q = 52
        while q >= 28:
            buf = io.BytesIO()
            thumb.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
            data = buf.getvalue()
            best = data
            if len(data) <= MAX_FILE: break
            q -= 6
        if best is not None and len(best) <= MAX_FILE:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f: f.write(best)
            return len(best)
        w = int(w * 0.8); h = int(h * 0.8)
    raise ValueError("could not compress under %d bytes: %s" % (MAX_FILE, src))

def rel_for(book, gid):
    rel = "sheets/thumbs/%s/%s.jpg" % (book, int(gid))
    if not sheets_catalog.THUMB_REL.fullmatch(rel):
        raise SystemExit("refusing unsafe thumb path " + rel)
    return rel

def load_catalog(repo):
    path = os.path.join(repo, "static", "sheets", "catalog.json")
    if not os.path.isfile(path): sys.exit("missing " + path + " — run scripts/sheets_catalog.py first")
    return json.load(open(path, encoding="utf-8")), path

def cotd_files(repo, books, tabs):
    path = os.path.join(repo, "static", "cotd", "today.json")
    if not os.path.isfile(path): sys.exit("missing " + path)
    doc = json.load(open(path, encoding="utf-8"))
    by_name = {str(b.get("name") or "").strip().lower(): b["id"] for b in books}
    by_tab = {(t["book"], str(t["tab"]).strip().lower()): t for t in tabs}
    files = []
    for it in doc.get("items") or []:
        bid = by_name.get(str(it.get("sheet") or "").strip().lower())
        tab = by_tab.get((bid, str(it.get("tab") or "").strip().lower())) if bid else None
        img = str(it.get("image") or "")
        if not tab or not img.startswith("cotd/") or ".." in img.split("/"):
            print("cotd item not in the catalog:", it.get("sheet"), "/", it.get("tab"))
            continue
        src = os.path.join(repo, "static", img)
        if not os.path.isfile(src):
            print("cotd image missing:", img); continue
        # Hand the real tab name so the matcher does not depend on the PNG filename.
        files.append({"path": src, "name": tab["tab"] + ".png", "parent": tab["book"],
                      "mtime": os.path.getmtime(src), "explicit": {"book": tab["book"], "gid": tab["gid"]}})
    return files

def publish(repo, chosen, dry):
    doc, dest = load_catalog(repo)
    tabs = doc.get("tabs") or []
    root = os.path.join(repo, "static", "sheets", "thumbs")
    tmp = root + ".building"
    if not dry:
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
    written, skipped = [], []
    used = set()
    for t in tabs:
        key = (t["book"], int(t["gid"]))
        cands = chosen.get(key) or []
        if not cands: continue
        rel = rel_for(t["book"], t["gid"])
        if dry:
            print("match %s  %s / %s  <= %s" % (cands[0]["how"], t["book"], t["tab"], os.path.basename(cands[0]["path"])))
            written.append(rel); used.add(key); continue
        out = os.path.join(tmp, t["book"], "%s.jpg" % int(t["gid"]))
        saved = None
        for cand in cands:
            try:
                saved = compress(cand["path"], out)
                print("%s  %d KB  %s / %s" % (rel, (saved + 1023) // 1024, t["book"], t["tab"]))
                break
            except ValueError as e:
                print("skip %s (%s)" % (cand["path"], e))
        if saved is None:
            skipped.append(t["tab"]); continue
        used.add(key)
        written.append(rel)
    # Keep thumbnails this run did not replace, so a partial folder does not wipe the library.
    if not dry:
        for t in tabs:
            key = (t["book"], int(t["gid"]))
            if key in used: continue
            prev = sheets_catalog.thumb_ok(t["book"], t["gid"], t.get("thumb"), repo)
            if not prev: continue
            src = os.path.join(repo, "static", prev)
            out = os.path.join(tmp, t["book"], "%s.jpg" % int(t["gid"]))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(src, out)
            written.append(prev)
        total = 0
        for dirpath, _dirs, names in os.walk(tmp):
            for fn in names: total += os.path.getsize(os.path.join(dirpath, fn))
        if total > TOTAL_MAX:
            shutil.rmtree(tmp, ignore_errors=True)
            sys.exit("thumbnails would be %.1f MiB, over the %d MiB cap. Compress further or publish fewer."
                     % (total / 1048576, TOTAL_MAX // 1048576))
        replaced = root + ".replaced"
        shutil.rmtree(replaced, ignore_errors=True)
        if os.path.isdir(root): os.rename(root, replaced)
        os.rename(tmp, root)
        shutil.rmtree(replaced, ignore_errors=True)
    n = 0
    for t in tabs:
        rel = rel_for(t["book"], t["gid"])
        path = os.path.join(repo, "static", rel)
        if (not dry) and os.path.isfile(path) and sheets_catalog.thumb_ok(t["book"], t["gid"], rel, repo):
            t["thumb"] = rel; n += 1
        elif dry and (rel in written or sheets_catalog.thumb_ok(t["book"], t["gid"], t.get("thumb"), repo)):
            t["thumb"] = t.get("thumb") or rel; n += 1
        else:
            t.pop("thumb", None)
    if dry:
        print("dry-run: %d tabs would have a thumbnail (%d matched this run, %d skipped)"
              % (n, len(used), len(skipped)))
        return
    doc["note"] = sheets_catalog.NOTE
    doc["thumbsGenerated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ordered = {"generated": doc.get("generated"), "note": doc["note"], "thumbsGenerated": doc["thumbsGenerated"],
               "books": doc.get("books") or [], "tabs": tabs}
    sheets_catalog.write_catalog(dest, ordered)
    removed = sheets_catalog.prune_thumbs(repo, tabs)
    total = 0
    for dirpath, _dirs, names in os.walk(root):
        for fn in names: total += os.path.getsize(os.path.join(dirpath, fn))
    print("wrote %s (%d thumbs, %.1f MiB, %d orphan files removed, %d blank/unreadable skipped)"
          % (dest, n, total / 1048576, removed, len(skipped)))

def collect(repo, dirs, use_cotd):
    doc, _path = load_catalog(repo)
    books, tabs = doc.get("books") or [], doc.get("tabs") or []
    files = []
    for d in dirs:
        if not os.path.isdir(d): sys.exit("not a folder: " + d)
        found, missing = iter_dir(d)
        files.extend(found)
        for m in missing: print("index.json entry missing on disk:", m)
    dir_chosen, unmatched, ambiguous = match_files(files, books, tabs) if files else ({}, [], [])
    for path, why in ambiguous: print("ambiguous, skipped:", path, "(", why, ")")
    if unmatched:
        print("%d files did not match a tab, for example: %s" % (len(unmatched), ", ".join(os.path.basename(p) for p in unmatched[:8])))
    if use_cotd:
        extra = cotd_files(repo, books, tabs)
        cotd_chosen, _u, cotd_amb = match_files(extra, books, tabs)
        for path, why in cotd_amb: print("ambiguous cotd, skipped:", path, "(", why, ")")
        for key, cands in cotd_chosen.items():
            if key not in dir_chosen: dir_chosen[key] = cands
    if not dir_chosen: sys.exit("no chart images matched a catalog tab")
    return dir_chosen

def self_test():
    books = [
        {"id": "bot1", "name": "Bot experiments", "spreadsheetId": "sheetBOT1"},
        {"id": "bot2", "name": "bot experiments 2", "spreadsheetId": "sheetBOT2"},
        {"id": "bot3", "name": "My Bot's Sheets", "spreadsheetId": "sheetBOT3"},
    ]
    tabs = [
        {"book": "bot1", "tab": "90 TSLA gross margin vs price", "gid": 10, "charts": ["Tesla margin"]},
        {"book": "bot1", "tab": "01 BTC vol vs VIX", "gid": 11, "charts": []},
        {"book": "bot2", "tab": "26 Tesla FSD subscriptions", "gid": 26, "charts": []},
        {"book": "bot3", "tab": "MSTR Volume", "gid": 30, "charts": ["MSTR 30-day volume"]},
        {"book": "bot1", "tab": "Same Chart Title", "gid": 40, "charts": ["Shared title"]},
        {"book": "bot2", "tab": "Other Chart Title", "gid": 41, "charts": ["Shared title"]},
    ]
    def f(path, parent, mtime, explicit=None):
        return {"path": path, "name": os.path.basename(path), "parent": parent, "mtime": mtime, "explicit": explicit}
    files = [
        f("/e/090 TSLA gross margin vs price 2026-10-19.png", "", 500, None),
        f("/e/090 TSLA gross margin vs price 2026-10-21.png", "", 100, None),  # newer date, older mtime
        f("/e/026 Tesla FSD subscriptions 2026-10-21.png", "bot experiments 2", 10, None),
        f("/e/001 BTC vol vs VIX.png", "sheetBOT1", 10, None),
        f("/e/MSTR Volume.png", "", 10, None),
        f("/e/MSTR Volume.png", "Bot experiments", 99, None),  # wrong book folder
        f("/e/10.png", "", 10, None),
        f("/e/sheetBOT2__26.png", "", 50, None),
        f("/e/custom.png", "", 200, {"gid": 30}),
        f("/e/Shared title.png", "", 10, None),
    ]
    # The second MSTR file is a different path; basename collides in this list on purpose.
    files[5]["path"] = "/wrong/MSTR Volume.png"
    files[5]["name"] = "MSTR Volume.png"
    chosen, unmatched, ambiguous = match_files(files, books, tabs)
    def winner(gid):
        cands = chosen[( [t["book"] for t in tabs if t["gid"] == gid][0], gid )]
        return os.path.basename(cands[0]["path"]), cands[0]["how"]
    assert winner(10)[0].endswith("2026-10-21.png") and winner(10)[1] == "tab", winner(10)
    assert winner(26)[1] in ("tab", "gid")
    assert any(c["how"] == "gid" and c["path"].endswith("sheetBOT2__26.png") for c in chosen[("bot2", 26)])
    assert winner(11) == ("001 BTC vol vs VIX.png", "tab")
    assert winner(30)[1] == "index"  # explicit gid outranks the filename; both match bot3
    assert ("bot1", 40) not in chosen and ("bot2", 41) not in chosen
    assert any(p.endswith("Shared title.png") for p, _w in ambiguous)
    assert any(p.startswith("/wrong/") for p in unmatched)
    # Newest mtime when the date is the same.
    files2 = [
        f("/e/MSTR Volume 2026-10-21.png", "", 10, None),
        f("/e/MSTR Volume 2026-10-21.png", "", 80, None),
    ]
    files2[1]["path"] = "/e/newer/MSTR Volume 2026-10-21.png"
    chosen2, _u, _a = match_files(files2, books, tabs)
    assert chosen2[("bot3", 30)][0]["path"].startswith("/e/newer/"), chosen2[("bot3", 30)][0]["path"]
    print("self-test ok")

def main():
    ap = argparse.ArgumentParser(description="Build compressed Sheets library search thumbnails.")
    ap.add_argument("--from-dir", action="append", default=[], help="folder of exported chart images; repeatable")
    ap.add_argument("--from-cotd", action="store_true", help="seed tabs that overlap today's Charts of the day")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test(); return
    if not a.from_dir and not a.from_cotd:
        sys.exit("pass --from-dir and/or --from-cotd")
    chosen = collect(a.repo, a.from_dir, a.from_cotd)
    publish(a.repo, chosen, a.dry_run)

if __name__ == "__main__": main()
