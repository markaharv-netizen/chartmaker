"""Waymo vs Tesla Robotaxi Fleets must show in the Sheets library.

The My Bot's Sheets tab (gid 197035552) has a native Sheets chart, but the
library PNG is Steve's weekly Python render. Pin the tab so a truncated
spreadsheets.get cannot drop it, list it when "Has a chart" is on, and keep
Monday's importer from overwriting the Python PNG with the native-chart export.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import import_full_pngs
import sheets_catalog

BOT3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
GID = 197035552
TAB = "Waymo vs Tesla Robotaxi Fleets"
CHART = "Waymo vs Tesla: Fully Autonomous Taxi Fleets"


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


class WaymoTeslaFleetsTest(unittest.TestCase):
    def test_tab_is_pinned_and_not_excluded(self):
        self.assertIn(("bot3", GID), sheets_catalog.PINNED_TABS)
        self.assertEqual(sheets_catalog.PINNED_TABS[("bot3", GID)], TAB)
        self.assertFalse(sheets_catalog.tab_excluded("bot3", GID))
        for key in sheets_catalog.PINNED_TABS:
            self.assertNotIn(key, sheets_catalog.EXCLUDED_TABS)

    def test_committed_catalog_includes_the_tab(self):
        path = os.path.join(ROOT, "static", "sheets", "catalog.json")
        with open(path, encoding="utf-8") as f:
            cat = json.load(f)
        hit = next((t for t in cat["tabs"] if t.get("book") == "bot3" and t.get("gid") == GID), None)
        self.assertIsNotNone(hit, "catalog.json is missing Waymo vs Tesla Robotaxi Fleets")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot3/%d.jpg" % GID)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID)))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "sheets", "thumbs", "bot3", "%d.jpg" % GID)))
        # Count-free on purpose: retirements and new tabs change the total, and
        # sheets_catalog.self_test pins the exact count. Here: no duplicate tabs.
        keys = {(t.get("book"), t.get("gid")) for t in cat["tabs"]}
        self.assertEqual(len(keys), len(cat["tabs"]))
        self.assertIn(("bot3", GID), keys)

    def test_generator_keeps_the_native_chart_from_the_dump(self):
        book, rows, hidden, excluded = sheets_catalog.one({
            "spreadsheetId": BOT3,
            "properties": {"title": "My Bot's Sheets"},
            "sheets": [
                _sheet("Other", 1, [{"spec": {"title": "A chart"}}], index=0),
                _sheet(TAB, GID, [{"spec": {"title": CHART}, "chartId": 1379339672}], index=1),
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
        repo = tempfile.mkdtemp(prefix="waymo-pin-")
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
                    "thumb": thumb_rel,
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
            self.assertTrue(any(str(GID) in w and "kept the existing catalog entry" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_rebuild_adds_a_fallback_row_when_no_prior_catalog(self):
        repo = tempfile.mkdtemp(prefix="waymo-fallback-")
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
            self.assertTrue(any(str(GID) in w and "added the pinned table tab" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_importer_skips_the_native_chart_export(self):
        self.assertIn(("bot3", GID), import_full_pngs.KEEP_MANUAL_PNGS)
        tabs = [{"book": "bot3", "gid": GID, "chartList": [{"n": 1, "title": CHART}]}]
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot3", GID, 1))
        # Other tabs are unchanged: a listed titled chart is still imported.
        other = [{"book": "bot3", "gid": 1618426207, "chartList": [{"n": 1, "title": "ASST 30-Day Average Daily Trading Volume"}]}]
        self.assertFalse(import_full_pngs.skip_chart_image(other, "bot3", 1618426207, 1))

    def test_sheets_search_finds_the_tab_with_has_a_chart_on_or_off(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("function sheetListedWithCharts(t){", html)
        self.assertIn("Number(t.gid)===197035552", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        tab = {"book": "bot3", "tab": TAB, "gid": GID, "charts": [CHART], "chartList": [{"n": 1, "title": CHART}]}
        blob = (tab["tab"] + " " + CHART + " My Bot's Sheets").lower()
        self.assertIn("waymo", blob)
        self.assertIn("tesla", blob)
        self.assertIn("robotaxi", blob)


if __name__ == "__main__":
    unittest.main()
