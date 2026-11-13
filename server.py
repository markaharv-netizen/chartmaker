"""Chartmaker backend: stdlib-only proxy for Yahoo Finance chart API + static frontend."""
import urllib.error, fin, heat, watch, treasury, valuation, valhist, json, os, re, sys, time, hmac, hashlib, secrets, threading, html, http.cookiejar, urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
def _port():
    env = os.environ.get("PORT")
    if env: return int(env)
    if len(sys.argv) > 1 and sys.argv[1].isdigit(): return int(sys.argv[1])
    return 8791
PORT = _port()
CACHE = {}
VIEWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views.json")
PASSWORD = os.environ.get("APP_PASSWORD") or ""
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()
VIEWS_STORE = "browser" if os.environ.get("VIEWS_STORE", "").lower() == "browser" else "server"
COOKIE, MAX_AGE = "cm_session", 30 * 86400
FAILS, FLOCK = {}, threading.Lock()  # ip -> [count, first_fail_ts, locked_until]
def sign(exp): return hmac.new(SECRET, f"v1.{exp}".encode(), hashlib.sha256).hexdigest()
def make_token(): exp = int(time.time()) + MAX_AGE; return f"{exp}.{sign(exp)}"
def token_ok(t):
    try: exp, sig = t.split(".", 1); return int(exp) > time.time() and hmac.compare_digest(sig, sign(int(exp)))
    except Exception: return False
def throttled(ip):
    with FLOCK: f = FAILS.get(ip); return bool(f and f[2] > time.time())
def record_fail(ip):
    with FLOCK:
        now = time.time(); f = FAILS.get(ip)
        if not f or now - f[1] > 900: f = [0, now, 0]
        f[0] += 1
        if f[0] >= 5: f[2] = now + min(900, 30 * 2 ** (f[0] - 5))  # 30s, 60s, ... up to 15 min
        FAILS[ip] = f
    time.sleep(1)
LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Chartmaker</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0F1115;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#e8e8ea}
form{background:#181b22;padding:36px 32px;border-radius:14px;box-shadow:0 10px 40px #0008;width:300px}h1{margin:0 0 6px;font-size:22px}p{margin:0 0 20px;color:#9aa0aa;font-size:14px}
input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:8px;border:1px solid #2c313c;background:#0F1115;color:#fff;font-size:15px;margin-bottom:14px}
button{width:100%;padding:11px;border:0;border-radius:8px;background:#F7931A;color:#111;font-weight:600;font-size:15px;cursor:pointer}.e{color:#ff6b6b;font-size:13px;margin:-4px 0 12px}</style></head>
<body><form method=post action=/login><h1>Chartmaker</h1><p>Enter the password to continue.</p>%ERR%<input type=password name=password autofocus autocomplete=current-password placeholder=Password><button>Sign in</button></form></body></html>"""
_SQ = {}
def ysearch(q):
    """Ticker autocomplete via Yahoo search (quotes only). Cached 10 min; any failure returns []."""
    q = q.strip()[:60]
    if not q: return []
    k = q.lower(); hit = _SQ.get(k)
    if hit and time.time() - hit[0] < 600: return hit[1]
    try:
        url = "https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({"q": q, "quotesCount": 10, "newsCount": 0, "listsCount": 0, "enableFuzzyQuery": "false"})
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=6) as r: j = json.load(r)
        out = [{"symbol": x["symbol"], "name": x.get("longname") or x.get("shortname") or x["symbol"], "exch": x.get("exchDisp") or x.get("exchange") or "", "type": x.get("quoteType")}
               for x in j.get("quotes", []) if x.get("symbol") and x.get("quoteType") in ("EQUITY", "ETF", "CRYPTOCURRENCY")][:8]
    except Exception: return []
    if len(_SQ) > 500: _SQ.clear()
    _SQ[k] = (time.time(), out); return out
# Saved views + current watchlist. With GITHUB_TOKEN set they live in views.json on a data-only branch (VIEWS_BRANCH) of VIEWS_REPO
# via the GitHub Contents API, so they survive redeploys and follow Mark to any browser. Without a token: local views.json file.
GH_TOKEN = os.environ.get("GITHUB_TOKEN") or ""
VIEWS_REPO = os.environ.get("VIEWS_REPO") or "markaharv-netizen/chartmaker"
VIEWS_BRANCH = os.environ.get("VIEWS_BRANCH") or "views-data"
_VS = {"doc": None, "sha": None, "at": 0}; _VLOCK = threading.Lock()
# Shared favorites live in the same document as saved views. One password, one list:
# every signed-in browser sees the same stars. There are no per-person accounts.
FAV_MAX, FAV_TOMBSTONES = 200, 500
FAV_KEY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]{0,24})/(\d+)/([1-9]\d?)$")
# Permanent retired charts. These stay rejected even if sheets_catalog cannot be
# imported. EXCLUDED_TABS is unioned in when that module loads. 1024131985 is
# Strategy tab 99 (PR #34) and is in EXCLUDED_TABS as well. 1791345316 and
# 1370617918 are the Korea/Taiwan and supercore tabs Mark deleted on 2026-11-09.
# The 2026-11-10 gids are the tabs Mark deleted that day, including bot2 418894352.
# 1308503061, 748431671, and 254001772 are the tabs Mark deleted on 2026-11-11.
# 1238930141, 1835714359, 492285771, 220064271, 79264442, 1755343475, and
# 1067583608 are the tabs Mark retired on 2026-11-12.
# 1924976898, 1978525586, 1255387315, 1522766504, 1163669375, 1576800449,
# 1192979444, 1912487083, 664060481, and 1778640335 are the tabs Mark retired on 2026-11-13.
def _favorite_retired():
    pairs = {
        ("bot1", 1547332232),
        ("bot1", 643299192),
        ("bot1", 333185169),
        ("bot1", 1024131985),
        ("bot1", 1791345316),
        ("bot1", 1370617918),
        ("bot2", 418894352),
        ("bot1", 292208014),
        ("bot1", 2119398138),
        ("bot1", 1584345646),
        ("bot1", 1186940001),
        ("bot1", 501919598),
        ("bot1", 579853621),
        ("bot1", 1308503061),
        ("bot1", 748431671),
        ("bot1", 254001772),
        ("bot1", 1238930141),
        ("bot1", 1835714359),
        ("bot1", 492285771),
        ("bot1", 220064271),
        ("bot1", 79264442),
        ("bot1", 1755343475),
        ("bot1", 1067583608),
        ("bot1", 1924976898),
        ("bot1", 1978525586),
        ("bot1", 1255387315),
        ("bot1", 1522766504),
        ("bot1", 1163669375),
        ("bot1", 1576800449),
        ("bot1", 1192979444),
        ("bot1", 1912487083),
        ("bot1", 664060481),
        ("bot1", 1778640335),
    }
    try:
        scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if scripts not in sys.path: sys.path.insert(0, scripts)
        import sheets_catalog
        pairs.update(tuple(x) for x in getattr(sheets_catalog, "EXCLUDED_TABS", ()))
    except Exception:
        pass
    return pairs
FAV_RETIRED = _favorite_retired()
def _fav_title(title):
    if not isinstance(title, str): return ""
    return " ".join(title.split())[:400]
def _fav_retired(key):
    m = FAV_KEY_RE.fullmatch(key or "")
    if not m: return True
    return (m.group(1), int(m.group(2))) in FAV_RETIRED
def _norm_fav(raw):
    """Canonical favorites document. Drops retired keys and keeps a bounded tombstone list."""
    if not isinstance(raw, dict): raw = {}
    try: rev = int(raw.get("rev") or 0)
    except (TypeError, ValueError): rev = 0
    if rev < 0: rev = 0
    items, seen = [], set()
    for item in raw.get("items") or []:
        if not isinstance(item, dict): continue
        k = item.get("k")
        if not isinstance(k, str) or not FAV_KEY_RE.fullmatch(k) or k in seen or _fav_retired(k): continue
        try: added = int(item.get("rev") or 0)
        except (TypeError, ValueError): added = 0
        if added < 0: added = 0
        items.append({"k": k, "title": _fav_title(item.get("title")), "rev": added})
        seen.add(k)
        if len(items) >= FAV_MAX: break
    items.sort(key=lambda x: (x["rev"], x["k"]))
    removed, rseen = [], set()
    for item in raw.get("removed") or []:
        if not isinstance(item, dict): continue
        k = item.get("k")
        if not isinstance(k, str) or not FAV_KEY_RE.fullmatch(k) or k in seen or k in rseen or _fav_retired(k): continue
        try: r = int(item.get("rev") or 0)
        except (TypeError, ValueError): r = 0
        if r < 0: r = 0
        removed.append({"k": k, "rev": r}); rseen.add(k)
    removed.sort(key=lambda x: (-x["rev"], x["k"]))
    return {"rev": rev, "items": items, "removed": removed[:FAV_TOMBSTONES]}
def _norm(d):
    if isinstance(d, list): d = {"views": d}
    if not isinstance(d, dict): d = {}
    v = d.get("views"); w = d.get("watchlist")
    return {"views": v if isinstance(v, list) else [],
            "watchlist": w if isinstance(w, dict) and isinstance(w.get("list"), list) else None,
            "favorites": _norm_fav(d.get("favorites"))}
def favorites_mode():
    """Where a successful write actually lives. file is the server disk; github survives a redeploy."""
    if VIEWS_STORE == "browser": return "off"
    if GH_TOKEN: return "github"
    return "file"
FAV_SCOPE = {
    "github": "Favorites are shared by everyone who signs in to this Chartmaker. There are no separate accounts. They are stored with saved views on the private GitHub branch views-data, so a redeploy does not erase them.",
    "file": "Favorites are shared by everyone using this server. There are no separate accounts. They are stored in views.json on this server's disk. That survives a restart when the disk does. A host whose disk is wiped on deploy loses them unless GITHUB_TOKEN is set.",
    "off": "Favorites stay in this browser only. They are not stored on the server and do not sync to other browsers.",
}
def favorites_public(fav, skipped=None):
    mode = favorites_mode()
    return {"rev": fav["rev"], "items": [{"k": x["k"], "title": x["title"]} for x in fav["items"]],
            "sync": mode, "shared": mode != "off", "scope": FAV_SCOPE[mode], "skipped": skipped or []}
def favorites_change(doc, body):
    """Merge one client's explicit changes into the current document.

    migrate only adds keys and never deletes. A tombstone blocks migrate, so an old
    browser cannot put a removed star back just by still having it saved locally.
    ops are clicks since that browser's snapshot. An add is refused when a newer
    removal happened after baseRev. A remove is refused when the star was added
    again after baseRev. Neither path replaces the whole list.
    """
    if not isinstance(body, dict): raise ValueError("favorites body must be an object")
    base = body.get("baseRev", None)
    if base is None: base = -1
    elif isinstance(base, bool) or not isinstance(base, int) or base < 0 or base > 2_000_000_000:
        raise ValueError("baseRev must be a non-negative integer")
    migrate, ops = body.get("migrate") or [], body.get("ops") or []
    if not isinstance(migrate, list) or not isinstance(ops, list): raise ValueError("migrate and ops must be lists")
    if len(migrate) > FAV_MAX or len(ops) > FAV_MAX: raise ValueError("too many favorite changes")
    fav = _norm_fav(doc.get("favorites"))
    start_rev = fav["rev"]
    by_k = {x["k"]: dict(x) for x in fav["items"]}
    tomb = {x["k"]: x["rev"] for x in fav["removed"]}
    skipped, changed = [], False
    def bump():
        fav["rev"] += 1
        return fav["rev"]
    def add_item(k, title, force):
        nonlocal changed
        if not isinstance(k, str) or not FAV_KEY_RE.fullmatch(k):
            skipped.append({"k": str(k)[:80], "reason": "invalid"}); return
        if _fav_retired(k):
            skipped.append({"k": k, "reason": "retired"}); return
        title = _fav_title(title)
        if k in by_k:
            if title and by_k[k]["title"] != title and not by_k[k]["title"]:
                by_k[k]["title"] = title; changed = True
            return
        trev = tomb.get(k, -1)
        if not force and trev >= 0:
            skipped.append({"k": k, "reason": "removed"}); return
        if force and trev > base:
            skipped.append({"k": k, "reason": "removed"}); return
        if len(by_k) >= FAV_MAX:
            skipped.append({"k": k, "reason": "cap"}); return
        rev = bump()
        by_k[k] = {"k": k, "title": title, "rev": rev}
        tomb.pop(k, None); changed = True
    def remove_item(k):
        nonlocal changed
        if not isinstance(k, str) or not FAV_KEY_RE.fullmatch(k):
            skipped.append({"k": str(k)[:80], "reason": "invalid"}); return
        item = by_k.get(k)
        if item is None: return
        if item["rev"] > base:
            skipped.append({"k": k, "reason": "newer"}); return
        rev = bump()
        del by_k[k]; tomb[k] = rev; changed = True
    for item in migrate:
        if isinstance(item, str): add_item(item, "", False)
        elif isinstance(item, dict): add_item(item.get("k"), item.get("title"), False)
        else: raise ValueError("migrate entries must be objects")
    for op in ops:
        if not isinstance(op, dict): raise ValueError("op must be an object")
        kind = op.get("op")
        if kind == "add": add_item(op.get("k"), op.get("title"), True)
        elif kind == "remove": remove_item(op.get("k"))
        else: raise ValueError("op must be add or remove")
    if not changed: return doc, skipped
    if fav["rev"] == start_rev: fav["rev"] += 1
    removed = [{"k": k, "rev": r} for k, r in tomb.items()]
    removed.sort(key=lambda x: (-x["rev"], x["k"]))
    fav["items"] = sorted(by_k.values(), key=lambda x: (x["rev"], x["k"]))
    fav["removed"] = removed[:FAV_TOMBSTONES]
    out = dict(doc); out["favorites"] = fav
    return out, skipped
def favorites_read():
    if favorites_mode() == "off": return None
    return store_read(fresh=True)["favorites"]
def favorites_write(body):
    if favorites_mode() == "off": return None
    skipped = []
    def fn(d):
        nonlocal skipped
        d, skipped = favorites_change(d, body)
        return d
    doc = store_update(fn, fresh=True)
    return favorites_public(doc["favorites"], skipped)
def _gh(method, body=None):
    url = f"https://api.github.com/repos/{VIEWS_REPO}/contents/views.json" + ("" if body else f"?ref={urllib.parse.quote(VIEWS_BRANCH)}")
    rq = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None, headers={
        "Authorization": "Bearer " + GH_TOKEN, "Accept": "application/vnd.github+json", "User-Agent": "chartmaker", "Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=20) as r: return json.load(r)
def _gh_load():
    import base64
    try: j = _gh("GET"); return _norm(json.loads(base64.b64decode(j["content"]) or b"{}")), j["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404: return _norm({}), None
        raise
def store_read(fresh=False):
    with _VLOCK:
        if not GH_TOKEN:
            try:
                with open(VIEWS, encoding="utf-8") as f: return _norm(json.load(f))
            except (OSError, ValueError): return _norm({})
        if fresh or _VS["doc"] is None or time.time() - _VS["at"] > 60: _VS["doc"], _VS["sha"] = _gh_load(); _VS["at"] = time.time()
        return json.loads(json.dumps(_VS["doc"]))
def store_update(fn, fresh=False):
    """Apply fn(doc)->doc and persist. On GitHub, a 409/422 (sha moved) reloads and retries.

    fresh=True reloads before the first attempt so a favorites poll sees another process's write.
    """
    import base64
    with _VLOCK:
        if not GH_TOKEN:
            try:
                with open(VIEWS, encoding="utf-8") as f: doc = _norm(json.load(f))
            except OSError:
                if os.path.exists(VIEWS): raise
                doc = _norm({})
            except ValueError: doc = _norm({})
            doc = _norm(fn(doc)); tmp = VIEWS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f: json.dump(doc, f, indent=1)
            os.replace(tmp, VIEWS); return doc
        need = fresh
        for attempt in range(4):
            if _VS["doc"] is None or attempt or need: _VS["doc"], _VS["sha"] = _gh_load(); _VS["at"] = time.time(); need = False
            doc = _norm(fn(json.loads(json.dumps(_VS["doc"]))))
            if doc == _VS["doc"]: return doc
            n_fav = len((doc.get("favorites") or {}).get("items") or [])
            body = {"message": f"Chartmaker data update ({len(doc['views'])} views, {n_fav} favorites) [skip ci] [skip render]", "branch": VIEWS_BRANCH,
                    "committer": {"name": "Chartmaker Bot", "email": "bot@chartmaker"},
                    "content": base64.b64encode(json.dumps(doc, indent=1).encode()).decode()}
            if _VS["sha"]: body["sha"] = _VS["sha"]
            try: j = _gh("PUT", body)
            except urllib.error.HTTPError as e:
                if e.code in (409, 422) and attempt < 3: continue
                raise
            _VS["doc"], _VS["sha"], _VS["at"] = doc, j["content"]["sha"], time.time(); return doc
def _chk_views(v):
    if not isinstance(v, list) or not all(isinstance(x, dict) and x.get("name") for x in v): raise ValueError("views must be a list of {name,...}")
    return v
def views_read(): return store_read()["views"]
def views_write(v):
    v = _chk_views(v); return store_update(lambda d: {**d, "views": v})["views"]
def _vkey(x): return (x.get("mode") or "prices", x.get("name"))
def store_merge(inc):
    """Merge a browser's copy into the server: match on mode+name, newest 'updated' (or 'saved') wins, never deletes."""
    inc = _norm(inc); _chk_views(inc["views"])
    ts = lambda x: str(x.get("updated") or x.get("saved") or "")
    def fn(d):
        idx = {_vkey(x): i for i, x in enumerate(d["views"])}
        for x in inc["views"]:
            k = _vkey(x)
            if k not in idx: idx[k] = len(d["views"]); d["views"].append(x)
            elif ts(x) > ts(d["views"][idx[k]]): d["views"][idx[k]] = x
        w = inc["watchlist"]
        if w and (not d["watchlist"] or str(w.get("updated") or "") > str(d["watchlist"].get("updated") or "")): d["watchlist"] = w
        return d
    return store_update(fn)
INTRA = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}
NAMES = {}
def company_name(sym, m=None):
    """Company name from chart meta (shortName/longName), else Yahoo search; cached per symbol."""
    if sym in NAMES: return NAMES[sym]
    n = (m or {}).get("shortName") or (m or {}).get("longName")
    if not n:
        try:
            u = "https://query1.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({"q": sym, "quotesCount": 5, "newsCount": 0})
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as r:
                for q in json.load(r).get("quotes", []):
                    if q.get("symbol", "").upper() == sym.upper(): n = q.get("shortname") or q.get("longname"); break
        except Exception: pass
    if n: NAMES[sym] = n
    return n or sym
def yahoo(sym, p1, p2, interval, rng=None, prepost=False, fresh=False):
    """rng (e.g. '1d','5d') asks Yahoo for its own session window; intraday cache TTL 20s, daily 60s; fresh bypasses cache."""
    key = (sym, None if rng else p1, None if rng else p2, interval, rng, prepost)
    ttl = 20 if (interval in INTRA or rng) else 60
    if not fresh and key in CACHE and time.time() - CACHE[key][0] < ttl: return CACHE[key][1]
    prm = {"interval": interval, "includePrePost": "true" if prepost else "false"}
    if rng: prm["range"] = rng
    else: prm.update(period1=p1, period2=p2)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?{urllib.parse.urlencode(prm)}&_={int(time.time())}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as r: d = json.load(r)
    res = (d.get("chart") or {}).get("result")
    if not res: raise ValueError(f"No data for {sym}: {(d.get('chart') or {}).get('error')}")
    r0 = res[0]; ts = r0.get("timestamp") or []; m = r0["meta"]
    q0 = r0["indicators"]["quote"][0]
    closes = q0.get("close") or []; highs = q0.get("high") or [None] * len(ts)
    opens = q0.get("open") or []; lows = q0.get("low") or []; vols = q0.get("volume") or []
    # bars: [time_ms, open, high, low, close, volume]. Kept beside `points` so line charts stay close-only.
    # Yahoo often leaves the latest daily close blank after the bell; the quote price is that close.
    last_px = m.get("regularMarketPrice")
    bars = []
    for i, t in enumerate(ts):
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        if c is None and i == len(ts) - 1 and last_px is not None and o is not None and h is not None and lo is not None:
            c = last_px
            if c > h: h = c
            if c < lo: lo = c
        if o is None or h is None or lo is None or c is None: continue
        v = vols[i] if i < len(vols) and vols[i] is not None else 0
        bars.append([t * 1000, o, h, lo, c, v])
    pts = [[t * 1000, c] for t, c in zip(ts, closes) if c is not None]
    if not pts: raise ValueError(f"No price points for {sym} in range")
    stitched = []
    if not rng and interval in ("1d", "1wk", "1mo") and sym.upper() in heat.ALT:
        sp, stitched = heat.extend(sym.upper(), [(t // 1000, c) for t, c in pts], {"period1": p1, "period2": p2, "interval": interval, "includePrePost": "false"}, p1)
        if stitched: pts = [[t * 1000, c] for t, c in sp]
    out = {"symbol": m.get("symbol", sym), "name": company_name(sym, m),
           "currency": m.get("currency"), "points": pts, "bars": bars, "lastTime": m.get("regularMarketTime"),
           "lastBarTime": pts[-1][0] // 1000, "lastPrice": m.get("regularMarketPrice"), "interval": interval,
           "highs": [[t * 1000, h] for t, h in zip(ts, highs) if h is not None],
           "prevClose": m.get("regularMarketPreviousClose") or m.get("previousClose") or (m.get("chartPreviousClose") if rng == "1d" else None),
           "chartPreviousClose": m.get("chartPreviousClose"), "dayHigh": m.get("regularMarketDayHigh"),
           "fiftyTwoWeekHigh": m.get("fiftyTwoWeekHigh"), "instrumentType": m.get("instrumentType"),
           "fetchedAt": int(time.time()), "stitched": stitched}
    CACHE[key] = (time.time(), out); return out
# Earnings dates. Yahoo's chart API `events=earn` no longer returns a history (only dividends/splits).
# quoteSummary `earnings` (same crumb cookie flow as the heatmap) gives the last 4 reported quarters
# plus the next date, with no API key. Older dates are a custom-marker job in the UI.
_EARN = {}
_EOP = None
_ECR = [None, 0]
EARN_COV = "Yahoo quoteSummary: last 4 reported quarters plus the next date. Older earnings are not in this free feed."
def _sym_ok(sym):
    return bool(sym) and len(sym) <= 24 and all(c.isalnum() or c in ".-^=" for c in sym)
def _earn_opener():
    global _EOP
    if _EOP is None: _EOP = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    return _EOP
def _earn_crumb(op, force=False):
    if force or not _ECR[0] or time.time() - _ECR[1] > 1800:
        try: op.open(urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": "Mozilla/5.0"}), timeout=10)
        except Exception: pass
        _ECR[0] = op.open(urllib.request.Request("https://query1.finance.yahoo.com/v1/test/getcrumb", headers={"User-Agent": "Mozilla/5.0"}), timeout=10).read().decode().strip()
        _ECR[1] = time.time()
    return _ECR[0]
def _ymd(block):
    if not isinstance(block, dict): return None
    fmt = block.get("fmt")
    if isinstance(fmt, str) and len(fmt) >= 10 and fmt[4] == "-" and fmt[7] == "-": return fmt[:10]
    raw = block.get("raw")
    if isinstance(raw, (int, float)) and raw > 0: return time.strftime("%Y-%m-%d", time.gmtime(int(raw)))
    return None
def _qshort(s):
    s = str(s or "")
    if len(s) >= 2 and s[0].isdigit() and s[1] == "Q": return "Q" + s[0]
    if len(s) >= 2 and s[0] == "Q" and s[1].isdigit(): return "Q" + s[1]
    return ""
def yahoo_earnings(sym):
    """{symbol, events:[{date, quarter, note, kind, upcoming?}], coverage, error?}."""
    sym = (sym or "").strip().upper()
    base = {"symbol": sym, "events": [], "coverage": EARN_COV}
    if not _sym_ok(sym): return {**base, "error": "bad symbol"}
    hit = _EARN.get(sym)
    if hit and time.time() - hit[0] < hit[1]: return hit[2]
    try:
        op = _earn_opener(); res = None; desc = "no earnings data"
        for attempt in (1, 2):
            crumb = _earn_crumb(op, force=attempt == 2)
            url = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/" + urllib.parse.quote(sym) + "?" + urllib.parse.urlencode({"modules": "earnings", "crumb": crumb})
            with op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r: j = json.load(r)
            res = ((j.get("quoteSummary") or {}).get("result") or [None])[0]
            if res: break
            err = (j.get("quoteSummary") or {}).get("error") or {}
            desc = str(err.get("description") or desc)
            if not (attempt == 1 and ("crumb" in desc.lower() or err.get("code") == "Unauthorized")): break
        if not res:
            out = {**base, "error": desc}
            _EARN[sym] = (time.time(), 120, out); return out
        chart = ((res.get("earnings") or {}).get("earningsChart") or {})
        events, seen = [], set()
        for q in chart.get("quarterly") or []:
            if not isinstance(q, dict): continue
            d = _ymd(q.get("reportedDate") or {})
            if not d or d in seen: continue
            seen.add(d)
            act, est = (q.get("actual") or {}).get("raw"), (q.get("estimate") or {}).get("raw")
            bits = []
            if isinstance(act, (int, float)): bits.append(f"EPS {act:.2f}")
            if isinstance(est, (int, float)): bits.append(f"vs {est:.2f} est")
            sp = q.get("surprisePct")
            if sp not in (None, ""): bits.append(f"surprise {sp}%")
            cq = q.get("calendarQuarter") or ""
            if cq: bits.append(str(cq))
            events.append({"date": d, "quarter": _qshort(cq or q.get("date")), "note": " · ".join(bits), "kind": "earn"})
        for ed in chart.get("earningsDate") or []:
            d = _ymd(ed if isinstance(ed, dict) else {})
            if not d or d in seen: continue
            seen.add(d)
            qlabel = _qshort(chart.get("currentQuarterEstimateDate"))
            note = "Next earnings" + (" (Yahoo estimates the date)" if chart.get("isEarningsDateEstimate") else "")
            est = (chart.get("currentQuarterEstimate") or {}).get("fmt")
            if est: note += f" · est EPS {est}"
            events.append({"date": d, "quarter": qlabel or "Next", "note": note, "kind": "earn", "upcoming": True})
        events.sort(key=lambda e: e["date"])
        out = {**base, "events": events}
        _EARN[sym] = (time.time(), 6 * 3600, out); return out
    except urllib.error.HTTPError as e:
        msg = "no earnings for this symbol" if e.code == 404 else str(e)
        out = {**base, "error": msg}
        _EARN[sym] = (time.time(), 6 * 3600 if e.code == 404 else 120, out); return out
    except Exception as e:
        out = {**base, "error": str(e)}
        _EARN[sym] = (time.time(), 120, out); return out
_PAPER = None
def paper_mod():
    global _PAPER
    if _PAPER is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "paper_trading.py")
        spec = importlib.util.spec_from_file_location("chartmaker_paper", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PAPER = mod
    return _PAPER
def briefing_doc():
    scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import briefing
    return briefing.build(os.path.dirname(os.path.abspath(__file__)))

def paper_view(fresh=False):
    """Reconcile the saved book with Yahoo daily bars. Does not write the file.

    Prices come from yahoo(), the same helper /api/chart uses.
    """
    import datetime
    from zoneinfo import ZoneInfo
    mod = paper_mod()
    root = os.path.dirname(os.path.abspath(__file__))
    doc = mod.load_json(os.path.join(root, "static", "price_targets.json"))
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    book = mod.load_book(os.path.join(root, "static", "paper_trading.json"))
    cfg = (book or {}).get("config") or mod.CONFIG
    symbols, seen = [], set()
    for row in rows or []:
        ticker = str((row or {}).get("ticker") or "").strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker); symbols.append(ticker)
    bench = cfg.get("benchmark") or mod.CONFIG["benchmark"]
    if bench not in seen:
        symbols.append(bench)
    tz = ZoneInfo(cfg.get("timezone") or "America/New_York")
    inception = cfg.get("historyStart") or cfg.get("inception") or mod.CONFIG.get("historyStart") or "2026-11-01"
    p1 = int(datetime.datetime.fromisoformat(inception).replace(tzinfo=tz).timestamp()) - 10 * 86400
    p2 = int(time.time()) + 86400
    prices, last = {}, {}
    for sym in symbols:
        try:
            quote = yahoo(sym, p1, p2, "1d", fresh=fresh)
        except Exception:
            prices[sym] = []
            continue
        prices[sym] = mod.sessions_from_ms_bars(quote.get("bars"), cfg.get("timezone"))
        px = quote.get("lastPrice")
        if px is None and quote.get("points"):
            px = quote["points"][-1][1]
        if px is not None:
            last[sym] = px
    updated = mod.update_book(book, rows, prices, mod.now_et())
    return {"book": mod.export_book(updated), "prices": last}

class H(SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def log_message(s, *a): pass
    def send_json(s, obj, code=200):
        b = json.dumps(obj).encode(); s.send_response(code)
        s.send_header("Content-Type", "application/json"); s.send_header("Content-Length", len(b)); s.end_headers(); s.wfile.write(b)
    def client_ip(s): return (s.headers.get("X-Forwarded-For") or s.client_address[0]).split(",")[0].strip()
    def authed(s):
        if not PASSWORD: return True
        for part in (s.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and token_ok(v): return True
        return False
    def send_login(s, err="", code=200):
        b = LOGIN_HTML.replace("%ERR%", f'<div class=e>{html.escape(err)}</div>' if err else "").encode()
        s.send_response(code); s.send_header("Content-Type", "text/html; charset=utf-8"); s.send_header("Cache-Control", "no-store")
        s.send_header("Content-Length", len(b)); s.end_headers(); s.wfile.write(b)
    def redirect(s, loc, cookie=None):
        s.send_response(303); s.send_header("Location", loc)
        if cookie is not None: s.send_header("Set-Cookie", cookie)
        s.send_header("Content-Length", "0"); s.end_headers()
    def gate(s, path):
        """Return True if the request may proceed; otherwise respond (401 JSON for /api, login page otherwise)."""
        if s.authed(): return True
        if path.startswith("/api/"): s.send_json({"error": "login required"}, 401)
        else: s.send_login(code=401)
        return False
    def same_origin(s):
        """Block cross-site writes. A missing Origin is allowed so non-browser clients and same-origin navigation still work. SameSite=Lax on the session cookie is the other half."""
        site = (s.headers.get("Sec-Fetch-Site") or "").lower()
        if site in ("cross-site", "same-site"): return False
        origin = s.headers.get("Origin")
        if not origin: return True
        host = (s.headers.get("Host") or "").split(",")[0].strip()
        return urllib.parse.urlparse(origin).netloc == host
    def favorites_get(s):
        if favorites_mode() == "off":
            return s.send_json({"error": "favorites stay in the browser on this server", "sync": "off", "shared": False, "scope": FAV_SCOPE["off"]}, 410)
        try: fav = favorites_read()
        except Exception: return s.send_json({"error": "storage unavailable"}, 502)
        return s.send_json(favorites_public(fav))
    def favorites_post(s):
        if favorites_mode() == "off":
            return s.send_json({"error": "favorites stay in the browser on this server", "sync": "off", "shared": False}, 410)
        if not s.same_origin(): return s.send_json({"error": "cross-site favorite changes are refused"}, 403)
        ctype = (s.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json": return s.send_json({"error": "content type must be application/json"}, 415)
        try:
            n = int(s.headers.get("Content-Length", 0) or 0)
            if n > 200_000: raise ValueError("too large")
            body = json.loads(s.rfile.read(n) or b"null")
            out = favorites_write(body)
        except urllib.error.URLError: return s.send_json({"error": "storage unavailable"}, 502)
        except OSError: return s.send_json({"error": "storage unavailable"}, 502)
        except Exception as e: return s.send_json({"error": str(e)[:300]}, 400)
        return s.send_json(out)
    def do_login(s):
        ip = s.client_ip()
        if throttled(ip): return s.send_login("Too many attempts. Try again in a few minutes.", 429)
        n = min(int(s.headers.get("Content-Length", 0) or 0), 4096)
        pw = (urllib.parse.parse_qs(s.rfile.read(n).decode("utf-8", "replace")).get("password") or [""])[0]
        if hmac.compare_digest(hashlib.sha256(pw.encode()).digest(), hashlib.sha256(PASSWORD.encode()).digest()):
            with FLOCK: FAILS.pop(ip, None)
            return s.redirect("/", f"{COOKIE}={make_token()}; Max-Age={MAX_AGE}; Path=/; HttpOnly; Secure; SameSite=Lax")
        record_fail(ip); s.send_login("Wrong password.", 401)
    def do_POST(s):
        path = urllib.parse.urlparse(s.path).path
        if PASSWORD and path == "/login": return s.do_login()
        if not s.gate(path): return
        if path == "/api/favorites": return s.favorites_post()
        if VIEWS_STORE == "browser" and path == "/api/views": return s.send_json({"error": "views are stored in the browser on this server"}, 410)
        if VIEWS_STORE == "browser" and path.startswith("/api/views"): return s.send_json({"error": "views are stored in the browser on this server"}, 410)
        if path not in ("/api/views", "/api/views/merge", "/api/views/watchlist"): return s.send_json({"error": "not found"}, 404)
        try:
            n = int(s.headers.get("Content-Length", 0))
            if n > 5_000_000: raise ValueError("too large")
            body = json.loads(s.rfile.read(n) or b"null")
            if path == "/api/views": s.send_json(views_write(body))
            elif path == "/api/views/merge": s.send_json(store_merge(body))
            else:
                w = _norm({"watchlist": body})["watchlist"]
                if not w: raise ValueError("watchlist must be {list,updated}")
                s.send_json(store_update(lambda d: {**d, "watchlist": w}))
        except urllib.error.URLError as e: s.send_json({"error": "storage unavailable: " + str(getattr(e, "code", "") or e.reason)}, 502)
        except Exception as e: s.send_json({"error": str(e)}, 400)
    def do_HEAD(s):
        if not s.gate(urllib.parse.urlparse(s.path).path): return
        super().do_HEAD()
    def do_GET(s):
        u = urllib.parse.urlparse(s.path)
        if PASSWORD and u.path == "/login": return s.redirect("/") if s.authed() else s.send_login()
        if PASSWORD and u.path == "/logout": return s.redirect("/", f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax")
        if not s.gate(u.path): return
        if u.path == "/api/config": return s.send_json({"viewsStore": VIEWS_STORE, "auth": bool(PASSWORD), "favoritesSync": favorites_mode()})
        if u.path == "/api/favorites": return s.favorites_get()
        if u.path in ("/api/views", "/api/views/all"):
            if VIEWS_STORE == "browser": return s.send_json([] if u.path == "/api/views" else _norm({}))
            try: d = store_read(fresh=u.path == "/api/views/all")
            except Exception as e: return s.send_json({"error": "storage unavailable: " + str(e)}, 502)
            return s.send_json(d["views"] if u.path == "/api/views" else d)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/api/search": return s.send_json(ysearch(qs.get("q", [""])[0]))
        if u.path == "/api/earnings": return s.send_json(yahoo_earnings(qs.get("symbol", [""])[0]))
        if u.path == "/api/paper":
            try: return s.send_json(paper_view(qs.get("fresh", [""])[0] == "1"))
            except Exception as e: return s.send_json({"error": str(e)}, 502)
        if u.path == "/api/briefing":
            try: return s.send_json(briefing_doc())
            except Exception as e: return s.send_json({"error": str(e)}, 500)
        if u.path == "/api/logo":
            b = heat.logo(qs.get("symbol", [""])[0])
            if not b: return s.send_json({"error": "no logo"}, 404)
            s.send_response(200); s.send_header("Content-Type", "image/webp" if b[:4] == b"RIFF" else "image/jpeg" if b[:2] == b"\xff\xd8" else "image/png"); s.send_header("Cache-Control", "public, max-age=604800")
            s.send_header("Content-Length", len(b)); s.end_headers(); s.wfile.write(b); return
        if u.path in ("/api/sentiment", "/api/sentiment_history"):
            fn = "sentiment.json" if u.path == "/api/sentiment" else "sentiment_history.json"
            try:
                with open(os.path.join(os.path.dirname(ROOT), fn), "rb") as f: b = f.read()
            except OSError: return s.send_json({"error": fn + " not found"}, 404)
            s.send_response(200); s.send_header("Content-Type", "application/json"); s.send_header("Cache-Control", "no-store")
            s.send_header("Content-Length", len(b)); s.end_headers(); s.wfile.write(b); return
        if u.path not in ("/api/chart", "/api/fin", "/api/heatmap", "/api/treasury", "/api/valuation", "/api/valhist", "/api/watch"): return super().do_GET()
        try:
          if u.path == "/api/watch":
            body, code = json.dumps(watch.compute(qs.get("tickers", [""])[0], qs.get("fresh", [""])[0] == "1")), 200
          elif u.path == "/api/valhist":
            g = lambda k, d="": qs.get(k, [d])[0]
            body, code = json.dumps(valhist.compute(g("tickers"), g("metric", "pe"), g("range", "5Y"), g("fresh") == "1")), 200
          elif u.path == "/api/valuation":
            g = lambda k, d="": qs.get(k, [d])[0]
            body, code = json.dumps(valuation.compute(g("tickers"), g("metric", "pe"), g("fresh") == "1")), 200
          elif u.path == "/api/treasury":
            body, code = json.dumps(treasury.compute(qs.get("fresh", [""])[0] == "1")), 200
          elif u.path == "/api/heatmap":
            g = lambda k, d="": qs.get(k, [d])[0]
            body, code = json.dumps(heat.heatmap(g("universe", "sp100"), g("tickers"), g("range", "1D"), g("fresh") == "1", g("stables") == "1")), 200
          elif u.path == "/api/fin":
            y = lambda k: int(qs[k][0]) if qs.get(k, [""])[0] else None
            body, code = json.dumps(fin.series(qs["symbol"][0].strip(), qs["metric"][0], qs.get("period", ["quarterly"])[0], y("y1"), y("y2"))), 200
          else:
            sym = qs["symbol"][0].strip().upper()
            p1 = int(qs.get("period1", [0])[0]); p2 = int(qs.get("period2", [int(time.time())])[0])
            iv = qs.get("interval", ["1d"])[0]
            g = lambda k: qs.get(k, [""])[0]
            body, code = json.dumps(yahoo(sym, p1, p2, iv, g("range") or None, g("prepost") == "1", g("fresh") == "1")), 200
        except Exception as e:
            body, code = json.dumps({"error": str(e)}), 502
        b = body.encode(); s.send_response(code)
        s.send_header("Content-Type", "application/json"); s.send_header("Cache-Control", "no-store"); s.send_header("Content-Length", len(b)); s.end_headers(); s.wfile.write(b)
if __name__ == "__main__":
    print(f"Chartmaker on http://localhost:{PORT} (password {'on' if PASSWORD else 'off'}, views: {VIEWS_STORE}{' via GitHub ' + VIEWS_REPO + '@' + VIEWS_BRANCH if GH_TOKEN and VIEWS_STORE == 'server' else ''})", flush=True); ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
