"""Parser and update-rule tests. No network. Run: python3 -m unittest tools.test_btctop100"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import btctop100 as b

MSTR = """
BTC Update On October 19, 2026, Strategy announced updates with respect to its bitcoin holdings:
During Period October 12, 2026 to October 18, 2026 As of October 18, 2026
BTC Purchased Aggregate Purchase Price Average Purchase Price Aggregate BTC Holdings
Aggregate Purchase Price Average Purchase Price
267 $ 22.1 $ 82,900 848,517 $ 64.02 $ 75,446
"""
ASST = """
As of October 9, 2026 As of October 16, 2026 Change
Cash and cash equivalents 310,300 317,500 7,200
Bitcoin held 32,773 36,818 4,045
Shares outstanding 101,169,907 114,847,610 13,677,703
"""
MARA = "As of June 30, 2026, we held approximately 35,577 bitcoin, including 9,270 bitcoin under loan. During the period we sold approximately 23,093 bitcoin and we mined 4,669 bitcoin."
META = "bringing its total holdings to 44,000 BTC as of September 30, 2026. Total Bitcoin Holdings (September 30, 2026) 44,000 BTC"


class Parse(unittest.TestCase):
    def test_mstr_table(self):
        hit = b.parse_mstr(b.re.sub(r"\s+", " ", MSTR))
        self.assertEqual(hit["btc"], 848517)
        self.assertEqual(hit["asof"], "2026-10-18")

    def test_asst_table(self):
        hit = b.parse_asst(b.re.sub(r"\s+", " ", ASST))
        self.assertEqual(hit["btc"], 36818)
        self.assertEqual(hit["asof"], "2026-10-16")

    def test_asst_rejects_a_row_that_does_not_add_up(self):
        text = "As of October 9, 2026 As of October 16, 2026 Bitcoin held 10 99 5"
        self.assertIsNone(b.parse_asst(text))

    def test_mara_prose_ignores_sales_and_mining(self):
        hits = b.parse_prose(MARA)
        self.assertTrue(any(h["btc"] == 35577 for h in hits))
        self.assertFalse(any(h["btc"] in (23093, 4669) for h in hits))

    def test_units_of_bitcoin_uses_the_later_as_of(self):
        tsla = "As of September 30, 2026 and December 31, 2025, the majority of our digital assets were comprised of 11,509 units of Bitcoin held at cost."
        spcx = "As of September 30, 2026 and December 31, 2025, the Company also held 18,712 units of Bitcoin with a cost basis of $661 million."
        coin = "Crypto assets held for investment (in thousands, except units): June 30, 2026 December 31, 2025 Units Cost Basis Fair Value Bitcoin 17,311 $ 1,213,691"
        borrowed = "Crypto assets borrowed (in thousands, except units): June 30, 2026 December 31, 2025 Units Cost Basis Fair Value Bitcoin 1,869 $ 1"
        xxi = "holdings as of June 30, 2026 and December 31, 2025: June 30, 2026 December 31, 2025 Bitcoin (Units) 43,514 43,515"
        self.assertEqual(b.pick(b.parse_units(tsla), 11509)["asof"], "2026-09-30")
        self.assertEqual(b.pick(b.parse_units(tsla), 11509)["btc"], 11509)
        self.assertEqual(b.pick(b.parse_units(spcx), 18712)["btc"], 18712)
        self.assertEqual(b.pick(b.parse_units(coin), 17311)["btc"], 17311)
        self.assertEqual(b.pick(b.parse_units(xxi), 43514)["btc"], 43514)
        self.assertEqual(b.pick(b.parse_units(xxi), 43514)["asof"], "2026-06-30")
        self.assertIsNone(b.pick(b.parse_units(borrowed), 17311))

    def test_prose_wins_over_a_partial_units_row(self):
        text = MARA + " Units Cost Basis Bitcoin 26,307 $ 1"
        hit = b.parse_document(text, "prose", 35577)
        self.assertEqual(hit["btc"], 35577)

    def test_metaplanet_sentence(self):
        hit = b.pick(b.parse_prose(META), 44000)
        self.assertEqual(hit["btc"], 44000)
        self.assertEqual(hit["asof"], "2026-09-30")

    def test_ambiguous_same_day(self):
        text = "As of June 30, 2026, we held 18,200 bitcoin. As of June 30, 2026, the company held 23,800 bitcoin."
        hit = b.pick(b.parse_prose(text), 18200)
        self.assertEqual(hit["btc"], 18200)
        self.assertIn(23800, hit["also"])


class Decide(unittest.TestCase):
    def row(self, btc=1000, asof="2026-06-30"):
        return {"ticker": "X", "btc": btc, "asof": asof, "source": "seed", "notes": "curator"}

    def test_newer_applies_and_records_change(self):
        d = b.decide(self.row(), {"btc": 1100, "asof": "2026-07-15", "url": "https://example.test/a", "source": "SEC Form 8-K (filed 2026-07-16)", "source_label": "SEC 8-K"})
        self.assertEqual(d["action"], "updated")
        self.assertEqual(d["change"], 100)
        self.assertEqual(d["source_url"], "https://example.test/a")

    def test_older_does_not_replace_a_newer_seed(self):
        d = b.decide(self.row(13530, "2026-09-30"), {"btc": 12205, "asof": "2026-06-30"})
        self.assertEqual(d["action"], "older")
        self.assertEqual(d["btc"], 13530)

    def test_same_day_match_confirms(self):
        d = b.decide(self.row(848517, "2026-10-18"), {"btc": 848517, "asof": "2026-10-18", "url": "https://www.sec.gov/x", "form": "8-K"})
        self.assertEqual(d["action"], "confirmed")
        self.assertEqual(d["btc"], 848517)

    def test_same_day_disagreement_is_a_conflict(self):
        d = b.decide(self.row(100, "2026-06-30"), {"btc": 140, "asof": "2026-06-30"})
        self.assertEqual(d["action"], "conflict")

    def test_jump_outside_band_is_not_applied(self):
        d = b.decide(self.row(1000, "2026-01-01"), {"btc": 5000, "asof": "2026-06-01"})
        self.assertEqual(d["action"], "flagged")
        self.assertEqual(d["btc"], 1000)

    def test_ambiguous_without_the_snapshot_figure_is_not_applied(self):
        d = b.decide(self.row(100, "2026-01-01"), {"ambiguous": True, "asof": "2026-06-30", "options": [80, 140]})
        self.assertEqual(d["action"], "flagged")


class Pdf(unittest.TestCase):
    def test_tj_strings_keep_their_spaces(self):
        # minimal PDF with one Flate stream is overkill; check the unescape/concat helper via a fake stream
        import zlib
        body = b"BT [(44) -2 (,000) ( BTC as of ) (September 30, 2026)] TJ ET"
        raw = b"%PDF-1.4\nstream\n" + zlib.compress(body) + b"\nendstream"
        text = b.pdf_to_text(raw)
        self.assertIn("44,000 BTC as of September 30, 2026", text)


class Snapshot(unittest.TestCase):
    def test_committed_snapshot_is_the_top_100(self):
        db = b.load()
        self.assertEqual(db["count"], 100)
        self.assertEqual([c["ticker"] for c in db["companies"][:5]], ["MSTR", "3350.T", "XXI", "ASST", "MARA"])
        self.assertEqual(db["companies"][0]["btc"], 848517)
        self.assertEqual(db["companies"][0]["asof"], "2026-10-18")
        self.assertTrue(db["companies"][0]["source_url"].startswith("https://www.sec.gov/"))
        self.assertEqual(db["companies"][1]["btc"], 44000)
        # Hut stays ex-American Bitcoin; the consolidated 10-Q figure is not written over it.
        hut = next(c for c in db["companies"] if c["ticker"] == "HUT")
        self.assertEqual(hut["btc"], 9314)
        self.assertEqual(hut["check"], "review")


if __name__ == "__main__":
    unittest.main()
