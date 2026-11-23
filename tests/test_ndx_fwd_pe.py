"""Nasdaq-100 Forward P/E must show Steve's Python render.

The My Bot's Sheets tab (gid 922821390) has a native line chart, but it is
the default-style Sheets export, so the library PNG is Steve's Python render
(black background, right-hand axis with x suffixes, current-level dashed
line). Pin the tab so a truncated spreadsheets.get cannot drop it, list it
when "Has a chart" is on, keep Monday's importer from overwriting the Python
PNG with the native-chart export, and leave Open in Sheets on because the
tab is real (not sheetless).
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

BOT3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
GID = 922821390
CHART_ID = 1658054427
TAB = "Nasdaq-100 Forward PE"
CHART = "Nasdaq-100 Forward P/E Ratio"
FULL_SHA = "2f04111f4176b23e27714a8a015218f30af8cbed3a3c61f98b62dea1c5c1d344"
FULL_SIZE = 142218
THUMB_SIZE = 8810
LAST_DATE = "2026-11-20"
LAST_PE = 16.42


def _dump(path, sheets, spreadsheet_id=BOT3, title="My Bot's Sheets"):
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


class NdxFwdPeTest(unittest.TestCase):
    def test_tab_is_pinned_and_not_sheetless_or_excluded(self):
        self.assertIn(("bot3", GID), sheets_catalog.PINNED_TABS)
        self.assertEqual(sheets_catalog.PINNED_TABS[("bot3", GID)], TAB)
        self.assertNotIn(("bot3", GID), sheets_catalog.SHEETLESS_TABS)
        self.assertLess(GID, sheets_catalog.SYNTHETIC_GID_MIN)
        self.assertFalse(sheets_catalog.tab_excluded("bot3", GID))
        self.assertNotIn(("bot3", GID), retired_charts.library_pairs())
        self.assertNotIn(("bot3", GID), retired_charts.cotd_pairs())
        self.assertNotIn(("bot3", GID), cotd_build.COTD_EXCLUDED)
        for key in sheets_catalog.PINNED_TABS:
            self.assertNotIn(key, sheets_catalog.EXCLUDED_TABS)

    def test_committed_catalog_includes_the_tab(self):
        cat = _catalog()
        hit = next((t for t in cat["tabs"] if t.get("book") == "bot3" and t.get("gid") == GID), None)
        self.assertIsNotNone(hit, "catalog.json is missing Nasdaq-100 Forward PE")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
        self.assertTrue(hit.get("pin"))
        self.assertFalse(hit.get("sheetless"))
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot3/%d.jpg" % GID)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID)
        thumb = os.path.join(ROOT, "static", "sheets", "thumbs", "bot3", "%d.jpg" % GID)
        self.assertTrue(os.path.isfile(full))
        self.assertTrue(os.path.isfile(thumb))
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
        self.assertIn(("bot3", GID), keys)

    def test_data_json_runs_through_nov_20(self):
        path = os.path.join(ROOT, "static", "sheets", "data", "bot3", "%d.json" % GID)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc.get("book"), "bot3")
        self.assertEqual(doc.get("gid"), GID)
        self.assertEqual(doc.get("chartId"), CHART_ID)
        self.assertEqual(doc.get("title"), CHART)
        self.assertEqual(doc.get("tab"), TAB)
        self.assertEqual(doc.get("type"), "line")
        cats = doc.get("categories") or []
        series = doc.get("series") or []
        self.assertGreaterEqual(len(series), 2)
        fwd = series[0].get("data") or []
        level = series[1].get("data") or []
        self.assertEqual(series[0].get("name"), "Forward P/E")
        self.assertEqual(series[1].get("name"), "Current level")
        self.assertEqual(len(cats), len(fwd))
        self.assertEqual(len(cats), len(level))
        self.assertGreaterEqual(len(cats), 394)
        self.assertEqual(cats[0], "2020-06-05")
        self.assertEqual(cats[-1], LAST_DATE)
        self.assertEqual(fwd[-1], LAST_PE)
        self.assertEqual(fwd[-5:], [18.19, 18.08, 18.25, 17.86, 16.42])
        self.assertEqual(set(level), {LAST_PE})
        self.assertIn("2026-11-13", cats)
        self.assertIn("2026-11-16", cats)

    def test_generator_keeps_the_native_chart_from_the_dump(self):
        book, rows, hidden, excluded = sheets_catalog.one({
            "spreadsheetId": BOT3,
            "properties": {"title": "My Bot's Sheets"},
            "sheets": [
                _sheet("Other", 1, [{"spec": {"title": "A chart"}}], index=0),
                _sheet(TAB, GID, [{"spec": {"title": CHART}, "chartId": CHART_ID}], index=1),
            ],
        })
        self.assertEqual(book["id"], "bot3")
        self.assertEqual(hidden, 0)
        self.assertEqual(excluded, 0)
        hit = next(t for t in rows if t["gid"] == GID)
        self.assertEqual(hit["tab"], TAB)
        self.assertEqual(hit["charts"], [CHART])
        self.assertEqual(hit["chartList"], [{"n": 1, "title": CHART}])

    def test_rebuild_keeps_the_tab_when_the_dump_omits_it(self):
        repo = tempfile.mkdtemp(prefix="ndx-fwd-pe-pin-")
        try:
            cat_dir = os.path.join(repo, "static", "sheets")
            os.makedirs(os.path.join(repo, "static", "sheets", "thumbs", "bot3"))
            thumb_rel = "sheets/thumbs/bot3/%d.jpg" % GID
            with open(os.path.join(repo, "static", thumb_rel), "wb") as f:
                f.write(b"thumb")
            prev = {
                "books": [{"id": "bot3", "name": "My Bot's Sheets", "spreadsheetId": BOT3}],
                "tabs": [{
                    "book": "bot3", "tab": TAB, "gid": GID,
                    "charts": [CHART], "chartList": [{"n": 1, "title": CHART}],
                    "pin": True, "thumb": thumb_rel,
                }],
            }
            with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump(prev, f)
            dump = _dump(os.path.join(repo, "bot3.json"), [
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
        repo = tempfile.mkdtemp(prefix="ndx-fwd-pe-fallback-")
        try:
            os.makedirs(os.path.join(repo, "static", "sheets"))
            dump = _dump(os.path.join(repo, "bot3.json"), [
                _sheet("Solo", 42, [{"spec": {"title": "Only chart"}}], index=0),
            ])
            built, warns = sheets_catalog.build_catalog([dump], repo, allow_partial=True)
            hit = next(t for t in built["tabs"] if t["gid"] == GID)
            self.assertEqual(hit["book"], "bot3")
            self.assertEqual(hit["tab"], TAB)
            self.assertEqual(hit["charts"], [])
            self.assertEqual(hit["chartList"], [])
            self.assertTrue(hit.get("pin"))
            self.assertFalse(hit.get("sheetless"))
            self.assertTrue(any(str(GID) in w and "added the pinned table tab" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_importer_skips_the_native_chart_export(self):
        self.assertIn(("bot3", GID), import_full_pngs.KEEP_MANUAL_PNGS)
        tabs = [{"book": "bot3", "gid": GID, "chartList": [{"n": 1, "title": CHART}]}]
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot3", GID, 1))
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot3", str(GID), 1))
        other = [{"book": "bot3", "gid": 1618426207, "chartList": [{"n": 1, "title": "ASST 30-Day Average Daily Trading Volume"}]}]
        self.assertFalse(import_full_pngs.skip_chart_image(other, "bot3", 1618426207, 1))

    def test_charts_of_the_day_pool_includes_the_python_png(self):
        cat = _catalog()
        pool = cotd_build.pool_items(cat, ROOT)
        hits = [it for it in pool if it["book"] == "bot3" and it["gid"] == GID]
        self.assertEqual(len(hits), 1, hits)
        item = hits[0]
        self.assertEqual(item["chart_n"], 1)
        self.assertEqual(item["kind"], "chart")
        self.assertEqual(item["title"], CHART)
        self.assertEqual(item["spreadsheetId"], BOT3)
        self.assertIsNone(cotd_build.explain_ineligible("bot3", GID, None, cat, ROOT))
        self.assertEqual(
            cotd_build.library_preview(ROOT, "bot3", GID, 1),
            os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID),
        )

    def test_today_card_uses_the_python_png_when_present(self):
        today_path = os.path.join(ROOT, "static", "cotd", "today.json")
        with open(today_path, encoding="utf-8") as f:
            today = json.load(f)
        rows = list(today.get("picks") or []) + list(today.get("items") or [])
        hits = [r for r in rows if r.get("title") == CHART or "gid=922821390" in str(r.get("sheet_url") or "")]
        if not hits:
            return
        self.assertEqual(len(hits), 1, hits)
        image = hits[0].get("image")
        self.assertTrue(str(image).startswith("cotd/%s/" % today["date"]), image)
        src = os.path.join(ROOT, "static", image)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID)
        self.assertTrue(os.path.isfile(src))
        fd, expected = tempfile.mkstemp(prefix="ndx-fwd-pe-cotd-", suffix=".png")
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
        self.assertIn("Number(t.gid)===922821390", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        self.assertIn("if(tab&&(tab.sheetless||Number(tab.gid)>=2147483648))return '';", html)
        tab = {"book": "bot3", "tab": TAB, "gid": GID, "charts": [CHART], "chartList": [{"n": 1, "title": CHART}]}
        self.assertFalse(bool(tab.get("sheetless")))
        self.assertLess(tab["gid"], 2147483648)
        blob = (tab["tab"] + " " + CHART + " My Bot's Sheets").lower()
        self.assertIn("nasdaq-100", blob)
        self.assertIn("forward p/e", blob)


if __name__ == "__main__":
    unittest.main()
