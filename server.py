"""Chartmaker backend: stdlib-only proxy for Yahoo Finance chart API + static frontend."""
import fin, json, os, sys, time, urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORT = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8791))
CACHE = {}
VIEWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views.json")
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
def yahoo(sym, p1, p2, interval):
    key = (sym, p1, p2, interval)
    if key in CACHE and time.time() - CACHE[key][0] < 60: return CACHE[key][1]
    q = urllib.parse.urlencode({"period1": p1, "period2": p2, "interval": interval, "includePrePost": "false"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r: d = json.load(r)
    res = (d.get("chart") or {}).get("result")
    if not res: raise ValueError(f"No data for {sym}: {(d.get('chart') or {}).get('error')}")
    r0 = res[0]; ts = r0.get("timestamp") or []
    q0 = r0["indicators"]["quote"][0]
    adj = (r0["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    closes = q0.get("close") or []
    pts = [[t * 1000, c] for t, c in zip(ts, closes) if c is not None]
    if not pts: raise ValueError(f"No price points for {sym} in range")
    m = r0["meta"]
    out = {"symbol": m.get("symbol", sym), "name": m.get("shortName") or m.get("longName") or sym,
           "currency": m.get("currency"), "points": pts, "lastTime": m.get("regularMarketTime"),
           "lastPrice": m.get("regularMarketPrice"), "interval": interval}
    # Use live regular-market price as final point for daily bars if newer
    CACHE[key] = (time.time(), out); return out
class H(SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def log_message(s, *a): pass
    def send_json(s, obj, code=200):
        b = json.dumps(obj).encode(); s.send_response(code)
        s.send_header("Content-Type", "application/json"); s.send_header("Content-Length", len(b)); s.end_headers(); s.wfile.write(b)
    def do_POST(s):
        if urllib.parse.urlparse(s.path).path != "/api/views": return s.send_json({"error": "not found"}, 404)
        try:
            n = int(s.headers.get("Content-Length", 0))
            if n > 5_000_000: raise ValueError("too large")
            s.send_json(views_write(json.loads(s.rfile.read(n) or b"[]")))
        except Exception as e: s.send_json({"error": str(e)}, 400)
    def do_GET(s):
        u = urllib.parse.urlparse(s.path)
        if u.path == "/api/views": return s.send_json(views_read())
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
            body, code = json.dumps(yahoo(sym, p1, p2, iv)), 200
        except Exception as e:
            body, code = json.dumps({"error": str(e)}), 502
        b = body.encode(); s.send_response(code)
        s.send_header("Content-Type", "application/json"); s.send_header("Content-Length", len(b)); s.end_headers(); s.wfile.write(b)
if __name__ == "__main__":
    print(f"Chartmaker on http://localhost:{PORT}"); ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
