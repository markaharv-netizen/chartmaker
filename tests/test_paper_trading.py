"""Paper book sized by paperWeight. No network."""
import datetime
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import paper_trading as pt
import price_targets

ET = ZoneInfo("America/New_York")


def at(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=ET)


def bars(**named):
    return {symbol: [{"date": d, "open": o, "close": c} for d, o, c in seq] for symbol, seq in named.items()}


def row(ticker, weight, rating="HOLD"):
    return {"ticker": ticker, "rating": rating, "paperWeight": weight}


def run(rows, prices, now, book=None):
    return pt.update_book(book, rows, prices, at(now))


def opened(rows, prices, morning="2026-11-06T08:30:00", later="2026-11-06T16:30:00"):
    """Weights are seen before the open, then the book is run again once that open can fill."""
    return run(rows, prices, later, run(rows, prices, morning))


def pos(book, ticker):
    return book["positions"][ticker]


class PaperWeightTest(unittest.TestCase):
    def test_missing_weight_keeps_the_book_in_cash(self):
        prices = bars(AAA=[("2026-11-06", 100, 110)], SPY=[("2026-11-06", 500, 505)])
        book = run([{"ticker": "AAA", "rating": "SELL"}], prices, "2026-11-06T16:30:00")
        self.assertTrue(book["waiting"])
        self.assertFalse(book["started"])
        self.assertEqual(book["trades"], [])
        self.assertEqual(book["cash"], 100000)
        self.assertEqual(pos(book, "AAA")["side"], "cash")
        self.assertEqual(pos(book, "AAA")["rating"], "SELL")
        self.assertEqual(pos(book, "AAA")["shares"], 0)

    def test_first_weights_fill_at_the_next_open(self):
        prices = bars(AAA=[("2026-11-06", 100, 110)], SPY=[("2026-11-06", 500, 510)])
        early = run([row("AAA", 10, "BUY")], prices, "2026-11-06T08:30:00")
        self.assertFalse(early["started"])
        self.assertEqual(early["pending"]["executeOn"], "2026-11-06")
        self.assertEqual(pos(early, "AAA")["status"], "pending")
        done = run([row("AAA", 10, "BUY")], prices, "2026-11-06T10:00:00", early)
        self.assertTrue(done["started"])
        self.assertIsNone(done["pending"])
        self.assertEqual(pos(done, "AAA")["side"], "long")
        self.assertEqual(pos(done, "AAA")["entryPrice"], 100)
        self.assertAlmostEqual(pos(done, "AAA")["shares"], 100, places=6)
        self.assertAlmostEqual(done["cash"], 90000, places=2)
        self.assertEqual(done["inception"], "2026-11-06")
        self.assertEqual(done["spyInceptionOpen"], 500)

    def test_weight_resize_adds_at_the_next_open(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100), ("2026-11-09", 110, 110)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 500, 500)],
        )
        book = opened([row("AAA", 10)], prices)
        queued = run([row("AAA", 20)], prices, "2026-11-09T08:30:00", book)
        self.assertEqual(queued["pending"]["executeOn"], "2026-11-09")
        self.assertAlmostEqual(pos(queued, "AAA")["shares"], 100, places=6)
        done = run([row("AAA", 20)], prices, "2026-11-10T08:30:00", queued)
        # NAV at the Nov 9 open is 90,000 + 100*110 = 101,000. 20% is 20,200.
        self.assertAlmostEqual(pos(done, "AAA")["shares"] * 110, 20200, places=2)
        self.assertAlmostEqual(pos(done, "AAA")["entryPrice"], 19200 / pos(done, "AAA")["shares"], places=4)
        self.assertAlmostEqual(done["cash"] + pos(done, "AAA")["shares"] * 110, 101000, places=2)
        self.assertGreater(pos(done, "AAA")["shares"], 100)

    def test_long_to_short_flip_covers_then_shorts(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100), ("2026-11-09", 120, 100)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 500, 500)],
        )
        book = opened([row("AAA", 10, "BUY")], prices)
        queued = run([row("AAA", -10, "SELL")], prices, "2026-11-09T08:30:00", book)
        self.assertEqual(queued["pending"]["executeOn"], "2026-11-09")
        self.assertEqual(pos(queued, "AAA")["side"], "long")
        done = run([row("AAA", -10, "SELL")], prices, "2026-11-10T08:30:00", queued)
        self.assertEqual(pos(done, "AAA")["side"], "short")
        self.assertLess(pos(done, "AAA")["shares"], 0)
        self.assertEqual(pos(done, "AAA")["entryPrice"], 120)
        self.assertEqual(pos(done, "AAA")["entryDate"], "2026-11-09")
        actions = [t["action"] for t in done["trades"]]
        self.assertEqual(actions, ["buy", "sell", "short"])
        sell = done["trades"][1]
        self.assertAlmostEqual(sell["realized"], 2000, places=2)
        # Pre-trade NAV 102,000. Short 10% is -10,200, which is 85 shares at 120.
        self.assertAlmostEqual(pos(done, "AAA")["shares"], -85, places=4)
        self.assertAlmostEqual(pt.portfolio_nav(done, {"AAA": 120}.get), 102000, places=2)
        # Price falls to 100: short P&L is (120-100)*85.
        self.assertAlmostEqual(pos(done, "AAA")["shares"] * (100 - 120), 1700, places=2)
        self.assertAlmostEqual(pt.portfolio_nav(done, {"AAA": 100}.get), 103700, places=2)

    def test_short_mark_uses_the_close(self):
        prices = bars(AAA=[("2026-11-06", 100, 80)], SPY=[("2026-11-06", 500, 490)])
        book = opened([row("AAA", -10, "SELL")], prices, later="2026-11-07T08:30:00")
        self.assertEqual(pos(book, "AAA")["side"], "short")
        self.assertAlmostEqual(book["marks"][0]["nav"], 102000, places=2)
        self.assertGreater(book["marks"][0]["nav"], 100000)

    def test_gross_cap_scales_every_weight(self):
        spec = pt.weight_spec([row("AAA", 80), row("BBB", -40)], cap=100)
        self.assertTrue(spec["scaled"])
        self.assertAlmostEqual(spec["grossRequested"], 120, places=4)
        self.assertAlmostEqual(abs(spec["effective"]["AAA"]) + abs(spec["effective"]["BBB"]), 100, places=4)
        self.assertAlmostEqual(spec["effective"]["AAA"] / 80, spec["scale"], places=4)
        self.assertLess(spec["effective"]["BBB"], 0)
        prices = bars(
            AAA=[("2026-11-06", 50, 50)],
            BBB=[("2026-11-06", 25, 25)],
            SPY=[("2026-11-06", 500, 500)],
        )
        book = opened([row("AAA", 80), row("BBB", -40)], prices)
        long_mv = pos(book, "AAA")["shares"] * 50
        short_mv = pos(book, "BBB")["shares"] * 25
        self.assertAlmostEqual(long_mv, 100000 * spec["effective"]["AAA"] / 100, places=2)
        self.assertAlmostEqual(short_mv, 100000 * spec["effective"]["BBB"] / 100, places=2)
        self.assertAlmostEqual(abs(long_mv) + abs(short_mv), 100000, places=2)
        self.assertAlmostEqual(book["cash"] + long_mv + short_mv, 100000, places=2)
        self.assertTrue(book["gross"]["warning"])

    def test_change_after_the_open_waits_for_the_next_session(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100), ("2026-11-09", 130, 130)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 500, 500)],
        )
        book = opened([row("AAA", 10)], prices)
        later = run([row("AAA", 25)], prices, "2026-11-09T10:00:00", book)
        self.assertEqual(later["pending"]["executeOn"], "2026-11-10")
        self.assertAlmostEqual(pos(later, "AAA")["shares"], 100, places=6)
        self.assertEqual(pos(later, "AAA")["entryPrice"], 100)

    def test_inception_backfill_fills_that_open_and_later_runs_keep_it(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 110), ("2026-11-09", 130, 140)],
            SPY=[("2026-11-06", 500, 510), ("2026-11-09", 520, 530)],
        )
        rows = [row("AAA", 10, "BUY")]
        late = run(rows, prices, "2026-11-06T10:00:00")
        self.assertFalse(late["started"])
        self.assertEqual(late["pending"]["executeOn"], "2026-11-07")
        monday = run(rows, prices, "2026-11-09T10:00:00", late)
        self.assertEqual(monday["inception"], "2026-11-09")
        self.assertEqual(pos(monday, "AAA")["entryPrice"], 130)

        back = pt.update_book(None, rows, prices, at("2026-11-06T10:00:00"), inception="2026-11-06")
        self.assertTrue(back["started"])
        self.assertIsNone(back["pending"])
        self.assertEqual(back["inception"], "2026-11-06")
        self.assertEqual(pos(back, "AAA")["entryPrice"], 100)
        self.assertEqual(pos(back, "AAA")["entryDate"], "2026-11-06")
        self.assertAlmostEqual(pos(back, "AAA")["shares"], 100, places=6)
        self.assertEqual(back["marks"], [])
        nxt = run(rows, prices, "2026-11-07T21:00:00", back)
        self.assertEqual(nxt["inception"], "2026-11-06")
        self.assertEqual(len(nxt["trades"]), len(back["trades"]))
        self.assertEqual(pos(nxt, "AAA")["shares"], pos(back, "AAA")["shares"])
        self.assertEqual(pos(nxt, "AAA")["entryPrice"], 100)
        self.assertEqual(nxt["cash"], back["cash"])
        self.assertEqual([m["date"] for m in nxt["marks"]], ["2026-11-06"])
        flagged = pt.update_book(nxt, rows, prices, at("2026-11-09T10:00:00"), inception="2026-11-06")
        self.assertEqual(len(flagged["trades"]), len(back["trades"]))
        self.assertEqual(flagged["inception"], "2026-11-06")
        self.assertEqual(pos(flagged, "AAA")["entryPrice"], 100)

    def test_rerun_does_not_double_trade(self):
        prices = bars(AAA=[("2026-11-06", 100, 110)], BBB=[("2026-11-06", 50, 40)], SPY=[("2026-11-06", 500, 505)])
        rows = [row("AAA", 10, "BUY"), row("BBB", -5, "HOLD")]
        book = opened(rows, prices, later="2026-11-07T08:30:00")
        again = run(rows, prices, "2026-11-07T08:30:00", book)
        self.assertEqual(len(again["trades"]), len(book["trades"]))
        self.assertEqual(again["cash"], book["cash"])
        self.assertEqual(pos(again, "AAA")["shares"], pos(book, "AAA")["shares"])
        self.assertEqual(len(again["marks"]), 1)

    def test_sheet_column_missing_or_blank_is_zero(self):
        header = "Ticker,My Base value/share,Bear,Bull,Rating (Base vs price),Street consensus PT (mean),# analysts,Consensus source / date,Current price,Price as-of (ET),Base vs price %,Consensus vs price %,Base vs consensus %,Notes,Base change vs previous update,Previous update detail"
        body = 'AAA,$10,$1,$20,SELL,$12,1,src,$11,asof,-9%,9%,-16%,n,+$1,"Prev Base $9 at Nov 1, 2026"'
        rows, _ = price_targets.parse_csv(header + "\n" + body + "\n")
        self.assertEqual(rows[0]["paperWeight"], 0)
        weighted, _ = price_targets.parse_csv(header + ",Paper weight %\n" + body + ",\n" + body.replace("AAA", "BBB", 1) + ",-5%\n")
        self.assertEqual([r["paperWeight"] for r in weighted], [0.0, -5.0])
        self.assertIsNone(price_targets.weight_column(header.split(",")))

    def test_bar_dates_follow_new_york(self):
        stamp = datetime.datetime(2026, 11, 5, 14, 30, tzinfo=datetime.timezone.utc).timestamp() * 1000
        sessions = pt.sessions_from_ms_bars([[stamp, 10, 11, 9, 10.5, 100]])
        self.assertEqual(sessions, [{"date": "2026-11-05", "open": 10, "close": 10.5}])


if __name__ == "__main__":
    unittest.main()
