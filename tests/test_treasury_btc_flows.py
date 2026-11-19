"""94 BTC treasury monthly flows must show in the Sheets library and Charts of the day.

The bot experiments 2 tab (gid 855902063) has a native Sheets chart, but it is
unstyled, so the library PNG is Steve's Python render (re-rendered on the 3rd of
each month). Pin the tab so a truncated spreadsheets.get cannot drop it, list it
when "Has a chart" is on, keep Monday's importer from overwriting the Python PNG
with the native-chart export, and keep it in the Charts of the day pool.
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

BOT2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
GID = 855902063
CHART_ID = 616637351
TAB = "94 BTC treasury monthly flows"
CHART = "Monthly net bitcoin flows of public treasury companies"
CAPTION = (
    "Monthly net BTC change per company (Strategy, MARA, Riot, Metaplanet, "
    "Strive incl. Semler, Trump Media, Nakamoto, plus other companies) from "
    "company filings, with the BTC price line."
)


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


class TreasuryBtcFlowsTest(unittest.TestCase):
    def test_tab_is_pinned_and_not_excluded(self):
        self.assertIn(("bot2", GID), sheets_catalog.PINNED_TABS)
        self.assertEqual(sheets_catalog.PINNED_TABS[("bot2", GID)], TAB)
        self.assertFalse(sheets_catalog.tab_excluded("bot2", GID))
        self.assertNotIn(("bot2", GID), retired_charts.library_pairs())
        self.assertNotIn(("bot2", GID), retired_charts.cotd_pairs())
        self.assertNotIn(("bot2", GID), cotd_build.COTD_EXCLUDED)

    def test_committed_catalog_includes_the_tab(self):
        cat = _catalog()
        hit = next((t for t in cat["tabs"] if t.get("book") == "bot2" and t.get("gid") == GID), None)
        self.assertIsNotNone(hit, "catalog.json is missing 94 BTC treasury monthly flows")
        self.assertEqual(hit.get("tab"), TAB)
        self.assertEqual(hit.get("charts"), [CHART])
        self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
        self.assertEqual(hit.get("thumb"), "sheets/thumbs/bot2/%d.jpg" % GID)
        full = os.path.join(ROOT, "static", "sheets", "full", "bot2", "%d.png" % GID)
        thumb = os.path.join(ROOT, "static", "sheets", "thumbs", "bot2", "%d.jpg" % GID)
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
        self.assertIn(("bot2", GID), keys)

    def test_generator_keeps_the_native_chart_from_the_dump(self):
        book, rows, hidden, excluded = sheets_catalog.one({
            "spreadsheetId": BOT2,
            "properties": {"title": "bot experiments 2"},
            "sheets": [
                _sheet("Other", 1, [{"spec": {"title": "A chart"}}], index=0),
                _sheet(TAB, GID, [{"spec": {"title": CHART}, "chartId": CHART_ID}], index=1),
            ],
        })
        self.assertEqual(book["id"], "bot2")
        self.assertEqual(hidden, 0)
        self.assertEqual(excluded, 0)
        hit = next(t for t in rows if t["gid"] == GID)
        self.assertEqual(hit["tab"], TAB)
        self.assertEqual(hit["charts"], [CHART])
        self.assertEqual(hit["chartList"], [{"n": 1, "title": CHART}])

    def test_rebuild_keeps_the_tab_when_the_dump_omits_it(self):
        repo = tempfile.mkdtemp(prefix="treasury-flows-pin-")
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
                    "thumb": thumb_rel,
                }],
            }
            with open(os.path.join(cat_dir, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump(prev, f)
            dump = _dump(os.path.join(repo, "bot2.json"), [
                _sheet("Solo", 42, [{"spec": {"title": "Only chart"}}], index=0),
            ])
            built, warns = sheets_catalog.build_catalog([dump], repo, allow_partial=True)
            by_key = {(t["book"], t["gid"]): t for t in built["tabs"]}
            self.assertIn(("bot2", 42), by_key)
            hit = by_key[("bot2", GID)]
            self.assertEqual(hit["tab"], TAB)
            self.assertEqual(hit.get("charts"), [CHART])
            self.assertEqual(hit.get("chartList"), [{"n": 1, "title": CHART}])
            self.assertEqual(hit.get("thumb"), thumb_rel)
            self.assertTrue(hit.get("pin"))
            self.assertTrue(any(str(GID) in w and "kept the existing catalog entry" in w for w in warns), warns)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_importer_skips_the_native_chart_export(self):
        self.assertIn(("bot2", GID), import_full_pngs.KEEP_MANUAL_PNGS)
        tabs = [{"book": "bot2", "gid": GID, "chartList": [{"n": 1, "title": CHART}]}]
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot2", GID, 1))
        self.assertTrue(import_full_pngs.skip_chart_image(tabs, "bot2", str(GID), 1))
        # Other bot2 tabs are unchanged: a listed titled chart is still imported.
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

    def test_curator_pick_with_caption_resolves(self):
        cat = _catalog()
        pool = cotd_build.pool_items(cat, ROOT)
        raw = {"book": "bot2", "gid": GID, "description": CAPTION,
               "reason": "New chart in the library."}
        item = cotd_build.resolve_pick(raw, 1, cat, pool, ROOT)
        self.assertEqual(item["section"], "picks")
        self.assertEqual(item["title"], CHART)
        self.assertEqual(item["description"], CAPTION)

    def test_sheets_search_finds_the_tab_with_has_a_chart_on_or_off(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("function sheetListedWithCharts(t){", html)
        self.assertIn("(String(t.book)==='bot2'&&Number(t.gid)===855902063)", html)
        self.assertIn(
            "if(need&&!sheetCharts(t).some(c=>!c.synthetic)&&!sheetListedWithCharts(t))return false;",
            html,
        )
        blob = (TAB + " " + CHART + " bot experiments 2").lower()
        for word in ("bitcoin", "btc", "treasury", "flows"):
            self.assertIn(word, blob)


if __name__ == "__main__":
    unittest.main()
