"""Charts of the day is a random draw of 20 charts and table tabs.

The builder self-test writes a temporary edition. This module checks the
exclusions, the 3-day no-repeat rule, table eligibility, and the count of 20.
It also opens the Charts of the day tab and checks that every card is shown
without the old recommendation notes.
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
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cotd_build

SCRIPT = os.path.join(ROOT, "scripts", "cotd_build.py")
INDEX = os.path.join(ROOT, "static", "index.html")
BOT1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
BOT2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
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
    "1308503061",  # 122 Bond vol vs stock vol
    "748431671",   # 232 Quits vs wage growth
    "254001772",   # 162 Factory construction boom
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


def cotd_source():
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    start = html.find("// ---------- Charts of the day")
    end = html.find("// ---------- Sheets library", start)
    if start < 0 or end < 0 or "function cotdenter" not in html[start:end]:
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
        self.assertNotIn("Chart 2", blob)
        self.assertNotIn("1536200109", blob)
        self.assertNotIn("Bitcoin correlation with gold", blob)

    def test_ui_source_renders_every_item(self):
        body = cotd_source()
        self.assertIn("(j.items||[]).map", body.replace(" ", ""))
        self.assertNotIn(".slice(", body)
        self.assertNotIn("nth-child", body)
        self.assertNotIn("Why it was picked", body)
        self.assertNotIn("Why it matters", body)
        self.assertNotIn("What you should know", body)
        self.assertNotIn("recommended", body.lower())
        self.assertNotIn("curated", body.lower())
        self.assertIn("tablepreview", body)
        self.assertIn("Open this table", body)
        self.assertIn("data-kind", body)
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
            os.makedirs(os.path.join(fixture, "sheets"))
            os.symlink(os.path.join(ROOT, "static", "sheets", "catalog.json"),
                       os.path.join(fixture, "sheets", "catalog.json"))
            items = []
            for i in range(1, 18):
                items.append({
                    "sheet": "Bot experiments",
                    "tab": f"sample tab {i}",
                    "title": f"Fixture chart {i:02d}",
                    "kind": "chart",
                    "chart_n": 1,
                    "image": f"cotd/2099-01-15/{i:02d}.png",
                    "picked": f"picked note {i} should stay off the card",
                    "matters": f"matters note {i}",
                    "know": f"know note {i} stays off the card",
                    "sheet_url": f"https://docs.google.com/spreadsheets/d/sheet/edit#gid={900000 + i}",
                })
            items.append({
                "sheet": "bot experiments 2",
                "tab": "13 BTC cost basis by holder",
                "title": "Average acquisition cost: treasury companies vs. miners",
                "kind": "chart",
                "chart_n": 2,
                "image": "cotd/2099-01-15/18.png",
                "picked": "picked note 18 should stay off the card",
                "matters": "matters note 18",
                "know": "know note 18 stays off the card",
                "sheet_url": "https://docs.google.com/spreadsheets/d/%s/edit#gid=1078237833" % BOT2,
            })
            items.append({
                "sheet": "bot experiments 2",
                "tab": "Sample table with preview",
                "title": "Sample table with preview",
                "kind": "table",
                "chart_n": 1,
                "image": "cotd/2099-01-15/19.png",
                "picked": "picked note table should stay off the card",
                "matters": "matters note table",
                "know": "know note table stays off the card",
                "sheet_url": "https://docs.google.com/spreadsheets/d/sheet/edit#gid=42",
            })
            items.append({
                "sheet": "bot experiments 2",
                "tab": "9 MSTR preferreds table",
                "title": "9 MSTR preferreds table",
                "kind": "table",
                "chart_n": 1,
                "picked": "picked note bare table should stay off the card",
                "matters": "matters note bare",
                "know": "know note bare stays off the card",
                "sheet_url": "https://docs.google.com/spreadsheets/d/%s/edit#gid=1714490525" % BOT2,
            })
            with open(os.path.join(fixture, "cotd", "today.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "date": "2099-01-15",
                    "random": True,
                    "theme": "20 items drawn at random from the sheet library.",
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
                    self.assertEqual(shown["titles"][17], "18. Average acquisition cost: treasury companies vs. miners")
                    self.assertEqual(shown["titles"][18], "19. Sample table with preview")
                    self.assertEqual(shown["titles"][19], "20. 9 MSTR preferreds table")
                    self.assertEqual(shown["imgs"], 19, shown)
                    self.assertEqual(shown["tables"], 2, shown)
                    self.assertEqual(shown["previews"], 1, shown)
                    self.assertEqual(shown["kinds"][18], "table")
                    self.assertEqual(shown["kinds"][19], "table")
                    self.assertIn("20 items drawn at random from the sheet library.", shown["text"])
                    self.assertIn("Bot experiments", shown["text"])
                    self.assertIn("Open this table", shown["text"])
                    self.assertIn("open in sheet", shown["text"])
                    self.assertNotIn("picked note", shown["text"])
                    self.assertNotIn("matters note", shown["text"])
                    self.assertNotIn("know note", shown["text"])
                    self.assertNotIn("Why it was picked", shown["text"])
                    self.assertNotIn("Why it matters", shown["text"])
                    self.assertNotIn("What you should know", shown["text"])
                    self.assertNotIn("recommended", shown["text"].lower())
                    self.assertNotIn("curated", shown["text"].lower())
                    self.assertEqual(shown["favs"][17], "bot2/1078237833/2")
                    self.assertEqual(shown["favs"][19], "bot2/1714490525/1")
                    narrow = browser.cards(f"{host}/fixture/index.html", width=390, height=800, mobile=True)
                    self.assertEqual(narrow["n"], 20, narrow)
                    self.assertEqual(narrow["hidden"], 0, narrow)
                    with open(os.path.join(ROOT, "static", "cotd", "today.json"), encoding="utf-8") as f:
                        saved = json.load(f)
                    live = browser.cards(f"{host}/real/index.html")
                    self.assertEqual(live["n"], len(saved["items"]), live["titles"])
                    self.assertEqual(live["hidden"], 0)
                    self.assertEqual(live["imgs"], len(saved["items"]))
                    if saved.get("random"):
                        self.assertIn(saved["theme"], live["text"])
                    else:
                        self.assertNotIn(saved["theme"][:48], live["text"])
                    picked = saved["items"][0].get("picked") or ""
                    if picked:
                        self.assertNotIn(picked[:48], live["text"])
                    self.assertNotIn("Why it was picked", live["text"])
                    self.assertNotIn("Why it matters", live["text"])
                    self.assertNotIn("What you should know", live["text"])
                    self.assertTrue(any(live["favs"]), live["favs"])
                    for gid in RETIRED:
                        self.assertNotIn(gid, live["text"])
                    for tab in RETIRED_TABS:
                        self.assertNotIn(tab, live["text"])
                    self.assertEqual(live["titles"][0], "1. " + saved["items"][0]["title"])
                    self.assertEqual(live["titles"][-1], f"{len(saved['items'])}. " + saved["items"][-1]["title"])
            finally:
                server.shutdown()
                server.server_close()


def _color_png(path):
    from PIL import Image
    im = Image.new("RGB", (64, 36))
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            px[x, y] = ((x * 17 + y * 3) % 256, (x * 5 + y * 13) % 256, (x * y + 40) % 256)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)


def _write_catalog(repo, tabs):
    dest = os.path.join(repo, "static", "sheets")
    os.makedirs(dest, exist_ok=True)
    doc = {
        "books": [
            {"id": "bot1", "name": "Bot experiments", "spreadsheetId": BOT1},
            {"id": "bot2", "name": "bot experiments 2", "spreadsheetId": BOT2},
        ],
        "tabs": tabs,
    }
    with open(os.path.join(dest, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return doc


def _chart(gid, title, book="bot1"):
    return {
        "book": book, "tab": title, "gid": gid,
        "charts": [title], "chartList": [{"n": 1, "title": title}],
    }


def _write_cards(repo, date, rows):
    folder = os.path.join(repo, "static", "cotd", date)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "cards.json"), "w", encoding="utf-8") as f:
        json.dump({"date": date, "items": rows}, f)


class CotdRandomTest(unittest.TestCase):
    def test_pool_drops_exclusions_and_includes_tables(self):
        catalog = {
            "books": [
                {"id": "bot1", "name": "Bot experiments", "spreadsheetId": BOT1},
                {"id": "bot2", "name": "bot experiments 2", "spreadsheetId": BOT2},
            ],
            "tabs": [
                _chart(1547332232, "161 Nvidia P-S and P-E"),
                _chart(645072508, "69 Mark MSTR red dot tracker", book="bot2"),
                {"book": "bot1", "tab": "retired table", "gid": 333185169, "charts": [], "chartList": []},
                {"book": "bot1", "tab": "plain table", "gid": 810001, "charts": [], "chartList": []},
                {"book": "bot1", "tab": "two", "gid": 810002, "charts": ["Alpha", "Beta"],
                 "chartList": [{"n": 1, "title": "Alpha"}, {"n": 2, "title": "Beta"}]},
                _chart(171599627, "160 China vs Mexico vs Taiwan"),
            ],
        }
        # Catalog order must not change the pool. Reversed tabs draw the same way.
        forward = cotd_build.pool_items(catalog)
        backward = cotd_build.pool_items({**catalog, "tabs": list(reversed(catalog["tabs"]))})
        self.assertEqual(forward, backward)
        keys = {(it["book"], it["gid"], it["chart_n"], it["kind"]) for it in forward}
        self.assertNotIn(("bot1", 1547332232, 1, "chart"), keys)
        self.assertNotIn(("bot2", 645072508, 1, "chart"), keys)
        self.assertNotIn(("bot1", 333185169, 1, "table"), keys)
        self.assertIn(("bot1", 810001, 1, "table"), keys)
        self.assertIn(("bot1", 810002, 1, "chart"), keys)
        self.assertIn(("bot1", 810002, 2, "chart"), keys)
        self.assertIn(("bot1", 171599627, 1, "chart"), keys)
        table = next(it for it in forward if it["kind"] == "table")
        self.assertEqual(table["title"], "plain table")
        self.assertEqual(table["chart_n"], 1)
        self.assertEqual(table["sheet"], "Bot experiments")

    def test_draw_is_twenty_and_skips_the_previous_three_days(self):
        tabs = [_chart(700000 + i, "chart %02d" % i) for i in range(1, 18)]
        tabs.append({"book": "bot1", "tab": "the table", "gid": 700100, "charts": [], "chartList": []})
        tabs.append({"book": "bot1", "tab": "two", "gid": 700200, "charts": ["Alpha", "Beta"],
                     "chartList": [{"n": 1, "title": "Alpha"}, {"n": 2, "title": "Beta"}]})
        tabs.append(_chart(700300, "blocked whole tab"))
        tabs.append(_chart(700301, "blocked on day 3"))
        tabs.append(_chart(700304, "four days ago"))
        tabs.append(_chart(1547332232, "161 Nvidia P-S and P-E"))
        with tempfile.TemporaryDirectory(prefix="cotd-draw-") as repo:
            _write_catalog(repo, tabs)
            _write_cards(repo, "2099-07-04", [{"book": "bot1", "gid": 700200, "chart_n": 2, "tab": "two", "title": "Beta"}])
            _write_cards(repo, "2099-07-03", [{"book": "bot1", "gid": 700300, "tab": "blocked whole tab", "title": "blocked whole tab"}])
            _write_cards(repo, "2099-07-02", [{"book": "bot1", "gid": 700301, "chart_n": 1, "tab": "blocked on day 3", "title": "blocked on day 3"}])
            _write_cards(repo, "2099-07-01", [{"book": "bot1", "gid": 700304, "chart_n": 1, "tab": "four days ago", "title": "four days ago"}])
            drawn = cotd_build.select_edition(repo, "2099-07-05")
            got = {(it["gid"], it.get("chart_n"), it["kind"]) for it in drawn}
            self.assertEqual(len(drawn), 20, sorted(got))
            self.assertEqual(got, {
                *{(700000 + i, 1, "chart") for i in range(1, 18)},
                (700100, 1, "table"),
                (700200, 1, "chart"),
                (700304, 1, "chart"),
            })
            self.assertNotIn((700200, 2, "chart"), got)
            self.assertNotIn((700300, 1, "chart"), got)
            self.assertNotIn((700301, 1, "chart"), got)
            self.assertNotIn((1547332232, 1, "chart"), got)
            self.assertEqual(drawn, cotd_build.select_edition(repo, "2099-07-05"))
            self.assertNotEqual(drawn, cotd_build.select_edition(repo, "2099-07-05", seed="other-seed"))

    def test_fewer_than_twenty_publishes_tables_and_no_notes(self):
        tabs = [
            _chart(810001, "good chart"),
            _chart(810002, "blank chart"),
            {"book": "bot1", "tab": "thumb table", "gid": 810003, "charts": [], "chartList": []},
            {"book": "bot1", "tab": "bare table", "gid": 810004, "charts": [], "chartList": []},
            {"book": "bot1", "tab": "multi", "gid": 810005, "charts": ["A", "B"],
             "chartList": [{"n": 1, "title": "A"}, {"n": 2, "title": "B"}]},
            _chart(645072508, "69 Mark MSTR red dot tracker", book="bot2"),
        ]
        with tempfile.TemporaryDirectory(prefix="cotd-short-") as repo:
            _write_catalog(repo, tabs)
            _color_png(os.path.join(repo, "static", "sheets", "full", "bot1", "810001.png"))
            from PIL import Image
            blank = os.path.join(repo, "static", "sheets", "full", "bot1", "810002.png")
            os.makedirs(os.path.dirname(blank), exist_ok=True)
            Image.new("RGB", (32, 32), "white").save(blank)
            thumb = os.path.join(repo, "static", "sheets", "thumbs", "bot1", "810003.jpg")
            os.makedirs(os.path.dirname(thumb), exist_ok=True)
            _color_png(os.path.join(repo, "static", "sheets", "full", "bot1", "810005.png"))
            im = Image.new("RGB", (40, 24))
            px = im.load()
            for y in range(24):
                for x in range(40):
                    px[x, y] = ((x * 9) % 256, (y * 11) % 256, (x + y * 3) % 256)
            im.save(thumb, "JPEG")
            doc = cotd_build.publish_random(repo, "2099-08-01", 0)
            self.assertTrue(doc["random"])
            # The white PNG is a blank chart, so it is not drawn. The other five are.
            self.assertEqual(len(doc["items"]), 5, doc["items"])
            self.assertEqual(doc["theme"], "5 items drawn at random from the sheet library.")
            by_gid = {}
            for row in doc["items"]:
                self.assertNotIn("picked", row)
                self.assertNotIn("matters", row)
                self.assertNotIn("know", row)
                self.assertGreaterEqual(row["chart_n"], 1)
                gid = int(row["sheet_url"].rsplit("gid=", 1)[1])
                by_gid[(gid, row["chart_n"])] = row
            self.assertEqual(set(by_gid), {(810001, 1), (810003, 1), (810004, 1), (810005, 1), (810005, 2)})
            self.assertNotIn((810002, 1), by_gid)
            self.assertEqual(by_gid[(810001, 1)]["kind"], "chart")
            self.assertTrue(by_gid[(810001, 1)]["image"].endswith(".png"))
            self.assertEqual(by_gid[(810003, 1)]["kind"], "table")
            self.assertTrue(by_gid[(810003, 1)]["image"].endswith(".png"))
            self.assertEqual(by_gid[(810004, 1)]["kind"], "table")
            self.assertEqual(by_gid[(810004, 1)]["title"], "bare table")
            self.assertNotIn("image", by_gid[(810004, 1)])
            self.assertIn("image", by_gid[(810005, 1)])
            self.assertNotIn("image", by_gid[(810005, 2)])
            self.assertNotIn(645072508, {gid for gid, _n in by_gid})
            with open(os.path.join(repo, "static", "cotd", "2099-08-01", "cards.json"), encoding="utf-8") as f:
                cards = json.load(f)
            self.assertTrue(any(it.get("kind") == "table" and it.get("chart_n") == 1 for it in cards["items"]))
            again = cotd_build.publish_random(repo, "2099-08-01", 0)
            self.assertEqual([row["title"] for row in again["items"]], [row["title"] for row in doc["items"]])
            run = subprocess.run(
                [sys.executable, SCRIPT, "--repo", repo, "--date", "2099-08-01", "--keep-old", "0"],
                capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            with open(os.path.join(repo, "static", "cotd", "today.json"), encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["items"]), 5)
            self.assertNotIn("810002", json.dumps(saved))
            refused = subprocess.run(
                [sys.executable, SCRIPT, os.path.join(repo, "picks.json")],
                capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0, refused.stdout)
            self.assertRegex(cotd_build.today_et(), r"^\d{4}-\d{2}-\d{2}$")

    def test_order_is_a_shuffle_and_a_single_draw_can_hit_every_item(self):
        tabs = [_chart(600000 + i, "item %d" % i) for i in range(5)]
        with tempfile.TemporaryDirectory(prefix="cotd-shuffle-") as repo:
            _write_catalog(repo, tabs)
            orders = []
            from collections import Counter
            hits = Counter()
            for seed in range(300):
                got = cotd_build.select_edition(repo, "2099-09-01", seed=seed)
                orders.append(tuple(it["gid"] for it in got))
                self.assertEqual(set(orders[-1]), {600000 + i for i in range(5)})
                one = cotd_build.select_edition(repo, "2099-09-02", seed=seed, size=1)
                self.assertEqual(len(one), 1)
                hits[one[0]["gid"]] += 1
            self.assertGreater(len(set(orders)), 1)
            self.assertEqual(set(hits), {600000 + i for i in range(5)})
            for gid, n in hits.items():
                self.assertGreater(n, 20, (gid, n, hits))

    def test_live_catalog_draws_twenty_outside_the_last_three_editions(self):
        pool = cotd_build.pool_items(cotd_build.load_catalog_doc(ROOT))
        pairs = {(it["book"], it["gid"]) for it in pool}
        self.assertNotIn(("bot2", 645072508), pairs)
        for gid in RETIRED:
            self.assertNotIn(("bot1", int(gid)), pairs)
            self.assertNotIn(("bot2", int(gid)), pairs)
        self.assertTrue(any(it["kind"] == "table" for it in pool))
        self.assertGreaterEqual(len(pool), 20)
        items = cotd_build.select_edition(ROOT, "2026-11-11")
        self.assertEqual(len(items), 20)
        recent = cotd_build.recent_charts(ROOT, "2026-11-11")
        self.assertTrue(recent)
        for it in items:
            self.assertIsNone(cotd_build.repeated_recent(it, recent))
            self.assertFalse(cotd_build.pick_excluded(it))
            self.assertIn(it["kind"], ("chart", "table"))
            self.assertGreaterEqual(it["chart_n"], 1)
        self.assertEqual(items, cotd_build.select_edition(ROOT, "2026-11-11"))

    def test_unreadable_history_excludes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="cotd-badhist-") as repo:
            _write_catalog(repo, [
                _chart(42, "only chart"),
                {"book": "bot1", "tab": "only table", "gid": 43, "charts": [], "chartList": []},
            ])
            folder = os.path.join(repo, "static", "cotd", "2099-11-01")
            os.makedirs(folder)
            with open(os.path.join(folder, "cards.json"), "w", encoding="utf-8") as f:
                f.write("{")
            drawn = cotd_build.select_edition(repo, "2099-11-02")
            self.assertEqual({(it["gid"], it["kind"]) for it in drawn}, {(42, "chart"), (43, "table")})

    def test_empty_pool_publishes_an_empty_edition(self):
        with tempfile.TemporaryDirectory(prefix="cotd-empty-") as repo:
            _write_catalog(repo, [_chart(1547332232, "161 Nvidia P-S and P-E")])
            doc = cotd_build.publish_random(repo, "2099-10-01", 0)
            self.assertEqual(doc["items"], [])
            self.assertEqual(doc["theme"], "0 items drawn at random from the sheet library.")
            self.assertTrue(doc["random"])


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
    kinds: cards.map(c => c.getAttribute('data-kind')),
    favs: cards.map(c => { const b = c.querySelector('button.fav'); return b ? (b.dataset.fav || '') : ''; }),
    imgs: root.querySelectorAll('img').length,
    tables: root.querySelectorAll('[data-kind=table]').length,
    previews: root.querySelectorAll('.tablepreview').length,
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
