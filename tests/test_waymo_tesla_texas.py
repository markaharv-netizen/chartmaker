"""Waymo vs Tesla Texas must show in the Sheets library and Charts of the day.

The My Bot's Sheets tab (gid 1408701012) has a plain native Sheets chart, so
the library PNG is Steve's Python render of driverless vehicles registered with
the Texas DMV. Pin the tab so a truncated spreadsheets.get cannot drop it, list
it when "Has a chart" is on, keep Monday's importer from overwriting the Python
PNG with the native-chart export, and keep it in the Charts of the day pool.
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
GID = 1408701012
CHART_ID = 1040684539
TAB = "Waymo vs Tesla Texas"
CHART = "Waymo vs Tesla in Texas"
CAPTION = (
    "Driverless vehicles registered with the Texas DMV, May 28 to Nov 18, "
    "2026, from the same registry for both companies. Tesla passed Waymo in "
    "Texas on Nov 17."
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


class WaymoTeslaTexasTest(unittest.TestCase):
    def test_tab_is_pinned_and_not_excluded(self):
        self.assertIn(("bot3", GID), sheets_catalog.PINNED_TABS)
        self.assertEqual(sheets_catalog.PINNED_TABS[("bot3", GID)], TAB)
        self.assertFalse(sheets_catalog.tab_excluded("bot3", GID))
        self.assertNotIn(("bot3", GID), retired_charts.library_pairs())
        self.assertNotIn(("bot3", GID), retired_charts.cotd_pairs())
        self.assertNotIn(("bot3", GID), cotd_build.COTD_EXCLUDED)

    def test_committed_catalog_includes_the_tab(self):
        cat = _catalog()
        hit = next((t for t in cat["tabs"] if t.get("book") == "bot3" and t.get("gid") == GID), None)
        self.assertIsNotNone(hit, "catalog.json is missing Waymo vs Tesla Texas")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot3/%d.jpg" % GID)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d.png" % GID)
        thumb = os.path.join(ROOT, "static", "sheets", "thumbs", "bot3", "%d.jpg" % GID)
        self.assertTrue(os.path.isfile(full))
        self.assertTrue(os.path.isfile(thumb))
        with open(full, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertLessEqual(os.path.getsize(thumb), 20_000)
        self.assertFalse(cotd_build.file_is_blank(full))
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
        repo = tempfile.mkdtemp(prefix="waymo-texas-pin-")
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
            by_key = {(t["book"], t["gid"]): t for t in built["tabs"]}
            self.assertIn(("bot3", 42), by_key)
            hit = by_key[("bot3", GID)]
            self.assertEqual(hit["tab"], TAB)
            self.assertEqual(hit.get("charts"), [CHART])
            self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
            self.assertEqual(hit.get("thumb"), thumb_rel)
            self.assertTrue(hit.get("pin"))
            self.assertTrue(any(str(GID) in w and "kept the existing catalog entry" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_importer_skips_the_native_chart_export(self):
        self.assertIn(("bot3", GID), import_full_pngs.KEEP_MANUAL_PNGS)
        tabs = [{"book": "bot3", "gid": GID, "chartList": [{"n": 1, "title": CHART}]}]
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot3", GID, 1))
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot3", str(GID), 1))
        # Other bot3 tabs are unchanged: a listed titled chart is still imported.
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

    def test_curator_pick_with_caption_resolves(self):
        cat = _catalog()
        pool = cotd_build.pool_items(cat, ROOT)
        raw = {"book": "bot3", "gid": GID, "description": CAPTION,
               "reason": "New chart in the library."}
        item = cotd_build.resolve_pick(raw, 1, cat, pool, ROOT)
        self.assertEqual(item["section"], "picks")
        self.assertEqual(item["title"], CHART)
        self.assertEqual(item["description"], CAPTION)

    def test_sheets_search_finds_the_tab_with_has_a_chart_on_or_off(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("function sheetListedWithCharts(t){", html)
        self.assertIn("(String(t.book)==='bot3'&&Number(t.gid)===1408701012)", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        blob = (TAB + " " + CHART + " My Bot's Sheets").lower()
        for word in ("waymo", "tesla", "texas"):
            self.assertIn(word, blob)


if __name__ == "__main__":
    unittest.main()
