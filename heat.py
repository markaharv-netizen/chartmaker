"""Heatmap backend: Yahoo v7 quote (crumb) for market cap + live 1D change; chart API for other ranges; logo proxy."""
import json, os, time, threading, urllib.request, urllib.parse, http.cookiejar
from concurrent.futures import ThreadPoolExecutor
UA = {"User-Agent": "Mozilla/5.0"}
_cj = http.cookiejar.CookieJar(); _op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_crumb = [None, 0]; _lock = threading.Lock()
QCACHE, RCACHE, LOGOS = {}, {}, {}
LOGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "logos")
# Static universe: large S&P 500 names with GICS sector (ranked live by market cap; top N taken)
SP = {"Technology": "AAPL MSFT NVDA AVGO ORCL CRM AMD CSCO ADBE ACN IBM NOW INTU QCOM TXN AMAT MU LRCX KLAC ADI INTC PANW ANET CRWD SNPS CDNS APH PLTR DELL MSI",
 "Communication": "GOOGL META NFLX TMUS DIS VZ T CMCSA",
 "Consumer Disc.": "AMZN TSLA HD MCD LOW BKNG TJX SBUX NKE ABNB ORLY CMG",
 "Financials": "BRK-B JPM V MA BAC WFC GS MS AXP SCHW C BLK SPGI PGR CB COF ICE CME PYPL HOOD KKR BX",
 "Health Care": "LLY UNH JNJ ABBV MRK TMO ABT ISRG AMGN DHR PFE BSX SYK GILD VRTX MDT BMY ELV CVS REGN",
 "Consumer Staples": "WMT COST PG KO PEP PM MO MDLZ CL",
 "Energy": "XOM CVX COP EOG SLB",
 "Industrials": "GE CAT RTX UBER HON UNP BA DE LMT ETN PH GEV UPS WM TT ADP",
 "Utilities": "NEE SO DUK CEG VST", "Real Estate": "PLD AMT WELL EQIX", "Materials": "LIN SHW APD"}
SECTOR = {s: k for k, v in SP.items() for s in v.split()}
MAG7 = "AAPL MSFT NVDA AMZN GOOGL META TSLA".split()
def crumb(force=False):
    with _lock:
        if force or not _crumb[0] or time.time() - _crumb[1] > 3600:
            try: _op.open(urllib.request.Request("https://fc.yahoo.com", headers=UA), timeout=10)
            except Exception: pass
            _crumb[0] = _op.open(urllib.request.Request("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=UA), timeout=10).read().decode().strip(); _crumb[1] = time.time()
        return _crumb[0]
def quotes(syms, fresh=False):
    """Batched v7 quote (50/request); cached 30s per symbol."""
    now, out, need = time.time(), {}, []
    for s in syms:
        c = QCACHE.get(s)
        if c and not fresh and now - c[0] < 30: out[s] = c[1]
        else: need.append(s)
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        for attempt in (0, 1):
            try:
                u = "https://query1.finance.yahoo.com/v7/finance/quote?" + urllib.parse.urlencode({"symbols": ",".join(chunk), "crumb": crumb(attempt == 1)})
                with _op.open(urllib.request.Request(u, headers=UA), timeout=15) as r: res = json.load(r)["quoteResponse"]["result"]
                break
            except Exception:
                if attempt: raise
        for q in res:
            d = {k: q.get(k) for k in ("symbol", "shortName", "longName", "marketCap", "regularMarketPrice", "regularMarketPreviousClose", "regularMarketChangePercent", "regularMarketTime", "currency", "quoteType")}
            QCACHE[q["symbol"]] = (now, d); out[q["symbol"]] = d
    return out
def range_ret(sym, rng, fresh=False):
    """% change over a Yahoo range from daily closes (5d uses 1d closes; base = close before window). Cached 5 min."""
    key = (sym, rng)
    c = RCACHE.get(key)
    if c and not fresh and time.time() - c[0] < 300: return c[1]
    yr = {"5D": "5d", "1M": "1mo", "6M": "6mo", "1Y": "1y", "5Y": "5y", "YTD": "ytd", "Max": "max"}[rng]
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?" + urllib.parse.urlencode({"range": yr, "interval": "1d" if rng not in ("5Y", "Max") else "1wk"})
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r: r0 = json.load(r)["chart"]["result"][0]
    cl = [x for x in r0["indicators"]["quote"][0]["close"] if x is not None]
    m = r0["meta"]; b = m.get("chartPreviousClose") if rng in ("5D", "1M", "6M", "1Y", "YTD") else None
    b = b or cl[0]; last = m.get("regularMarketPrice") or cl[-1]
    v = (last / b - 1) * 100; RCACHE[key] = (time.time(), v); return v
GECKO, CIMG = [0, []], {}
STABLE = set("USDT USDC DAI USDE USDS FDUSD TUSD PYUSD USDD USD1 RLUSD USDTB BUSD GUSD USDP FRAX LUSD EURC EURS USDX USDG USDF USYC BUIDL USTB OUSG SUSDE SUSDS USD0 USDB USDL XAUT PAXG FIGR_HELOC USDY EURSAFO SYRUPUSDC USDAI USR".split())
WRAP = set("WBTC WETH STETH WSTETH WEETH CBBTC RETH METH WBETH LBTC SOLVBTC BTCB JITOSOL BNSOL MSOL EZETH RSETH CBETH SAVAX OSETH TBTC CLBTC LSETH UNIBTC WBNB BSC-USD SWETH".split())
def gecko(fresh=False):
    """CoinGecko /coins/markets top 250 (one request, no key), cached 120s; stale copy reused on error."""
    if fresh and time.time() - GECKO[0] < 20: fresh = False  # free tier: ~30 req/min
    if GECKO[1] and not fresh and time.time() - GECKO[0] < 120: return GECKO[1]
    u = "https://api.coingecko.com/api/v3/coins/markets?" + urllib.parse.urlencode({"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": 1, "price_change_percentage": "24h,7d,30d,1y"})
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={**UA, "Accept": "application/json"}), timeout=20) as r: GECKO[:] = [time.time(), json.load(r)]
    except Exception:
        if not GECKO[1]: raise
    for c in GECKO[1]: CIMG.setdefault(c["symbol"].upper(), c.get("image"))
    return GECKO[1]
