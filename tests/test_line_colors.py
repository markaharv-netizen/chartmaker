"""Line colors and the Charts tab name.

The palette, the custom hex, saved orange/violet values, and ?tab=prices stay
compatible. Download PNG uses the color the chart actually draws.
"""
import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_cotd_build import Chrome

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")
STATIC = os.path.join(ROOT, "static")
OLD_PALETTE = ["#F7931A", "#8B5CF6", "#0EA5E9", "#22C55E", "#EF4444", "#111827", "#EC4899", "#14B8A6"]
NEW_HUES = ["#1D4ED8", "#4D7C0F", "#A21CAF", "#78350F"]


def _html():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def _array(name, html):
    token = "const %s=" % name
    start = html.find(token)
    if start < 0:
        raise AssertionError(name + " is missing")
    i = html.find("[", start)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1].replace("'", '"'))
    raise AssertionError(name + " did not close")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return


def _serve(directory):
    handler = functools.partial(_Quiet, directory=directory)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _ev(cdp, expression, await_promise=False, timeout=30):
    result = cdp.call("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": await_promise,
    }, timeout=timeout)
    details = result.get("exceptionDetails")
    if details:
        raise AssertionError(json.dumps(details)[:800])
    return result["result"].get("value")


def _open(cdp, url):
    cdp.call("Page.navigate", {"url": url})
    deadline = time.time() + 20
    while time.time() < deadline:
        if _ev(cdp, "document.readyState") == "complete" and _ev(cdp, "typeof setLineColor==='function'"):
            return
        time.sleep(0.05)
    raise AssertionError("page did not load " + url)


class LineColorSourceTest(unittest.TestCase):
    def test_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is required to parse the page script")
        html = _html()
        start = html.find("<script>\nconst $=")
        end = html.find("</script>", start)
        self.assertGreater(start, 0)
        script = html[start + len("<script>\n"):end]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            run = subprocess.run([node, "--check", path], capture_output=True, text=True)
        finally:
            os.unlink(path)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_palette_keeps_the_original_eight_then_adds_hues(self):
        html = _html()
        palette = _array("PALETTE", html)
        names = _array("LINE_COLORS", html)
        self.assertEqual(palette[:8], OLD_PALETTE)
        self.assertEqual(palette[8:], NEW_HUES)
        self.assertEqual(names[0], ["#F7931A", "Orange"])
        self.assertIn(["#8B5CF6", "Violet"], names)
        self.assertEqual({row[0] for row in names}, set(palette))
        bright = html[html.find("const BRIGHT="):html.find(";", html.find("const BRIGHT="))]
        for hex_color in palette:
            self.assertIn(hex_color, bright)

    def test_charts_tab_keeps_the_prices_link(self):
        html = _html()
        self.assertIn('id="mPrices" title="Price lines, percent change, candles, and ratios">Charts</button>', html)
        self.assertNotIn(">Prices</button>", html)
        self.assertIn("prices:'prices'", html)
        self.assertIn("charts:'prices'", html)
        self.assertIn("q.has('tickers')?'prices'", html)
        self.assertNotIn("colorSwatches", html)
        self.assertNotIn('class="swatches"', html)
        self.assertIn('id="colorCustom" hidden', html)
        self.assertIn('id="fcolorCustom" hidden', html)
        self.assertIn('id="vcolorCustom" hidden', html)
        for piece in ("id=\"colorPick\"", "id=\"colorHex\"", "id=\"fcolorPick\"", "id=\"vcolorHex\"", "Custom…", "setAttribute('role','listbox')"):
            self.assertIn(piece, html)


class LineColorBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not chrome:
            raise unittest.SkipTest("google-chrome is required")
        cls.httpd = _serve(STATIC)
        cls.url = "http://127.0.0.1:%d/" % cls.httpd.server_address[1]
        cls.browser = Chrome(chrome)
        cls.browser.__enter__()
        cls.cdp = cls.browser.cdp

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser:
            browser.__exit__(None, None, None)
        httpd = getattr(cls, "httpd", None)
        if httpd:
            httpd.shutdown()

    def ev(self, expression, await_promise=False):
        return _ev(self.cdp, expression, await_promise=await_promise)

    def open(self, query, width=1280, height=900, mobile=False):
        self.cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
        _open(self.cdp, self.url + query)

    def test_defaults_saved_colors_custom_hex_and_png(self):
        self.open("?tab=charts")
        ui = self.ev(r"""
(() => {
  const names = [...document.getElementById('color').options].map(o => [o.value, o.text]);
  const menu = document.getElementById('colorMenu');
  const opts = [...menu.querySelectorAll('[role=option]')].map(o => [o.dataset.c, o.textContent.trim(), !!o.querySelector('.chip')]);
  return {
    tab: document.getElementById('mPrices').textContent,
    on: document.getElementById('mPrices').classList.contains('on'),
    ctl: getComputedStyle(document.getElementById('ctl')).display,
    fin: getComputedStyle(document.getElementById('fctl')).display,
    value: lineColor('color'),
    fvalue: lineColor('fcolor'),
    vvalue: lineColor('vcolor'),
    names, opts,
    menuHidden: menu.hidden,
    customHidden: document.getElementById('colorCustom').hidden,
    fCustomHidden: document.getElementById('fcolorCustom').hidden,
    vCustomHidden: document.getElementById('vcolorCustom').hidden,
    swatches: document.querySelectorAll('.swatch').length,
    label: document.getElementById('colorLbl').textContent,
    multi: colors(9).slice(0, 9)
  };
})()
""")
        self.assertEqual(ui["tab"], "Charts")
        self.assertTrue(ui["on"])
        self.assertEqual(ui["ctl"], "flex")
        self.assertEqual(ui["fin"], "none")
        self.assertEqual(ui["value"], "#F7931A")
        self.assertEqual(ui["fvalue"], "#F7931A")
        self.assertEqual(ui["vvalue"], "#F7931A")
        self.assertEqual(ui["names"][0], ["#F7931A", "Orange"])
        self.assertIn(["#8B5CF6", "Violet"], ui["names"])
        self.assertEqual(ui["names"][-1][0], "__custom")
        self.assertTrue(ui["menuHidden"])
        self.assertTrue(ui["customHidden"])
        self.assertTrue(ui["fCustomHidden"])
        self.assertTrue(ui["vCustomHidden"])
        self.assertEqual(ui["swatches"], 0)
        self.assertEqual(ui["label"], "Orange")
        self.assertEqual(ui["opts"][0][0], "#F7931A")
        self.assertTrue(all(chip for _, _, chip in ui["opts"]))
        self.assertEqual(ui["opts"][-1][0], "__custom")
        self.assertEqual(ui["opts"][-1][1], "Custom…")
        self.assertEqual(ui["multi"][:8], OLD_PALETTE)
        self.assertEqual(ui["multi"][8], "#1D4ED8")

        picked = self.ev(r"""
(() => {
  document.getElementById('colorBtn').click();
  const open = !document.getElementById('colorMenu').hidden;
  document.querySelector('#colorMenu [data-c="#1D4ED8"]').click();
  $('theme').value = 'white'; applyTheme();
  const light = bc('#1D4ED8');
  $('theme').value = 'black'; applyTheme();
  const dark = bc('#1D4ED8');
  const ink = bc('#111827');
  const orange = bc('#F7931A');
  $('theme').value = 'white'; applyTheme();
  return {open, value: lineColor('color'), text: $('color').selectedOptions[0].text, label: $('colorLbl').textContent,
    menuHidden: $('colorMenu').hidden, customHidden: $('colorCustom').hidden, light, dark, ink, orange,
    pairOrange: pairColor('#F7931A'), pairViolet: pairColor('#8B5CF6'), pairBlue: pairColor('#1D4ED8')};
})()
""")
        self.assertTrue(picked["open"])
        self.assertEqual(picked["value"], "#1D4ED8")
        self.assertEqual(picked["text"], "Blue")
        self.assertEqual(picked["label"], "Blue")
        self.assertTrue(picked["menuHidden"])
        self.assertTrue(picked["customHidden"])
        self.assertEqual(picked["light"], "#1D4ED8")
        self.assertEqual(picked["dark"], "#6183E4")
        self.assertEqual(picked["ink"], "#E5E7EB")
        self.assertEqual(picked["orange"], "#FFA033")
        self.assertEqual(picked["pairOrange"], "#8B5CF6")
        self.assertEqual(picked["pairViolet"], "#F7931A")
        self.assertEqual(picked["pairBlue"], "#4D7C0F")

        saved = self.ev(r"""
(() => {
  setLineColor('color', '#8B5CF6');
  const old = snap('violet');
  setLineColor('color', '#123456');
  const custom = snap('custom');
  setLineColor('fcolor', '#4D7C0F');
  setLineColor('vcolor', 'abc');
  vapply({name:'violet', mode:'prices', fields:{color:'#8B5CF6'}});
  const restored = lineColor('color');
  const violetHidden = $('colorCustom').hidden;
  vapply({name:'custom', mode:'prices', fields:{color:'#123456'}});
  return {old: old.fields.color, custom: custom.fields.color, restored, violetHidden,
    again: lineColor('color'), select: $('color').value, customShown: !$('colorCustom').hidden,
    f: lineColor('fcolor'), v: lineColor('vcolor'),
    bad: setLineColor('color', 'nope')};
})()
""")
        self.assertEqual(saved["old"], "#8B5CF6")
        self.assertEqual(saved["custom"], "#123456")
        self.assertEqual(saved["restored"], "#8B5CF6")
        self.assertTrue(saved["violetHidden"])
        self.assertEqual(saved["again"], "#123456")
        self.assertEqual(saved["select"], "__custom")
        self.assertTrue(saved["customShown"])
        self.assertEqual(saved["f"], "#4D7C0F")
        self.assertEqual(saved["v"], "#AABBCC")
        self.assertFalse(saved["bad"])
        self.assertEqual(self.ev("lineColor('color')"), "#123456")

        themed = self.ev(r"""
(() => {
  $('theme').value = 'white'; applyTheme();
  const sunk = bc('#FFFF00');
  $('theme').value = 'black'; applyTheme();
  const lifted = bc('#112233');
  const named = bc('#F7931A');
  $('theme').value = 'white'; applyTheme();
  return {sunk, sunkContrast: contrast(sunk, '#FFFFFF'), lifted, liftedContrast: contrast(lifted, '#000000'),
    namedStays: bc('#F7931A'), darkNamed: named};
})()
""")
        self.assertNotEqual(themed["sunk"], "#FFFF00")
        self.assertGreaterEqual(themed["sunkContrast"], 2.4)
        self.assertNotEqual(themed["lifted"], "#112233")
        self.assertGreaterEqual(themed["liftedContrast"], 3)
        self.assertEqual(themed["namedStays"], "#F7931A")
        self.assertEqual(themed["darkNamed"], "#FFA033")

        png = self.ev(r"""
(async () => {
  async function pixels(hex){
        const [r, gg, b] = hexRgb(hex);
    const url = chart.getDataURL({type:'png', pixelRatio:1, backgroundColor:T.bg});
    const im = new Image();
    await new Promise((res, rej) => { im.onload = res; im.onerror = rej; im.src = url; });
    const c = document.createElement('canvas'); c.width = im.width; c.height = im.height;
    const g = c.getContext('2d'); g.drawImage(im, 0, 0);
    const data = g.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < data.length; i += 4){
      if (Math.abs(data[i]-r) < 18 && Math.abs(data[i+1]-gg) < 18 && Math.abs(data[i+2]-b) < 18) n++;
    }
    return n;
  }
  function paint(hex){
    setLineColor('color', hex);
    const o = base('Color', '');
    const t0 = Date.UTC(2024, 0, 1);
    const data = [];
    for (let i = 0; i < 90; i++) data.push([t0 + i*86400000, 10 + Math.sin(i/6)*5]);
    lineOpt(o, [{label:'ACME', color: lineColor('color'), data}], v => String(v));
    chart.setOption(o, true);
    const raw = (chart.getOption().series[0].lineStyle || {}).color || '';
    return raw;
  }
  $('theme').value = 'white'; applyTheme();
  const blueRaw = paint('#1D4ED8');
  const blueN = await pixels('#1D4ED8');
  $('theme').value = 'black'; applyTheme();
  const oliveRaw = paint('#4D7C0F');
  const oliveN = await pixels(bc('#4D7C0F'));
  const customRaw = paint('#112233');
  const lifted = bc('#112233');
  const customN = await pixels(lifted);
  const customDarkN = await pixels('#112233');
  return {blueRaw, blueN, oliveRaw, oliveN, customRaw, lifted, customN, customDarkN};
})()
""", await_promise=True)
        self.assertEqual(png["blueRaw"].upper(), "#1D4ED8")
        self.assertGreater(png["blueN"], 50, png)
        self.assertEqual(png["oliveRaw"].upper(), "#BEF264")
        self.assertGreater(png["oliveN"], 50, png)
        self.assertNotEqual(png["lifted"].upper(), "#112233")
        self.assertEqual(png["customRaw"].upper(), png["lifted"].upper())
        self.assertGreater(png["customN"], 50, png)
        self.assertLess(png["customDarkN"], png["customN"])

    def test_mobile_picker_fits_and_old_links_open_charts(self):
        # mobile:true inflates the layout viewport in this Chrome, so the phone
        # check is a 390px CSS viewport, which is what the media query uses.
        self.open("?tab=charts", width=390, height=800, mobile=False)
        box = self.ev(r"""
(() => {
  const btn = document.getElementById('colorBtn');
  const closed = btn.getBoundingClientRect();
  btn.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowDown', bubbles:true}));
  const menu = document.getElementById('colorMenu');
  const opened = !menu.hidden;
  menu.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowDown', bubbles:true}));
  menu.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
  const keyed = lineColor('color');
  btn.click();
  const mr = menu.getBoundingClientRect();
  const opt = menu.querySelector('[role=option]').getBoundingClientRect();
  menu.dispatchEvent(new KeyboardEvent('keydown', {key:'End', bubbles:true}));
  menu.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
  return {left: closed.left, right: closed.right, inner: window.innerWidth,
    menuLeft: mr.left, menuRight: mr.right, opened, optH: opt.height, keyed,
    afterCustom: lineColor('color'), customShown: !document.getElementById('colorCustom').hidden,
    menuClosed: menu.hidden, hex: document.getElementById('colorHex').getBoundingClientRect().height,
    tab: document.getElementById('mPrices').textContent};
})()
""")
        self.assertEqual(box["tab"], "Charts")
        self.assertGreaterEqual(box["left"], -1)
        self.assertLessEqual(box["right"], box["inner"] + 1, box)
        self.assertTrue(box["opened"])
        self.assertEqual(box["keyed"], "#78350F")
        self.assertGreaterEqual(box["menuLeft"], -1)
        self.assertLessEqual(box["menuRight"], box["inner"] + 1, box)
        self.assertGreaterEqual(box["optH"], 40)
        self.assertEqual(box["afterCustom"], "#78350F")
        self.assertTrue(box["customShown"])
        self.assertTrue(box["menuClosed"])
        self.assertGreaterEqual(box["hex"], 36)

        self.open("?tab=prices")
        self.assertEqual(self.ev("document.getElementById('mPrices').textContent"), "Charts")
        self.assertTrue(self.ev("document.getElementById('mPrices').classList.contains('on')"))
        self.assertEqual(self.ev("getComputedStyle(document.getElementById('ctl')).display"), "flex")

        self.open("?tab=charts&color=8B5CF6&fcolor=%234D7C0F")
        self.assertEqual(self.ev("lineColor('color')"), "#8B5CF6")
        self.assertEqual(self.ev("lineColor('fcolor')"), "#4D7C0F")
        self.assertTrue(self.ev("document.getElementById('mPrices').classList.contains('on')"))

        self.open("?tickers=ZZZTEST&color=nope")
        self.assertTrue(self.ev("document.getElementById('mPrices').classList.contains('on')"))
        self.assertEqual(self.ev("lineColor('color')"), "#F7931A")
        self.assertEqual(self.ev("document.getElementById('tickers').value"), "ZZZTEST")


if __name__ == "__main__":
    unittest.main()
