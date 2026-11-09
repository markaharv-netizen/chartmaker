"""BTC Treasury chart columns, and the snapshot the weekday sync rewrites.

The chart in static/index.html draws rank, company, ticker, country, BTC,
and Change. It does not draw USD or % of 21M. scripts/refresh_btctop100.py
rewrites btctop100.json only, and that file does not store those fields.
/api/treasury still returns usd and pctSupply for anything that already
reads the response.
"""
import importlib.util
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
INDEX = os.path.join(ROOT, "static", "index.html")
SNAPSHOT = os.path.join(ROOT, "btctop100.json")
SCRIPT = os.path.join(ROOT, "scripts", "refresh_btctop100.py")

COMPANY_KEYS = {
    "rank", "name", "ticker", "country", "change", "btc", "asOf",
    "source", "sourceLabel", "sourceUrl", "notes",
}
DRAWN_HEADERS = ["#", "Company", "Ticker", "Country", "BTC", "Change"]


def load_refresh():
    spec = importlib.util.spec_from_file_location("refresh_btctop100", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tdraw_source():
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    start = html.index("function tdraw()")
    end = html.index("async function trenderFresh")
    return html[start:end]


def sheet(rows):
    """A BTCTC-shaped grid: title, header, then one row per company."""
    grid = [[
        "", "Corporate Bitcoin Treasury Holdings",
    ], [
        "", "Rank", "Company", "Ticker", "Country", "Change", "BTC Holdings",
        "", "As-of Date", "Source", "Source Link", "Notes",
    ]]
    for n, btc, change in rows:
        grid.append([
            "", str(n), f"Co {n}", f"T{n:03d}", "US", change, str(btc), "",
            "2026-10-18", "A filing",
            '=HYPERLINK("https://example.com/a","SEC 8-K")', "",
        ])
    return grid


class TreasuryChartColumnsTest(unittest.TestCase):
    def test_drawn_headers_omit_usd_and_supply_share(self):
        src = tdraw_source()
        headers = re.findall(r"hy,'([^']*)'", src)
        self.assertEqual(headers, DRAWN_HEADERS)
        self.assertNotIn("USD", headers)
        self.assertNotIn("% of 21M", headers)
        self.assertNotIn("tUsd(", src)
        self.assertNotIn("pctSupply", src)
        self.assertNotIn("usdW", src)
        self.assertNotIn("pctW", src)
        self.assertNotIn("% of 21M", src)
        # Change is the right edge, so the removed columns do not leave a gap.
        self.assertIn("chgR=right", src.replace(" ", ""))
        self.assertIn("tBtc(r.btc)", src)
        self.assertIn("tChg(r.change)", src)
        self.assertIn("String(r.rank)", src)
        self.assertIn("r.name", src)
        self.assertIn("r.ticker", src)
        self.assertIn("r.country", src)
        # The remaining total is BTC holdings. The dollar total and supply share are not drawn.
        self.assertIn("Top ${rows.length}: ${tBtc(totB)} BTC", src)
        self.assertNotIn("totU", src)
        self.assertNotIn("totP", src)
        self.assertNotIn("21M supply", src)
        self.assertIn("fitChart()", src)
        with open(INDEX, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("function fitChart()", page)
        self.assertIn("avail<1600", page.replace(" ", ""))

    def test_rows_stay_in_snapshot_order(self):
        src = tdraw_source()
        self.assertIn("rows[c*per+i]", src)
        self.assertNotIn(".sort(", src)


class TreasurySnapshotTest(unittest.TestCase):
    def test_committed_snapshot_has_no_display_fields(self):
        with open(SNAPSHOT, encoding="utf-8") as f:
            doc = json.load(f)
        refresh = load_refresh()
        refresh.validate(doc)
        self.assertEqual(len(doc["companies"]), 100)
        prev = None
        for i, c in enumerate(doc["companies"]):
            self.assertEqual(c["rank"], i + 1)
            self.assertTrue(COMPANY_KEYS.issuperset(c.keys()), sorted(c.keys()))
            self.assertNotIn("usd", c)
            self.assertNotIn("pctSupply", c)
            if prev is not None:
                self.assertLessEqual(c["btc"], prev)
            prev = c["btc"]

    def test_sync_build_does_not_emit_usd_or_supply_share(self):
        refresh = load_refresh()
        rows = [(n, 20000 - n, "+810" if n == 1 else "") for n in range(1, 101)]
        doc = refresh.build(sheet(rows))
        self.assertEqual([c["rank"] for c in doc["companies"]], list(range(1, 101)))
        self.assertEqual(doc["companies"][0]["change"], "+810")
        self.assertIsNone(doc["companies"][1]["change"])
        for c in doc["companies"]:
            self.assertEqual(set(c), COMPANY_KEYS)
        # The sync writes the snapshot and nothing else.
        self.assertEqual(os.path.basename(refresh.OUT), "btctop100.json")
        self.assertEqual(os.path.dirname(refresh.OUT), refresh.ROOT)
        with open(SCRIPT, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("index.html", text)
        self.assertIn("btctop100.json", text)

    def test_api_still_returns_fields_the_chart_ignores(self):
        import heat
        import treasury

        def fake_quotes(symbols, fresh=False, stale=None):
            if stale is not None:
                stale.append("BTC-USD")
            return {"BTC-USD": {"regularMarketPrice": 100_000, "regularMarketTime": 1_700_000_000}}

        orig = heat.quotes
        heat.quotes = fake_quotes
        try:
            out = treasury.compute(fresh=True)
        finally:
            heat.quotes = orig
        self.assertEqual(len(out["rows"]), 100)
        self.assertEqual([r["rank"] for r in out["rows"]], list(range(1, 101)))
        with open(SNAPSHOT, encoding="utf-8") as f:
            snap_btc = json.load(f)["companies"][0]["btc"]
        self.assertEqual(out["rows"][0]["btc"], snap_btc)
        btc = out["rows"][0]["btc"]
        self.assertEqual(out["rows"][0]["usd"], btc * 100_000)
        self.assertAlmostEqual(out["rows"][0]["pctSupply"], btc / 21_000_000 * 100)
        self.assertTrue(any("cached" in f for f in out["flags"]))
        for r in out["rows"]:
            for key in ("rank", "name", "ticker", "country", "btc", "change"):
                self.assertIn(key, r)

    def test_api_omits_usd_when_the_quote_is_missing(self):
        import heat
        import treasury

        def fake_quotes(symbols, fresh=False, stale=None):
            raise RuntimeError("no quote")

        orig = heat.quotes
        heat.quotes = fake_quotes
        try:
            out = treasury.compute(fresh=True)
        finally:
            heat.quotes = orig
        self.assertIsNone(out["btcPrice"])
        self.assertIsNone(out["rows"][0]["usd"])
        self.assertTrue(any("USD" in f for f in out["flags"]))
        self.assertGreater(out["rows"][0]["pctSupply"], 0)


if __name__ == "__main__":
    unittest.main()
