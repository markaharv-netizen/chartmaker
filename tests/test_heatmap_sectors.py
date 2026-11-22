"""Heatmap sector grouping.

static/sectors.json is the committed Yahoo assetProfile sector for every
ticker in shares.json. The heatmap reads that file (no browser request to
Yahoo). Flat stays the default. By sector nests tiles in market-cap blocks.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)
from test_cotd_build import Chrome

import heat
import refresh_sectors

INDEX = os.path.join(ROOT, "static", "index.html")
SHARES = os.path.join(ROOT, "shares.json")
SECTORS = os.path.join(ROOT, "static", "sectors.json")
SCRIPT = os.path.join(ROOT, "scripts", "refresh_sectors.py")
# Snapshot of the committed Yahoo lookup. HOGPF (H100 Group) has no sector.
COUNTS = {
    "Technology": 43,
    "Financial Services": 36,
    "Healthcare": 20,
    "Industrials": 14,
    "Consumer Cyclical": 13,
    "Consumer Defensive": 11,
    "Communication Services": 10,
    "Energy": 5,
    "Utilities": 5,
    "Real Estate": 4,
    "Basic Materials": 3,
    "Unclassified": 1,
}


def _shares():
    with open(SHARES, encoding="utf-8") as f:
        return json.load(f)["shares"]


def _sectors():
    with open(SECTORS, encoding="utf-8") as f:
        return json.load(f)


class SectorFileTest(unittest.TestCase):
    def test_file_covers_the_share_count_universe(self):
        doc = _sectors()
        symbols = _shares()
        self.assertEqual(doc["unclassified"], "Unclassified")
        self.assertEqual(doc["source"], "Yahoo Finance quoteSummary assetProfile.sector")
        sectors = doc["sectors"]
        self.assertEqual(set(sectors), set(symbols))
        self.assertEqual(len(sectors), 165)
        counts = {}
        for name in sectors.values():
            self.assertIsInstance(name, str)
            self.assertTrue(name.strip())
            counts[name] = counts.get(name, 0) + 1
        self.assertEqual(counts, COUNTS)
        self.assertEqual(sectors["AAPL"], "Technology")
        self.assertEqual(sectors["JPM"], "Financial Services")
        self.assertEqual(sectors["AMZN"], "Consumer Cyclical")
        self.assertEqual(sectors["WMT"], "Consumer Defensive")
        self.assertEqual(sectors["XOM"], "Energy")
        self.assertEqual(sectors["MSTR"], "Technology")
        self.assertEqual(sectors["COIN"], "Financial Services")
        self.assertEqual(sectors["3350.T"], "Financial Services")
        self.assertEqual(sectors["HOGPF"], "Unclassified")

    def test_heatmap_uses_the_file_and_not_the_network(self):
        heat._SECDB[0] = None
        self.assertEqual(heat.sector_of("AAPL"), "Technology")
        self.assertEqual(heat.sector_of("HOGPF"), "Unclassified")
        self.assertEqual(heat.sector_of("NOT_A_TICKER"), "Unclassified")
        self.assertEqual(heat.assign_sector({"symbol": "MSTR"})["sector"], "Technology")
        crypto = heat.assign_sector({"symbol": "BTC", "crypto": True, "sector": "Crypto"})
        self.assertEqual(crypto["sector"], "Crypto")
        blank = heat.assign_sector({"symbol": "BTC", "crypto": True})
        self.assertEqual(blank["sector"], "Crypto")
        heat._SECDB[0] = {"AAPL": "  Technology  ", "ZZ": "Other", "QQ": ""}
        try:
            self.assertEqual(heat.sector_of("AAPL"), "Technology")
            self.assertEqual(heat.sector_of("ZZ"), "Unclassified")
            self.assertEqual(heat.sector_of("QQ"), "Unclassified")
        finally:
            heat._SECDB[0] = None

    def test_refresh_script_fails_soft_and_checks(self):
        run = subprocess.run([sys.executable, SCRIPT, "--self-test"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("self-test ok", run.stdout)
        run = subprocess.run([sys.executable, SCRIPT, "--check"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        # A total Yahoo failure must not rewrite the committed file.
        with open(SECTORS, "rb") as f:
            before = f.read()
        found, failed = {}, [("AAPL", "down")]
        sectors, warnings = refresh_sectors.refresh(
            ["AAPL"], {"AAPL": "Technology"}, found, failed, only_missing=False)
        self.assertIsNone(sectors)
        self.assertTrue(any("unchanged" in w for w in warnings))
        # A new ticker is added only when Yahoo answers. A failed new ticker is left out.
        added, warnings = refresh_sectors.refresh(
            ["NEW"], {}, {"NEW": "Healthcare"}, [], only_missing=True)
        self.assertEqual(added["NEW"], "Healthcare")
        skipped, warnings = refresh_sectors.refresh(
            ["NEW"], {"AAPL": "Technology"}, {}, [("NEW", "429")], only_missing=True)
        self.assertIsNone(skipped)
        self.assertTrue(any("NEW" in w for w in warnings))
        with open(SECTORS, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_page_has_no_sector_network_call(self):
        with open(INDEX, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("sectors.json", html)
        self.assertNotIn("assetProfile", html)
        self.assertIn('id="hflat"', html)
        self.assertIn('id="hbysector"', html)
        self.assertIn('id="hsector"', html)
        self.assertIn("chartmaker_hgroup", html)
        self.assertIn("hgroup", html)


def _html_script():
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    start = html.find("<script>\nconst $=")
    end = html.find("</script>", start)
    return html[start + len("<script>\n"):end]


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


SAMPLE = r"""
({
  range:'1D', source:'Yahoo Finance', asOf:1700000000, missing:[], notes:[], eq:[],
  rows:[
    {symbol:'AAPL', shortName:'Apple', marketCap:3e12, chg:1.2, sector:'Technology'},
    {symbol:'MSFT', shortName:'Microsoft', marketCap:2e12, chg:-0.4, sector:'Technology'},
    {symbol:'NVDA', shortName:'NVIDIA', marketCap:2.5e12, chg:-2, sector:'Technology'},
    {symbol:'JPM', shortName:'JPMorgan', marketCap:6e11, chg:0.3, sector:'Financial Services'},
    {symbol:'V', shortName:'Visa', marketCap:4e11, chg:-0.2, sector:'Financial Services'},
    {symbol:'XOM', shortName:'Exxon', marketCap:5e11, chg:0.8, sector:'Energy'},
    {symbol:'TINY', shortName:'Tiny Co', marketCap:1e9, chg:3, sector:'Utilities'},
    {symbol:'ZZ', shortName:'No Sector', marketCap:2e11, chg:-1.5, sector:''}
  ]
})
"""


class HeatmapSectorBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if node:
            script = _html_script()
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(script)
                path = f.name
            try:
                run = subprocess.run([node, "--check", path], capture_output=True, text=True)
            finally:
                os.unlink(path)
            if run.returncode != 0:
                raise AssertionError(run.stderr)
        chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not chrome:
            raise unittest.SkipTest("google-chrome is required")
        cls.httpd = _serve(os.path.join(ROOT, "static"))
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

    def open(self, query, width=1400, height=1000, mobile=False):
        self.cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
        self.cdp.call("Page.navigate", {"url": self.url + query})
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.ev("document.readyState") == "complete" and self.ev("typeof hgroupSet==='function'"):
                return
            time.sleep(0.05)
        raise AssertionError("page did not load " + query)

    def test_default_is_flat_and_choice_is_remembered(self):
        self.open("?tab=heat")
        ui = self.ev(r"""