GK = {"1D": "price_change_percentage_24h_in_currency", "7D": "price_change_percentage_7d_in_currency", "1M": "price_change_percentage_30d_in_currency", "1Y": "price_change_percentage_1y_in_currency"}
def is_stable(c):
    s = c["symbol"].upper()
    return s in STABLE or s in WRAP or ((c.get("current_price") or 0) > 0.97 and (c.get("current_price") or 0) < 1.03 and any(k in (c.get("name") or "").lower() for k in ("usd", "dollar", "stable")))
def crow(c, rng, fresh):
    s = c["symbol"].upper()
    d = {"symbol": s, "shortName": c.get("name"), "marketCap": c.get("market_cap"), "regularMarketPrice": c.get("current_price"),
         "regularMarketTime": int(time.time()), "crypto": True, "logoKey": "c:" + s, "sector": "Crypto"}
    if rng in GK: d["chg"] = c.get(GK[rng]) if rng != "1D" else (c.get(GK[rng]) if c.get(GK[rng]) is not None else c.get("price_change_percentage_24h"))
    else: d["chg"] = _safe(range_ret, s + "-USD", "5D" if rng == "7D" else rng, fresh)
    return d
def heatmap(universe, tickers, rng, fresh=False, stables=False):
    if universe in ("crypto50", "crypto100"):
        cs = [c for c in gecko(fresh) if c.get("market_cap") and (stables or not is_stable(c))][:50 if universe == "crypto50" else 100]
        if rng in GK: rows = [crow(c, rng, fresh) for c in cs]
        else:
            with ThreadPoolExecutor(8) as ex: rows = list(ex.map(lambda c: crow(c, rng, fresh), cs))
        ok = [d for d in rows if d.get("chg") is not None]
        return {"rows": ok, "missing": [d["symbol"] for d in rows if d.get("chg") is None], "asOf": int(GECKO[0]), "range": rng,
                "source": "CoinGecko" + ("" if rng in GK else " · Yahoo Finance daily closes")}
    return _heatmap(universe, tickers, rng, fresh)
