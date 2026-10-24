"""Weekly / rolling sentiment history for the "Over time" view.

  ingest  python3 tools/sentiment_history.py ingest SYM WEEK_START RAW.json [RAW2.json ...]
          Normalise raw search_posts_all results (the connector's JSON, wrapper text allowed) into
          tools/sentiment_input/history/SYM/WEEK_START.json (deduped posts, engagement, auto label).
  build   python3 tools/sentiment_history.py build
          Recompute every point from the saved inputs and rewrite the "series" block of sentiment_history.json.

A point file covers the 7 days ending at its "end" timestamp. Weekly backfill points (src "x-weekly") end on
Sundays 16:00Z; daily refresh points (src "x-rolling7") end at the refresh time. Both use the same two
searches, the same rules labeler (tools/sentiment_lexicon.py) and the same math as the board
(tools/build_sentiment.py score()); X posts only, no news.
"""
import json, os, sys, glob, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sentiment_lexicon import classify
from build_sentiment import score, ROOT, HIST
HDIR = os.path.join(ROOT, "tools", "sentiment_input", "history")
HISTORY_TICKERS = ["MSTR", "TSLA", "SPCX", "SATA", "STRC", "BTC-USD", "META"]
DDIR = os.path.join(ROOT, "tools", "sentiment_input", "history_daily")
# Daily points: all 18 board tickers + the 3 history-only names. One UTC day per point, 1 opinion search (<=25 posts).
DAILY_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "INTC", "MSTR", "COIN", "BTC-USD", "ETH-USD",
                 "CRWV", "NBIS", "IREN", "APLD", "CIFR", "WULF", "ASST", "MU", "SPCX", "RKLB", "ASTS", "SATA", "STRC"]
DAILY_START = "2026-07-21"  # target backfill start (~3 months)

def load_raw(path):
    s = open(path).read(); i = s.find('{"data"')
    if i < 0: return [], {}
    d = json.JSONDecoder().raw_decode(s[i:])[0]
    users = {u["id"]: u.get("username") for u in d.get("includes", {}).get("users", [])}
    return d.get("data", []), users

def ingest(sym, start, raws, end=None, src="x-weekly", queries=None, outdir=None, key=None):
    s = datetime.datetime.fromisoformat(start)
    if s.tzinfo is None: s = s.replace(hour=16, tzinfo=datetime.timezone.utc)
    e = datetime.datetime.fromisoformat(end) if end else s + datetime.timedelta(days=7)
    posts = {}
    for r in raws:
        data, users = load_raw(r)
        for p in data:
            t = datetime.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
            if not (s <= t < e) or p["id"] in posts: continue
            m = p.get("public_metrics", {}); u = users.get(p.get("author_id"), "i")
            eng = m.get("like_count", 0) + 2 * m.get("retweet_count", 0) + m.get("reply_count", 0) + 2 * m.get("quote_count", 0)
            posts[p["id"]] = {"kind": "post", "label": classify(p["text"]), "eng": eng, "user": u,
                              "url": f"https://x.com/{u}/status/{p['id']}", "date": p["created_at"][:10], "text": p["text"]}
    out = {"symbol": sym, "start": s.isoformat().replace("+00:00", "Z"), "end": e.isoformat().replace("+00:00", "Z"),
           "src": src, "labeler": "rules (tools/sentiment_lexicon.py)", "queries": queries or [], "items": list(posts.values())}
    os.makedirs(os.path.join(outdir or HDIR, sym), exist_ok=True)
    fn = os.path.join(outdir or HDIR, sym, (key or e.strftime("%Y-%m-%d")) + ".json")  # keyed by window END date
    json.dump(out, open(fn, "w"), indent=1, ensure_ascii=False)
    return fn, len(posts)

