"""One retirement list, the retire tool, and the published-tree check."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_retired
import cotd_build
import retired_charts
import retire_chart
import sheets_catalog
import sheets_data

# Every library gid that used to live in EXCLUDED_TABS, RETIRED_2026_11_17, or
# server.py's favorite list. Losing one of these is a failed migration.
LIBRARY = {
    ("bot1", 333185169), ("bot1", 1547332232), ("bot1", 643299192),
    ("bot1", 1791345316), ("bot1", 1370617918), ("bot1", 1024131985),
    ("bot2", 418894352), ("bot1", 292208014), ("bot1", 2119398138),
    ("bot1", 1584345646), ("bot1", 1186940001), ("bot1", 501919598),
    ("bot1", 579853621), ("bot1", 1308503061), ("bot1", 748431671),
    ("bot1", 254001772), ("bot1", 1238930141), ("bot1", 1835714359),
    ("bot1", 492285771), ("bot1", 220064271), ("bot1", 79264442),
    ("bot1", 1755343475), ("bot1", 1067583608), ("bot1", 1924976898),
    ("bot1", 1978525586), ("bot1", 1255387315), ("bot1", 1522766504),
    ("bot1", 1163669375), ("bot1", 1576800449), ("bot1", 1192979444),
    ("bot1", 1912487083), ("bot1", 664060481), ("bot1", 1778640335),
    ("bot1", 1476428159), ("bot1", 1459587255), ("bot1", 687897000),
    ("bot1", 546391999), ("bot1", 195161343), ("bot1", 321339250),
    ("bot1", 2128724732), ("bot1", 189736118), ("bot1", 1981719475),
    ("bot1", 519508812), ("bot1", 45161413), ("bot2", 1794999542),
    ("bot1", 567489150), ("bot1", 601460115), ("bot1", 1024470894),
    ("bot1", 1771590089), ("bot1", 1566394978), ("bot1", 780786735),
    ("bot1", 248629477), ("bot2", 971499761), ("bot1", 1965360503),
    ("bot1", 583946273), ("bot1", 327679061), ("bot1", 909478550),
    ("bot1", 1828414017), ("bot1", 902803117), ("bot1", 949971563),
    ("bot1", 1234832613), ("bot1", 727565635), ("bot1", 626951791),
    ("bot1", 673256883), ("bot1", 711603553), ("bot1", 254194955),
    ("bot2", 1149583871), ("bot2", 2047295555), ("bot2", 84709656),
    ("bot1", 1328859831), ("bot1", 125248111), ("bot1", 208477863),
    ("bot1", 1834386145), ("bot1", 1635736949), ("bot1", 747711957),
    ("bot1", 378380409), ("bot2", 4940912), ("bot2", 1188646727),
    ("bot1", 847095656), ("bot1", 1694432360),
    # 2026-11-19
    ("bot2", 1229225245), ("bot1", 1229759147), ("bot1", 145827481),
    ("bot1", 972156540), ("bot1", 2037404230), ("bot1", 1457832817),
    ("bot1", 620817139), ("bot1", 1589445755), ("bot1", 1973893486),
    ("bot3", 251431790),
}
DATED_2026_11_17 = {
    ("bot1", 909478550), ("bot1", 1828414017), ("bot1", 902803117),
    ("bot1", 949971563), ("bot1", 1234832613), ("bot1", 727565635),
    ("bot1", 673256883), ("bot1", 254194955), ("bot1", 626951791),
    ("bot1", 711603553), ("bot2", 1149583871), ("bot2", 2047295555),
    ("bot2", 84709656),
}
DATED_2026_11_18 = {
    ("bot1", 1328859831), ("bot1", 125248111), ("bot1", 208477863),
    ("bot1", 1834386145), ("bot1", 1635736949), ("bot1", 747711957),
    ("bot1", 378380409), ("bot2", 4940912), ("bot2", 1188646727),
    ("bot1", 847095656), ("bot1", 1694432360),
}


def _png(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")


class LoaderTest(unittest.TestCase):
    def test_library_migration_keeps_every_gid(self):
        self.assertEqual(retired_charts.library_pairs(), LIBRARY)
        self.assertEqual(set(sheets_catalog.EXCLUDED_TABS), LIBRARY)
        self.assertEqual(set(sheets_data.RETIRED_2026_11_17), DATED_2026_11_17)
        self.assertEqual(set(sheets_data.RETIRED_2026_11_18), DATED_2026_11_18)
        self.assertTrue(DATED_2026_11_17 <= LIBRARY)
        self.assertTrue(DATED_2026_11_18 <= LIBRARY)
        self.assertEqual(retired_charts.cotd_pairs(), {("bot2", 645072508)})
        self.assertEqual(set(cotd_build.COTD_EXCLUDED), {("bot2", 645072508)})
        self.assertNotIn(("bot2", 645072508), sheets_catalog.EXCLUDED_TABS)
        self.assertFalse(sheets_catalog.tab_excluded("bot2", 645072508))
        self.assertTrue(sheets_catalog.tab_excluded("bot1", 333185169))
        self.assertTrue(cotd_build.pick_excluded({
            "spreadsheetId": "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ",
            "gid": 645072508,
        }))

    def test_known_fields_and_table_chart_id(self):
        row = retired_charts.find("bot1", 1547332232)
        self.assertEqual(row["chartId"], 727606877)
        self.assertEqual(row["tab"], "161 Nvidia P-S and P-E")
        self.assertEqual(row["retiredOn"], "2026-11-07")
        self.assertEqual(row["reason"], "retired")
        self.assertIn("161 Nvidia P-S and P-E.png", row["drivePngs"])
        self.assertEqual(row["scope"], "library")
        table = retired_charts.find("bot2", 2047295555)
        self.assertIsNone(table["chartId"])
        self.assertEqual(table["tab"], "23 Nvidia commitments table")
        self.assertIsNone(table["chartTitle"])
        kept = retired_charts.find("bot1", 333185169)
        self.assertEqual(kept["reason"], "excluded")
        self.assertEqual(kept["scope"], "library")
        cotd = retired_charts.find("bot2", 645072508)
        self.assertEqual(cotd["scope"], "cotd")
        self.assertEqual(cotd["reason"], "excluded")
        self.assertNotIn(("bot2", 645072508), retired_charts.library_pairs())

    def test_duplicate_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "retired_charts.json")
            row = {
                "book": "bot1", "gid": 1, "chartId": None, "tab": "t",
                "chartTitle": None, "retiredOn": "2026-11-18", "reason": "retired",
                "drivePngs": [], "scope": "library",
            }
            retired_charts.save([row, dict(row)], path)
            with self.assertRaises(ValueError):
                retired_charts.load(path)


class RetireChartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        os.makedirs(os.path.join(self.repo, "config"))
        retired_charts.save([{
            "book": "bot1", "gid": 10, "chartId": None, "tab": "already",
            "chartTitle": "Already gone", "retiredOn": "2026-11-01", "reason": "retired",
            "drivePngs": [], "scope": "library",
        }], os.path.join(self.repo, "config", "retired_charts.json"), note="test")
        catalog = {
            "tabs": [
                {"book": "bot1", "gid": 20, "tab": "stay", "charts": ["Stay"],
                 "chartList": [{"n": 1, "title": "Stay"}]},
                {"book": "bot1", "gid": 30, "tab": "go", "charts": ["Go"],
                 "chartList": [{"n": 1, "title": "Go"}]},
            ]
        }
        os.makedirs(os.path.join(self.repo, "static", "sheets"))
        with open(os.path.join(self.repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        _png(os.path.join(self.repo, "static", "sheets", "full", "bot1", "30.png"))
        _png(os.path.join(self.repo, "static", "sheets", "full", "bot1", "30_2.png"))
        _png(os.path.join(self.repo, "static", "sheets", "full", "bot1", "20.png"))
        os.makedirs(os.path.join(self.repo, "static", "sheets", "thumbs", "bot1"))
        with open(os.path.join(self.repo, "static", "sheets", "thumbs", "bot1", "30.jpg"), "wb") as f:
            f.write(b"jpg")
        data = os.path.join(self.repo, "static", "sheets", "data", "bot1")
        os.makedirs(data)
        with open(os.path.join(data, "30.json"), "w", encoding="utf-8") as f:
            f.write("{}\n")
        with open(os.path.join(self.repo, "static", "sheets", "data", "index.json"), "w", encoding="utf-8") as f:
            json.dump({"charts": [
                {"book": "bot1", "gid": 30, "n": 1, "file": "sheets/data/bot1/30.json"},
                {"book": "bot1", "gid": 20, "n": 1, "file": "sheets/data/bot1/20.json"},
            ]}, f)
        edition = "2026-11-18"
        os.makedirs(os.path.join(self.repo, "static", "cotd", edition))
        _png(os.path.join(self.repo, "static", "cotd", edition, "02.png"))
        _png(os.path.join(self.repo, "static", "cotd", edition, "03.png"))
        items = [
            {"sheet": "Bot experiments", "tab": "go", "title": "Go",
             "sheet_url": "https://docs.google.com/spreadsheets/d/abc/edit#gid=30",
             "image": "cotd/2026-11-18/02.png", "chart_n": 1},
            {"sheet": "Bot experiments", "tab": "stay", "title": "Stay",
             "sheet_url": "https://docs.google.com/spreadsheets/d/abc/edit#gid=20",
             "image": "cotd/2026-11-18/03.png", "chart_n": 1},
        ]
        today = {"date": edition, "items": items, "picks": []}
        cards = {"date": edition, "items": [
            {"book": "bot1", "gid": 30, "chart_n": 1, "tab": "go", "title": "Go", "image": "cotd/2026-11-18/02.png"},
            {"book": "bot1", "gid": 20, "chart_n": 1, "tab": "stay", "title": "Stay", "image": "cotd/2026-11-18/03.png"},
        ], "random": [
            {"book": "bot1", "gid": 30, "chart_n": 1, "tab": "go", "title": "Go"},
            {"book": "bot1", "gid": 20, "chart_n": 1, "tab": "stay", "title": "Stay"},
        ]}
        with open(os.path.join(self.repo, "static", "cotd", "today.json"), "w", encoding="utf-8") as f:
            json.dump(today, f)
        with open(os.path.join(self.repo, "static", "cotd", edition, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards, f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_writes_nothing(self):
        before = open(os.path.join(self.repo, "config", "retired_charts.json"), encoding="utf-8").read()
        code = retire_chart.main(["--repo", self.repo, "--book", "bot1", "--gid", "30", "--tab", "go", "--date", "2026-11-18"])
        self.assertEqual(code, 0)
        after = open(os.path.join(self.repo, "config", "retired_charts.json"), encoding="utf-8").read()
        self.assertEqual(before, after)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "static", "sheets", "full", "bot1", "30.png")))
        catalog = json.load(open(os.path.join(self.repo, "static", "sheets", "catalog.json"), encoding="utf-8"))
        self.assertEqual(sorted(t["gid"] for t in catalog["tabs"]), [20, 30])

    def test_apply_removes_assets_and_does_not_renumber_cards(self):
        code = retire_chart.main([
            "--repo", self.repo, "--book", "bot1", "--gid", "30", "--chart-id", "99",
            "--tab", "go", "--chart-title", "Go", "--drive-png", "go.png",
            "--date", "2026-11-18", "--apply",
        ])
        self.assertEqual(code, 0)
        rows = retired_charts.charts(os.path.join(self.repo, "config", "retired_charts.json"))
        added = [r for r in rows if r["gid"] == 30]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["chartId"], 99)
        self.assertEqual(added[0]["drivePngs"], ["go.png"])
        self.assertEqual(added[0]["scope"], "library")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "static", "sheets", "full", "bot1", "30.png")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "static", "sheets", "full", "bot1", "30_2.png")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "static", "sheets", "thumbs", "bot1", "30.jpg")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "static", "sheets", "data", "bot1", "30.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "static", "sheets", "full", "bot1", "20.png")))
        catalog = json.load(open(os.path.join(self.repo, "static", "sheets", "catalog.json"), encoding="utf-8"))
        self.assertEqual([t["gid"] for t in catalog["tabs"]], [20])
        index = json.load(open(os.path.join(self.repo, "static", "sheets", "data", "index.json"), encoding="utf-8"))
        self.assertEqual([c["gid"] for c in index["charts"]], [20])
        today = json.load(open(os.path.join(self.repo, "static", "cotd", "today.json"), encoding="utf-8"))
        self.assertEqual([it["image"] for it in today["items"]], ["cotd/2026-11-18/03.png"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "static", "cotd", "2026-11-18", "02.png")))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "static", "cotd", "2026-11-18", "03.png")))
        cards = json.load(open(os.path.join(self.repo, "static", "cotd", "2026-11-18", "cards.json"), encoding="utf-8"))
        self.assertEqual([it["gid"] for it in cards["items"]], [20])
        self.assertEqual([it["gid"] for it in cards["random"]], [20])
        self.assertEqual(check_retired.problems(self.repo), [])

    def test_second_apply_does_not_duplicate_or_blank_the_title(self):
        retire_chart.main(["--repo", self.repo, "--book", "bot1", "--gid", "30", "--tab", "go", "--date", "2026-11-18", "--apply"])
        retire_chart.main(["--repo", self.repo, "--book", "bot1", "--gid", "30", "--date", "2026-11-18", "--apply"])
        rows = [r for r in retired_charts.charts(os.path.join(self.repo, "config", "retired_charts.json")) if r["gid"] == 30]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tab"], "go")


class CheckRetiredTest(unittest.TestCase):
    def test_live_repo_is_clean(self):
        hits = check_retired.problems(ROOT)
        self.assertEqual(hits, [], hits)

    def test_script_exits_nonzero_when_a_retired_png_remains(self):
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "config"))
            os.makedirs(os.path.join(repo, "static", "sheets"))
            retired_charts.save([{
                "book": "bot1", "gid": 30, "chartId": 5, "tab": "go",
                "chartTitle": "Go", "retiredOn": "2026-11-18", "reason": "retired",
                "drivePngs": [], "scope": "library",
            }, {
                "book": "bot2", "gid": 645072508, "chartId": None, "tab": "red dot",
                "chartTitle": "Mark's Red Dot Tracker: MSTR", "retiredOn": None,
                "reason": "excluded", "drivePngs": [], "scope": "cotd",
            }], os.path.join(repo, "config", "retired_charts.json"))
            with open(os.path.join(repo, "static", "sheets", "catalog.json"), "w", encoding="utf-8") as f:
                json.dump({"tabs": [
                    {"book": "bot1", "gid": 30, "tab": "go", "chartList": [{"n": 1, "title": "Go", "chartId": 5}]},
                    {"book": "bot2", "gid": 645072508, "tab": "red dot", "charts": ["Mark's Red Dot Tracker: MSTR"]},
                ]}, f)
            self.assertTrue(any(hit.startswith("catalog:") for hit in check_retired.problems(repo)))
            run = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_retired.py"), "--repo", repo],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertIn("FAIL", run.stdout)
            # The CotD-only chart is allowed to stay in the catalog.
            self.assertNotIn("645072508", run.stdout)


if __name__ == "__main__":
    unittest.main()
