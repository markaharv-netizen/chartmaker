"""Interactive Sheets chart export from a saved metadata + values dump.

No Google calls. Retired tabs stay out of the snapshot.
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sheets_catalog
import sheets_data

BOT1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "sheets_dumps")


def _bundle(sheet_id, title, charts, rows, formatted=None, sid=BOT1, sheet_title="Data"):
    end = len(rows)
    width = max(len(r) for r in rows) - 1
    doc = {
        "spreadsheet": {
            "spreadsheetId": sid,
            "properties": {"title": title},
            "sheets": [{
                "properties": {"sheetId": sheet_id, "title": sheet_title, "index": 0},
                "charts": charts,
            }],
        },
        "values": {"spreadsheetId": sid, "valueRanges": [
            sheets_data._range(sheet_title, 0, 0, width, end, rows),
        ]},
    }
    if formatted is not None:
        doc["formatted"] = {"spreadsheetId": sid, "valueRanges": [
            sheets_data._range(sheet_title, 0, 0, width, end, formatted),
        ]}
    return doc


def _one(chart_type, rows, *, sheet_id=42, title="Example", axes=None, series=None, legend="NO_LEGEND", sid=BOT1, sheet_title="Data", formatted=None, position=0):
    end = len(rows)
    if series is None:
        series = [sheets_data._series(sheets_data._grid(sheet_id, 0, 1, end, 2), "#111827")]
    if axes is None:
        axes = [
            {"position": "BOTTOM_AXIS", "title": "Date"},
            {"position": "LEFT_AXIS", "title": ""},
        ]
    chart = sheets_data._chart(
        7, title, chart_type,
        sheets_data._grid(sheet_id, 0, 0, end, 1),
        series, position, axes, legend=legend,
    )
    return _bundle(sheet_id, "Bot experiments", [chart], rows, formatted, sid=sid, sheet_title=sheet_title)


class ExportDumpTest(unittest.TestCase):
    def test_pilot_debt_columns_percent_and_date_title(self):
        charts, warnings = sheets_data.convert_bundle(sheets_data.sample_bundles()[2])
        debt = [c for c in charts if c.get("supported") and c["gid"] == 1145735379]
        self.assertEqual([c["n"] for c in debt], [1, 2, 3, 4])
        self.assertEqual([c["title"] for c in debt], [
            "Strategy bitcoin on balance sheet",
            "Strategy BTC Holdings Q/Q Change",
            "Strategy BTC/Share (in Sats)",
            "MSTR mNAV",
        ])
        first = debt[0]
        self.assertEqual(first["type"], "column")
        self.assertEqual(first["series"][0]["color"], "#F7931A")
        self.assertEqual(first["series"][0]["name"], "Bitcoin")
        self.assertEqual(first["series"][0]["data"][0], 38250)
        self.assertEqual(first["series"][0]["data"][-1], 850364)
        self.assertEqual(first["categories"][0], "9/30/20")
        self.assertEqual(first["x"]["title"], "")
        self.assertTrue(first["series"][0]["labels"])
        self.assertTrue(first["sample"])
        change = debt[1]
        self.assertEqual(change["y"][0]["format"], "percent")
        self.assertEqual(change["series"][0]["data"][0], 84.2)
        self.assertEqual(change["series"][0]["data"][1], 29.6)
        self.assertEqual(change["series"][0]["data"][-1], 0.5)
        self.assertEqual(change["y"][0]["max"], 100)
        self.assertEqual(change["series"][0]["color"], "#202124")
        sats = debt[2]
        self.assertEqual(sats["categories"][0], "112,135")
        self.assertEqual(sats["series"][0]["data"][0], 91097)
        self.assertEqual(sats["series"][0]["data"][-1], 186826)
        mnav = debt[3]
        self.assertEqual(mnav["type"], "area")
        self.assertIn("Jul 27", mnav["subtitle"])
        self.assertEqual(mnav["y"][0]["min"], 0.8)
        self.assertEqual(mnav["x"]["title"], "")
        self.assertFalse(any("333185169" in w for w in warnings))

    def test_pilot_lines_keep_explicit_colors_and_log(self):
        bot1 = sheets_data.sample_bundles()[0]
        charts, _ = sheets_data.convert_bundle(bot1)
        by_gid = {c["gid"]: c for c in charts if c.get("supported")}
        vol = by_gid[1546024641]
        self.assertEqual(vol["type"], "line")
        self.assertEqual(vol["series"][0]["name"], "BTC 30d realized vol (% ann.)")
        self.assertEqual(vol["series"][1]["name"], "VIX")
        self.assertEqual(vol["series"][0]["color"], "#4285F4")
        self.assertEqual(vol["series"][1]["color"], "#DB4437")
        self.assertEqual(vol["x"]["title"], "")
        self.assertIn("VIX", vol["y"][0]["title"])
        march = vol["categories"].index("2020-03")
        self.assertGreater(vol["series"][0]["data"][march], 150)
        self.assertGreater(vol["series"][1]["data"][march], 50)
        ma = by_gid[1810821762]
        self.assertTrue(ma["y"][0]["log"])
        self.assertEqual(ma["x"]["title"], "")
        self.assertGreater(min(ma["series"][0]["data"]), 0)
        sp = by_gid[1540677114]
        self.assertEqual(sp["title"], "The S&P 500 in today's dollars (CPI-adjusted)")
        self.assertEqual(len(sp["series"]), 2)
        self.assertEqual(sp["x"]["title"], "")

    def test_pilot_leverage_and_reserve(self):
        charts, _ = sheets_data.convert_bundle(sheets_data.sample_bundles()[1])
        by_gid = {c["gid"]: c for c in charts if c.get("supported")}
        lev = by_gid[1690294676]
        self.assertEqual(lev["series"][0]["color"], "#F7931A")
        self.assertEqual(lev["series"][1]["color"], "#4285F4")
        self.assertEqual(lev["y"][0]["title"], "%")
        self.assertEqual(lev["series"][1]["data"][0], 0)
        self.assertGreater(lev["series"][1]["data"][-1], 40)
        reserve = by_gid[49428948]
        self.assertEqual(reserve["series"][0]["color"], "#8B5CF6")
        self.assertEqual(reserve["series"][1]["color"], "#F7931A")
        self.assertIn(None, reserve["series"][0]["data"])
        self.assertFalse(reserve["connectNulls"])

    def test_missing_color_uses_house_palette(self):
        rows = [["Date", "Bitcoin", "Other"], ["2024-01-01", 10, 3], ["2024-02-01", 12, 4]]
        series = [
            {"series": {"sourceRange": {"sources": [sheets_data._grid(42, 0, 1, 3, 2)]}}, "targetAxis": "LEFT_AXIS"},
            {"series": {"sourceRange": {"sources": [sheets_data._grid(42, 0, 2, 3, 3)]}}, "targetAxis": "LEFT_AXIS"},
        ]
        charts, _ = sheets_data.convert_bundle(_one(
            "LINE", rows, series=series, legend="BOTTOM_LEGEND",
            axes=[{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS", "title": ""}],
        ))
        got = charts[0]
        self.assertEqual(got["series"][0]["color"], "#F7931A")
        self.assertEqual(got["series"][1]["color"], "#8B5CF6")
        self.assertEqual(got["x"]["title"], "")

    def test_explicit_color_wins_over_bitcoin_orange(self):
        rows = [["Date", "BTC price"], ["2024-01-01", 100], ["2024-02-01", 110]]
        charts, _ = sheets_data.convert_bundle(_one("LINE", rows, series=[
            sheets_data._series(sheets_data._grid(42, 0, 1, 3, 2), "#4285F4"),
        ]))
        self.assertEqual(charts[0]["series"][0]["color"], "#4285F4")

    def test_combo_dual_axis(self):
        rows = [["Date", "Price", "Volume"], ["2024-01-01", 10, 1000], ["2024-02-01", 12, 1500], ["2024-03-01", 9, 800]]
        series = [
            sheets_data._series(sheets_data._grid(42, 0, 1, 4, 2), "#F7931A", typ="LINE", axis="LEFT_AXIS"),
            sheets_data._series(sheets_data._grid(42, 0, 2, 4, 3), "#8B5CF6", typ="COLUMN", axis="RIGHT_AXIS"),
        ]
        axes = [
            {"position": "BOTTOM_AXIS", "title": "Date"},
            {"position": "LEFT_AXIS", "title": "USD"},
            {"position": "RIGHT_AXIS", "title": "Shares"},
        ]
        charts, _ = sheets_data.convert_bundle(_one("COMBO", rows, series=series, axes=axes, legend="BOTTOM_LEGEND"))
        got = charts[0]
        self.assertTrue(got["supported"])
        self.assertEqual(got["type"], "combo")
        self.assertEqual(got["series"][0]["type"], "line")
        self.assertEqual(got["series"][0]["axis"], "left")
        self.assertEqual(got["series"][1]["type"], "column")
        self.assertEqual(got["series"][1]["axis"], "right")
        self.assertEqual([a["title"] for a in got["y"]], ["USD", "Shares"])
        self.assertEqual(got["x"]["title"], "")

    def test_pie_candlestick_and_3d_are_skipped(self):
        sheet_id = 77
        pie = {
            "chartId": 9,
            "spec": {"title": "A pie", "pieChart": {"legendPosition": "BOTTOM_LEGEND"}},
            "position": {"overlayPosition": {"anchorCell": {"rowIndex": 0, "columnIndex": 0}}},
        }
        candle = {
            "chartId": 10,
            "spec": {"title": "Candles", "candlestickChart": {"data": []}},
            "position": {"overlayPosition": {"anchorCell": {"rowIndex": 20, "columnIndex": 0}}},
        }
        three = sheets_data._chart(
            11, "Solid", "COLUMN",
            sheets_data._grid(sheet_id, 0, 0, 3, 1),
            [sheets_data._series(sheets_data._grid(sheet_id, 0, 1, 3, 2), "#F7931A")],
            40, [{"position": "BOTTOM_AXIS", "title": ""}, {"position": "LEFT_AXIS"}],
        )
        three["spec"]["basicChart"]["threeDimensional"] = True
        rows = [["Label", "Value"], ["A", 1], ["B", 2]]
        bundle = _bundle(sheet_id, "Bot experiments", [pie, candle, three], rows)
        charts, warnings = sheets_data.convert_bundle(bundle)
        kinds = [c.get("type") for c in charts]
        self.assertIn("pie", kinds)
        self.assertIn("candlestick", kinds)
        self.assertTrue(any("3D" in (c.get("reason") or "") for c in charts))
        self.assertFalse(any(c.get("supported") for c in charts))
        self.assertTrue(warnings)

    def test_anchor_order_not_api_order(self):
        sheet_id = 88
        rows = [["Date", "A", "B"], ["2024-01-01", 1, 2], ["2024-02-01", 3, 4]]

        def colchart(cid, title, col, row):
            return sheets_data._chart(
                cid, title, "COLUMN",
                sheets_data._grid(sheet_id, 0, 0, 3, 1),
                [sheets_data._series(sheets_data._grid(sheet_id, 0, col, 3, col + 1), "#F7931A")],
                row, [{"position": "BOTTOM_AXIS", "title": "Year"}, {"position": "LEFT_AXIS"}],
            )
        # API order is bottom chart first. Anchor row puts the top one at n=1.
        charts_in = [colchart(2, "Lower", 2, 30), colchart(1, "Upper", 1, 0)]
        bundle = _bundle(sheet_id, "Bot experiments", charts_in, rows)
        charts, _ = sheets_data.convert_bundle(bundle)
        supported = [c for c in charts if c.get("supported")]
        self.assertEqual([(c["n"], c["title"]) for c in supported], [(1, "Upper"), (2, "Lower")])
        self.assertTrue(all(c["x"]["title"] == "" for c in supported))

    def test_date_serials_and_formatted_labels(self):
        # 2024-01-01 and 2024-02-01 as Sheets serials (1899-12-30 epoch).
        serials = [45292, 45323]
        self.assertEqual(sheets_data.serial_to_iso(serials[0]), "2024-01-01")
        rows = [["Date", "Value"], [serials[0], 5], [serials[1], 6]]
        charts, _ = sheets_data.convert_bundle(_one("LINE", rows))
        self.assertEqual(charts[0]["categories"], ["2024-01-01", "2024-02-01"])
        self.assertEqual(charts[0]["x"]["title"], "")
        formatted = [["Date", "Value"], ["Jan 2024", "5"], ["Feb 2024", "6"]]
        charts, _ = sheets_data.convert_bundle(_one("LINE", rows, formatted=formatted))
        self.assertEqual(charts[0]["categories"], ["Jan 2024", "Feb 2024"])

    def test_horizontal_bar_and_percent_text(self):
        rows = [["Name", "Share"], ["Strategy", "31%"], ["Strive", "65%"]]
        chart = sheets_data._chart(
            3, "Leverage snapshot", "BAR",
            sheets_data._grid(42, 0, 0, 3, 1),
            [sheets_data._series(sheets_data._grid(42, 0, 1, 3, 2), "#F7931A")],
            0, [{"position": "LEFT_AXIS", "title": "Year"}, {"position": "BOTTOM_AXIS", "title": "%"}],
        )
        bundle = _bundle(42, "Bot experiments", [chart], rows)
        charts, _ = sheets_data.convert_bundle(bundle)
        got = charts[0]
        self.assertEqual(got["type"], "bar")
        self.assertTrue(got["horizontal"])
        self.assertEqual(got["series"][0]["data"], [31, 65])
        self.assertEqual(got["y"][0]["format"], "percent")
        self.assertEqual(got["x"]["title"], "")

    def test_stacked_percent(self):
        rows = [["Year", "A", "B"], ["2020", 25, 75], ["2021", 40, 60]]
        series = [
            sheets_data._series(sheets_data._grid(42, 0, 1, 3, 2), "#F7931A"),
            sheets_data._series(sheets_data._grid(42, 0, 2, 3, 3), "#8B5CF6"),
        ]
        chart = sheets_data._chart(
            4, "Mix", "COLUMN",
            sheets_data._grid(42, 0, 0, 3, 1),
            series, 0,
            [{"position": "BOTTOM_AXIS", "title": ""}, {"position": "LEFT_AXIS", "title": ""}],
            stacked="PERCENT_STACKED", legend="BOTTOM_LEGEND",
        )
        charts, _ = sheets_data.convert_bundle(_bundle(42, "Bot experiments", [chart], rows))
        got = charts[0]
        self.assertEqual(got["stacked"], "percent")
        self.assertEqual(got["series"][0]["data"], [25, 40])
        self.assertEqual(got["series"][1]["data"], [75, 60])
        self.assertEqual(got["y"][0]["format"], "percent")

    def test_comma_and_dollar_strings(self):
        rows = [["Date", "Amount"], ["2024-01-01", "$1,234"], ["2024-02-01", "(20)"]]
        charts, _ = sheets_data.convert_bundle(_one("COLUMN", rows))
        self.assertEqual(charts[0]["series"][0]["data"], [1234, -20])

    def test_headerless_series_name(self):
        rows = [[10], [12]]
        # No header row. Domain and series still need two columns.
        rows = [["2024-01-01", 10], ["2024-02-01", 12]]
        chart = sheets_data._chart(
            5, "Bare", "LINE",
            sheets_data._grid(42, 0, 0, 2, 1),
            [sheets_data._series(sheets_data._grid(42, 0, 1, 2, 2), "#F7931A")],
            0, [{"position": "BOTTOM_AXIS", "title": ""}, {"position": "LEFT_AXIS"}],
            header=0,
        )
        charts, _ = sheets_data.convert_bundle(_bundle(42, "Bot experiments", [chart], rows))
        self.assertEqual(charts[0]["series"][0]["name"], "Series 1")
        self.assertEqual(charts[0]["categories"], ["2024-01-01", "2024-02-01"])

    def test_excluded_tab_is_not_written_and_stale_file_is_removed(self):
        gid = 333185169
        self.assertIn(("bot1", gid), sheets_catalog.EXCLUDED_TABS)
        rows = [["Date", "Value"], ["2024-01-01", 1], ["2024-02-01", 2]]
        bundle = _one("LINE", rows, sheet_id=gid, title="should not return", sheet_title="87 TSLA beta to Nasdaq")
        repo = tempfile.mkdtemp(prefix="sheets-data-")
        try:
            stale = os.path.join(repo, "static", "sheets", "data", "bot1")
            os.makedirs(stale)
            with open(os.path.join(stale, "%s.json" % gid), "w", encoding="utf-8") as f:
                f.write('{"book":"bot1","gid":%s,"n":1,"supported":true}\n' % gid)
            result = sheets_data.export_bundles([bundle], repo)
            self.assertFalse(os.path.exists(os.path.join(stale, "%s.json" % gid)))
            self.assertTrue(any("excluded" in w for w in result["warnings"]))
            index = json.load(open(os.path.join(repo, "static", "sheets", "data", "index.json"), encoding="utf-8"))
            self.assertFalse(any(c.get("gid") == gid for c in index["charts"]))
            self.assertEqual(result["written"], [])
        finally:
            shutil.rmtree(repo)

    def test_multi_chart_without_anchors_keeps_existing_file(self):
        sheet_id = 55
        bare = {"chartId": 1, "spec": {"title": "One", "basicChart": {"chartType": "LINE"}}}
        other = {"chartId": 2, "spec": {"title": "Two", "basicChart": {"chartType": "LINE"}}}
        bundle = {
            "spreadsheet": {
                "spreadsheetId": BOT1,
                "properties": {"title": "Bot experiments"},
                "sheets": [{"properties": {"sheetId": sheet_id, "title": "Multi", "index": 0}, "charts": [bare, other]}],
            },
            "values": {"valueRanges": []},
        }
        repo = tempfile.mkdtemp(prefix="sheets-data-hold-")
        try:
            folder = os.path.join(repo, "static", "sheets", "data", "bot1")
            os.makedirs(folder)
            kept = os.path.join(folder, "%s.json" % sheet_id)
            with open(kept, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "book": "bot1", "gid": sheet_id, "n": 1, "supported": True,
                           "title": "Kept", "type": "line", "categories": ["2024-01-01"],
                           "x": {"title": ""}, "y": [{"title": "", "format": "number"}],
                           "series": [{"name": "A", "type": "line", "axis": "left", "color": "#F7931A", "data": [1]}]}, f)
            result = sheets_data.export_bundles([bundle], repo)
            self.assertTrue(any("no position" in w for w in result["warnings"]))
            self.assertTrue(os.path.isfile(kept))
            self.assertEqual(json.load(open(kept, encoding="utf-8"))["title"], "Kept")
        finally:
            shutil.rmtree(repo)

    def test_export_rewrites_index_and_check_passes(self):
        repo = tempfile.mkdtemp(prefix="sheets-data-ok-")
        try:
            sheets_data.export_bundles(sheets_data.sample_bundles(), repo)
            problems = sheets_data.check_data(repo)
            self.assertEqual(problems, [])
            index = json.load(open(os.path.join(repo, "static", "sheets", "data", "index.json"), encoding="utf-8"))
            keys = {(c["book"], c["gid"], c["n"]) for c in index["charts"]}
            self.assertIn(("bot3", 1145735379, 1), keys)
            self.assertIn(("bot3", 1145735379, 4), keys)
            self.assertIn(("bot2", 1690294676, 1), keys)
            self.assertIn(("bot1", 1810821762, 1), keys)
            for book, gid in sheets_catalog.EXCLUDED_TABS:
                self.assertFalse(any(c["book"] == book and c["gid"] == gid for c in index["charts"]))
        finally:
            shutil.rmtree(repo)


class LiveFlagTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for key in list(os.environ):
            if key.startswith("GOOGLE_") or key.startswith("CHARTMAKER_GOOGLE_") or key == "SHEETS_LIVE":
                os.environ.pop(key, None)
        sheets_data._LIVE_CACHE.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        sheets_data._LIVE_CACHE.clear()

    def test_off_does_not_fetch(self):
        def boom(*_a, **_k):
            raise AssertionError("fetch should not run")
        body, code = sheets_data.live_chart_response("bot1", "1546024641", "1", fetch=boom)
        self.assertEqual(code, 404)
        self.assertFalse(body["live"])
        self.assertEqual(body["fallback"], "static")

    def test_retired_tab_does_not_fetch(self):
        os.environ["SHEETS_LIVE"] = "1"
        os.environ["GOOGLE_ACCESS_TOKEN"] = "should-not-be-sent"

        def boom(*_a, **_k):
            raise AssertionError("fetch should not run")
        body, code = sheets_data.live_chart_response("bot1", "333185169", "1", fetch=boom)
        self.assertEqual(code, 410)
        self.assertNotIn("should-not-be-sent", json.dumps(body))

    def test_missing_credentials_lists_names_only(self):
        os.environ["SHEETS_LIVE"] = "1"
        body, code = sheets_data.live_chart_response("bot3", "1145735379", "1", fetch=lambda *_a, **_k: None)
        self.assertEqual(code, 503)
        text = json.dumps(body)
        self.assertIn("GOOGLE_ACCESS_TOKEN", text)
        self.assertIn("spreadsheets.readonly", text)
        self.assertIn("SHEETS_LIVE=1", text)
        self.assertNotIn("private_key", text)
        self.assertNotIn("ya29", text)
        status = sheets_data.credential_status()
        self.assertFalse(status["configured"])

    def test_service_account_path_is_not_echoed(self):
        os.environ["SHEETS_LIVE"] = "1"
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/does-not-exist-sa.json"
        body, code = sheets_data.live_chart_response("bot1", "1", "1")
        self.assertEqual(code, 503)
        self.assertNotIn("/tmp/does-not-exist-sa.json", json.dumps(body))
        self.assertTrue(sheets_data.credential_status()["serviceAccountFileMissing"])


class CommittedSnapshotTest(unittest.TestCase):
    def test_pilot_files_are_checked_in(self):
        self.assertEqual(sheets_data.check_data(ROOT), [])
        with open(os.path.join(ROOT, "static", "sheets", "data", "index.json"), encoding="utf-8") as f:
            index = json.load(f)
        keys = {(c["book"], c["gid"], c["n"]) for c in index["charts"]}
        self.assertEqual(keys, {
            ("bot3", 1145735379, 1), ("bot3", 1145735379, 2),
            ("bot3", 1145735379, 3), ("bot3", 1145735379, 4),
            ("bot2", 1690294676, 1), ("bot2", 49428948, 1),
            ("bot1", 1546024641, 1), ("bot1", 1810821762, 1), ("bot1", 1540677114, 1),
        })
        for book in ("bot1", "bot2", "bot3"):
            path = os.path.join(FIXTURES, book + ".json")
            self.assertTrue(os.path.isfile(path), path)
        for book, gid in sheets_catalog.EXCLUDED_TABS:
            self.assertFalse(any(c["book"] == book and c["gid"] == gid for c in index["charts"]))


class FrontendHookTest(unittest.TestCase):
    def test_page_offers_interactive_and_keeps_png(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("button.live", html)
        self.assertIn("dataZoom", html)
        self.assertIn("View PNG still works", html)
        self.assertIn("__sheetLiveRender", html)
        self.assertIn("sheets/data/index.json", html)
        self.assertIn("/api/sheets/chart", html)


if __name__ == "__main__":
    unittest.main()
