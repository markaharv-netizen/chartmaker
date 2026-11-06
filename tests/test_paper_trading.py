"""Trade rules for the long-only paper book.

BUY is a long. HOLD and SELL are cash. A rating change trades at the next
session open. Tests pass prices in; nothing here talks to the network.
"""
import datetime
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import paper_trading as pt

ET = ZoneInfo("America/New_York")


def at(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=ET)


def rows(*pairs):
    return [{"ticker": ticker, "rating": rating} for ticker, rating in pairs]


def bars(**named):
    out = {}
    for symbol, seq in named.items():
        out[symbol] = [{"date": date, "open": opened, "close": closed} for date, opened, closed in seq]
    return out


def book_of(*pairs, prices, now, book=None):
    return pt.update_book(book, rows(*pairs), prices, at(now))


def pos(book, ticker):
    return book["positions"][ticker]


def actions(book):
    return [trade["action"] for trade in book["trades"]]


class PaperTradingTest(unittest.TestCase):
    def assert_no_shorts(self, book):
        self.assertFalse(any(trade["action"] in ("short", "cover") for trade in book["trades"]))
        for item in book["positions"].values():
            if item.get("shares") is not None:
                self.assertGreaterEqual(item["shares"], 0)
            self.assertIn(item.get("side"), ("long", "cash"))

    def test_inception_waits_for_the_open(self):
        prices = bars(AAA=[("2026-11-05", 10, 11)], BBB=[("2026-11-05", 20, 21)], SPY=[("2026-11-05", 500, 505)])
        book = book_of(("AAA", "BUY"), ("BBB", "SELL"), ("CCC", "HOLD"), prices=prices, now="2026-11-05T19:00:00")
        self.assertFalse(book["started"])
        self.assertEqual(book["trades"], [])
        self.assertEqual(book["cash"], 100000)
        self.assertEqual(book["config"]["weight"], "1/3")
        self.assertAlmostEqual(pos(book, "AAA")["targetWeight"], 1 / 3)
        self.assertAlmostEqual(pos(book, "BBB")["targetWeight"], 1 / 3)
        self.assertEqual(pos(book, "AAA")["side"], "long")
        self.assertEqual(pos(book, "AAA")["status"], "pending")
        self.assertIsNone(pos(book, "AAA")["shares"])
        self.assertEqual(pos(book, "BBB")["side"], "cash")
        self.assertEqual(pos(book, "CCC")["side"], "cash")
        self.assertEqual(book["pending"][0]["executeOn"], "2026-11-06")
        self.assert_no_shorts(book)

    def test_inception_fills_buys_and_leaves_hold_and_sell_in_cash(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 110)],
            BBB=[("2026-11-06", 50, 40)],
            CCC=[("2026-11-06", 20, 10)],
            SPY=[("2026-11-06", 500, 510)],
        )
        book = book_of(("AAA", "BUY"), ("BBB", "SELL"), ("CCC", "HOLD"), prices=prices, now="2026-11-06T10:00:00")
        self.assertTrue(book["started"])
        self.assertEqual(pos(book, "AAA")["side"], "long")
        self.assertEqual(pos(book, "AAA")["entryDate"], "2026-11-06")
        self.assertEqual(pos(book, "AAA")["entryPrice"], 100)
        self.assertAlmostEqual(pos(book, "AAA")["shares"], pos(book, "AAA")["notional"] / 100, places=6)
        self.assertAlmostEqual(pos(book, "AAA")["notional"], round(100000 / 3, 2), places=2)
        self.assertEqual(pos(book, "BBB")["side"], "cash")
        self.assertEqual(pos(book, "BBB")["shares"], 0)
        self.assertEqual(pos(book, "CCC")["side"], "cash")
        self.assertEqual(pos(book, "CCC")["shares"], 0)
        self.assertEqual(actions(book).count("buy"), 1)
        self.assertEqual(actions(book).count("cash"), 2)
        self.assertNotIn("sell", actions(book))
        # Two cash slots still uninvested. The long is marked at the open, so NAV is the stake.
        nav = pt.portfolio_nav(book, {"AAA": 100, "BBB": 40, "CCC": 10}.get)
        self.assertAlmostEqual(nav, 100000, places=2)
        self.assertGreater(book["cash"], 100000 * 2 / 3 - 0.02)
        self.assertLess(book["cash"], 100000)
        self.assert_no_shorts(book)

    def test_long_pnl_uses_the_close(self):
        prices = bars(AAA=[("2026-11-06", 100, 110)], SPY=[("2026-11-06", 500, 505)])
        book = book_of(("AAA", "BUY"), prices=prices, now="2026-11-06T10:00:00")
        self.assertEqual(book["marks"], [])
        book = book_of(("AAA", "BUY"), prices=prices, now="2026-11-07T08:30:00", book=book)
        self.assertEqual(len(book["marks"]), 1)
        self.assertEqual(book["marks"][0]["date"], "2026-11-06")
        self.assertAlmostEqual(book["marks"][0]["nav"], 110000, places=2)
        self.assertEqual(book["marks"][0]["spyClose"], 505)
        self.assertEqual(book["spyInceptionOpen"], 500)
        self.assertAlmostEqual(pt.portfolio_nav(book, {"AAA": 110}.get) - 100000, 10000, places=2)

    def test_sell_and_hold_do_not_profit_when_price_falls(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100)],
            BBB=[("2026-11-06", 50, 20)],
            SPY=[("2026-11-06", 500, 500)],
        )
        book = book_of(("AAA", "SELL"), ("BBB", "HOLD"), prices=prices, now="2026-11-07T08:30:00")
        self.assertEqual(pos(book, "AAA")["shares"], 0)
        self.assertEqual(pos(book, "BBB")["shares"], 0)
        self.assertAlmostEqual(book["marks"][0]["nav"], 100000, places=2)
        self.assertAlmostEqual(book["cash"], 100000, places=2)
        self.assert_no_shorts(book)

    def test_downgrade_sells_at_the_next_open(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100), ("2026-11-09", 120, 121)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 510, 512)],
        )
        book = book_of(("AAA", "BUY"), prices=prices, now="2026-11-06T16:30:00")
        # Detected before the open, and today's bar is already in the fixture. Do not fill it yet.
        early = book_of(("AAA", "HOLD"), prices=prices, now="2026-11-09T08:30:00", book=book)
        self.assertTrue(pt._held(pos(early, "AAA")))
        pending = early["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["action"], "sell")
        self.assertEqual(pending[0]["executeOn"], "2026-11-09")
        # The next morning the Nov 9 open is a past session. Exit is 120, not the old entry.
        done = book_of(("AAA", "HOLD"), prices=prices, now="2026-11-10T08:30:00", book=early)
        self.assertFalse(pt._held(done["positions"].get("AAA")))
        self.assertEqual(pos(done, "AAA")["side"], "cash")
        self.assertEqual(pos(done, "AAA")["shares"], 0)
        sell = next(trade for trade in done["trades"] if trade["action"] == "sell")
        self.assertEqual(sell["price"], 120)
        self.assertEqual(sell["date"], "2026-11-09")
        self.assertAlmostEqual(sell["realized"], 20000, places=2)
        self.assertAlmostEqual(done["cash"], 120000, places=2)
        self.assert_no_shorts(done)

    def test_downgrade_after_the_open_waits_for_the_following_session(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100), ("2026-11-09", 120, 121)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 510, 512)],
        )
        book = book_of(("AAA", "BUY"), prices=prices, now="2026-11-06T16:30:00")
        later = book_of(("AAA", "SELL"), prices=prices, now="2026-11-09T10:00:00", book=book)
        self.assertTrue(pt._held(pos(later, "AAA")))
        self.assertEqual(later["pending"][0]["action"], "sell")
        self.assertEqual(later["pending"][0]["executeOn"], "2026-11-10")
        self.assertNotIn("sell", actions(later))
        self.assert_no_shorts(later)

    def test_upgrade_buys_at_the_next_open(self):
        prices = bars(
            AAA=[("2026-11-06", 80, 80), ("2026-11-09", 90, 90)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 500, 500)],
        )
        book = book_of(("AAA", "HOLD"), prices=prices, now="2026-11-06T16:30:00")
        self.assertEqual(pos(book, "AAA")["shares"], 0)
        queued = book_of(("AAA", "BUY"), prices=prices, now="2026-11-09T08:30:00", book=book)
        self.assertEqual(queued["pending"][0]["action"], "buy")
        self.assertEqual(queued["pending"][0]["executeOn"], "2026-11-09")
        self.assertEqual(pos(queued, "AAA")["shares"], 0)
        done = book_of(("AAA", "BUY"), prices=prices, now="2026-11-10T08:30:00", book=queued)
        self.assertEqual(pos(done, "AAA")["side"], "long")
        self.assertEqual(pos(done, "AAA")["entryPrice"], 90)
        self.assertEqual(pos(done, "AAA")["entryDate"], "2026-11-09")
        self.assertGreater(pos(done, "AAA")["shares"], 0)
        self.assert_no_shorts(done)

    def test_hold_to_sell_is_not_a_trade(self):
        prices = bars(AAA=[("2026-11-06", 10, 12)], SPY=[("2026-11-06", 500, 500)])
        book = book_of(("AAA", "HOLD"), prices=prices, now="2026-11-06T16:30:00")
        before = len(book["trades"])
        cash = book["cash"]
        moved = book_of(("AAA", "SELL"), prices=prices, now="2026-11-09T10:00:00", book=book)
        self.assertEqual(len(moved["trades"]), before)
        self.assertEqual(moved["pending"], [])
        self.assertEqual(pos(moved, "AAA")["rating"], "SELL")
        self.assertEqual(pos(moved, "AAA")["side"], "cash")
        self.assertEqual(pos(moved, "AAA")["shares"], 0)
        self.assertEqual(moved["cash"], cash)

    def test_sell_to_hold_is_not_a_trade(self):
        prices = bars(AAA=[("2026-11-06", 10, 4)], SPY=[("2026-11-06", 500, 500)])
        book = book_of(("AAA", "SELL"), prices=prices, now="2026-11-06T16:30:00")
        moved = book_of(("AAA", "HOLD"), prices=prices, now="2026-11-09T15:00:00", book=book)
        self.assertEqual(actions(moved), ["cash"])
        self.assertEqual(pos(moved, "AAA")["shares"], 0)
        self.assertAlmostEqual(moved["cash"], 100000, places=2)

    def test_running_twice_does_not_double_fill(self):
        prices = bars(AAA=[("2026-11-06", 100, 110)], BBB=[("2026-11-06", 50, 55)], SPY=[("2026-11-06", 500, 505)])
        book = book_of(("AAA", "BUY"), ("BBB", "SELL"), prices=prices, now="2026-11-07T08:30:00")
        again = book_of(("AAA", "BUY"), ("BBB", "SELL"), prices=prices, now="2026-11-07T08:30:00", book=book)
        self.assertEqual(len(again["trades"]), len(book["trades"]))
        self.assertEqual(len(again["marks"]), 1)
        self.assertEqual(again["cash"], book["cash"])
        self.assertEqual(pos(again, "AAA")["shares"], pos(book, "AAA")["shares"])

    def test_later_name_is_funded_from_cash_without_resizing(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100), ("2026-11-09", 100, 100)],
            BBB=[("2026-11-06", 50, 50), ("2026-11-09", 50, 50)],
            CCC=[("2026-11-09", 25, 25)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 500, 500)],
        )
        book = book_of(("AAA", "BUY"), ("BBB", "HOLD"), prices=prices, now="2026-11-06T16:30:00")
        held = pos(book, "AAA")["shares"]
        queued = book_of(
            ("AAA", "BUY"), ("BBB", "HOLD"), ("CCC", "BUY"),
            prices=prices, now="2026-11-09T08:30:00", book=book,
        )
        self.assertEqual(pos(queued, "CCC")["status"], "pending")
        self.assertEqual(queued["pending"][0]["executeOn"], "2026-11-09")
        self.assertEqual(pos(queued, "AAA")["shares"], held)
        added = book_of(
            ("AAA", "BUY"), ("BBB", "HOLD"), ("CCC", "BUY"),
            prices=prices, now="2026-11-10T08:30:00", book=queued,
        )
        self.assertEqual(pos(added, "AAA")["shares"], held)
        self.assertEqual(pos(added, "CCC")["entryPrice"], 25)
        self.assertEqual(pos(added, "CCC")["entryDate"], "2026-11-09")
        self.assertAlmostEqual(pos(added, "CCC")["targetWeight"], 0.5)
        self.assertAlmostEqual(pos(added, "CCC")["notional"], 50000, places=2)
        self.assertGreaterEqual(added["cash"], 0)
        self.assert_no_shorts(added)

    def test_no_leverage_when_cash_is_gone(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 100), ("2026-11-09", 100, 100)],
            BBB=[("2026-11-09", 10, 10)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 500, 500)],
        )
        book = book_of(("AAA", "BUY"), prices=prices, now="2026-11-06T16:30:00")
        self.assertAlmostEqual(book["cash"], 0, places=2)
        queued = book_of(("AAA", "BUY"), ("BBB", "BUY"), prices=prices, now="2026-11-09T08:30:00", book=book)
        self.assertEqual(queued["pending"][0]["action"], "buy")
        self.assertEqual(queued["pending"][0]["executeOn"], "2026-11-09")
        added = book_of(("AAA", "BUY"), ("BBB", "BUY"), prices=prices, now="2026-11-10T08:30:00", book=queued)
        self.assertEqual(pos(added, "BBB")["status"], "unfunded")
        self.assertEqual(pos(added, "BBB")["shares"], 0)
        self.assertGreaterEqual(added["cash"], 0)
        self.assertEqual(pos(added, "AAA")["shares"], pos(book, "AAA")["shares"])
        self.assert_no_shorts(added)

    def test_change_after_the_friday_close_uses_monday_open(self):
        prices = bars(
            AAA=[("2026-11-06", 100, 101), ("2026-11-09", 130, 128)],
            SPY=[("2026-11-06", 500, 501), ("2026-11-09", 520, 518)],
        )
        book = book_of(("AAA", "BUY"), prices=prices, now="2026-11-06T16:30:00")
        friday = book_of(("AAA", "HOLD"), prices=prices, now="2026-11-06T18:00:00", book=book)
        self.assertTrue(pt._held(pos(friday, "AAA")))
        self.assertEqual(friday["pending"][0]["executeOn"], "2026-11-07")
        self.assertNotIn("sell", actions(friday))
        monday = book_of(("AAA", "HOLD"), prices=prices, now="2026-11-09T10:00:00", book=friday)
        sell = next(trade for trade in monday["trades"] if trade["action"] == "sell")
        self.assertEqual(sell["date"], "2026-11-09")
        self.assertEqual(sell["price"], 130)
        self.assert_no_shorts(monday)

    def test_removed_long_is_sold_at_the_next_open(self):
        prices = bars(
            AAA=[("2026-11-06", 40, 40), ("2026-11-09", 42, 43)],
            SPY=[("2026-11-06", 500, 500), ("2026-11-09", 500, 500)],
        )
        book = book_of(("AAA", "BUY"), ("BBB", "HOLD"), prices=prices, now="2026-11-06T16:30:00")
        queued = book_of(("BBB", "HOLD"), prices=prices, now="2026-11-09T08:30:00", book=book)
        self.assertEqual(queued["pending"][0]["action"], "sell")
        self.assertEqual(queued["pending"][0]["executeOn"], "2026-11-09")
        self.assertIn("AAA", queued["positions"])
        gone = book_of(("BBB", "HOLD"), prices=prices, now="2026-11-10T08:30:00", book=queued)
        self.assertNotIn("AAA", gone["positions"])
        sell = next(trade for trade in gone["trades"] if trade["ticker"] == "AAA" and trade["action"] == "sell")
        self.assertEqual(sell["price"], 42)
        self.assertGreaterEqual(gone["cash"], 0)
        self.assert_no_shorts(gone)

    def test_bar_dates_follow_new_york(self):
        stamp = datetime.datetime(2026, 11, 5, 14, 30, tzinfo=datetime.timezone.utc).timestamp() * 1000
        sessions = pt.sessions_from_ms_bars([[stamp, 10, 11, 9, 10.5, 100]])
        self.assertEqual(sessions, [{"date": "2026-11-05", "open": 10, "close": 10.5}])


if __name__ == "__main__":
    unittest.main()
