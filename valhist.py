"""Valuation over time: Yahoo v8 closes x point-in-time SEC XBRL (fin.py). Each SEC value is used only from the date
it was first filed (earliest 10-Q/10-K filing containing that period end); values are the latest-reported figure for
that period (restatements may be reflected). Disk cache 6h. Nothing invented: unavailable points are null (gaps)."""
import bisect, json, os, time, datetime as dt
import fin, heat
CDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "valhist")
TTL = 6 * 3600
DA_TAGS = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"]
METRICS = {"pe": ("Trailing P/E", "price / TTM diluted EPS (SEC)"), "ps": ("Price/Sales (TTM)", "price × SEC shares / TTM revenue"),
           "evrev": ("EV/Revenue", "(market cap + SEC debt − cash) / TTM revenue"),
           "evebitda": ("EV/EBITDA", "EV / TTM (operating income + D&A) (SEC)"), "mcap": ("Market cap (USD)", "price × SEC shares outstanding"),
           "mnav": ("mNAV", "market cap / (SEC-filed BTC held × BTC price)")}
RANGES = {"1Y": (366, "1d"), "3Y": (3 * 366, "1d"), "5Y": (5 * 366, "1wk"), "10Y": (3653, "1wk"), "Max": (None, "1wk")}
MAXAGE = 200  # days: drop an SEC value if its period end is older than this at the price date
def _avail(f, tags):
    """period end -> earliest filing date (YYYY-MM-DD) among 10-Q/10-K facts with that end for these tags."""
    g = f["facts"].get("us-gaap", {}); out = {}
    for t in tags:
        for u in g.get(t, {}).get("units", {}).values():
            for x in u:
                if x.get("form") in fin.FORMS and x.get("filed"):
                    e = x["end"]
                    if e not in out or x["filed"] < out[e]: out[e] = x["filed"]
    return out
def _pit(pts, avail):
    """[(avail_ts, end, val)] sorted by availability; keeps only values with a known filing date."""
    rows = []
    for e, p in pts.items():
        a = avail.get(e)
        if a: rows.append((int(dt.datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) + 86400, e, p["val"]))
    rows.sort(); return rows
def _asof(rows, t):
    """latest-ending value already filed by t (ties: newest period end) and not older than MAXAGE."""
    i = bisect.bisect_right([r[0] for r in rows], t)
    if not i: return None
    best = max(rows[:i], key=lambda r: r[1])
    if (dt.datetime.utcfromtimestamp(t).date() - dt.date.fromisoformat(best[1])).days > MAXAGE: return None
    return best[2]
def _raw_first(f, kind, unit, tags, ns="us-gaap"):
    """Like fin._raw but the ORIGINAL (earliest-filed) value per period wins: point-in-time, no restatement lookahead.
    Each value carries its filing date."""
    g = f["facts"].get(ns, {}); out = {}
    for pri, tag in enumerate(tags):
        units = g.get(tag, {}).get("units", {})
        for x in (units.get(unit) or []):
            if x.get("form") not in fin.FORMS or not x.get("filed"): continue
            k = (x.get("start"), x["end"])
            if (kind == "inst") != (k[0] is None): continue
            cur = out.get(k)
            if cur is None or pri < cur["pri"] or (pri == cur["pri"] and x["filed"] < cur["filed"]):
                out[k] = {"val": x["val"], "filed": x["filed"], "form": x["form"], "pri": pri, "tag": tag, "cur": unit}
    return out
def _ttm_first(f, tags, unit="USD", sp=None):
    """TTM from original filings; available from the filing date of the latest quarter in the window. Per-share values are
    put on today's share basis BEFORE quarter derivation (each fact by the splits after its own filing date)."""
    raw = _raw_first(f, "flow", unit, tags)
    if sp:
        for x in raw.values(): x["val"] = x["val"] / _k(sp, x["filed"])
    q = fin._quarters(raw); t = fin._ttm(q)
    return [(_ts(q[e]["filed"]), e, p["val"]) for e, p in t.items() if q.get(e, {}).get("filed")]
