"""Options OI % of Market Cap must show in the Sheets library.

The My Bot's Sheets tab (gid 19854586) has a native column chart, but the
library PNG is Steve's monthly Python render (black / X-dark background,
grey bars, MSTR in orange). Pin the tab so a truncated spreadsheets.get
cannot drop it, list it when "Has a chart" is on, keep Monday's importer
from overwriting the Python PNG with the native-chart export, and leave
Open in Sheets on because the tab is real (not sheetless).
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
import import_full_pngs
import retired_charts
import sheets_catalog

BOT3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
GID = 19854586
TAB = "Options OI % of Market Cap"
CHART = "Options open interest as a % of market cap"
NATIVE_CHART_ID = 44617522
SOURCES = "sheets/full/bot3/19854586.SOURCES.md"
PNG_SHA256 = "8c187ff009034b99ac4d74782a6176f7969631875ef7e7a6e14d78e40ec77392"
PNG_SIZE = 147227
THUMB_SIZE = 10437


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


class OptionsOiPctMcapTest(unittest.TestCase):
    def test_tab_is_pinned_and_not_sheetless_or_excluded(self):
        self.assertIn(("bot3", GID), sheets_catalog.PINNED_TABS)
        self.assertEqual(sheets_catalog.PINNED_TABS[("bot3", GID)], TAB)
        self.assertNotIn(("bot3", GID), sheets_catalog.SHEETLESS_TABS)
        self.assertLess(GID, sheets_catalog.SYNTHETIC_GID_MIN)
        self.assertFalse(sheets_catalog.tab_excluded("bot3", GID))
        self.assertNotIn(("bot3", GID), retired_charts.library_pairs())
        for key in sheets_catalog.PINNED_TABS:
            self.assertNotIn(key, sheets_catalog.EXCLUDED_TABS)

    def test_committed_catalog_includes_the_tab(self):
        path = os.path.join(ROOT, "static", "sheets", "catalog.json")
        with open(path, encoding="utf-8") as f:
            cat = json.load(f)
        hit = next((t for t in cat["tabs"] if t.get("book") == "bot3" and t.get("gid") == GID), None)
        self.assertIsNotNone(hit, "catalog.json is missing Options OI % of Market Cap")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
        self.assertTrue(hit.get("pin"))
        self.assertFalse(hit.get("sheetless"))
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot3/%d.jpg" % GID)
        self.assertEqual(len(cat["tabs"]), 389)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID)
        thumb = os.path.join(ROOT, "static", "sheets", "thumbs", "bot3", "%d.jpg" % GID)
        sources = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.SOURCES.md" % GID)
        self.assertTrue(os.path.isfile(full))
        self.assertTrue(os.path.isfile(thumb))
        self.assertTrue(os.path.isfile(sources))
        with open(full, "rb") as f:
            data = f.read()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(len(data), PNG_SIZE)
        self.assertEqual(hashlib.sha256(data).hexdigest(), PNG_SHA256)
        self.assertLessEqual(os.path.getsize(thumb), 20_000)
        self.assertEqual(os.path.getsize(thumb), THUMB_SIZE)
        text = open(sources, encoding="utf-8").read()
        self.assertIn("Strategy.com", text)
        self.assertIn("KEEP_MANUAL_PNGS", text)
        keys = {(t.get("book"), t.get("gid")) for t in cat["tabs"]}
        self.assertEqual(len(keys), len(cat["tabs"]))
        self.assertIn(("bot3", GID), keys)

    def test_generator_keeps_the_native_chart_from_the_dump(self):
        book, rows, hidden, excluded = sheets_catalog.one({
            "spreadsheetId": BOT3,
            "properties": {"title": "My Bot's Sheets"},
            "sheets": [
                _sheet("Other", 1, [{"spec": {"title": "A chart"}}], index=0),
                _sheet(TAB, GID, [{"spec": {"title": CHART}, "chartId": NATIVE_CHART_ID}], index=1),
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
        repo = tempfile.mkdtemp(prefix="options-oi-pin-")
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
        repo = tempfile.mkdtemp(prefix="options-oi-fallback-")
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

    def test_sheets_search_finds_the_tab_and_keeps_open_in_sheets(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("function sheetListedWithCharts(t){", html)
        self.assertIn("Number(t.gid)===19854586", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        # Open in Sheets is hidden only for sheetless / synthetic gids.
        self.assertIn("if(tab&&(tab.sheetless||Number(tab.gid)>=2147483648))return '';", html)
        tab = {"book": "bot3", "tab": TAB, "gid": GID, "charts": [CHART], "chartList": [{"n": 1, "title": CHART}]}
        self.assertFalse(bool(tab.get("sheetless")))
        self.assertLess(tab["gid"], 2147483648)
        blob = (tab["tab"] + " " + CHART + " My Bot's Sheets").lower()
        self.assertIn("options", blob)
        self.assertIn("open interest", blob)
        self.assertIn("market cap", blob)
        self.assertIn(SOURCES, open(os.path.join(ROOT, "README.md"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
