"""Charts of the day editions are 20 charts, and retired gids stay unpublished.

The builder self-test writes a temporary edition. This module also opens the
Charts of the day tab and checks that every card in the file is shown.
"""
import base64
import json
import os
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
                    "picked": f"picked note {i} " + ("quality " * 8),
                    "matters": f"matters note {i}",
                    "know": know,
                    "sheet_url": f"https://docs.google.com/spreadsheets/d/sheet/edit#gid={900000 + i}",
                })
            with open(os.path.join(fixture, "cotd", "today.json"), "w", encoding="utf-8") as f:
                json.dump({"date": "2099-01-15", "theme": "fixture of twenty", "items": items}, f)
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
                    for know in notes:
                        self.assertIn(know, shown["text"])
                    self.assertIn("picked note 20", shown["text"])
                    self.assertIn("matters note 1", shown["text"])
                    narrow = browser.cards(f"{host}/fixture/index.html", width=390, height=800, mobile=True)
                    self.assertEqual(narrow["n"], 20, narrow)
                    self.assertEqual(narrow["hidden"], 0, narrow)
                    with open(os.path.join(ROOT, "static", "cotd", "today.json"), encoding="utf-8") as f:
                        saved = json.load(f)
                    live = browser.cards(f"{host}/real/index.html")
                    self.assertEqual(live["n"], len(saved["items"]), live["titles"])
                    self.assertEqual(live["hidden"], 0)
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