def _ts(d): return int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) + 86400
def _inst_first(f, tags, unit="USD", ns="us-gaap"):
    return sorted((_ts(v["filed"]), e, v["val"]) for (_, e), v in _raw_first(f, "inst", unit, tags, ns).items())
SPLITS = {}
def splits(sym):
    """Yahoo split events [(ts, ratio)] (cached 1 day)."""
    c = SPLITS.get(sym)
    if c and time.time() - c[0] < 86400: return c[1]
    import urllib.request, urllib.parse
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?period1=0&period2={int(time.time())}&interval=1mo&events=split"
    with urllib.request.urlopen(urllib.request.Request(u, headers=heat.UA), timeout=20) as r: ev = (json.load(r)["chart"]["result"][0].get("events") or {}).get("splits") or {}
    out = sorted((x["date"], x["numerator"] / x["denominator"]) for x in ev.values() if x.get("denominator"))
    SPLITS[sym] = (time.time(), out); return out
def _k(sp, filed):
    """cumulative split ratio after a filing date: a value filed before a split is on the old share basis."""
    te = _ts(filed) - 86400; k = 1.0
    for t, r in sp:
        if t > te: k *= r
    return k
def _adj(rows, sp, per_share):
    """Express SEC values (rows = (avail_ts, end, val)) in today's share basis using Yahoo splits after each filing date."""
    out = []
    for a, e, val in rows:
        k = _k(sp, dt.datetime.utcfromtimestamp(a - 86400).strftime("%Y-%m-%d"))
        out.append((a, e, val / k if per_share else val * k))
    return out
def _wavg(f):
    """quarterly weighted-avg basic shares; fiscal Q4 (10-K has only the 12-month figure) filled with the FY average."""
    raw = _raw_first(f, "avg", "shares", ["WeightedAverageNumberOfSharesOutstandingBasic"])
    q = fin._quarters(raw, additive=False); rows = {e: v for e, v in q.items() if v.get("filed")}
    for (s, e), v in raw.items():
        if e not in rows and fin._isy(fin._days(s, e)): rows[e] = v
    return [(_ts(v["filed"]), e, v["val"]) for e, v in rows.items()]
def _shares(f, sp):
    """Point-in-time share count, one consistent source: dei cover-page count, else weighted-avg basic (quarterly, all classes),
    else us-gaap CommonStockSharesOutstanding. Split-adjusted to today's basis via Yahoo splits."""
    for rows, src in ((_inst_first(f, ["EntityCommonStockSharesOutstanding"], "shares", "dei"), "cover-page shares (dei)"),
                      (_wavg(f), "weighted-avg basic shares"),
                      (_inst_first(f, ["CommonStockSharesOutstanding"], "shares"), "CommonStockSharesOutstanding")):
        if len(rows) >= 4: return sorted(_adj(rows, sp, False)), src
    return [], None
def series(sym, metric, rng, fresh=False):
    sym = sym.strip().upper(); key = f"{sym}_{metric}_{rng}".replace("/", "_")
    p = os.path.join(CDIR, key + ".json")
    if not fresh and os.path.exists(p) and time.time() - os.path.getmtime(p) < TTL:
        with open(p) as fh: return dict(json.load(fh), cached=True)
    out = _series(sym, metric, rng)
    try:
        os.makedirs(CDIR, exist_ok=True)
        with open(p + ".tmp", "w") as fh: json.dump(out, fh)
        os.replace(p + ".tmp", p)
    except OSError: pass
    return out
