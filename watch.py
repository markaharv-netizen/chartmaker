"""Watchlist backend: one batched call. Live price / 1D (vs previous regular close) / market cap reuse heat._heatmap
(v7 -> v8 chart fallback -> disk cache; shares.json / SEC share counts; CoinPaprika for crypto when CoinGecko 429s).
5D / 1M / YTD come from Yahoo v8 daily closes (one request per symbol, memory 5 min + disk fallback)."""
import heat, json, os, time, datetime, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
HDISK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "watch_hist.json")
HMEM = {}
def _hdisk():
    try:
        with open(HDISK) as f: return json.load(f)
    except Exception: return {}
def _hsave(new):
    try:
        d = _hdisk(); d.update(new); os.makedirs(os.path.dirname(HDISK), exist_ok=True)
        with open(HDISK + ".tmp", "w") as f: json.dump(d, f)
        os.replace(HDISK + ".tmp", HDISK)
    except Exception: pass
def daily(sym, fresh=False):
    """[(ts, close)] daily regular-session closes since ~Dec 1 of last year (>= 45 days)."""
    c = HMEM.get(sym)
    if c and not fresh and time.time() - c[0] < 300: return c[1]
    now = int(time.time()); y = datetime.datetime.utcnow().year
    p1 = min(int(datetime.datetime(y - 1, 12, 1, tzinfo=datetime.timezone.utc).timestamp()), now - 50 * 86400)
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?" + urllib.parse.urlencode({"period1": p1, "period2": now, "interval": "1d", "includePrePost": "false"})
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=heat.UA), timeout=15) as r: r0 = json.load(r)["chart"]["result"][0]
        pts = [(t, x) for t, x in zip(r0.get("timestamp") or [], r0["indicators"]["quote"][0]["close"]) if x is not None]
        if not pts: raise ValueError("no points")
        HMEM[sym] = (time.time(), pts); _hsave({sym: pts}); return pts
    except Exception:
        d = _hdisk().get(sym)
        if d: return [tuple(p) for p in d]
        raise
def rets(pts, px, crypto):
    """5D: vs close 5 sessions before the latest bar; 1M: last close on/before latest bar date - 1 calendar month; YTD: last close of prior year."""
    if px is None: px = pts[-1][1]
    out = {}
    if len(pts) >= 6: out["r5d"] = (px / pts[-6][1] - 1) * 100
    lt = datetime.datetime.fromtimestamp(pts[-1][0], datetime.timezone.utc)
    m, y = (lt.month - 1, lt.year) if lt.month > 1 else (12, lt.year - 1)
    try: tgt = lt.replace(year=y, month=m)
    except ValueError: tgt = lt.replace(year=y, month=m, day=28)
    b = [c for t, c in pts if t <= tgt.timestamp() + 43200]
    if b: out["r1m"] = (px / b[-1] - 1) * 100
    jan1 = datetime.datetime(lt.year, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
    b = [c for t, c in pts if t < jan1]
    if b: out["rytd"] = (px / b[-1] - 1) * 100
    return out
def compute(tickers, fresh=False):
    syms = []
    for s in (tickers or "").split(","):
        s = s.strip().upper()
        if s and s not in syms: syms.append(s)
    syms = syms[:60]
    if not syms: raise ValueError("Add some tickers")
    try: hm = heat._heatmap("custom", ",".join(syms), "1D", fresh)
    except Exception as e: hm = {"rows": [], "missing": list(syms), "error": str(e)}
    by = {}
    for d in hm.get("rows", []):
        k = d["symbol"] + "-USD" if d.get("crypto") else d["symbol"]
        by[k] = d
        if d.get("crypto"): by.setdefault(d["symbol"], d)
    rows = []
    for s in syms:
        d = by.get(s) or (by.get(s + "-USD") if s + "-USD" in by else None)
        r = {"symbol": s}
        if d:
            cr = bool(d.get("crypto"))
            r.update(name=d.get("longName") or d.get("shortName") or s, price=d.get("regularMarketPrice"), currency="USD" if cr else d.get("currency"),
                     r1d=d.get("chg"), crypto=cr, mcap=None if d.get("eqSize") else d.get("marketCap"), mcapEst=bool(d.get("mcapEst")), time=d.get("regularMarketTime"))
        else:
            try:
                q = heat.chart_quote(s); pc, px = q.get("regularMarketPreviousClose"), q.get("regularMarketPrice")
                r.update(name=q.get("longName") or q.get("shortName") or s, price=px, currency=q.get("currency"), r1d=(px / pc - 1) * 100 if pc and px else None,
                         crypto=s.endswith("-USD"), mcap=None, time=q.get("regularMarketTime"))
            except Exception as e: r["error"] = "No quote: " + str(e)[:80]
        rows.append(r)
    def hist(r):
        if r.get("error"): return
        hs = r["symbol"] if not r.get("crypto") or r["symbol"].endswith("-USD") else r["symbol"] + "-USD"
        try: r.update(rets(daily(hs, fresh), r.get("price"), r.get("crypto")))
        except Exception as e: r["histError"] = str(e)[:80]
    with ThreadPoolExecutor(8) as ex: list(ex.map(hist, rows))
    src = "Yahoo Finance" + (", " + heat.GECKO_SRC[0] if any(r.get("crypto") for r in rows) else "")
    return {"rows": rows, "asOf": int(time.time()), "source": src}