def ingest_daily(sym, day, raws, queries=None):
    fn, n = ingest(sym, day + "T00:00:00+00:00", raws, end=(datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat() + "T00:00:00+00:00",
                   src="x-daily", queries=queries, outdir=DDIR, key=day)  # daily files keyed by the day itself
    return fn, n

def build_daily(hist):
    series, gaps = {}, {}
    today = datetime.datetime.now(datetime.timezone.utc).date()
    days = [(datetime.date.fromisoformat(DAILY_START) + datetime.timedelta(days=i)).isoformat()
            for i in range((today - datetime.date.fromisoformat(DAILY_START)).days)]  # complete UTC days only
    for sym in DAILY_TICKERS:
        pts = []
        for d in days:
            fn = os.path.join(DDIR, sym, d + ".json")
            if not os.path.exists(fn): continue
            B = json.load(open(fn)); B["items"] = [i for i in B["items"] if i["kind"] == "post"]
            if not B["items"]: continue
            p, lo, hi, nb, nr, neff = score(B)
            pts.append({"t": d, "start": d, "bull": round(p, 4), "lo": round(lo, 4), "hi": round(hi, 4), "posts": len(B["items"]),
                        "n_bull": nb, "n_bear": nr, "n_eff": round(neff, 1), "src": "x-daily", "thin": (nb + nr) < 8})
        have = {x["t"] for x in pts}
        gaps[sym] = [d for d in days if d not in have and not (sym == "SPCX" and d < "2026-06-12")]
        if pts: series[sym] = pts
    hist["series_daily"] = series; hist["daily_gaps"] = gaps
    hist["series_daily_note"] = ("X posts only; one UTC day per point; 1 opinion-keyword search (<=25 posts, relevancy); rules labeler; "
                                 "days not yet collected are gaps (never interpolated); <8 bull+bear posts = thin")
    for sym in DAILY_TICKERS:
        print(f"daily {sym:8} {len(series.get(sym, [])):3} pts  {len(gaps[sym]):3} gap days")

def build():
    hist = json.load(open(HIST)) if os.path.exists(HIST) else {}
    if "series" not in hist and "daily" not in hist: hist = {"daily": hist}  # migrate {date:{SYM:bull}}
    series = {}
    for sym in HISTORY_TICKERS:
        pts = []
        for fn in sorted(glob.glob(os.path.join(HDIR, sym, "*.json"))):
            B = json.load(open(fn)); B["items"] = [i for i in B["items"] if i["kind"] == "post"]
            p, lo, hi, nb, nr, neff = score(B)
            pts.append({"t": B["end"][:10], "start": B["start"][:10], "bull": round(p, 4), "lo": round(lo, 4), "hi": round(hi, 4),
                        "posts": len(B["items"]), "n_bull": nb, "n_bear": nr, "n_eff": round(neff, 1), "src": B["src"],
                        "thin": (nb + nr) < 8})
        if pts: series[sym] = pts
    hist["series"] = series
    hist["series_note"] = ("X posts only (no news); 2 searches per window (plain + opinion keywords), <=25 posts each, "
                           "rules labeler validated vs hand labels; points with <8 bull+bear posts flagged thin")
    build_daily(hist)
    json.dump(hist, open(HIST, "w"), indent=1)
    for sym, pts in series.items():
        print(f"{sym:8} {len(pts):2} pts  " + " ".join(f"{x['bull']*100:.0f}{'*' if x['thin'] else ''}" for x in pts))

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "ingest":
        opt = lambda k: a[a.index(k) + 1] if k in a else None
        raws = [x for i, x in enumerate(a[3:], 3) if not x.startswith("--") and not a[i - 1].startswith("--")]
        print(*ingest(a[1], a[2], raws, end=opt("--end"), src=opt("--src") or "x-weekly",
                      queries=[a[i + 1] for i, x in enumerate(a) if x == "--q"]))
    elif a and a[0] == "ingest-daily":  # ingest-daily SYM YYYY-MM-DD RAW.json [--q QUERY]
        raws = [x for i, x in enumerate(a[3:], 3) if not x.startswith("--") and not a[i - 1].startswith("--")]
        print(*ingest_daily(a[1], a[2], raws, queries=[a[i + 1] for i, x in enumerate(a) if x == "--q"]))
    elif a and a[0] == "build": build()
    else: print(__doc__)
