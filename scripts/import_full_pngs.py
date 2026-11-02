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
FULL_REL = re.compile(r"^sheets/full/([A-Za-z0-9][A-Za-z0-9_-]{0,24})/(\d+)\.png$")


def dest_for(repo, book, gid):
    rel = "sheets/full/%s/%d.png" % (book, int(gid))
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


def publish(repo, chosen, unmatched, ambiguous, optimize_png, dry):
    n = 0
    for (book, gid), cands in sorted(chosen.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        src = cands[0]["path"]
        data = open(src, "rb").read()
        if not data.startswith(PNG_MAGIC):
            print("skip %s (not a png)" % src)
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
        path, rel = dest_for(repo, book, gid)
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
    books, tabs = doc.get("books") or [], doc.get("tabs") or []
    files, skipped = collect(dirs)
    chosen, unmatched, ambiguous = sheets_thumbs.match_files(files, books, tabs) if files else ({}, [], [])
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
        for path, blob in ((older, old_bytes), (dated, new_bytes), (noise, old_bytes), (tsla_old, old_bytes), (tsla_new, new_bytes)):
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
        assert n == 3, n
        assert u == 2, u  # noise.png and the stray in bot2
        assert a == 0, a
        got = open(os.path.join(repo, "static", "sheets", "full", "bot1", "1546024641.png"), "rb").read()
        assert got == new_bytes, "dated export should win over a newer mtime"
        assert open(os.path.join(repo, "static", "sheets", "full", "bot1", "10.png"), "rb").read() == new_bytes
        assert open(os.path.join(repo, "static", "sheets", "full", "bot2", "30.png"), "rb").read() == old_bytes
        # optimize() keeps a valid PNG and does not grow a tiny file into garbage.
        out, tool = optimize(tiny)
        assert out.startswith(PNG_MAGIC), tool
        assert tool in ("copied", "oxipng", "optipng", "pngquant", "pillow")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(src, ignore_errors=True)
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
