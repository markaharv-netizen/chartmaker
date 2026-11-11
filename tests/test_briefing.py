"""Daily briefing diffs. No network. Fixtures are in memory; one test reads the repo files."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import briefing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


def row(ticker, **kw):
    base = {
        "ticker": ticker, "base": 10, "bear": 1, "bull": 20, "consensus": 12,
        "analysts": 3, "rating": "HOLD", "notes": "note", "why": "because",
        "previousDetail": "", "baseChangedAt": None, "previousBase": None,
    }
    base.update(kw)
    return base


def company(ticker, btc, rank, name=None, change=None):
    return {"ticker": ticker, "name": name or ticker, "btc": btc, "rank": rank, "change": change}


class PriceTargetTest(unittest.TestCase):
    def test_window_diffs_against_the_snapshot_before_it(self):
        baseline = {"generated": "2026-10-30T14:00:00Z", "rows": [
            row("AAA", base=10, rating="HOLD", consensus=12, analysts=3),
            row("BBB", base=5),
            row("DDD", previousDetail="Prev Base $1 -> $10 at Oct 22, 2026; unchanged in Oct 30 scans"),
        ]}
        current = {"generated": "2026-11-09T13:00:00Z", "rows": [
            row("AAA", base=14, previousBase=10, baseChangedAt="2026-11-02", rating="BUY",
                consensus=15, analysts=4, notes="rewritten note", why="because"),
            row("CCC", base=8, rating="HOLD", consensus=None, analysts=None, why="fresh name",
                previousDetail="New model added Nov 5, 2026 (v1). Nov 9, 2026: Q3 checked; valuation unchanged."),
            row("DDD", previousDetail="Prev Base $1 -> $10 at Oct 22, 2026; unchanged in Oct 30, Nov 2 and Nov 4 scans"),
        ]}
        section = briefing.price_target_section(current, [baseline])
        self.assertEqual(section["asOf"], "2026-11-09")
        self.assertEqual(section["since"], "2026-10-30")
        self.assertFalse(section["empty"])
        by = {item["ticker"]: item for item in section["changes"]}
        self.assertEqual(set(by), {"AAA", "CCC", "BBB"})
        self.assertNotIn("DDD", by)
        self.assertEqual(by["AAA"]["kind"], "update")
        self.assertEqual(by["AAA"]["date"], "2026-11-02")
        self.assertTrue(any(line.startswith("Base $10.00 → $14.00") for line in by["AAA"]["lines"]))
        self.assertTrue(any(line.startswith("Rating HOLD → BUY") for line in by["AAA"]["lines"]))
        self.assertTrue(any("Street consensus" in line for line in by["AAA"]["lines"]))
        self.assertEqual(by["CCC"]["kind"], "new")
        self.assertEqual(by["CCC"]["date"], "2026-11-05")
        self.assertTrue(any("valuation unchanged" in line for line in by["CCC"]["lines"]))
        self.assertEqual(by["BBB"]["kind"], "removed")
        self.assertTrue(all(item["href"].startswith("/?tab=pt") for item in section["changes"]))

    def test_empty_state_names_the_baseline_date(self):
        snap = {"generated": "2026-10-30T14:00:00Z", "rows": [row("AAA")]}
        current = {"generated": "2026-11-09T13:00:00Z", "rows": [row("AAA")]}
        section = briefing.price_target_section(current, [snap])
        self.assertTrue(section["empty"])
        self.assertEqual(section["emptyMessage"], "No price target changes since Oct 30, 2026.")
        self.assertEqual(section["changes"], [])

    def test_missing_file_is_honest(self):
        section = briefing.price_target_section(None, [])
        self.assertTrue(section["empty"])
        self.assertIn("not available", section["emptyMessage"])

    def test_embedded_dates_when_there_is_no_earlier_snapshot(self):
        current = {"generated": "2026-11-09T13:00:00Z", "rows": [
            row("OLD", base=10, previousBase=8, baseChangedAt="2026-10-01"),
            row("NEW", base=3, previousBase=1, baseChangedAt="2026-11-04", rating="SELL"),
        ]}
        section = briefing.price_target_section(current, [])
        tickers = [item["ticker"] for item in section["changes"]]
        self.assertEqual(tickers, ["NEW"])
        self.assertIn("Nov 2, 2026", section["emptyMessage"] or section["sinceLabel"])


class SentimentTest(unittest.TestCase):
    def test_thin_sample_is_not_ranked_as_a_swing(self):
        board = {"asOf": "2026-11-10T16:05:00-05:00", "tickers": [
            {"symbol": "META", "name": "Meta", "bull": 1.0, "classified": 1, "n_bull": 1, "n_bear": 0},
            {"symbol": "TSLA", "name": "Tesla", "bull": 0.4, "classified": 11, "n_bull": 5, "n_bear": 6},
        ]}
        history = {"daily": {
            "2026-11-02": {"META": 0.45, "TSLA": 0.8},
            "2026-11-10": {"META": 1.0, "TSLA": 0.4},
        }}
        section = briefing.sentiment_section(board, history)
        self.assertEqual(section["asOf"], "2026-11-10")
        self.assertEqual(section["previous"], "2026-11-02")
        self.assertEqual([item["symbol"] for item in section["swings"]], ["TSLA"])
        self.assertEqual(section["swings"][0]["points"], -40.0)
        self.assertFalse(section["swings"][0]["thin"])
        self.assertEqual(section["thin"][0]["symbol"], "META")
        self.assertTrue(section["thin"][0]["thin"])
        self.assertEqual(section["thin"][0]["classified"], 1)
        self.assertLess(section["thin"][0]["classified"], briefing.THIN_ITEMS)

    def test_no_previous_point_names_the_board_date(self):
        board = {"asOf": "2026-11-10T16:05:00-05:00", "tickers": [
            {"symbol": "TSLA", "name": "Tesla", "bull": 0.4, "classified": 11},
        ]}
        section = briefing.sentiment_section(board, {"daily": {"2026-11-10": {"TSLA": 0.4}}})
        self.assertTrue(section["empty"])
        self.assertIn("No earlier sentiment board", section["emptyMessage"])
        self.assertIn("Nov 10, 2026", section["emptyMessage"])
        self.assertEqual(section["swings"], [])


class TreasuryTest(unittest.TestCase):
    def test_dust_is_ignored_and_entrants_are_kept(self):
        prior = {"fetchedAt": "2026-11-09T16:00:00Z", "companies": [
            company("MARA", 100, 1),
            company("DUST", 10, 2),
            company("GONE", 5, 3),
        ]}
        current = {"fetchedAt": "2026-11-10T16:00:00Z", "companies": [
            company("MARA", 80, 1),
            company("DUST", 10.4, 2),
            company("NEW", 7, 3, name="New Co", change="+2"),
        ]}
        section = briefing.treasury_section(current, [prior])
        self.assertEqual(section["asOfLabel"], "Nov 10, 2026")
        self.assertEqual(section["previousLabel"], "Nov 9, 2026")
        self.assertEqual([item["ticker"] for item in section["changes"]], ["MARA"])
        self.assertAlmostEqual(section["changes"][0]["delta"], -20)
        self.assertEqual(section["entered"][0]["ticker"], "NEW")
        self.assertEqual(section["left"][0]["ticker"], "GONE")
        self.assertFalse(section["empty"])

    def test_no_prior_snapshot_names_the_as_of_date(self):
        current = {"fetchedAt": "2026-11-10T16:00:00Z", "companies": [company("MARA", 80, 1)]}
        section = briefing.treasury_section(current, [])
        self.assertTrue(section["empty"])
        self.assertIn("No earlier treasury snapshot", section["emptyMessage"])
        self.assertIn("Nov 10, 2026", section["emptyMessage"])
        self.assertEqual(section["changes"], [])

    def test_archive_stores_each_refresh_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "btctop100_history.json")
            first = {"fetchedAt": "2026-11-09T16:00:00Z", "companies": [company("MARA", 100, 1)]}
            briefing.archive_treasury(first, path)
            briefing.archive_treasury(first, path)
            second = {"fetchedAt": "2026-11-10T16:00:00Z", "companies": [company("MARA", 80, 1)]}
            briefing.archive_treasury(second, path)
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual([snap["fetchedAt"] for snap in saved["snapshots"]],
                             ["2026-11-09T16:00:00Z", "2026-11-10T16:00:00Z"])


class ChartsTest(unittest.TestCase):
    def test_excluded_tabs_are_dropped(self):
        bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
        bot2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
        doc = {"date": "2026-11-10", "theme": "A Tuesday.", "items": [
            {"title": "Kept chart", "sheet": "Bot experiments", "tab": "52 Core goods",
             "image": "cotd/2026-11-10/01.png",
             "sheet_url": f"https://docs.google.com/spreadsheets/d/{bot1}/edit#gid=200447618"},
            {"title": "Retired tab", "sheet": "Bot experiments", "tab": "87 TSLA beta",
             "sheet_url": f"https://docs.google.com/spreadsheets/d/{bot1}/edit#gid=333185169"},
            {"title": "Red dot tracker", "sheet": "bot experiments 2", "tab": "69",
             "sheet_url": f"https://docs.google.com/spreadsheets/d/{bot2}/edit#gid=645072508"},
            {"title": "Bad image", "sheet": "Elsewhere", "tab": "x", "image": "cotd/../../secret.png",
             "sheet_url": "https://example.com/not-a-sheet"},
        ]}
        section = briefing.charts_section(doc)
        titles = [item["title"] for item in section["items"]]
        self.assertEqual(section["asOf"], "2026-11-10")
        self.assertIn("Kept chart", titles)
        self.assertNotIn("Retired tab", titles)
        self.assertNotIn("Red dot tracker", titles)
        bad = next(item for item in section["items"] if item["title"] == "Bad image")
        self.assertEqual(bad["image"], "")
        self.assertTrue(bad["href"].startswith("https://"))

    def test_missing_edition_names_the_file(self):
        section = briefing.charts_section(None)
        self.assertTrue(section["empty"])
        self.assertIn("static/cotd/today.json", section["emptyMessage"])


class PaperTest(unittest.TestCase):
    def test_nav_change_uses_the_last_two_marks(self):
        book = {
            "generated": "2026-11-10T13:00:00Z", "started": True, "waiting": False,
            "config": {"startingCash": 100000},
            "marks": [
                {"date": "2026-11-09", "nav": 110},
                {"date": "2026-11-06", "nav": 100},
            ],
        }
        section = briefing.paper_section(book)
        self.assertEqual(section["markDate"], "2026-11-09")
        self.assertEqual(section["prevMarkDate"], "2026-11-06")
        self.assertEqual(section["change"], 10)
        self.assertIn("Nov 9, 2026", section["message"])
        self.assertIn("Nov 6, 2026", section["message"])
        self.assertIn("up", section["message"])
        self.assertEqual(section["href"], "/?tab=paper")

    def test_waiting_book_does_not_invent_a_change(self):
        section = briefing.paper_section({
            "generated": "2026-11-10T13:00:00Z", "started": False, "waiting": True, "marks": [],
        })
        self.assertTrue(section["empty"])
        self.assertIn("waiting for weights", section["message"])
        self.assertNotIn("change", section)


class LiveFilesTest(unittest.TestCase):
    def test_repo_briefing_has_an_as_of_for_every_section(self):
        doc = briefing.build(ROOT)
        for key in ("priceTargets", "paper", "sentiment", "treasury", "charts"):
            section = doc[key]
            self.assertTrue(section.get("asOf") or section.get("emptyMessage"), key)
            self.assertTrue(section.get("href", "").startswith("/?tab="), key)
        for change in doc["treasury"]["changes"]:
            self.assertGreaterEqual(abs(change["delta"]), briefing.MIN_BTC)
        for swing in doc["sentiment"]["swings"]:
            self.assertGreaterEqual(swing["classified"], briefing.THIN_ITEMS)
            self.assertFalse(swing["thin"])
        for swing in doc["sentiment"]["thin"]:
            self.assertLess(swing["classified"], briefing.THIN_ITEMS)
        for item in doc["charts"]["items"]:
            self.assertFalse(briefing.chart_excluded({
                "sheet_url": item["href"] if "gid=" in item["href"] else "",
            }))
        self.assertTrue(doc["priceTargets"]["asOf"])
        self.assertTrue(doc["sentiment"]["previous"] or doc["sentiment"]["empty"])

    def test_briefing_is_the_landing_tab(self):
        with open(INDEX, encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn('id="mBrief" class="on"', html)
        self.assertIn("/api/briefing", html)
        self.assertIn("briefing:'briefing'", html)
        self.assertIn("watch:'watch'", html)
        self.assertIn(":'briefing')", html)
        self.assertNotIn(":'watch')", html)


if __name__ == "__main__":
    unittest.main()
