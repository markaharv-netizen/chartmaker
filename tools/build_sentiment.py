"""Build sentiment.json from a labeled input bundle (tools/sentiment_input/<date>.json).

The bundle is produced by an agent with the X connector (search_posts_all + search_news; see README "Sentiment").
Each item is a post or news story labeled bull / bear / neutral; this script does only the math, so it is
deterministic and needs no network access (Render never scrapes).

  python3 tools/build_sentiment.py tools/sentiment_input/2026-10-18.json [--asof 2026-10-18T12:30:00-04:00]
"""
import json, math, os, sys, statistics, datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT, HIST = os.path.join(ROOT, "sentiment.json"), os.path.join(ROOT, "sentiment_history.json")
NEWS_W, Z, OWN_MIN = 3.0, 1.96, 20  # news weight; 95% Wilson; history points needed before sigma switches to own z-score

def weight(it): return NEWS_W if it["kind"] == "news" else 1 + math.log1p(max(0, it.get("eng", 0)))

def wilson(p, n):
    if n <= 0: return 0.0, 1.0
    d = 1 + Z * Z / n; c = (p + Z * Z / (2 * n)) / d; h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)

def score(t):
    lab = [it for it in t["items"] if it["label"] in ("bull", "bear")]
    wb = sum(weight(i) for i in lab if i["label"] == "bull"); wr = sum(weight(i) for i in lab if i["label"] == "bear")
    W = wb + wr; p = wb / W if W else 0.5
    neff = W * W / sum(weight(i) ** 2 for i in lab) if lab else 0  # Kish effective sample size
    lo, hi = wilson(p, neff)
    nb = sum(i["label"] == "bull" for i in lab); nr = len(lab) - nb
    return p, lo, hi, nb, nr, neff

def main(path, asof=None):
    B = json.load(open(path)); date = os.path.basename(path).split(".")[0]
    asof = asof or datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="minutes")
    H = json.load(open(HIST)) if os.path.exists(HIST) else {}
    if "daily" not in H and "series" not in H: H = {"daily": H}  # migrate the old flat {date:{SYM:bull}} file
    hist, series = H.setdefault("daily", {}), H.get("series", {})
    rows = []
    for sym, t in B["tickers"].items():
        p, lo, hi, nb, nr, neff = score(t)
        lab = [i for i in t["items"] if i["label"] in ("bull", "bear")]
        top = lambda s: sorted([i for i in lab if i["label"] == s and i["kind"] == "post"], key=lambda i: -i.get("eng", 0))[:2]
        bear = t.get("bear_headline") if nr >= 2 and (1 - p) >= 0.15 else None  # no dominant bear case -> null
        rows.append({"symbol": sym, "name": t["name"], "bull": round(p, 4), "lo": round(lo, 4), "hi": round(hi, 4),
            "bull_headline": t.get("bull_headline"), "bear_headline": bear,
            "posts": sum(i["kind"] == "post" for i in t["items"]), "news": sum(i["kind"] == "news" for i in t["items"]),
            "classified": len(lab), "n_bull": nb, "n_bear": nr, "n_eff": round(neff, 1),
            **{k: t[k] for k in ("display", "logoKey", "kind") if k in t},
            "sources": [{"label": i["label"], "user": i.get("user"), "url": i["url"]} for i in top("bull") + top("bear")]})
    hist[date] = {r["symbol"]: r["bull"] for r in rows}
    ps = [r["bull"] for r in rows]; mu, sd = statistics.mean(ps), statistics.pstdev(ps) or 1
    for r in rows:
        # own-history sigma: latest point of the ticker's X-only history series (same rules labeler throughout)
        # against its earlier points, so the z-score never mixes hand-labeled and rules-labeled levels
        s = series.get(r["symbol"], []); own = [x["bull"] for x in s[:-1]]
        if len(own) >= OWN_MIN and statistics.pstdev(own) > 0:
            r["sigma"], r["sigma_basis"] = round((s[-1]["bull"] - statistics.mean(own)) / statistics.pstdev(own), 2), "own"
        else:
            r["sigma"], r["sigma_basis"] = round((r["bull"] - mu) / sd, 2), "cross"
    rows.sort(key=lambda r: -r["sigma"])
    out = {"asOf": asof, "window": B.get("window"), "method": "engagement-weighted bull/(bull+bear) of classified X posts (w=1+ln(1+likes+2*reposts+replies+2*quotes)) and news (w=3); band = 95%% Wilson on Kish effective n; sigma = z-score of the latest X-only history point vs that ticker's own history once it has %d points (sigma_basis own), else cross-sectional z across the board" % OWN_MIN,
           "source": "X posts & news", "tickers": rows}
    json.dump(out, open(OUT, "w"), indent=1); json.dump(H, open(HIST, "w"), indent=1)
    for r in rows: print(f'{r["symbol"]:6} {r["sigma"]:+.2f}σ bull {r["bull"]:.0%} [{r["lo"]:.0%}-{r["hi"]:.0%}] {r["n_bull"]}/{r["n_bear"]}')

if __name__ == "__main__":
    a = sys.argv[1:]; asof = a[a.index("--asof") + 1] if "--asof" in a else None
    main(a[0], asof)
