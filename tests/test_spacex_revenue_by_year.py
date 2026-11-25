"""96 SpaceX revenue by year must show Steve's Python render.

The bot experiments 2 tab (gid 818663135) has a native column chart, but
the library PNG is Steve's Python render (purple bars, cream background,
shaded estimates band, no watermark). Pin the tab so a truncated
spreadsheets.get cannot drop it, list it when "Has a chart" is on, keep
Monday's importer from overwriting the Python PNG with the native-chart
export, and leave Open in Sheets on because the tab is real (not
sheetless).
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
GID = 818663135
CHART_ID = 354143861
TAB = "96 SpaceX revenue by year"
CHART = "SpaceX revenue by year"
FULL_SHA = "c85b88b16a8ba58f8b89246ac9f9d0cb910620c998237b6e29be398951712c2b"
FULL_SIZE = 65677
THUMB_SIZE = 6959
THUMB_SHA = "6b915dc6b59e55fc2e072c02f9d9d17ab6471dfa8635c35a7789f03e00734b85"
YEARS = [str(y) for y in range(2023, 2029)]
ACTUAL_2025 = 18.674
ESTIMATE_2026 = 49.06
ESTIMATE_2028 = 335.81


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


class SpacexRevenueByYearTest(unittest.TestCase):
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
        self.assertIsNotNone(hit, "catalog.json is missing 96 SpaceX revenue by year")
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
        with open(full, "rb") as f:
            data = f.read()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(len(data), FULL_SIZE)
        self.assertEqual(hashlib.sha256(data).hexdigest(), FULL_SHA)
        with open(thumb, "rb") as f:
            tdata = f.read()
        self.assertEqual(tdata[:3], b"\xff\xd8\xff")
        self.assertLessEqual(len(tdata), 20_000)
        self.assertEqual(len(tdata), THUMB_SIZE)
        self.assertEqual(hashlib.sha256(tdata).hexdigest(), THUMB_SHA)
        self.assertFalse(cotd_build.file_is_blank(full))
        keys = {(t.get("book"), t.get("gid")) for t in cat["tabs"]}
        self.assertEqual(len(keys), len(cat["tabs"]))
        self.assertIn(("bot2", GID), keys)
        self.assertEqual(len(cat["tabs"]), 394)

    def test_data_json_covers_2023_through_2028(self):
        path = os.path.join(ROOT, "static", "sheets", "data", "bot2", "%d.json" % GID)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc.get("book"), "bot2")
        self.assertEqual(doc.get("gid"), GID)
        self.assertEqual(doc.get("n"), 1)
        self.assertEqual(doc.get("chartId"), CHART_ID)
        self.assertEqual(doc.get("tab"), TAB)
        self.assertEqual(doc.get("title"), CHART)
        self.assertTrue(doc.get("supported"))
        self.assertEqual(doc.get("type"), "column")
        cats = doc.get("categories") or []
        series = doc.get("series") or []
        self.assertEqual(cats, YEARS)
        self.assertEqual(len(series), 2)
        by_name = {s.get("name"): s for s in series}
        self.assertIn("Actual ($B)", by_name)
        self.assertIn("Estimate ($B)", by_name)
        actual = by_name["Actual ($B)"].get("data") or []
        estimate = by_name["Estimate ($B)"].get("data") or []
        self.assertEqual(len(actual), len(cats))
        self.assertEqual(len(estimate), len(cats))
        self.assertEqual(actual[YEARS.index("2025")], ACTUAL_2025)
        self.assertIsNone(actual[YEARS.index("2026")])
        self.assertIsNone(estimate[YEARS.index("2025")])
        self.assertEqual(estimate[YEARS.index("2026")], ESTIMATE_2026)
        self.assertEqual(estimate[YEARS.index("2028")], ESTIMATE_2028)
        with open(os.path.join(ROOT, "static", "sheets", "data", "index.json"), encoding="utf-8") as f:
            index = json.load(f)
        hit = next((c for c in index["charts"] if c.get("book") == "bot2" and c.get("gid") == GID and c.get("n") == 1), None)
        self.assertIsNotNone(hit, "data index is missing 96 SpaceX revenue by year")
        self.assertEqual(hit.get("type"), "column")
        self.assertEqual(hit.get("title"), CHART)
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("file"), "sheets/data/bot2/%d.json" % GID)

    def test_generator_keeps_the_native_chart_from_the_dump(self):
        book, rows, hidden, excluded = sheets_catalog.one({
            "spreadsheetId": BOT2,
            "properties": {"title": "bot experiments 2"},
            "sheets": [
                _sheet("Other", 1, [{"spec": {"title": "A chart"}}], index=0),
                _sheet(TAB, GID, [
                    {"spec": {"title": CHART}, "chartId": CHART_ID},
                ], index=1),
            ],
        })
        self.assertEqual(book["id"], "bot2")
        self.assertEqual(hidden, 0)
        self.assertEqual(excluded, 0)
        hit = next(t for t in rows if t["gid"] == GID)
        self.assertEqual(hit["tab"], TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])

    def test_rebuild_keeps_the_tab_when_the_dump_omits_it(self):
        repo = tempfile.mkdtemp(prefix="spacex-revenue-pin-")
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
        repo = tempfile.mkdtemp(prefix="spacex-revenue-fallback-")
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

    def test_importer_skips_the_native_chart_export(self):
        self.assertIn(("bot2", GID), import_full_pngs.KEEP_MANUAL_PNGS)
        tabs = [{"book": "bot2", "gid": GID, "chartList": [{"n": 1, "title": CHART}]}]
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot2", GID, 1))
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot2", str(GID), 1))
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
        hits = [r for r in rows if r.get("title") == CHART or "gid=%d" % GID in str(r.get("sheet_url") or "")]
        if not hits:
            return
        self.assertEqual(len(hits), 1, hits)
        image = hits[0].get("image")
        self.assertTrue(str(image).startswith("cotd/%s/" % today["date"]), image)
        src = os.path.join(ROOT, "static", image)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot2", "%d.png" % GID)
        self.assertTrue(os.path.isfile(src))
        fd, expected = tempfile.mkstemp(prefix="spacex-revenue-cotd-", suffix=".png")
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
        self.assertIn("Number(t.gid)===818663135", html)
        self.assertIn(
            "(String(t.book)==='bot2'&&(Number(t.gid)===855902063||Number(t.gid)===978808662||Number(t.gid)===73869283||Number(t.gid)===572335799||Number(t.gid)===818663135))",
            html,
        )
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        self.assertIn("if(tab&&(tab.sheetless||Number(tab.gid)>=2147483648))return '';", html)
        tab = {"book": "bot2", "tab": TAB, "gid": GID, "charts": [CHART], "chartList": [{"n": 1, "title": CHART}]}
        self.assertFalse(bool(tab.get("sheetless")))
        self.assertLess(tab["gid"], 2147483648)
        blob = (tab["tab"] + " " + CHART + " bot experiments 2").lower()
        self.assertIn("spacex", blob)
        self.assertIn("revenue", blob)
        self.assertIn("year", blob)


if __name__ == "__main__":
    unittest.main()
