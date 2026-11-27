"""Founder-CEO Tier List must show in the Sheets library as a static card.

This is a PNG-only library chart: there is no Google Sheet tab. The reserved
synthetic gid 9000000004 sits in bot3 above Google's int32 sheetId max, so it
cannot collide with a real tab. The library PNG is a static Python-rendered
tier list, not part of Monday data refreshes. Pin the tab so a catalog rebuild
cannot drop it, list it when "Has a chart" is on, keep Monday's importer from
overwriting the PNG, hide Open in Sheets, keep it out of the retirement lists,
and show Wikimedia photo credits on the catalog row.
"""
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
GID = 9000000004
TAB = "Founder-CEO Tier List"
CHART = "Founder-CEO Tier List"
CREDITS = "sheets/full/bot3/9000000004.SOURCES.md"
NOTES = (
    "Portraits from Wikimedia Commons (CC BY, CC BY-SA, and CC0; "
    "attribution requested). Abel Avellan is a silhouette placeholder "
    "because no freely licensed portrait was found."
)


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


class FounderCeoTierListTest(unittest.TestCase):
    def test_tab_is_pinned_sheetless_and_not_excluded(self):
        self.assertIn(("bot3", GID), sheets_catalog.PINNED_TABS)
        self.assertEqual(sheets_catalog.PINNED_TABS[("bot3", GID)], TAB)
        self.assertIn(("bot3", GID), sheets_catalog.SHEETLESS_TABS)
        self.assertEqual(sheets_catalog.SHEETLESS_TABS[("bot3", GID)], CHART)
        self.assertEqual(sheets_catalog.SHEETLESS_NOTES[("bot3", GID)], NOTES)
        self.assertEqual(sheets_catalog.SHEETLESS_CREDITS[("bot3", GID)], CREDITS)
        self.assertGreaterEqual(GID, sheets_catalog.SYNTHETIC_GID_MIN)
        self.assertFalse(sheets_catalog.tab_excluded("bot3", GID))
        self.assertNotIn(("bot3", GID), retired_charts.library_pairs())
        self.assertNotIn(("bot3", GID), retired_charts.cotd_pairs())
        self.assertNotIn(("bot3", GID), cotd_build.COTD_EXCLUDED)
        for key in sheets_catalog.PINNED_TABS:
            self.assertNotIn(key, retired_charts.library_pairs())

    def test_committed_catalog_includes_the_tab_and_credits(self):
        cat = _catalog()
        hit = next((t for t in cat["tabs"] if t.get("book") == "bot3" and t.get("gid") == GID), None)
        self.assertIsNotNone(hit, "catalog.json is missing Founder-CEO Tier List")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
        self.assertTrue(hit.get("pin"))
        self.assertTrue(hit.get("sheetless"))
        self.assertEqual(hit.get("notes"), NOTES)
        self.assertEqual(hit.get("credits"), CREDITS)
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot3/%d.jpg" % GID)
        self.assertEqual(len(cat["tabs"]), 387)
        self.assertTrue(any(t.get("book") == "bot3" and t.get("gid") == GID for t in cat["tabs"]))
        full = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID)
        thumb = os.path.join(ROOT, "static", "sheets", "thumbs", "bot3", "%d.jpg" % GID)
        credits = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.SOURCES.md" % GID)
        self.assertTrue(os.path.isfile(full))
        self.assertTrue(os.path.isfile(thumb))
        self.assertTrue(os.path.isfile(credits))
        with open(full, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertLessEqual(os.path.getsize(thumb), 20_000)
        self.assertFalse(cotd_build.file_is_blank(full))
        text = open(credits, encoding="utf-8").read()
        self.assertIn("Wikimedia Commons", text)
        self.assertIn("Gage Skidmore", text)
        self.assertIn("Abel Avellan", text)
        keys = {(t.get("book"), t.get("gid")) for t in cat["tabs"]}
        self.assertEqual(len(keys), len(cat["tabs"]))
        self.assertIn(("bot3", GID), keys)
        real = [t["gid"] for t in cat["tabs"]
                if int(t.get("gid") or 0) < sheets_catalog.SYNTHETIC_GID_MIN]
        self.assertTrue(real)
        self.assertLess(max(real), sheets_catalog.SYNTHETIC_GID_MIN)
        self.assertGreaterEqual(GID, sheets_catalog.SYNTHETIC_GID_MIN)

    def test_rebuild_keeps_the_tab_when_the_dump_omits_it(self):
        repo = tempfile.mkdtemp(prefix="ceo-tier-pin-")
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
                    "pin": True, "sheetless": True, "notes": NOTES,
                    "credits": CREDITS, "thumb": thumb_rel,
                }],
            }
            with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump(prev, f)
            dump = _dump(os.path.join(repo, "bot3.json"), [
                _sheet("Solo", 42, [{"spec": {"title": "Only chart"}}], index=0),
            ])
            built, warns = sheets_catalog.build_catalog([dump], repo, allow_partial=True)
            by_key = {(t["book"], t["gid"]): t for t in built["tabs"]}
            self.assertIn(("bot3", 42), by_key)
            hit = by_key[("bot3", GID)]
            self.assertEqual(hit["tab"], TAB)
            self.assertEqual(hit.get("charts"), [CHART])
            self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
            self.assertEqual(hit.get("thumb"), thumb_rel)
            self.assertTrue(hit.get("pin"))
            self.assertTrue(hit.get("sheetless"))
            self.assertEqual(hit.get("notes"), NOTES)
            self.assertEqual(hit.get("credits"), CREDITS)
            self.assertTrue(any(str(GID) in w and "kept the existing catalog entry" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_rebuild_adds_a_sheetless_chart_row_without_a_prior_catalog(self):
        repo = tempfile.mkdtemp(prefix="ceo-tier-bare-")
        try:
            os.makedirs(os.path.join(repo, "static", "sheets"))
            dump = _dump(os.path.join(repo, "bot3.json"), [
                _sheet("Solo", 42, [{"spec": {"title": "Only chart"}}], index=0),
            ])
            built, warns = sheets_catalog.build_catalog([dump], repo, allow_partial=True)
            hit = next(t for t in built["tabs"] if t.get("book") == "bot3" and t.get("gid") == GID)
            self.assertEqual(hit["tab"], TAB)
            self.assertEqual(hit.get("charts"), [CHART])
            self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
            self.assertTrue(hit.get("pin"))
            self.assertTrue(hit.get("sheetless"))
            self.assertEqual(hit.get("notes"), NOTES)
            self.assertEqual(hit.get("credits"), CREDITS)
            self.assertTrue(any(str(GID) in w and "added the pinned table tab" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_importer_skips_any_matching_export(self):
        self.assertIn(("bot3", GID), import_full_pngs.KEEP_MANUAL_PNGS)
        tabs = [{"book": "bot3", "gid": GID, "chartList": [{"n": 1, "title": CHART}]}]
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot3", GID, 1))
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot3", str(GID), 1))
        other = [{"book": "bot3", "gid": 1618426207, "chartList": [{"n": 1, "title": "ASST 30-Day Average Daily Trading Volume"}]}]
        self.assertFalse(import_full_pngs.skip_chart_image(other, "bot3", 1618426207, 1))

    def test_charts_of_the_day_pool_includes_the_png(self):
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

    def test_sheets_search_finds_the_tab_and_hides_open_in_sheets(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("function sheetListedWithCharts(t){", html)
        self.assertIn("(String(t.book)==='bot3'&&Number(t.gid)===9000000004)", html)
        self.assertIn("function sheetCredits(t){", html)
        self.assertIn("Photo credits", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        self.assertIn("if(tab&&(tab.sheetless||Number(tab.gid)>=2147483648))return '';", html)
        self.assertIn("const hideSheet=!!(hit&&hit.tab&&(hit.tab.sheetless||Number(hit.tab.gid)>=2147483648));", html)
        blob = (TAB + " " + CHART + " My Bot's Sheets " + NOTES).lower()
        for word in ("founder-ceo", "tier list", "wikimedia"):
            self.assertIn(word, blob)


if __name__ == "__main__":
    unittest.main()
