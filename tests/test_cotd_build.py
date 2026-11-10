"""Charts of the day draws 20 catalog charts at random, and retired gids stay unpublished.

The builder self-test writes a temporary edition. This module also opens the
Charts of the day tab and checks that every card in the file is shown, without
the old note sections.
"""
import base64
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cotd_build
SCRIPT = os.path.join(ROOT, "scripts", "cotd_build.py")
INDEX = os.path.join(ROOT, "static", "index.html")
RETIRED = (
    "1547332232",  # 161 Nvidia P-S and P-E
    "643299192",   # 202 Nvidia revenue by geography
    "333185169",   # 87 TSLA beta to Nasdaq
    "1024131985",  # 99 MSTR BTC per share (Strategy)
    "1791345316",  # 158 Korea & Taiwan vs S&P
    "1370617918",  # 163 Supercore vs core CPI
    "292208014",   # 98 MSTR vs BTC drawdowns
    "2119398138",  # 151 Big Tech capex vs Nvidia
    "1584345646",  # 223 Micron revenue & margin
    "1186940001",  # 226 OpenAI vs Anthropic revenue
    "501919598",   # 263 Tariff pass-through
    "579853621",   # 12 Breakeven vs CPI
    "418894352",   # 81 CoreWeave margins (bot2)
)
RETIRED_TABS = (
    "161 Nvidia P-S and P-E",
    "202 Nvidia revenue by geography",
    "87 TSLA beta to Nasdaq",
    "99 MSTR BTC per share",
    "158 Korea & Taiwan vs S&P",
    "163 Supercore vs core CPI",
    "98 MSTR vs BTC drawdowns",
    "151 Big Tech capex vs Nvidia",
    "223 Micron revenue & margin",
    "226 OpenAI vs Anthropic revenue",
    "263 Tariff pass-through",
    "12 Breakeven vs CPI",
    "81 CoreWeave margins",
)


def _write_chart_png(path):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im = Image.new("RGB", (64, 36))
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            px[x, y] = ((x * 17 + y * 3) % 256, (x * 5 + y * 13) % 256, (x * y + 40) % 256)
    im.save(path)


def cotd_source():
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    start = html.find("async function cotdenter")
    end = html.find("// ---------- Sheets library", start)
    if start < 0 or end < 0:
        raise AssertionError("cotdenter() is missing from static/index.html")
    return html[start:end]