(() => {
  const flat = document.getElementById('hflat'), sec = document.getElementById('hbysector');
  const box = document.getElementById('hsector');
  const r = flat.getBoundingClientRect();
  return {
    checked: box.checked,
    flatPressed: flat.getAttribute('aria-pressed'),
    secPressed: sec.getAttribute('aria-pressed'),
    flatOn: flat.classList.contains('on'),
    secOn: sec.classList.contains('on'),
    flatText: flat.textContent,
    secText: sec.textContent,
    stored: localStorage.getItem('chartmaker_hgroup'),
    hctl: getComputedStyle(document.getElementById('hctl')).display,
    visible: r.width > 0 && r.height > 0
  };
})()
""")
        self.assertFalse(ui["checked"])
        self.assertEqual(ui["flatPressed"], "true")
        self.assertEqual(ui["secPressed"], "false")
        self.assertTrue(ui["flatOn"])
        self.assertFalse(ui["secOn"])
        self.assertEqual(ui["flatText"], "Flat")
        self.assertEqual(ui["secText"], "By sector")
        self.assertIsNone(ui["stored"])
        self.assertEqual(ui["hctl"], "flex")
        self.assertTrue(ui["visible"])

        clicked = self.ev(r"""
(() => {
  document.getElementById('hbysector').click();
  return {
    checked: document.getElementById('hsector').checked,
    stored: localStorage.getItem('chartmaker_hgroup'),
    secPressed: document.getElementById('hbysector').getAttribute('aria-pressed'),
    flatPressed: document.getElementById('hflat').getAttribute('aria-pressed')
  };
})()
""")
        self.assertTrue(clicked["checked"])
        self.assertEqual(clicked["stored"], "sector")
        self.assertEqual(clicked["secPressed"], "true")
        self.assertEqual(clicked["flatPressed"], "false")

        self.open("?tab=heat")
        again = self.ev(r"""
