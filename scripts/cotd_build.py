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
    """True when this Charts of the day pick is a tab the library must not publish."""
    sid = str(item.get("spreadsheetId") or "").strip()
    gid = item.get("gid")
    if gid in (None, ""):
        return False
    pair = sheets_catalog.BY_ID.get(sid)
    if not pair:
        return False
    return sheets_catalog.tab_excluded(pair[0], gid)

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
    root = os.path.join(repo, "static", "cotd"); out = os.path.join(root, d)
    tmp = out + ".tmp"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    rows = []
    for i, x in enumerate(items, 1):
        fn = f"{i:02d}.png"; n = fit(x["png"], os.path.join(tmp, fn))
        url = f"https://docs.google.com/spreadsheets/d/{x['spreadsheetId']}/edit" + (f"#gid={x['gid']}" if x.get("gid") not in (None, "") else "")
        rows.append({"sheet": x["sheet"], "tab": x["tab"], "title": x["title"], "image": f"cotd/{d}/{fn}",
                     "picked": x["picked"].strip(), "matters": x["matters"].strip(), "know": x["know"].strip(), "sheet_url": x.get("sheet_url") or url})
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
    assert EDITION_MIN == 1 and EDITION_MAX == 20
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1547332232})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 643299192})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 333185169})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1476428159})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": ""})
    live = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Retired charts stay out of the published edition and the sheet catalog.
    # 1024131985 is Strategy tab 99 (MSTR BTC per share), removed in PR #34.
    for rel in ("static/cotd/today.json", "static/sheets/catalog.json"):
        with open(os.path.join(live, rel), encoding="utf-8") as f:
            blob = f.read()
        for gid in (1547332232, 643299192, 333185169, 1024131985):
            assert str(gid) not in blob, (rel, gid)

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
    for gid, tab in (
        (1547332232, "161 Nvidia P-S and P-E"),
        (643299192, "202 Nvidia revenue by geography"),
        (333185169, "87 TSLA beta to Nasdaq"),
    ):
        items = [item(i) for i in range(1, EDITION_MAX + 1)]
        items[4] = item(5, gid=gid, tab=tab, spreadsheetId=bot1)
        rejected(items, f"gid {gid}")

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