class CotdBuildTest(unittest.TestCase):
    def test_builder_accepts_twenty_and_rejects_excluded_gids(self):
        today = os.path.join(ROOT, "static", "cotd", "today.json")
        with open(today, "rb") as f:
            before = f.read()
        run = subprocess.run([sys.executable, SCRIPT, "--self-test"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
        self.assertIn("self-test ok", run.stdout)
        with open(today, "rb") as f:
            after = f.read()
        self.assertEqual(after, before)
        blob = after.decode("utf-8")
        for gid in RETIRED:
            self.assertNotIn(gid, blob)
        for tab in RETIRED_TABS:
            self.assertNotIn(tab, blob)

    def test_ui_source_renders_every_item(self):
        body = cotd_source()
        self.assertIn("(j.items||[]).map", body.replace(" ", ""))
        self.assertNotIn(".slice(", body)
        self.assertNotIn("nth-child", body)
        with open(INDEX, encoding="utf-8") as f:
            html = f.read()
        css = html.split("</style>", 1)[0]
        cotd_css = "\n".join(line for line in css.splitlines() if "#cotd" in line)
        self.assertNotIn("nth-child", cotd_css)
        self.assertNotIn("max-height", cotd_css)
        self.assertNotIn("Why it was picked", body)
        self.assertNotIn("Why it matters", body)
        self.assertNotIn("What you should know", body)
        self.assertNotIn("recommended", body.lower())
        self.assertIn("drawn at random", body)

    def test_random_draw_skips_exclusions_and_recent_charts_and_draws_twenty(self):
        bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
        bot2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
        date = "2099-04-01"
        refused = subprocess.run([sys.executable, SCRIPT, "picks.json"], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("python3 scripts/cotd_build.py", refused.stderr)
        with tempfile.TemporaryDirectory(prefix="cotd-draw-") as repo:
            tabs = []

            def add(book, gid, title, charts, png=True):
                if png:
                    for n, _title in charts:
                        suffix = "" if n <= 1 else "_%d" % n
                        _write_chart_png(os.path.join(repo, "static", "sheets", "full", book, "%d%s.png" % (gid, suffix)))
                tabs.append({
                    "book": book, "tab": title, "gid": gid,
                    "charts": [title for _n, title in charts],
                    "chartList": [{"n": n, "title": title} for n, title in charts],
                })

            for i in range(6, 25):
                add("bot1", 720000 + i, "sample %02d" % i, [(1, "Chart %02d" % i)])
            add("bot1", 720001, "sample 01", [(1, "Chart 01")])
            add("bot1", 720002, "sample 02", [(1, "Chart 02")])
            add("bot1", 720003, "sample 03", [(1, "Chart 03")])
            add("bot1", 720004, "sample 04", [(1, "Chart 04"), (2, "Chart 04 panel 2")])
            add("bot1", 720005, "sample 05", [(1, "Chart 05")])
            add("bot1", 720200, "two panels", [(1, "Panel A"), (2, "Panel B")])
            add("bot1", 720300, "missing png", [(1, "No file")], png=False)
            add("bot1", 1547332232, "161 Nvidia P-S and P-E", [(1, "retired nvidia")])
            add("bot1", 333185169, "87 TSLA beta to Nasdaq", [(1, "retired beta")])
            add("bot2", 418894352, "81 CoreWeave margins", [(1, "retired margins")])
            add("bot2", 645072508, "69 Mark MSTR red dot tracker", [(1, "red dot")])
            tabs.append({"book": "bot1", "tab": "a table", "gid": 720100, "charts": [], "chartList": []})
            os.makedirs(os.path.join(repo, "static", "sheets"), exist_ok=True)
            with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "books": [
                        {"id": "bot1", "name": "Bot experiments", "spreadsheetId": bot1},
                        {"id": "bot2", "name": "bot experiments 2", "spreadsheetId": bot2},
                    ],
                    "tabs": tabs,
                }, f)
            history = {
                "2099-03-31": [
                    {"book": "bot1", "gid": 720001, "chart_n": 1, "tab": "sample 01", "title": "Chart 01"},
                    {"book": "bot1", "gid": 720200, "chart_n": 2, "tab": "two panels", "title": "Panel B"},
                ],
                "2099-03-29": [
                    {"book": "bot1", "gid": 720003, "chart_n": 1, "tab": "sample 03", "title": "Chart 03"},
                    {"book": "bot1", "gid": 720004, "tab": "sample 04", "title": "Chart 04"},
                ],
                "2099-03-28": [
                    {"book": "bot1", "gid": 720005, "chart_n": 1, "tab": "sample 05", "title": "Chart 05"},
                ],
            }
            for day, rows in history.items():
                folder = os.path.join(repo, "static", "cotd", day)
                os.makedirs(folder)
                with open(os.path.join(folder, "cards.json"), "w", encoding="utf-8") as f:
                    json.dump({"date": day, "items": rows}, f)
            broken = os.path.join(repo, "static", "cotd", "2099-03-30")
            os.makedirs(broken)
            with open(os.path.join(broken, "cards.json"), "w", encoding="utf-8") as f:
                f.write("{")

            pool = cotd_build.eligible_charts(repo, date)
            ids = {(c["book"], c["gid"], c["chart_n"]) for c in pool}
            self.assertEqual(len(pool), 22, sorted(ids))
            for blocked in (
                ("bot1", 1547332232, 1),
                ("bot1", 333185169, 1),
                ("bot2", 418894352, 1),
                ("bot2", 645072508, 1),
                ("bot1", 720001, 1),
                ("bot1", 720003, 1),
                ("bot1", 720004, 1),
                ("bot1", 720004, 2),
                ("bot1", 720200, 2),
                ("bot1", 720100, 1),
                ("bot1", 720300, 1),
            ):
                self.assertNotIn(blocked, ids, blocked)
            self.assertIn(("bot1", 720002, 1), ids)
            self.assertIn(("bot1", 720005, 1), ids)
            self.assertIn(("bot1", 720200, 1), ids)
            panel = next(c for c in pool if c["gid"] == 720200)
            self.assertEqual(panel["chart_n"], 1)
            self.assertTrue(panel["png"].endswith("/720200.png"), panel["png"])
            self.assertEqual(cotd_build.EDITION_SIZE, 20)
            drawn = cotd_build.choose_charts(pool, cotd_build.EDITION_SIZE, cotd_build.edition_rng(date))
            self.assertEqual(len(drawn), 20)
            self.assertEqual(len({(c["book"], c["gid"], c["chart_n"]) for c in drawn}), 20)
            mirror = random.Random()
            mirror.seed(date)
            self.assertEqual(drawn, mirror.sample(list(pool), 20))

            run = subprocess.run(
                [sys.executable, SCRIPT, "--repo", repo, "--date", date, "--keep-old", "0"],
                capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
            with open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc["draw"], "random")
            self.assertEqual(doc["theme"], "20 charts drawn at random from the catalog.")
            self.assertEqual(len(doc["items"]), 20)
            self.assertEqual([row["title"] for row in doc["items"]], [c["title"] for c in drawn])
            published = set()
            for row in doc["items"]:
                self.assertNotIn("picked", row)
                self.assertNotIn("matters", row)
                self.assertNotIn("know", row)
                self.assertIn(row["sheet"], ("Bot experiments", "bot experiments 2"))
                self.assertTrue(row["tab"])
                self.assertGreaterEqual(row["chart_n"], 1)
                gid = int(row["sheet_url"].rsplit("gid=", 1)[1])
                published.add((gid, row["chart_n"]))
                self.assertNotIn(str(gid), RETIRED)
                self.assertNotIn(row["tab"], RETIRED_TABS)
            self.assertNotIn((720001, 1), published)
            self.assertNotIn((720003, 1), published)
            self.assertNotIn((720004, 1), published)
            self.assertNotIn((720004, 2), published)
            self.assertNotIn((720200, 2), published)
            self.assertNotIn((645072508, 1), published)
            with open(os.path.join(repo, "static", "cotd", date, "cards.json"), encoding="utf-8") as f:
                cards = json.load(f)
            self.assertEqual(cards["date"], date)
            self.assertEqual(len(cards["items"]), 20)
            self.assertTrue(all(it.get("book") and it.get("gid") and it.get("chart_n") for it in cards["items"]))
            names = sorted(os.listdir(os.path.join(repo, "static", "cotd", date)))
            self.assertEqual(names, ["%02d.png" % i for i in range(1, 21)] + ["cards.json"])

    def test_random_draw_publishes_every_remaining_chart_when_fewer_than_twenty(self):
        bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
        date = "2099-05-01"
        with tempfile.TemporaryDirectory(prefix="cotd-short-") as repo:
            tabs = []
            for i in range(1, 8):
                gid = 730000 + i
                _write_chart_png(os.path.join(repo, "static", "sheets", "full", "bot1", "%d.png" % gid))
                tabs.append({
                    "book": "bot1", "tab": "short %d" % i, "gid": gid,
                    "charts": ["Short %d" % i], "chartList": [{"n": 1, "title": "Short %d" % i}],
                })
            os.makedirs(os.path.join(repo, "static", "sheets"), exist_ok=True)
            with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "books": [{"id": "bot1", "name": "Bot experiments", "spreadsheetId": bot1}],
                    "tabs": tabs,
                }, f)
            run = subprocess.run(
                [sys.executable, SCRIPT, "--repo", repo, "--date", date, "--keep-old", "0"],
                capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
            with open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(len(doc["items"]), 7)
            self.assertEqual(doc["theme"], "7 charts drawn at random from the catalog.")
            self.assertEqual({row["title"] for row in doc["items"]}, {"Short %d" % i for i in range(1, 8)})
            self.assertTrue(all("picked" not in row and row["chart_n"] == 1 for row in doc["items"]))


class CotdDisplayTest(unittest.TestCase):
    def test_tab_shows_twenty_fixture_cards_and_the_saved_edition(self):
        chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not chrome:
            self.fail("google-chrome is required to check Charts of the day layout")
        with tempfile.TemporaryDirectory(prefix="cotd-ui-") as tmp:
            fixture = os.path.join(tmp, "fixture")
            os.makedirs(os.path.join(fixture, "cotd"))
            os.symlink(INDEX, os.path.join(fixture, "index.html"))
            os.symlink(os.path.join(ROOT, "static", "echarts.min.js"), os.path.join(fixture, "echarts.min.js"))
            notes = []
            items = []
            for i in range(1, 21):
                know = f"know note {i} stays intact — figure {i}.25 on 2026-11-01, sentence two, sentence three."
                notes.append(know)
                items.append({
                    "sheet": "Bot experiments",
                    "tab": f"sample tab {i}",
                    "title": f"Fixture chart {i:02d}",
                    "image": f"cotd/2099-01-15/{i:02d}.png",
                    "chart_n": 1,
                    "picked": f"picked note {i} " + ("quality " * 8),
                    "matters": f"matters note {i}",
                    "know": know,
                    "sheet_url": f"https://docs.google.com/spreadsheets/d/sheet/edit#gid={900000 + i}",
                })
            with open(os.path.join(fixture, "cotd", "today.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "date": "2099-01-15",
                    "draw": "random",
                    "theme": "20 charts drawn at random from the catalog.",
                    "items": items,
                }, f)
            roots = {"fixture": fixture, "real": os.path.join(ROOT, "static")}
            server = _serve(roots)
            port = server.server_address[1]
            host = f"http://127.0.0.1:{port}"
            try:
                with Chrome(chrome) as browser:
                    shown = browser.cards(f"{host}/fixture/index.html")
                    self.assertEqual(shown["n"], 20, shown)
                    self.assertEqual(shown["hidden"], 0, shown)
                    self.assertEqual(shown["display"], "block")
                    self.assertEqual(shown["titles"][0], "1. Fixture chart 01")
                    self.assertEqual(shown["titles"][9], "10. Fixture chart 10")
                    self.assertEqual(shown["titles"][10], "11. Fixture chart 11")
                    self.assertEqual(shown["titles"][19], "20. Fixture chart 20")
                    self.assertEqual(shown["imgs"], 20)
                    self.assertEqual(shown["stars"], 0)
                    self.assertIn("20 charts drawn at random from the catalog.", shown["text"])
                    self.assertIn("Bot experiments", shown["text"])
                    self.assertIn("sample tab 1", shown["text"])
                    self.assertIn("sample tab 20", shown["text"])
                    self.assertNotIn("Why it was picked", shown["text"])
                    self.assertNotIn("Why it matters", shown["text"])
                    self.assertNotIn("What you should know", shown["text"])
                    self.assertNotIn("picked note 20", shown["text"])
                    self.assertNotIn("matters note 1", shown["text"])
                    for know in notes:
                        self.assertNotIn(know, shown["text"])
                    narrow = browser.cards(f"{host}/fixture/index.html", width=390, height=800, mobile=True)
                    self.assertEqual(narrow["n"], 20, narrow)
                    self.assertEqual(narrow["hidden"], 0, narrow)
                    with open(os.path.join(ROOT, "static", "cotd", "today.json"), encoding="utf-8") as f:
                        saved = json.load(f)
                    live = browser.cards(f"{host}/real/index.html")
                    self.assertEqual(live["n"], len(saved["items"]), live["titles"])
                    self.assertEqual(live["hidden"], 0)
                    self.assertEqual(live["stars"], live["n"], live["titles"])
                    self.assertNotIn("Why it was picked", live["text"])
                    self.assertNotIn("Why it matters", live["text"])
                    self.assertNotIn("What you should know", live["text"])
                    self.assertNotIn(saved["theme"], live["text"])
                    self.assertNotIn(saved["items"][0]["picked"][:60], live["text"])
                    self.assertIn(saved["items"][0]["sheet"], live["text"])
                    self.assertIn(saved["items"][0]["tab"], live["text"])
                    for gid in RETIRED:
                        self.assertNotIn(gid, live["text"])
                    for tab in RETIRED_TABS:
                        self.assertNotIn(tab, live["text"])
                    self.assertEqual(live["titles"][0], "1. " + saved["items"][0]["title"])
                    self.assertEqual(live["titles"][-1], f"{len(saved['items'])}. " + saved["items"][-1]["title"])
            finally:
                server.shutdown()
                server.server_close()


def _serve(roots):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            parts = parsed.path.lstrip("/").split("/", 1)
            root = roots.get(parts[0]) if parts and parts[0] else None
            rel = parts[1] if root and len(parts) > 1 and parts[1] else "index.html"
            path = None
            if root:
                rel_path = os.path.normpath(urllib.parse.unquote(rel))
                if not rel_path.startswith("..") and not os.path.isabs(rel_path):
                    candidate = os.path.join(root, rel_path)
                    parent = os.path.realpath(os.path.dirname(candidate))
                    base = os.path.realpath(root)
                    if os.path.commonpath([base, parent]) == base and os.path.isfile(candidate):
                        path = candidate
            if not path:
                self.send_error(404)
                return
            ext = os.path.splitext(path)[1].lower()
            ctype = {".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".png": "image/png"}.get(ext, "application/octet-stream")
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class Chrome:
    def __init__(self, binary):
        self.binary = binary
        self.proc = None
        self.tmpdir = None

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cotd-chrome-")
        port = _free_port()
        self.proc = subprocess.Popen(
            [self.binary, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
             f"--remote-debugging-port={port}", f"--user-data-dir={self.tmpdir}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.cdp = _connect_browser(port, self.proc)
        return self

    def __exit__(self, *exc):
        sock = getattr(getattr(self, "cdp", None), "sock", None)
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def cards(self, url, width=1280, height=900, mobile=False):
        self.cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
        self.cdp.call("Page.navigate", {"url": url})
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            state = self.cdp.evaluate("document.readyState")
            has = self.cdp.evaluate("typeof setMode === 'function'")
            if state == "complete" and has:
                ready = True
                break
            time.sleep(0.1)
        if not ready:
            raise AssertionError("page did not load " + url)
        self.cdp.evaluate("document.getElementById('mCotd').click()")
        deadline = time.time() + 10
        last = None
        while time.time() < deadline:
            last = self.cdp.evaluate(_CARD_JS)
            if last and last.get("n"):
                return last
            time.sleep(0.1)
        raise AssertionError("no charts rendered: %r" % (last,))


_CARD_JS = r"""
(() => {
  const root = document.getElementById('cotd');
  const cards = [...root.querySelectorAll('.card')];
  return {
    n: cards.length,
    display: getComputedStyle(root).display,
    titles: cards.map(c => c.querySelector('h3').textContent),
    imgs: root.querySelectorAll('img').length,
    stars: root.querySelectorAll('button.fav').length,
    hidden: cards.filter(c => getComputedStyle(c).display === 'none' || c.offsetHeight === 0).length,
    text: root.innerText,
  };
})()
"""


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _connect_browser(port, proc):
    deadline = time.time() + 20
    last = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError("chrome exited before DevTools was ready")
        try:
            targets = json.load(urllib_request(f"http://127.0.0.1:{port}/json/list"))
        except OSError as e:
            last = e
            time.sleep(0.1)
            continue
        page = next((t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
        if page:
            cdp = Cdp(page["webSocketDebuggerUrl"])
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            return cdp
        time.sleep(0.1)
    raise AssertionError("chrome DevTools did not come up: %r" % (last,))


def urllib_request(url):
    import urllib.request
    return urllib.request.urlopen(url, timeout=2)


class Cdp:
    def __init__(self, wsurl):
        parsed = urllib.parse.urlparse(wsurl)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        req = (f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            buf += chunk
        head, _, extra = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode("utf-8", "replace"))
        self.buf = extra
        self.n = 0

    def call(self, method, params=None, timeout=20):
        self.n += 1
        mid = self.n
        self._send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._recv()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result")
        raise TimeoutError(method)

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        details = result.get("exceptionDetails")
        if details:
            raise RuntimeError(json.dumps(details)[:500])
        return result["result"].get("value")

    def _send(self, text):
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        header = bytearray([0x81])
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", n))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv(self):
        while True:
            frame = self._frame()
            if frame is None:
                continue
            return json.loads(frame)

    def _frame(self):
        def take(n):
            while len(self.buf) < n:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("websocket closed")
                self.buf += chunk
            out, self.buf = self.buf[:n], self.buf[n:]
            return out
        b0, b1 = take(2)
        opcode = b0 & 0x0F
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", take(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", take(8))[0]
        if b1 & 0x80:
            mask = take(4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(take(ln)))
        else:
            payload = take(ln)
        if opcode == 8:
            raise ConnectionError("websocket closed by chrome")
        if opcode == 9:
            self._pong(payload)
            return None
        if opcode == 10:
            return None
        return payload.decode()

    def _pong(self, payload):
        mask = os.urandom(4)
        header = bytearray([0x8A, 0x80 | len(payload)])
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)


if __name__ == "__main__":
    unittest.main()