def _heatmap(universe, tickers, rng, fresh=False):
    crypto = []
    if universe == "mag7": syms = MAG7
    elif universe == "custom":
        syms = [s.strip().upper() for s in tickers.split(",") if s.strip()][:200]
        # crypto symbols: "BTC-USD" always; bare "BTC"/"ETH" when it is a top-100 coin by market cap (wins over a same-named stock/ETF)
        try: top = {c["symbol"].upper(): c for c in gecko(fresh)[:100]}; allc = {c["symbol"].upper(): c for c in gecko()}
        except Exception: top, allc = {}, {}
        for x in list(syms):
            b = x[:-4] if x.endswith("-USD") else x
            c = allc.get(b) if x.endswith("-USD") else top.get(b)
            if c: crypto.append(crow(c, rng, fresh)); syms.remove(x)
    else: syms = list(SECTOR)
    if not syms and not crypto: raise ValueError("Enter tickers")
    q = quotes(syms, fresh) if syms else {}
    rows = [d for s in syms if (d := q.get(s)) and d.get("marketCap")]
    rows.sort(key=lambda d: -d["marketCap"])
    if universe == "sp100": rows = rows[:100]
    errs = [s for s in syms if s not in q or not q[s].get("marketCap")]
    if rng == "1D":
        for d in rows:
            pc, px = d.get("regularMarketPreviousClose"), d.get("regularMarketPrice")
            d["chg"] = (px / pc - 1) * 100 if pc and px else d.get("regularMarketChangePercent")
    else:
        with ThreadPoolExecutor(8) as ex: res = list(ex.map(lambda d: _safe(range_ret, d["symbol"], rng, fresh), rows))
        for d, v in zip(rows, res): d["chg"] = v
    for d in rows: d["sector"] = SECTOR.get(d["symbol"], "Other")
    rows = sorted(rows + crypto, key=lambda d: -(d.get("marketCap") or 0))
    src = "Yahoo Finance" if not crypto else ("CoinGecko" if not syms else "Yahoo Finance, CoinGecko")
    return {"source": src, "cryptoMixed": bool(crypto and syms), "rows": [d for d in rows if d.get("chg") is not None], "missing": errs + [d["symbol"] for d in rows if d.get("chg") is None],
            "asOf": max([d.get("regularMarketTime") or 0 for d in rows] or [0]), "range": rng}
def _safe(f, *a):
    try: return f(*a)
    except Exception: return None
def logo(sym):
    if sym.startswith("c:"): return clogo(sym[2:])
    return slogo(sym)
def clogo(sym):
    sym = "".join(c for c in sym.upper() if c.isalnum())[:15]; key = "c_" + sym
    c = LOGOS.get(key)
    if c and (c[1] or time.time() - c[0] < 3600): return c[1]
    p = os.path.join(LOGO_DIR, key + ".png")
    if os.path.exists(p): b = open(p, "rb").read(); LOGOS[key] = (time.time(), b); return b
    if sym not in CIMG: _safe(gecko)
    b = None; u = CIMG.get(sym)
    if u:
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=10) as r:
                if (r.headers.get("Content-Type") or "").startswith("image/"): b = r.read()
        except Exception: pass
    if b:
        try: os.makedirs(LOGO_DIR, exist_ok=True); open(p, "wb").write(b)
        except OSError: pass
    LOGOS[key] = (time.time(), b); return b
def slogo(sym):
    """PNG bytes or None. Sources: financialmodelingprep image-stock, then parqet. Memory + disk cache (negative cached 1 day)."""
    sym = "".join(c for c in sym.upper() if c.isalnum() or c in "-.")[:12]
    c = LOGOS.get(sym)
    if c and (c[1] or time.time() - c[0] < 86400): return c[1]
    p = os.path.join(LOGO_DIR, sym + ".png")
    if os.path.exists(p):
        b = open(p, "rb").read(); LOGOS[sym] = (time.time(), b); return b
    b = None
    for u in (f"https://financialmodelingprep.com/image-stock/{sym}.png", f"https://assets.parqet.com/logos/symbol/{sym}?format=png"):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=10) as r:
                if r.status == 200 and (r.headers.get("Content-Type") or "").startswith("image/"):
                    x = r.read()
                    if len(x) > 300: b = x; break
        except Exception: pass
    if b:
        try: os.makedirs(LOGO_DIR, exist_ok=True); open(p, "wb").write(b)
        except OSError: pass
    LOGOS[sym] = (time.time(), b); return b