(() => ({
  checked: document.getElementById('hsector').checked,
  stored: localStorage.getItem('chartmaker_hgroup'),
  secPressed: document.getElementById('hbysector').getAttribute('aria-pressed')
}))()
""")
        self.assertTrue(again["checked"])
        self.assertEqual(again["stored"], "sector")
        self.assertEqual(again["secPressed"], "true")

        self.open("?tab=heat&hgroup=flat")
        forced = self.ev(r"""
(() => ({
  checked: document.getElementById('hsector').checked,
  stored: localStorage.getItem('chartmaker_hgroup'),
  flatPressed: document.getElementById('hflat').getAttribute('aria-pressed')
}))()
""")
        self.assertFalse(forced["checked"])
        self.assertEqual(forced["stored"], "sector")
        self.assertEqual(forced["flatPressed"], "true")

        self.open("?tab=heat&hgroup=sector")
        linked = self.ev("document.getElementById('hsector').checked")
        self.assertTrue(linked)
        # The link does not overwrite the saved choice. Reload without the param uses storage.
        self.ev("localStorage.setItem('chartmaker_hgroup','flat')")
        self.open("?tab=heat&hgroup=by-sector")
        self.assertTrue(self.ev("document.getElementById('hsector').checked"))
        self.assertEqual(self.ev("localStorage.getItem('chartmaker_hgroup')"), "flat")

    def test_grouped_blocks_keep_colors_tooltips_and_readable_labels(self):
        self.open("?tab=heat", width=1400, height=1100)
        # The static server has no /api/heatmap. Draw a fixed book instead of waiting on Yahoo.
        drawn = self.ev(r"""
