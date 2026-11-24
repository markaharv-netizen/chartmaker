"""93 BTC treasury buys vs sales must show Steve's 3-month net-flows render.

The bot experiments 2 tab (gid 73869283) has two native Sheets charts
(bought vs sold, and the 4-week rolling ratio), but those exports are
default-style, so the library PNG is Steve's Python render of weekly net
BTC flows over the last 3 months (green/red bars, title only, no
watermark). Pin the tab so a truncated spreadsheets.get cannot drop it,
list it when "Has a chart" is on, keep Monday's importer from overwriting
the Python PNG with either native-chart export, and leave Open in Sheets
on because the tab is real (not sheetless).
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cotd_build
import import_full_pngs
import retired_charts
import sheets_catalog

BOT2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
GID = 73869283
CHART_ID = 1261407373
TAB = "93 BTC treasury buys vs sales"
CHART = "Bitcoin treasury companies: weekly net BTC flows (last 3 months)"
FULL_SHA = "907e3b7fede66ce1f7ee43a212a450cf8d7585d0a34bb73e84e7faf91df9dce0"
FULL_SIZE = 95340
THUMB_SIZE = 9291
LAST_WEEK = "2026-11-22"


def _dump(path, sheets, spreadsheet_id=BOT2, title="bot experiments 2"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "spreadsheetId": spreadsheet_id,
            "properties": {"title": title},
            "sheets": sheets,
        }, f)
    return path


def _sheet(title, gid, charts=None, hidden=False, index=0):
    props = {"title": title, "sheetId": gid, "index": index}
    if hidden:
        props["hidden"] = True
    return {"properties": props, "charts": list(charts or [])}


def _catalog():
    with open(os.path.join(ROOT, "static", "sheets", "catalog.json"), encoding="utf-8") as f:
        return json.load(f)


class BtcTreasuryNet3mTest(unittest.TestCase):
    def test_tab_is_pinned_and_not_sheetless_or_excluded(self):
        self.assertIn(("bot2", GID), sheets_catalog.PINNED_TABS)
        self.assertEqual(sheets_catalog.PINNED_TABS[("bot2", GID)], TAB)
        self.assertNotIn(("bot2", GID), sheets_catalog.SHEETLESS_TABS)
        self.assertLess(GID, sheets_catalog.SYNTHETIC_GID_MIN)
        self.assertFalse(sheets_catalog.tab_excluded("bot2", GID))
        self.assertNotIn(("bot2", GID), retired_charts.library_pairs())
        self.assertNotIn(("bot2", GID), retired_charts.cotd_pairs())
        self.assertNotIn(("bot2", GID), cotd_build.COTD_EXCLUDED)
        for key in sheets_catalog.PINNED_TABS:
            self.assertNotIn(key, sheets_catalog.EXCLUDED_TABS)

    def test_committed_catalog_includes_the_tab(self):
        cat = _catalog()
        hit = next((t for t in cat["tabs"] if t.get("book") == "bot2" and t.get("gid") == GID), None)
        self.assertIsNotNone(hit, "catalog.json is missing 93 BTC treasury buys vs sales")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
        self.assertTrue(hit.get("pin"))
        self.assertFalse(hit.get("sheetless"))
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot2/%d.jpg" % GID)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot2", "%d.png" % GID)
        thumb = os.path.join(ROOT, "static", "sheets", "thumbs", "bot2", "%d.jpg" % GID)
        self.assertTrue(os.path.isfile(full))
        self.assertTrue(os.path.isfile(thumb))
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "static", "sheets", "full", "bot2", "%d_2.png" % GID)))
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "static", "sheets", "thumbs", "bot2", "%d_2.jpg" % GID)))
        with open(full, "rb") as f:
            data = f.read()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(len(data), FULL_SIZE)
        self.assertEqual(hashlib.sha256(data).hexdigest(), FULL_SHA)
        self.assertLessEqual(os.path.getsize(thumb), 20_000)
        self.assertEqual(os.path.getsize(thumb), THUMB_SIZE)
        self.assertFalse(cotd_build.file_is_blank(full))
        keys = {(t.get("book"), t.get("gid")) for t in cat["tabs"]}
        self.assertEqual(len(keys), len(cat["tabs"]))
        self.assertIn(("bot2", GID), keys)
        self.assertEqual(len(cat["tabs"]), 408)

    def test_data_json_runs_through_nov_22(self):
        path = os.path.join(ROOT, "static", "sheets", "data", "bot2", "%d.json" % GID)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc.get("book"), "bot2")
        self.assertEqual(doc.get("gid"), GID)
        self.assertEqual(doc.get("n"), 1)
        self.assertEqual(doc.get("chartId"), CHART_ID)
        self.assertEqual(doc.get("tab"), TAB)
        self.assertTrue(doc.get("supported"))
        self.assertEqual(doc.get("type"), "column")
        cats = doc.get("categories") or []
        series = doc.get("series") or []
        self.assertGreaterEqual(len(cats), 14)
        self.assertEqual(cats[-1], LAST_WEEK)
        self.assertIn("2026-11-08", cats)
        self.assertIn("2026-11-15", cats)
        self.assertGreaterEqual(len(series), 1)
        for s in series:
            vals = s.get("data") or s.get("values") or []
            self.assertEqual(len(vals), len(cats))
        # The second native chart's data bundle stays in place; there is no _2 PNG.
        path2 = os.path.join(ROOT, "static", "sheets", "data", "bot2", "%d_2.json" % GID)
        self.assertTrue(os.path.isfile(path2))
        with open(path2, encoding="utf-8") as f:
            doc2 = json.load(f)
        self.assertEqual(doc2.get("book"), "bot2")
        self.assertEqual(doc2.get("gid"), GID)
        self.assertEqual(doc2.get("n"), 2)
        self.assertEqual(doc2.get("tab"), TAB)
        self.assertTrue(doc2.get("supported"))
        cats2 = doc2.get("categories") or []
        self.assertEqual(cats2[-1], LAST_WEEK)

    def test_generator_keeps_the_native_charts_from_the_dump(self):
        book, rows, hidden, excluded = sheets_catalog.one({
            "spreadsheetId": BOT2,
            "properties": {"title": "bot experiments 2"},
            "sheets": [
                _sheet("Other", 1, [{"spec": {"title": "A chart"}}], index=0),
                _sheet(TAB, GID, [
                    {"spec": {"title": "Bitcoin treasury companies: weekly BTC bought vs sold, Nov 2023 – Nov 2026"}, "chartId": CHART_ID},
                    {"spec": {"title": "4-week rolling ratio: BTC bought ÷ BTC sold (blank = no sales)"}, "chartId": 1894470086},
                ], index=1),
            ],
        })
        self.assertEqual(book["id"], "bot2")
        self.assertEqual(hidden, 0)
        self.assertEqual(excluded, 0)
        hit = next(t for t in rows if t["gid"] == GID)
        self.assertEqual(hit["tab"], TAB)
        # A title-only dump of a 2-chart tab is unpositioned and must not
        # invent API-order numbers. Pinning keeps the committed catalog row.
        self.assertTrue(hit.get("_unpositioned"))

    def test_rebuild_keeps_the_tab_when_the_dump_omits_it(self):
        repo = tempfile.mkdtemp(prefix="btc-treasury-net-3m-pin-")
        try:
            cat_dir = os.path.join(repo, "static", "sheets")
            os.makedirs(os.path.join(repo, "static", "sheets", "thumbs", "bot2"))
            thumb_rel = "sheets/thumbs/bot2/%d.jpg" % GID
            with open(os.path.join(repo, "static", thumb_rel), "wb") as f:
                f.write(b"thumb")
            prev = {
                "books": [{"id": "bot2", "name": "bot experiments 2", "spreadsheetId": BOT2}],
                "tabs": [{
                    "book": "bot2", "tab": TAB, "gid": GID,
                    "charts": [CHART], "chartList": [{"n": 1, "title": CHART}],
                    "pin": True, "thumb": thumb_rel,
                }],
            }
            with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump(prev, f)
            dump = _dump(os.path.join(repo, "bot2.json"), [
                _sheet("Solo", 42, [{"spec": {"title": "Only chart"}}], index=0),
            ])
            built, warns = sheets_catalog.build_catalog([dump], repo, allow_partial=True)
            by_gid = {t["gid"]: t for t in built["tabs"]}
            self.assertIn(42, by_gid)
            self.assertIn(GID, by_gid)
            self.assertEqual(by_gid[GID]["tab"], TAB)
            self.assertEqual(by_gid[GID].get("charts"), [CHART])
            self.assertEqual(by_gid[GID].get("chartList"), [{"n": 1, "title": CHART}])
            self.assertEqual(by_gid[GID].get("thumb"), thumb_rel)
            self.assertTrue(by_gid[GID].get("pin"))
            self.assertFalse(by_gid[GID].get("sheetless"))
            self.assertTrue(any(str(GID) in w and "kept the existing catalog entry" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_rebuild_adds_a_fallback_row_when_no_prior_catalog(self):
        repo = tempfile.mkdtemp(prefix="btc-treasury-net-3m-fallback-")
        try:
            os.makedirs(os.path.join(repo, "static", "sheets"))
            dump = _dump(os.path.join(repo, "bot2.json"), [
                _sheet("Solo", 42, [{"spec": {"title": "Only chart"}}], index=0),
            ])
            built, warns = sheets_catalog.build_catalog([dump], repo, allow_partial=True)
            hit = next(t for t in built["tabs"] if t["gid"] == GID)
            self.assertEqual(hit["book"], "bot2")
            self.assertEqual(hit["tab"], TAB)
            self.assertEqual(hit["charts"], [])
            self.assertEqual(hit["chartList"], [])
            self.assertTrue(hit.get("pin"))
            self.assertFalse(hit.get("sheetless"))
            self.assertTrue(any(str(GID) in w and "added the pinned table tab" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_unpositioned_dump_keeps_the_python_catalog_row(self):
        repo = tempfile.mkdtemp(prefix="btc-treasury-net-3m-unpos-")
        try:
            cat_dir = os.path.join(repo, "static", "sheets")
            os.makedirs(os.path.join(repo, "static", "sheets", "thumbs", "bot2"))
            thumb_rel = "sheets/thumbs/bot2/%d.jpg" % GID
            with open(os.path.join(repo, "static", thumb_rel), "wb") as f:
                f.write(b"thumb")
            prev = {
                "books": [{"id": "bot2", "name": "bot experiments 2", "spreadsheetId": BOT2}],
                "tabs": [{
                    "book": "bot2", "tab": TAB, "gid": GID,
                    "charts": [CHART], "chartList": [{"n": 1, "title": CHART}],
                    "pin": True, "thumb": thumb_rel,
                }],
            }
            with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump(prev, f)
            dump = _dump(os.path.join(repo, "bot2.json"), [
                _sheet(TAB, GID, [
                    {"spec": {"title": "Bitcoin treasury companies: weekly BTC bought vs sold, Nov 2023 – Nov 2026"}, "chartId": CHART_ID},
                    {"spec": {"title": "4-week rolling ratio: BTC bought ÷ BTC sold (blank = no sales)"}, "chartId": 1894470086},
                ], index=0),
            ])
            built, warns = sheets_catalog.build_catalog([dump], repo, allow_partial=True)
            hit = next(t for t in built["tabs"] if t["gid"] == GID)
            self.assertEqual(hit.get("charts"), [CHART])
            self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
            self.assertEqual(hit.get("thumb"), thumb_rel)
            self.assertTrue(hit.get("pin"))
            self.assertFalse(hit.get("sheetless"))
            self.assertTrue(any(str(GID) in w and "kept the existing catalog entry" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_importer_skips_the_native_chart_exports(self):
        self.assertIn(("bot2", GID), import_full_pngs.KEEP_MANUAL_PNGS)
        tabs = [{"book": "bot2", "gid": GID, "chartList": [
            {"n": 1, "title": CHART},
            {"n": 2, "title": "4-week rolling ratio: BTC bought ÷ BTC sold (blank = no sales)"},
        ]}]
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot2", GID, 1))
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot2", str(GID), 1))
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot2", GID, 2))
        other = [{"book": "bot2", "gid": 956514556, "chartList": [{"n": 1, "title": "Bitcoin held: public companies vs US spot ETFs"}]}]
        self.assertFalse(import_full_pngs.skip_chart_image(other, "bot2", 956514556, 1))

    def test_charts_of_the_day_pool_includes_the_python_png(self):
        cat = _catalog()
        pool = cotd_build.pool_items(cat, ROOT)
        hits = [it for it in pool if it["book"] == "bot2" and it["gid"] == GID]
        self.assertEqual(len(hits), 1, hits)
        item = hits[0]
        self.assertEqual(item["chart_n"], 1)
        self.assertEqual(item["kind"], "chart")
        self.assertEqual(item["title"], CHART)
        self.assertEqual(item["spreadsheetId"], BOT2)
        self.assertIsNone(cotd_build.explain_ineligible("bot2", GID, None, cat, ROOT))
        self.assertEqual(
            cotd_build.library_preview(ROOT, "bot2", GID, 1),
            os.path.join(ROOT, "static", "sheets", "full", "bot2", "%d.png" % GID),
        )

    def test_today_card_uses_the_python_png_when_present(self):
        today_path = os.path.join(ROOT, "static", "cotd", "today.json")
        with open(today_path, encoding="utf-8") as f:
            today = json.load(f)
        rows = list(today.get("picks") or []) + list(today.get("items") or [])
        hits = [r for r in rows if r.get("title") == CHART or "gid=73869283" in str(r.get("sheet_url") or "")]
        if not hits:
            return
        self.assertEqual(len(hits), 1, hits)
        image = hits[0].get("image")
        self.assertTrue(str(image).startswith("cotd/%s/" % today["date"]), image)
        src = os.path.join(ROOT, "static", image)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot2", "%d.png" % GID)
        self.assertTrue(os.path.isfile(src))
        fd, expected = tempfile.mkstemp(prefix="btc-treasury-net-3m-cotd-", suffix=".png")
        os.close(fd)
        try:
            cotd_build.fit(full, expected)
            with open(src, "rb") as f, open(expected, "rb") as g:
                self.assertEqual(f.read(), g.read())
        finally:
            try:
                os.remove(expected)
            except OSError:
                pass

    def test_sheets_search_finds_the_tab_and_keeps_open_in_sheets(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("function sheetListedWithCharts(t){", html)
        self.assertIn("Number(t.gid)===73869283", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        self.assertIn("if(tab&&(tab.sheetless||Number(tab.gid)>=2147483648))return '';", html)
        tab = {"book": "bot2", "tab": TAB, "gid": GID, "charts": [CHART], "chartList": [{"n": 1, "title": CHART}]}
        self.assertFalse(bool(tab.get("sheetless")))
        self.assertLess(tab["gid"], 2147483648)
        blob = (tab["tab"] + " " + CHART + " bot experiments 2").lower()
        self.assertIn("btc", blob)
        self.assertIn("treasury", blob)
        self.assertIn("net btc flows", blob)


if __name__ == "__main__":
    unittest.main()
