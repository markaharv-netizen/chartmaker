#!/usr/bin/env python3
"""Build a "Charts of the day" edition for Chartmaker.  Runs on the box, not on Render.

Usage:  python3 scripts/cotd_build.py picks.json [--repo /workspace/chartmaker] [--keep-old N]

picks.json:
{
  "date": "2026-10-21",                      # edition date (YYYY-MM-DD)
  "theme": "one-line theme",
  "items": [                                  # exactly 10
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

def self_test():
    bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
    assert pick_excluded({"spreadsheetId": bot1, "gid": 1547332232})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 643299192})
    assert pick_excluded({"spreadsheetId": bot1, "gid": 333185169})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": 1476428159})
    assert not pick_excluded({"spreadsheetId": bot1, "gid": ""})
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
    d = P["date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d): sys.exit("date must be YYYY-MM-DD")
    items = P["items"]
    if len(items) != 10: sys.exit(f"need exactly 10 items, got {len(items)}")
    req = ["spreadsheetId", "sheet", "tab", "png", "title", "picked", "matters", "know"]
    for i, x in enumerate(items, 1):
        miss = [k for k in req if not str(x.get(k, "")).strip()]
        if miss: sys.exit(f"item {i} missing {miss}")
        if pick_excluded(x):
            sys.exit(f"item {i} is an excluded tab ({x.get('tab')} gid {x.get('gid')}) and cannot be published")
    root = os.path.join(a.repo, "static", "cotd"); out = os.path.join(root, d)
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
    if a.keep_old:
        ed = sorted(x for x in os.listdir(root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x))
        for old in ed[:-a.keep_old]: shutil.rmtree(os.path.join(root, old)); print("removed old edition", old)
    print("wrote", t)

if __name__ == "__main__": main()