def _series(sym, metric, rng):
    days, iv = RANGES[rng]; now = int(time.time())
    prm = {"period1": now - days * 86400 if days else 0, "period2": now, "interval": iv, "includePrePost": "false"}
    base = {"symbol": sym, "metric": metric, "range": rng, "points": [], "t": time.time()}
    try: cik, title = fin.cik_of(sym); f = fin.facts(cik)
    except Exception as e: return dict(base, nm=f"no SEC filings ({e})")
    if not f["facts"].get("us-gaap"): return dict(base, nm="no US-GAAP SEC data (foreign/IFRS filer)")
    px = heat.alt_closes(sym, prm)
    if not px: return dict(base, nm="no Yahoo prices")
    T = fin.TAGS; sp = splits(sym); srcs = []
    need_sh = metric in ("ps", "evrev", "evebitda", "mcap", "mnav")
    if need_sh: sh, s_src = _shares(f, sp); srcs.append(s_src or "no SEC share count")
    rev = sorted(_ttm_first(f, T["revenue"][2])) if metric in ("ps", "evrev") else []
    eps = sorted(_ttm_first(f, T["eps_diluted"][2], "USD/shares", sp)) if metric == "pe" else []
    if metric in ("evrev", "evebitda"):
        lt, nc, cu, st = (dict(((e, (a, v)) for a, e, v in _inst_first(f, T[k][2]))) for k in ("debt_lt", "debt_ltnc", "debt_ltc", "debt_st"))
        debt = []
        for e in set(lt) | set(nc):
            parts = [lt[e]] if e in lt else [nc[e]] + ([cu[e]] if e in cu else [])
            if e in st: parts.append(st[e])
            debt.append((max(x[0] for x in parts), e, sum(x[1] for x in parts)))
        debt.sort()
        cash = _inst_first(f, T["cash"][2])
    if metric == "evebitda":
        oi = {e: (a, v) for a, e, v in _ttm_first(f, T["operating_income"][2])}; da = {e: (a, v) for a, e, v in _ttm_first(f, DA_TAGS)}
        ebitda = sorted((max(oi[e][0], da[e][0]), e, oi[e][1] + da[e][1]) for e in oi if e in da)
    if metric == "mnav":
        g = f["facts"]["us-gaap"].get("CryptoAssetNumberOfUnits", {}).get("units", {}).get("Bitcoin", [])
        hold = {x["end"]: {"val": x["val"]} for x in g if x.get("form") in fin.FORMS and "start" not in x}
        if not hold: return dict(base, nm="no SEC-filed BTC holdings (us-gaap CryptoAssetNumberOfUnits)")
        btc = _inst_first(f, ["CryptoAssetNumberOfUnits"], "Bitcoin")
        bpx = dict((t // 86400, c) for t, c in heat.alt_closes("BTC-USD", dict(prm, interval="1d")))
    pts = []
    for t, c in px:
        v = None; s = _asof(sh, t) if need_sh else None; mc = c * s if s else None
        if metric == "pe":
            e = _asof(eps, t); v = c / e if e and e > 0 else None
        elif metric == "mcap": v = mc
        elif metric == "ps":
            r = _asof(rev, t); v = mc / r if mc and r and r > 0 else None
        elif metric in ("evrev", "evebitda"):
            d, ca = _asof(debt, t), _asof(cash, t)
            den = _asof(rev, t) if metric == "evrev" else _asof(ebitda, t)
            ev = mc + (d or 0) - ca if mc and ca is not None else None
            v = ev / den if ev and den and den > 0 and ev > 0 else None
        elif metric == "mnav":
            b = _asof(btc, t); bp = bpx.get(t // 86400) or bpx.get(t // 86400 - 1)
            v = mc / (b * bp) if mc and b and bp else None
        pts.append([t * 1000, v])
    valid = [x for x in pts if x[1] is not None]
    return dict(base, name=f.get("entityName") or title, points=pts, nm=None if valid else "no point-in-time SEC data for this metric/range",
                firstValid=valid[0][0] if valid else None, sharesSrc=srcs[0] if srcs else None)
def compute(tickers, metric, rng, fresh=False):
    if metric not in METRICS or rng not in RANGES: raise ValueError("unsupported metric/range for Over time")
    syms = [s.strip().upper() for s in tickers.split(",") if s.strip()][:12]
    from concurrent.futures import ThreadPoolExecutor
    def one(s):
        try: return series(s, metric, rng, fresh)
        except Exception as e: return {"symbol": s, "points": [], "nm": f"error: {e}"}
    with ThreadPoolExecutor(4) as ex: res = list(ex.map(one, syms))
    return {"metric": metric, "label": METRICS[metric][0], "def": METRICS[metric][1], "range": rng, "series": res}
