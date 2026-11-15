"""Price-target and treasury snapshot archives used by the refresh scripts."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import briefing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


def company(ticker, btc, rank, name=None, change=None, as_of=None, notes=None, source=None, source_label=None):
    return {
        "ticker": ticker, "name": name or ticker, "btc": btc, "rank": rank, "change": change,
        "asOf": as_of, "notes": notes, "source": source, "sourceLabel": source_label,
    }


class ArchiveTest(unittest.TestCase):
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

    def test_price_target_archive_skips_identical_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = tmp
            os.makedirs(os.path.join(repo, "static"))
            path = os.path.join(repo, "static", "price_targets_history.json")
            doc = {"generated": "2026-11-09T13:00:00Z", "rows": [
                {"ticker": "AAA", "base": 10, "bear": 1, "bull": 20, "consensus": 12,
                 "analysts": 3, "rating": "HOLD", "notes": "n", "why": "w"},
            ]}
            briefing.archive_price_targets(repo, doc, path)
            briefing.archive_price_targets(repo, doc, path)
            later = dict(doc)
            later["generated"] = "2026-11-10T13:00:00Z"
            later["rows"] = [dict(doc["rows"][0], base=14)]
            briefing.archive_price_targets(repo, later, path)
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual([snap["generated"] for snap in saved["snapshots"]],
                             ["2026-11-09T13:00:00Z", "2026-11-10T13:00:00Z"])


class LandingTest(unittest.TestCase):
    def test_briefing_is_the_landing_tab(self):
        with open(INDEX, encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn('id="mBrief" class="on"', html)
        self.assertIn("/api/briefing", html)
        self.assertIn("briefing:'briefing'", html)
        self.assertIn(":'briefing')", html)


if __name__ == "__main__":
    unittest.main()
