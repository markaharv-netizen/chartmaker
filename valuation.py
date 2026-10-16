"""Valuation mode: ranked valuation multiples from Yahoo quoteSummary (+ v7 quote), disk-cached 6h.
Fallbacks when Yahoo's crumb endpoints are rate-limited: stale disk copy (flagged), then SEC-derived ratios via fin.py
(price from the v8 chart API). Nothing invented: missing/negative values are returned as None with a reason."""
import json, os, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import heat, fin
DISK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "valuation.json")
TTL = 6 * 3600
METRICS = {  # id: (label, definition)
 "pe": ("Trailing P/E", "price / trailing 12-month EPS"), "fpe": ("Forward P/E", "price / analysts' next-fiscal-year EPS estimate"),
 "ps": ("Price/Sales (TTM)", "market cap / trailing 12-month revenue"), "evebitda": ("EV/EBITDA", "enterprise value / trailing 12-month EBITDA"),
 "evrev": ("EV/Revenue", "enterprise value / trailing 12-month revenue"), "pb": ("Price/Book", "price / book value per share"),
 "peg": ("PEG", "P/E / expected EPS growth (Yahoo, 5y)"), "mcap": ("Market cap (USD)", "price × shares, converted to USD"),
 "dy": ("Dividend yield", "forward annual dividend / price")}
def _disk():
    try:
        with open(DISK) as f: return json.load(f)
    except Exception: return {}
def _save(sym, rec):
    d = _disk(); d[sym] = rec
    try:
        os.makedirs(os.path.dirname(DISK), exist_ok=True)
        with open(DISK + ".tmp", "w") as f: json.dump(d, f)
        os.replace(DISK + ".tmp", DISK)
    except Exception: pass
def _qs(sym):
    if os.environ.get("CM_FORCE_V7_FAIL"): raise RuntimeError("HTTP 429 (simulated)")
    last = None
    for host in ("query2", "query1"):
        for force in (False, True):
            try:
                u = f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(sym)}?" + urllib.parse.urlencode({"modules": "defaultKeyStatistics,summaryDetail,financialData,price", "crumb": heat.crumb(force)})
                with heat._op.open(urllib.request.Request(u, headers=heat.UA), timeout=15) as r: return json.load(r)["quoteSummary"]["result"][0]
            except Exception as e: last = e
    raise last
def _raw(m, *path):
    for mod, k in path:
        v = ((m.get(mod) or {}).get(k) or {})
        v = v.get("raw") if isinstance(v, dict) else v
        if isinstance(v, (int, float)): return v
    return None
def yahoo_rec(sym):
    m = _qs(sym); cur = (m.get("price") or {}).get("currency") or (m.get("summaryDetail") or {}).get("currency")
    mc = _raw(m, ("price", "marketCap"), ("summaryDetail", "marketCap"))
    fx = heat.fx_to_usd([cur]) if cur else {}; maj, _ = heat.MINOR.get(cur, (cur, 1)); r = fx.get(maj)
    v = {"pe": _raw(m, ("summaryDetail", "trailingPE")), "fpe": _raw(m, ("summaryDetail", "forwardPE"), ("defaultKeyStatistics", "forwardPE")),
         "ps": _raw(m, ("summaryDetail", "priceToSalesTrailing12Months")), "evebitda": _raw(m, ("defaultKeyStatistics", "enterpriseToEbitda")),
         "evrev": _raw(m, ("defaultKeyStatistics", "enterpriseToRevenue")), "pb": _raw(m, ("defaultKeyStatistics", "priceToBook")),
         "peg": _raw(m, ("defaultKeyStatistics", "pegRatio"), ("defaultKeyStatistics", "trailingPegRatio")),
         "mcap": mc * r if mc and r else None, "dy": _raw(m, ("summaryDetail", "dividendYield"))}
    name = (m.get("price") or {}).get("shortName") or (m.get("price") or {}).get("longName") or sym
    return {"symbol": sym, "name": name, "v": v, "src": "Yahoo Finance", "t": time.time()}
def _last(sym, metric, period="ttm"):
    rows = fin.series(sym, metric, period)["rows"]
    return rows[-1] if rows else None
def sec_rec(sym):
    """SEC XBRL (fin.py) + v8 price. US-GAAP USD filers only. P/E, P/S, EV/Revenue, market cap; others unavailable."""
    q = heat.chart_quote(sym); px = q["regularMarketPrice"]
    if q.get("currency") != "USD": raise ValueError("SEC fallback only for USD listings")
    g = lambda *a: heat._safe(_last, sym, *a)
    sh, rev, eps, debt, cash = g("shares", "quarterly"), g("revenue"), g("eps_diluted"), g("total_debt", "quarterly"), g("cash", "quarterly")
    mc = px * sh["value"] if sh else None
    ev = mc + (debt["value"] if debt else 0) - (cash["value"] if cash else 0) if mc and debt and cash else None
    v = {k: None for k in METRICS}
    v.update(mcap=mc, ps=mc / rev["value"] if mc and rev and rev["value"] else None, pe=px / eps["value"] if eps and eps["value"] else None,
             evrev=ev / rev["value"] if ev and rev and rev["value"] else None)
    asof = max([x["end"] for x in (sh, rev, eps) if x] or [""])
    return {"symbol": sym, "name": q.get("shortName") or q.get("longName") or sym, "v": v, "src": "SEC filings + Yahoo price", "secThrough": asof, "t": time.time(), "sec": True}
def get(sym, fresh=False):
    d = _disk().get(sym)
    if d and not fresh and time.time() - d["t"] < TTL and not d.get("sec"): return dict(d, cached=True)
    try: rec = yahoo_rec(sym); _save(sym, rec); return rec
    except Exception as e: err = str(e)
    if d and not d.get("sec"): return dict(d, stale=True)
    try: return sec_rec(sym)
    except Exception as e2: return {"symbol": sym, "name": sym, "v": {k: None for k in METRICS}, "src": None, "t": time.time(), "error": f"Yahoo: {err}; SEC: {e2}"}
def compute(tickers, metric, fresh=False):
    if metric not in METRICS: raise ValueError("unknown metric")
    syms = [s.strip().upper() for s in tickers.split(",") if s.strip()][:40]
    if not syms: raise ValueError("Enter tickers")
    with ThreadPoolExecutor(6) as ex: recs = list(ex.map(lambda s: get(s, fresh), syms))
    rows, nm = [], []
    for r in recs:
        x = r["v"].get(metric)
        if x is None or x <= 0: nm.append({"symbol": r["symbol"], "why": "error" if r.get("error") else "negative" if x is not None else "n/a"}); continue
        rows.append({"symbol": r["symbol"], "name": r["name"], "value": x, "src": r["src"], "secThrough": r.get("secThrough"), "t": r["t"], "stale": bool(r.get("stale"))})
    return {"metric": metric, "label": METRICS[metric][0], "def": METRICS[metric][1], "rows": rows, "nm": nm,
            "errors": [f"{r['symbol']}: {r['error']}" for r in recs if r.get("error")], "all": {r["symbol"]: r["v"] for r in recs}}
