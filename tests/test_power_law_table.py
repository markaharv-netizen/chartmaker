"""The Power Law tab's "Bitcoin Return to Power-Law Estimate" table stays in the library.

My Bot's Sheets tab "Power Law" (bot3 gid 1099402183) has three native charts
and a cell-range table at P86:T98. Monday's native export only sees embedded
charts, so the table is a Python render committed as <gid>_9.png and listed
as chart n=9 through sheets_catalog.TAB_EXTRA_ITEMS. A catalog rebuild must
keep it and the PNG import must never overwrite it.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sheets_catalog  # noqa: E402
import import_full_pngs  # noqa: E402

BOT3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
GID = 1099402183
N = 9
TITLE = "Bitcoin Return to Power-Law Estimate"
NATIVE = ["Bitcoin Power Law", "Bitcoin power law", "Bitcoin Deviation from Power Law Trend"]


def _chart(title, row, col=0):
    return {"spec": {"title": title},
            "position": {"overlayPosition": {"anchorCell": {"rowIndex": row, "columnIndex": col}}}}


def _power_law_sheet(native=NATIVE):
    return {"properties": {"title": "Power Law", "sheetId": GID, "index": 0},
            "charts": [_chart(t, i * 20) for i, t in enumerate(native)]}


class PowerLawTableTest(unittest.TestCase):
    def test_extra_item_config(self):
        self.assertEqual(sheets_catalog.TAB_EXTRA_ITEMS[("bot3", GID)], [{"n": N, "title": TITLE}])
        self.assertFalse(sheets_catalog.tab_excluded("bot3", GID))
        self.assertIn(("bot3", GID, N), import_full_pngs.KEEP_MANUAL_ITEMS)

    def test_committed_catalog_and_png(self):
        with open(os.path.join(ROOT, "static", "sheets", "catalog.json"), encoding="utf-8") as f:
            cat = json.load(f)
        hit = next(t for t in cat["tabs"] if t.get("book") == "bot3" and t.get("gid") == GID)
        self.assertEqual(hit["tab"], "Power Law")
        self.assertIn({"n": N, "title": TITLE}, hit["chartList"])
        self.assertIn(TITLE, hit["charts"])
        self.assertEqual(len(hit["charts"]), len(hit["chartList"]))
        full = os.path.join(ROOT, "static", "sheets", "full", "bot3", "%d_%d.png" % (GID, N))
        with open(full, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertTrue(import_full_pngs.FULL_REL.fullmatch("sheets/full/bot3/%d_%d.png" % (GID, N)))

    def test_rebuild_appends_after_native_charts(self):
        _book, rows, _h, _e = sheets_catalog.one({
            "spreadsheetId": BOT3, "properties": {"title": "My Bot's Sheets"},
            "sheets": [_power_law_sheet()],
        })
        rows, warns = sheets_catalog.apply_tab_extra_items(rows)
        self.assertEqual(warns, [])
        row = rows[0]
        self.assertEqual([c["n"] for c in row["chartList"]], [1, 2, 3, N])
        self.assertEqual(row["charts"], NATIVE + [TITLE])
        # Idempotent on a second pass (a kept previous catalog row).
        rows, warns = sheets_catalog.apply_tab_extra_items(rows)
        self.assertEqual([c["n"] for c in rows[0]["chartList"]], [1, 2, 3, N])
        self.assertEqual(warns, [])

    def test_native_chart_at_same_n_wins(self):
        native = ["c%d" % i for i in range(1, 10)]
        _book, rows, _h, _e = sheets_catalog.one({
            "spreadsheetId": BOT3, "properties": {"title": "My Bot's Sheets"},
            "sheets": [_power_law_sheet(native)],
        })
        rows, warns = sheets_catalog.apply_tab_extra_items(rows)
        self.assertEqual(len(rows[0]["chartList"]), 9)
        self.assertEqual(rows[0]["chartList"][-1]["title"], "c9")
        self.assertEqual(len(warns), 1)

    def test_build_catalog_keeps_item(self):
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "static", "sheets"))
            dump = os.path.join(repo, "bot3.json")
            with open(dump, "w", encoding="utf-8") as f:
                json.dump({"spreadsheetId": BOT3, "properties": {"title": "My Bot's Sheets"},
                           "sheets": [_power_law_sheet()]}, f)
            doc, _w = sheets_catalog.build_catalog([dump], repo, True)
            row = next(t for t in doc["tabs"] if t["gid"] == GID)
            self.assertIn({"n": N, "title": TITLE}, row["chartList"])

    def test_import_never_overwrites_the_render(self):
        tabs = {("bot3", GID): {"book": "bot3", "gid": GID,
                                "chartList": [{"n": i, "title": t} for i, t in enumerate(NATIVE, 1)]
                                + [{"n": N, "title": TITLE}]}}
        cat_tabs = list(tabs.values())
        self.assertTrue(import_full_pngs.skip_chart_image(cat_tabs, "bot3", GID, N))
        self.assertFalse(import_full_pngs.skip_chart_image(cat_tabs, "bot3", GID, 1))


if __name__ == "__main__":
    unittest.main()