(() => {
  hlast = %s;
  $('huni').value = 'sp100';
  $('hshow').value = 'all';
  $('hsize').value = 'mcap';
  $('htitle').value = '';
  hgroupSet(false, false);
  hdraw();
  const flatColors = {};
  chart.getModel().getSeriesByIndex(0).getData().tree.root.eachNode(n => {
    if (n.children && n.children.length) return;
    const raw = n.getModel().option;
    if (raw && raw.d) flatColors[raw.d.symbol] = (raw.itemStyle || {}).color;
  });
  const flatData = chart.getOption().series[0].data;
  hgroupSet(true, false);
  hdraw();
  const opt = chart.getOption().series[0];
  const sectors = opt.data.map(d => ({name:d.name, value:d.value, n:d.children.length,
    colors:d.children.map(c => [c.name, c.itemStyle.color])}));
  const info = [];
  chart.getModel().getSeriesByIndex(0).getData().tree.root.eachNode(n => {
    if (!n.children || !n.children.length || n === chart.getModel().getSeriesByIndex(0).getData().tree.root) return;
    const L = n.getLayout() || {};
    info.push({name:n.name, w:L.width, h:L.height, area:(L.width||0)*(L.height||0),
      fs:n.getModel().get(['upperLabel','fontSize']), show:n.getModel().get(['upperLabel','show'])});
  });
  const tip = opt.tooltip.formatter({name:'AAPL', data:{name:'AAPL', d:{shortName:'Apple', chg:1.2, marketCap:3e12}}});
  const parentTip = opt.tooltip.formatter({name:'Technology', data:{name:'Technology'}});
  $('hsize').value = 'eq';
  hdraw();
  const eq = chart.getOption().series[0].data.map(d => ({name:d.name, value:d.value,
    child:d.children.map(c => c.value)}));
  $('hsize').value = 'sqrt';
  hdraw();
  const sq = chart.getOption().series[0].data.map(d => ({name:d.name, value:d.value}));
  $('hsize').value = 'mcap';
  hgroupSet(false, false);
  hdraw();
  return {flatKids: flatData.some(d => d.children), flatColors, sectors, info, tip, parentTip, eq, sq,
    nodeClick: opt.nodeClick, foot: (chart.getOption().graphic || []).map(g => (g.style || {}).text).join(' | ')};
})()
""" % SAMPLE)
        self.assertFalse(drawn["flatKids"])
        self.assertEqual([s["name"] for s in drawn["sectors"]], ["Technology", "Financial Services", "Energy", "Unclassified", "Utilities"])
        self.assertEqual(drawn["sectors"][0]["value"], 3e12 + 2e12 + 2.5e12)
        self.assertEqual(drawn["sectors"][1]["value"], 6e11 + 4e11)
        self.assertEqual(drawn["sectors"][2]["value"], 5e11)
        self.assertEqual(drawn["sectors"][3]["value"], 2e11)
        self.assertEqual(drawn["sectors"][4]["value"], 1e9)
        self.assertEqual(drawn["sectors"][0]["n"], 3)
        for sec in drawn["sectors"]:
            for name, color in sec["colors"]:
                self.assertEqual(color, drawn["flatColors"][name])
        self.assertIn("Apple", drawn["tip"])
        self.assertIn("+1.20%", drawn["tip"])
        self.assertIn("$3000B", drawn["tip"])
        self.assertEqual(drawn["parentTip"], "Technology")
        self.assertFalse(drawn["nodeClick"])
        tech = next(s for s in drawn["info"] if s["name"] == "Technology")
        fin = next(s for s in drawn["info"] if s["name"] == "Financial Services")
        self.assertGreaterEqual(tech["fs"], 15)
        self.assertGreaterEqual(fin["fs"], 15)
        self.assertTrue(tech["show"])
        self.assertGreater(tech["area"], fin["area"])
        for s in drawn["info"]:
            if s["show"]:
                self.assertGreaterEqual(s["fs"], 15)
            else:
                self.assertTrue(s["w"] < 36 or s["h"] < 24)
        # Equal tiles inside a block; the block is still that sector's market cap.
        eq = {s["name"]: s for s in drawn["eq"]}
        self.assertEqual(eq["Technology"]["value"], 7.5e12)
        self.assertEqual(len(set(round(v, 2) for v in eq["Technology"]["child"])), 1)
        self.assertEqual(eq["Financial Services"]["value"], 1e12)
        sq = {s["name"]: s["value"] for s in drawn["sq"]}
        self.assertEqual(sq["Technology"], 7.5e12)
        self.assertEqual(sq["Energy"], 5e11)
        self.assertGreater(sq["Technology"] / sq["Energy"], 10)

    def test_mobile_control_fits_and_chart_scales(self):
        # Layout viewport, not the legacy 980px mobile fallback (the page has no viewport meta).
        self.open("?tab=heat", width=390, height=844, mobile=False)
        ui = self.ev(r"""
(() => {
  hlast = %s;
  $('hsize').value = 'mcap';
  $('hshow').value = 'all';
  hgroupSet(true, false);
  hdraw();
  const flat = document.getElementById('hflat');
  const sec = document.getElementById('hbysector');
  const fr = flat.getBoundingClientRect(), sr = sec.getBoundingClientRect();
  const box = document.getElementById('chart').getBoundingClientRect();
  const info = [];
  const td = chart.getModel().getSeriesByIndex(0).getData();
  td.tree.root.eachNode(n => {
    if (!n.children || !n.children.length || n === td.tree.root) return;
    const L = n.getLayout() || {};
    info.push({name:n.name, fs:n.getModel().get(['upperLabel','fontSize']), show:n.getModel().get(['upperLabel','show']), w:L.width, h:L.height});
  });
  return {
    vw: document.documentElement.clientWidth,
    flatH: fr.height, secH: sr.height,
    flatRight: fr.right, secRight: sr.right,
    flatLeft: fr.left, secLeft: sr.left,
    chartW: box.width, chartH: box.height,
    transform: getComputedStyle(document.getElementById('chart')).transform,
    info
  };
})()
""" % SAMPLE)
        self.assertLessEqual(ui["vw"], 400)
        self.assertGreaterEqual(ui["flatH"], 44)
        self.assertGreaterEqual(ui["secH"], 44)
        self.assertGreaterEqual(ui["flatLeft"], 0)
        self.assertLessEqual(ui["secRight"], ui["vw"] + 1)
        self.assertLessEqual(ui["flatRight"], ui["vw"] + 1)
        self.assertLess(ui["chartW"], ui["vw"])
        self.assertGreater(ui["chartW"], 200)
        self.assertNotEqual(ui["transform"], "none")
        shown = [s for s in ui["info"] if s["show"]]
        self.assertTrue(shown)
        for s in shown:
            self.assertGreaterEqual(s["fs"], 15)


if __name__ == "__main__":
    unittest.main()
