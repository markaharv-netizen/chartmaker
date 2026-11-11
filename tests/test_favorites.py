"""Sheet-chart favorites stay in this browser, survive reload, and omit missing charts.

Opens the real static app in headless Chrome. No Google calls and no server writes.
"""
import functools
import json
import os
import sys
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_cotd_build import Chrome, _free_port

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
FAVK = "chartmaker_sheet_favs"
MULTI = "bot2/1078237833"  # 13 BTC cost basis by holder, two charts
ONE = "bot1/200447618"  # 52 Core goods vs services, one chart that stays in the library
TABLE = "bot2/1714490525"  # 9 MSTR preferreds table, no charts
ROBOTAXI = "bot2/1670158709"  # table favorite, not tied to today's edition


def cotd_card_count():
    """How many cards today's edition actually publishes. Tests must not hard-code this."""
    with open(os.path.join(STATIC, "cotd", "today.json"), encoding="utf-8") as f:
        return len(json.load(f)["items"])
RETIRED = (
    "1547332232", "643299192", "333185169", "1024131985", "1791345316", "1370617918",
    "292208014", "2119398138", "1584345646", "1186940001", "501919598", "579853621", "418894352",
    "1308503061", "748431671", "254001772",
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
    "122 Bond vol vs stock vol",
    "232 Quits vs wage growth",
    "162 Factory construction boom",
)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return


