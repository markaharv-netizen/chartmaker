"""Chartmaker backend: stdlib-only proxy for Yahoo Finance chart API + static frontend."""
import fin, json, os, sys, time, hmac, hashlib, secrets, threading, html, urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORT = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8791))
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
def views_read():
    try:
        with open(VIEWS, encoding="utf-8") as f: v = json.load(f)
        return v if isinstance(v, list) else []
    except (OSError, ValueError): return []
def views_write(v):
    if not isinstance(v, list) or not all(isinstance(x, dict) and x.get("name") for x in v): raise ValueError("views must be a list of {name,...}")
    tmp = VIEWS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(v, f, indent=1)
    os.replace(tmp, VIEWS); return v
INTRA = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}
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
    r0 = res[0]; ts = r0.get("timestamp") or []
    q0 = r0["indicators"]["quote"][0]
    closes = q0.get("close") or []; highs = q0.get("high") or [None] * len(ts)
    pts = [[t * 1000, c] for t, c in zip(ts, closes) if c is not None]
    if not pts: raise ValueError(f"No price points for {sym} in range")
    m = r0["meta"]
    out = {"symbol": m.get("symbol", sym), "name": m.get("shortName") or m.get("longName") or sym,
           "currency": m.get("currency"), "points": pts, "lastTime": m.get("regularMarketTime"),
           "lastBarTime": pts[-1][0] // 1000, "lastPrice": m.get("regularMarketPrice"), "interval": interval,
           "highs": [[t * 1000, h] for t, h in zip(ts, highs) if h is not None],
           "prevClose": m.get("regularMarketPreviousClose") or m.get("previousClose") or (m.get("chartPreviousClose") if rng == "1d" else None),
           "chartPreviousClose": m.get("chartPreviousClose"), "dayHigh": m.get("regularMarketDayHigh"),
           "fiftyTwoWeekHigh": m.get("fiftyTwoWeekHigh"), "instrumentType": m.get("instrumentType"),
           "fetchedAt": int(time.time())}
    CACHE[key] = (time.time(), out); return out
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
        if VIEWS_STORE == "browser" and path == "/api/views": return s.send_json({"error": "views are stored in the browser on this server"}, 410)
        if path != "/api/views": return s.send_json({"error": "not found"}, 404)
        try:
            n = int(s.headers.get("Content-Length", 0))
            if n > 5_000_000: raise ValueError("too large")
            s.send_json(views_write(json.loads(s.rfile.read(n) or b"[]")))
        except Exception as e: s.send_json({"error": str(e)}, 400)
    def do_HEAD(s):
        if not s.gate(urllib.parse.urlparse(s.path).path): return
        super().do_HEAD()
    def do_GET(s):
        u = urllib.parse.urlparse(s.path)
        if PASSWORD and u.path == "/login": return s.redirect("/") if s.authed() else s.send_login()
        if PASSWORD and u.path == "/logout": return s.redirect("/", f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax")
        if not s.gate(u.path): return
        if u.path == "/api/config": return s.send_json({"viewsStore": VIEWS_STORE, "auth": bool(PASSWORD)})
        if u.path == "/api/views": return s.send_json([] if VIEWS_STORE == "browser" else views_read())
        if u.path not in ("/api/chart", "/api/fin"): return super().do_GET()
        qs = urllib.parse.parse_qs(u.query)
        try:
          if u.path == "/api/fin":
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
    print(f"Chartmaker on http://localhost:{PORT} (password {'on' if PASSWORD else 'off'}, views: {VIEWS_STORE})", flush=True); ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
