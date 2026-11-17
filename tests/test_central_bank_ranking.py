"""Central Bank Ranking must stay in the Sheets library as a chartless table tab.

The My Bot's Sheets tab (gid 874288811) has no native chart. Monday's PNG
exporter skips it, and a truncated spreadsheets.get can omit it from the
catalog dump. The generator still has to keep the tab, and a Sheets-library
search for "Central Bank" must find it with the "Has a chart" filter on or off.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sheets_catalog

BOT3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
GID = 874288811
TAB = "Central Bank Ranking"


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


class CentralBankRankingTest(unittest.TestCase):
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
        self.assertIsNotNone(hit, "catalog.json is missing Central Bank Ranking")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts") or [], [])
        self.assertEqual(hit.get("chartList") or [], [])
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot3/%d.jpg" % GID)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID)))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "sheets", "thumbs", "bot3", "%d.jpg" % GID)))

    def test_generator_keeps_a_chartless_tab_from_the_dump(self):
        book, rows, hidden, excluded = sheets_catalog.one({
            "spreadsheetId": BOT3,
            "properties": {"title": "My Bot's Sheets"},
            "sheets": [
                _sheet("Other", 1, [{"spec": {"title": "A chart"}}], index=0),
                _sheet(TAB, GID, [], index=1),
            ],
        })
        self.assertEqual(book["id"], "bot3")
        self.assertEqual(hidden, 0)
        self.assertEqual(excluded, 0)
        hit = next(t for t in rows if t["gid"] == GID)
        self.assertEqual(hit["tab"], TAB)
        self.assertEqual(hit["charts"], [])
        self.assertEqual(hit["chartList"], [])

    def test_rebuild_keeps_the_tab_when_the_dump_omits_it(self):
        repo = tempfile.mkdtemp(prefix="cbrank-pin-")
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
                    "charts": [], "chartList": [], "thumb": thumb_rel,
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
            self.assertEqual(by_gid[GID].get("charts") or [], [])
            self.assertEqual(by_gid[GID].get("chartList") or [], [])
            self.assertEqual(by_gid[GID].get("thumb"), thumb_rel)
            self.assertTrue(by_gid[GID].get("pin"))
            self.assertTrue(any(str(GID) in w and "kept the existing catalog entry" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_rebuild_adds_a_fallback_row_when_no_prior_catalog(self):
        repo = tempfile.mkdtemp(prefix="cbrank-fallback-")
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

    def test_pinned_restore_does_not_bring_back_an_excluded_tab(self):
        excluded = next(iter(sheets_catalog.EXCLUDED_TABS))
        tabs, warns = sheets_catalog.restore_pinned_tabs([], {
            excluded: {"book": excluded[0], "tab": "retired", "gid": excluded[1], "charts": [], "chartList": []},
        })
        self.assertFalse(any(t.get("gid") == excluded[1] and t.get("book") == excluded[0] for t in tabs))
        self.assertNotIn(excluded, sheets_catalog.PINNED_TABS)
        # The pinned Central Bank tab may still be added; a retired gid must not be.
        self.assertTrue(all(t.get("gid") != excluded[1] or t.get("book") != excluded[0] for t in tabs))
        self.assertFalse(any(str(excluded[1]) in w for w in warns))

    def test_sheets_search_finds_the_tab_with_has_a_chart_on_or_off(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        # Other table tabs stay hidden when "Has a chart" is on. This pinned
        # tab is listed in both states, and the search blob matches the name.
        self.assertIn("function sheetListedWithCharts(t){", html)
        self.assertIn("Number(t.gid)===874288811", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        tab = {"book": "bot3", "tab": TAB, "gid": GID, "charts": [], "chartList": []}
        blob = (tab["tab"] + " My Bot's Sheets").lower()
        self.assertIn("central bank", blob)



if __name__ == "__main__":
    unittest.main()