def _serve(directory):
    handler = functools.partial(_QuietHandler, directory=directory)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class FavoritesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chrome = __import__("shutil").which("google-chrome") or __import__("shutil").which("google-chrome-stable")
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
            httpd.server_close()

    def setUp(self):
        self.viewport(1280, 900, False)
        self.cdp.call("Page.navigate", {"url": self.url})
        self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
        self.cdp.evaluate("localStorage.clear()")
        self.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait("document.readyState==='complete' && typeof favToggle==='function'", lambda v: v is True)

    def viewport(self, width, height, mobile):
        self.cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})

    def wait(self, expr, ok, timeout=20):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self.cdp.evaluate(expr)
            except Exception as exc:
                last = str(exc)
            else:
                if ok(last):
                    return last
            time.sleep(0.1)
        raise AssertionError("timeout waiting for %s; last=%r" % (expr, last))

    def open_sheets(self):
        self.cdp.evaluate("document.getElementById('mSheets').click()")
        self.wait(
            "document.querySelectorAll('#shlist .row').length",
            lambda n: isinstance(n, int) and n > 400)
        self.wait(
            "(document.getElementById('favlocal')||{}).textContent||''",
            lambda t: "this browser" in t and "not stored on the server" in t)

    def search(self, text):
        self.cdp.evaluate(
            "(()=>{const i=document.getElementById('shq');i.value=%s;i.dispatchEvent(new Event('input'));})()"
            % json.dumps(text))
        needle = json.dumps(text.lower())
        self.wait(
            """(() => {
              const q=%s;
              const rows=[...document.querySelectorAll('#shlist .row')];
              if(!rows.length){const t=document.getElementById('shlist').innerText;return t.includes('No tabs match')||t.includes('No favorites');}
              return rows.every(r=>r.innerText.toLowerCase().includes(q));
            })()""" % needle,
            lambda v: v is True)
        return json.loads(self.cdp.evaluate(
            "JSON.stringify([...document.querySelectorAll('#shlist .row')].map(r=>({tab:(r.querySelector('h3')||{}).textContent||'',keys:[...r.querySelectorAll('button.fav')].map(b=>b.dataset.fav)})))"))

    def show_favorites(self):
        self.cdp.evaluate(
            "(()=>{const b=document.getElementById('shfav');if(b&&b.getAttribute('aria-pressed')!=='true')b.click();})()")
        self.wait(
            "(document.getElementById('shcount')||{}).textContent||''",
            lambda t: "favorite" in t)

    def stored_keys(self):
        raw = self.cdp.evaluate("localStorage.getItem(%s)" % json.dumps(FAVK))
        if not raw:
            return []
        data = json.loads(raw)
        items = data["items"] if isinstance(data, dict) else data
        return [item["k"] if isinstance(item, dict) else item for item in items]

    def test_empty_state_stars_and_chartless_tabs(self):
        self.open_sheets()
        self.show_favorites()
        empty = self.cdp.evaluate("document.getElementById('shlist').innerText")
        self.assertIn("No favorites yet", empty)
        self.assertIn("this browser", self.cdp.evaluate("document.getElementById('favlocal').textContent"))
        self.cdp.evaluate("document.getElementById('shfav').click()")
        self.wait("document.querySelectorAll('#shlist .row').length", lambda n: isinstance(n, int) and n > 400)
        rows = self.search("9 MSTR preferreds table")
        self.assertEqual(rows, [{"tab": "9 MSTR preferreds table", "keys": [TABLE + "/1"]}])
        label = self.cdp.evaluate("document.querySelector('button.fav').getAttribute('aria-label')")
        self.assertTrue(label.startswith("Add to favorites"))
        self.assertIn("9 MSTR preferreds table", label)
        self.cdp.evaluate("document.querySelector('button.fav').click()")
        self.wait("localStorage.getItem(%s)||''" % json.dumps(FAVK), lambda raw: TABLE in (raw or ""))
        stored = json.loads(self.cdp.evaluate("localStorage.getItem(%s)" % json.dumps(FAVK)))
        self.assertEqual(stored["items"][0]["k"], TABLE + "/1")
        self.assertEqual(stored["items"][0]["title"], "9 MSTR preferreds table")
        self.show_favorites()
        fav = self.cdp.evaluate(r"""(() => {
          const r = document.querySelector('#shlist .row');
          const png = r.querySelector('a[href*=".png"]');
          return {key: r.getAttribute('data-favrow'),
            label: (r.querySelector('button.fav') || {}).getAttribute('aria-label') || '',
            href: png ? png.getAttribute('href') : ''};
        })()""")
        self.assertEqual(fav["key"], TABLE + "/1")
        self.assertIn("9 MSTR preferreds table", fav["label"])
        self.assertIn("1714490525.png", fav["href"])
        self.assertNotIn("_2", fav["href"])
        self.cdp.evaluate("document.getElementById('shfav').click()")
        self.wait("document.querySelectorAll('#shlist .row').length", lambda n: n == 1)
        self.cdp.evaluate("document.getElementById('shcharts').click()")
        self.wait(
            "document.querySelectorAll('#shlist .row').length",
            lambda n: n == 0)
        self.assertIn("No tabs match", self.cdp.evaluate("document.getElementById('shlist').innerText"))

    def test_star_persists_and_matches_across_views(self):
        self.open_sheets()
        rows = self.search("13 BTC cost basis by holder")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["keys"], [MULTI + "/1", MULTI + "/2"])
        self.cdp.evaluate("document.querySelector('button.fav[data-fav=%s]').click()" % json.dumps(MULTI + "/1"))
        self.cdp.evaluate("document.querySelector('button.fav[data-fav=%s]').click()" % json.dumps(MULTI + "/2"))
        self.wait("localStorage.getItem(%s)||''" % json.dumps(FAVK), lambda raw: MULTI + "/2" in (raw or ""))
        pressed = json.loads(self.cdp.evaluate(
            "JSON.stringify([...document.querySelectorAll('button.fav')].map(b=>[b.dataset.fav,b.getAttribute('aria-pressed'),b.getAttribute('aria-label')]))"))
        by_key = {k: (on, label) for k, on, label in pressed}
        self.assertEqual(by_key[MULTI + "/1"][0], "true")
        self.assertEqual(by_key[MULTI + "/2"][0], "true")
        self.assertNotEqual(by_key[MULTI + "/1"][1], by_key[MULTI + "/2"][1])
        self.assertTrue(by_key[MULTI + "/1"][1].startswith("Remove from favorites"))
        paths = self.cdp.evaluate(
            "JSON.stringify([sheetFull({book:'bot2',gid:1078237833},1),sheetFull({book:'bot2',gid:1078237833},2)])")
        self.assertEqual(json.loads(paths), [
            "sheets/full/bot2/1078237833.png",
            "sheets/full/bot2/1078237833_2.png",
        ])
        ident = self.cdp.evaluate(r"""(() => {
          const bot1='1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY';
          const bot2='1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ';
          const bot3='1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU';
          const url=(sid,gid)=>'https://docs.google.com/spreadsheets/d/'+sid+'/edit#gid='+gid;
          const n=hit=>hit&&hit.ch.n;
          const multi=url(bot2,1078237833);
          const table=cotdChart({title:'editorial table',sheet_url:url(bot2,1670158709)}, SHEETS);
          const pref=SHEETS.tabs.find(t=>t.book==='bot2'&&t.gid===1714490525);
          const prefCharts=sheetCharts(pref);
          const held=sheetCharts(SHEETS.tabs.find(t=>t.gid===1078237833)).map(c=>c.n);
          return {
            one: n(cotdChart({title:'editorial title',sheet_url:url(bot1,200447618)}, SHEETS)),
            editorial: cotdChart({title:"Bitcoin's price versus what ETFs, companies and Strategy paid",sheet_url:multi}, SHEETS),
            byN: n(cotdChart({title:"Bitcoin's price versus what ETFs, companies and Strategy paid",chart_n:1,sheet_url:multi}, SHEETS)),
            byN2: n(cotdChart({title:'not a chart title',chart_n:2,sheet_url:multi}, SHEETS)),
            missN: cotdChart({title:'not a chart title',chart_n:9,sheet_url:multi}, SHEETS),
            missNTitle: n(cotdChart({title:'Average acquisition cost: treasury companies vs. miners',chart_n:9,sheet_url:multi}, SHEETS)),
            missOnSingle: cotdChart({title:'editorial title',chart_n:9,sheet_url:url(bot1,200447618)}, SHEETS),
            exact: n(cotdChart({title:'Average acquisition cost: treasury companies vs. miners',sheet_url:multi}, SHEETS)),
            exactOverridden: n(cotdChart({title:'Average acquisition cost: treasury companies vs. miners',chart_n:1,sheet_url:multi}, SHEETS)),
            missing: n(cotdChart({title:'Anthropic',sheet_url:url(bot3,978356289)}, SHEETS)),
            retired: cotdChart({title:'retired',sheet_url:url(bot1,1547332232)}, SHEETS),
            dup: cotdChart({title:'Strive denominated in BTC (ASST/BTC)',sheet_url:url(bot3,1045685066)}, SHEETS),
            tableN: table&&table.ch.n,
            tableLabel: table&&table.ch.label,
            tableKey: table&&(table.tab.book+'/'+table.tab.gid+'/'+table.ch.n),
            prefN: prefCharts.length,
            prefLabel: prefCharts[0]&&prefCharts[0].label,
            prefSynthetic: !!(prefCharts[0]&&prefCharts[0].synthetic),
            held: held
          };
        })()""")
        self.assertEqual(ident["one"], 1)
        self.assertIsNone(ident["editorial"])
        self.assertEqual(ident["byN"], 1)
        self.assertEqual(ident["byN2"], 2)
        self.assertIsNone(ident["missN"])
        self.assertEqual(ident["missNTitle"], 2)
        self.assertIsNone(ident["missOnSingle"])
        self.assertEqual(ident["exact"], 2)
        self.assertEqual(ident["exactOverridden"], 1)
        # 978356289 is one chart and stays in the library, so a card on that tab maps to chart 1.
        self.assertEqual(ident["missing"], 1)
        self.assertIsNone(ident["retired"])
        self.assertIsNone(ident["dup"])
        self.assertEqual(ident["tableN"], 1)
        self.assertEqual(ident["tableLabel"], "27 Robotaxi coverage table")
        self.assertEqual(ident["tableKey"], ROBOTAXI + "/1")
        self.assertEqual(ident["prefN"], 1)
        self.assertEqual(ident["prefLabel"], "9 MSTR preferreds table")
        self.assertTrue(ident["prefSynthetic"])
        self.assertEqual(ident["held"], [1, 2])

        self.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
        self.open_sheets()
        self.search("13 BTC cost basis by holder")
        still = json.loads(self.cdp.evaluate(
            "JSON.stringify([...document.querySelectorAll('button.fav')].map(b=>b.getAttribute('aria-pressed')))"))
        self.assertEqual(still, ["true", "true"])
        self.show_favorites()
        favs = self.cdp.evaluate(r"""(() => {
          return [...document.querySelectorAll('#shlist .row')].map(r => ({
            key: r.getAttribute('data-favrow'),
            open: (r.querySelector('a.open')||{}).textContent || '',
            links: [...r.querySelectorAll('a.open')].map(a => ({text:a.textContent, href:a.getAttribute('href'), download:a.getAttribute('download')})),
            note: r.innerText
          }));
        })()""")
        by_row = {row["key"]: row for row in favs}
        self.assertEqual(set(by_row), {MULTI + "/1", MULTI + "/2"})
        first = by_row[MULTI + "/1"]
        texts = [link["text"] for link in first["links"]]
        self.assertEqual(texts, ["Open in Sheets", "View PNG", "Download PNG"])
        self.assertIn("1078237833.png", first["links"][1]["href"])
        self.assertNotIn("_2", first["links"][1]["href"])
        self.assertTrue(first["links"][2]["download"])
        self.assertIn("gid=1078237833", first["links"][0]["href"])
        second = by_row[MULTI + "/2"]
        self.assertEqual([link["text"] for link in second["links"]], ["Open in Sheets", "View PNG", "Download PNG"])
        self.assertIn("1078237833_2.png", second["links"][1]["href"])
        self.assertNotIn("No PNG in the library for this chart.", second["note"])

        self.cdp.evaluate("document.getElementById('mCotd').click()")
        expected = cotd_card_count()
        self.wait("document.querySelectorAll('#cotd .card').length", lambda n: n == expected)
        cards = self.cdp.evaluate(r"""(() => {
          return [...document.querySelectorAll('#cotd .card')].map(c => ({
            title: c.querySelector('h3').textContent,
            key: (c.querySelector('button.fav')||{}).dataset ? c.querySelector('button.fav').dataset.fav : '',
            pressed: (c.querySelector('button.fav')||{}).getAttribute ? c.querySelector('button.fav').getAttribute('aria-pressed') : '',
            label: (c.querySelector('button.fav')||{}).getAttribute ? (c.querySelector('button.fav').getAttribute('aria-label') || '') : ''
          }));
        })()""")
        with open(os.path.join(STATIC, "cotd", "today.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(cards), len(saved["items"]))
        self.assertEqual(cards[0]["title"], "1. " + saved["items"][0]["title"])
        self.assertEqual(cards[-1]["title"], "%d. %s" % (len(saved["items"]), saved["items"][-1]["title"]))
        note = self.cdp.evaluate("document.getElementById('cotd').innerText")
        self.assertTrue("this browser" in note or "no separate accounts" in note, note)
        for gid in RETIRED:
            self.assertNotIn(gid, note)
        for tab in RETIRED_TABS:
            self.assertNotIn(tab, note)
        starred = next(c for c in cards if c["key"])
        self.assertEqual(starred["pressed"], "false")
        self.cdp.evaluate("document.querySelector('#cotd button.fav[data-fav=%s]').click()" % json.dumps(starred["key"]))
        self.wait("localStorage.getItem(%s)||''" % json.dumps(FAVK), lambda raw: starred["key"] in (raw or ""))
        self.cdp.evaluate("document.getElementById('mSheets').click()")
        self.wait("document.querySelectorAll('#shlist .row').length", lambda n: isinstance(n, int) and n > 0)
        self.show_favorites()
        keys = json.loads(self.cdp.evaluate(
            "JSON.stringify([...document.querySelectorAll('#shlist .row')].map(r=>r.getAttribute('data-favrow')))"))
        self.assertIn(starred["key"], keys)
        self.assertIn(MULTI + "/1", keys)
        self.cdp.evaluate("document.querySelector('#shlist button.fav[data-fav=%s]').click()" % json.dumps(starred["key"]))
        self.wait(
            "JSON.stringify([...document.querySelectorAll('#shlist .row')].map(r=>r.getAttribute('data-favrow')))",
            lambda raw: starred["key"] not in (raw or ""))
        self.cdp.evaluate("document.getElementById('mCotd').click()")
        self.wait("document.querySelectorAll('#cotd .card').length", lambda n: n == expected)
        pressed_again = self.cdp.evaluate(
            "document.querySelector('#cotd button.fav[data-fav=%s]').getAttribute('aria-pressed')" % json.dumps(starred["key"]))
        self.assertEqual(pressed_again, "false")
        self.assertNotIn(starred["key"], self.stored_keys())
        self.assertIn(MULTI + "/1", self.stored_keys())

    def test_missing_retired_and_retitled_charts_are_not_shown(self):
        self.open_sheets()
        title = self.cdp.evaluate(
            "sheetCharts(SHEETS.tabs.find(t=>t.book==='bot1'&&t.gid===200447618))[0].title")
        payload = {
            "v": 1,
            "items": [
                {"k": ONE + "/1", "title": title},
                {"k": "bot1/1965360503/1", "title": "a different chart now lives here"},
                {"k": "bot1/1547332232/1", "title": "Nvidia valuation: price-to-sales and trailing P/E (month-end)"},
                {"k": "bot1/643299192/1", "title": "Where Nvidia bills its sales"},
                {"k": "bot1/333185169/1", "title": "Tesla beta"},
                {"k": "bot1/1024131985/1", "title": "MSTR BTC per share"},
                {"k": "bot1/999999999/1", "title": "deleted chart"},
                {"k": "not a key", "title": "junk"},
            ],
        }
        self.cdp.evaluate("localStorage.setItem(%s,%s)" % (json.dumps(FAVK), json.dumps(json.dumps(payload))))
        self.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
        self.open_sheets()
        self.show_favorites()
        rows = json.loads(self.cdp.evaluate(
            "JSON.stringify([...document.querySelectorAll('#shlist .row')].map(r=>r.getAttribute('data-favrow')))"))
        self.assertEqual(rows, [ONE + "/1"])
        text = self.cdp.evaluate("document.getElementById('sheets').innerText")
        self.assertIn("no longer in the library", text)
        for gid in RETIRED + ("999999999",):
            self.assertNotIn(gid, text)
        for tab in RETIRED_TABS:
            self.assertNotIn(tab, text)
        self.assertNotIn("deleted chart", text)
        self.assertNotIn("a different chart now lives here", text)
        self.assertNotIn("1965360503", self.cdp.evaluate("document.getElementById('shlist').innerHTML"))
        keys = self.stored_keys()
        self.assertEqual(keys, [ONE + "/1"])
        for gid in RETIRED:
            self.assertNotIn(gid, " ".join(keys))

    def test_legacy_string_keys_and_cap(self):
        self.open_sheets()
        packed = self.cdp.evaluate(r"""(() => {
          const items=[]; let extra=null;
          for (const t of SHEETS.tabs) for (const c of sheetCharts(t)) {
            const row={k:t.book+'/'+t.gid+'/'+c.n, title:c.title, tab:t.tab};
            if (items.length<200) items.push(row); else if (!extra) extra=row;
          }
          return {items, extra};
        })()""")
        self.assertEqual(len(packed["items"]), 200)
        extra = packed["extra"]
        self.assertIsNotNone(extra)
        self.cdp.evaluate("localStorage.setItem(%s,%s)" % (
            json.dumps(FAVK), json.dumps(json.dumps([ONE + "/1"]))))
        self.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
        self.open_sheets()
        self.search("52 Core goods vs services")
        self.assertEqual(self.cdp.evaluate(
            "document.querySelector('button.fav').getAttribute('aria-pressed')"), "true")
        stored = json.loads(self.cdp.evaluate("localStorage.getItem(%s)" % json.dumps(FAVK)))
        self.assertEqual(stored["v"], 2)
        self.assertEqual(stored["items"][0]["k"], ONE + "/1")
        self.cdp.evaluate("localStorage.setItem(%s,%s)" % (
            json.dumps(FAVK), json.dumps(json.dumps({"v": 1, "items": packed["items"]}))))
        self.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
        self.open_sheets()
        self.search(extra["tab"])
        before = self.cdp.evaluate(
            "document.querySelector('button.fav[data-fav=%s]').getAttribute('aria-pressed')" % json.dumps(extra["k"]))
        self.assertEqual(before, "false")
        self.cdp.evaluate("document.querySelector('button.fav[data-fav=%s]').click()" % json.dumps(extra["k"]))
        msg = self.wait("(document.getElementById('favmsg')||{}).textContent||''", lambda t: "200" in t)
        self.assertIn("200", msg)
        self.assertEqual(self.cdp.evaluate(
            "document.querySelector('button.fav[data-fav=%s]').getAttribute('aria-pressed')" % json.dumps(extra["k"])),
            "false")
        self.assertNotIn(extra["k"], self.stored_keys())

    def test_bad_storage_does_not_pretend_to_save(self):
        self.cdp.evaluate("localStorage.setItem(%s,'{')" % json.dumps(FAVK))
        self.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
        self.open_sheets()
        self.search("52 Core goods vs services")
        state = self.cdp.evaluate(r"""(() => {
          const b=document.querySelector('button.fav');
          return {disabled:b.disabled, pressed:b.getAttribute('aria-pressed'), msg:document.getElementById('favmsg').textContent, raw:localStorage.getItem('chartmaker_sheet_favs')};
        })()""")
        self.assertTrue(state["disabled"])
        self.assertEqual(state["pressed"], "false")
        self.assertIn("could not be read", state["msg"])
        self.assertEqual(state["raw"], "{")
        self.cdp.evaluate("document.querySelector('button.fav').click()")
        self.assertEqual(self.cdp.evaluate("localStorage.getItem(%s)" % json.dumps(FAVK)), "{")

        self.cdp.evaluate("localStorage.clear()")
        self.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
        self.open_sheets()
        self.search("52 Core goods vs services")
        self.cdp.evaluate(r"""(() => {
          const proto=Storage.prototype, orig=proto.setItem;
          proto.setItem=function(){throw new Error('blocked')};
          window.__favSet=orig;
        })()""")
        self.cdp.evaluate("document.querySelector('button.fav').click()")
        msg = self.cdp.evaluate("document.getElementById('favmsg').textContent")
        self.assertIn("Could not save", msg)
        self.assertEqual(self.cdp.evaluate("document.querySelector('button.fav').getAttribute('aria-pressed')"), "false")
        self.assertFalse(self.cdp.evaluate("localStorage.getItem(%s)" % json.dumps(FAVK)))
        self.cdp.evaluate("Storage.prototype.setItem=window.__favSet")

        added = self.cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": r"""
          Object.defineProperty(Storage.prototype, 'getItem', {configurable:true, writable:true, value:function(){throw new Error('blocked')}});
        """})
        try:
            self.cdp.call("Page.reload", {"ignoreCache": True})
            self.wait("document.readyState==='complete' && typeof setMode==='function'", lambda v: v is True)
            self.open_sheets()
            msg = self.cdp.evaluate("document.getElementById('favmsg').textContent")
            self.assertIn("blocked storage", msg)
            self.search("52 Core goods vs services")
            self.assertTrue(self.cdp.evaluate("document.querySelector('button.fav').disabled"))
        finally:
            ident = added.get("identifier")
            if ident:
                self.cdp.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": ident})

    def test_keyboard_and_narrow_layout(self):
        self.open_sheets()
        self.search("52 Core goods vs services")
        self.cdp.evaluate("document.querySelector('button.fav').focus()")
        self.assertEqual(self.cdp.evaluate("document.activeElement.className"), "fav")
        for kind, text in (("rawKeyDown", ""), ("keyDown", ""), ("char", "\r"), ("keyUp", "")):
            params = {"type": kind, "key": "Enter", "code": "Enter",
                      "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
            if text:
                params["text"] = text
                params["unmodifiedText"] = text
            self.cdp.call("Input.dispatchKeyEvent", params)
        self.wait(
            "document.querySelector('button.fav').getAttribute('aria-pressed')",
            lambda v: v == "true")
        self.assertIn(ONE + "/1", self.stored_keys())
        self.viewport(390, 800, True)
        self.show_favorites()
        box = self.cdp.evaluate(r"""(() => {
          const btn=document.querySelector('#shlist button.fav');
          const r=btn.getBoundingClientRect();
          const sheet=document.getElementById('sheets');
          const row=document.querySelector('#shlist .row');
          const links=[...row.querySelectorAll('a.open')].map(a=>a.textContent);
          return {w:r.width,h:r.height,sheetOverflow:sheet.scrollWidth>sheet.clientWidth+2,
            rowRight:row.getBoundingClientRect().right,view:window.innerWidth,links};
        })()""")
        self.assertGreaterEqual(box["w"], 40)
        self.assertGreaterEqual(box["h"], 40)
        self.assertFalse(box["sheetOverflow"])
        self.assertLessEqual(box["rowRight"], box["view"] + 1)
        self.assertEqual(box["links"], ["Open in Sheets", "View PNG", "Download PNG"])
        self.viewport(1280, 900, False)
        wide = self.cdp.evaluate(r"""(() => {
          const sheet=document.getElementById('sheets');
          const row=document.querySelector('#shlist .row');
          return {overflow:sheet.scrollWidth>sheet.clientWidth+2, rowRight:row.getBoundingClientRect().right, view:window.innerWidth,
            img:!!row.querySelector('img')};
        })()""")
        self.assertFalse(wide["overflow"])
        self.assertLessEqual(wide["rowRight"], wide["view"] + 1)
        self.assertTrue(wide["img"])
        self.cdp.evaluate("setMode('prices')")
        self.assertEqual(self.cdp.evaluate("getComputedStyle(document.getElementById('ctl')).display"), "flex")
        self.cdp.evaluate("setMode('paper')")
        self.assertEqual(self.cdp.evaluate("getComputedStyle(document.getElementById('paper')).display"), "block")
        self.cdp.evaluate("setMode('cotd')")
        self.wait("document.querySelectorAll('#cotd .card').length", lambda n: n == cotd_card_count())


if __name__ == "__main__":
    unittest.main()
