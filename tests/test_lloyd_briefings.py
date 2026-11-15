"""Lloyd 9 AM briefing archive: sanitize, upsert, and the landing tab hook."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import lloyd_briefings as lloyd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


class SanitizeTest(unittest.TestCase):
    def test_strips_scripts_iframes_and_images(self):
        raw = (
            '<p>Hello <script>alert(1)</script>world</p>'
            '<iframe src="https://evil.example/"></iframe>'
            '<img src="https://tracker.example/pixel.gif" alt="x">'
            '<p>Safe <a href="https://example.com/story">link</a>.</p>'
        )
        cleaned = lloyd.sanitize_html(raw)
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("alert(1)", cleaned)
        self.assertNotIn("<iframe", cleaned)
        self.assertNotIn("<img", cleaned)
        self.assertNotIn("tracker.example", cleaned)
        self.assertIn("Hello", cleaned)
        self.assertIn('href="https://example.com/story"', cleaned)
        self.assertIn('rel="noopener noreferrer"', cleaned)
        self.assertIn('target="_blank"', cleaned)

    def test_drops_unsubscribe_and_javascript_links(self):
        raw = (
            '<p><a href="https://news.example/unsubscribe?u=1">Unsubscribe</a></p>'
            '<p><a href="javascript:alert(1)">xss</a> text</p>'
            '<p><a href="https://example.com/a?utm_source=mail&id=2">keep</a></p>'
        )
        cleaned = lloyd.sanitize_html(raw)
        self.assertNotIn("unsubscribe", cleaned.lower())
        self.assertNotIn("javascript:", cleaned)
        self.assertIn("https://example.com/a?id=2", cleaned)
        self.assertNotIn("utm_source", cleaned)

    def test_text_renders_sections_and_urls(self):
        text = (
            "News window: Sat 9:00am – Sun 9:00am ET\n\n"
            "MARKETS\n\n"
            "1. Bitcoin fell. See https://example.com/btc.\n\n"
            "READS\n\n"
            "1. A piece. https://arstechnica.com/space/2026/11/story/"
        )
        html = lloyd.text_to_html(text)
        self.assertIn("<h3>MARKETS</h3>", html)
        self.assertIn("<h3>READS</h3>", html)
        self.assertIn('href="https://example.com/btc"', html)
        self.assertIn("Bitcoin fell", html)


class UpsertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "lloyd_briefings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_keys_by_et_date_and_is_idempotent(self):
        entry, status = lloyd.upsert(
            "Sun Nov 15 briefing: test",
            "2026-11-15T13:58:52Z",
            body_text="MARKETS\n\n1. Hello https://example.com/a",
            message_id="mid-1",
            path=self.path,
        )
        self.assertEqual(status, "inserted")
        self.assertEqual(entry["date"], "2026-11-15")
        self.assertEqual(entry["sentAtEt"], "Sunday, Nov 15, 2026, 8:58 AM ET")
        _, status = lloyd.upsert(
            "Sun Nov 15 briefing: test",
            "2026-11-15T13:58:52Z",
            body_text="MARKETS\n\n1. Hello https://example.com/a",
            message_id="mid-1",
            path=self.path,
        )
        self.assertEqual(status, "unchanged")
        store = lloyd.load_store(self.path)
        self.assertEqual(len(store["briefings"]), 1)

    def test_later_mail_replaces_earlier_same_day(self):
        lloyd.upsert("first", "2026-09-12T13:09:58Z", body_text="one",
                     message_id="a", path=self.path)
        entry, status = lloyd.upsert(
            "Sat Sep 12 briefing + sheets: later",
            "2026-09-12T13:30:56Z",
            body_text="two",
            message_id="c",
            path=self.path,
        )
        self.assertEqual(status, "updated")
        self.assertEqual(entry["subject"], "Sat Sep 12 briefing + sheets: later")
        _, status = lloyd.upsert(
            "middle", "2026-09-12T13:26:25Z", body_text="mid",
            message_id="b", path=self.path,
        )
        self.assertEqual(status, "skipped")
        store = lloyd.load_store(self.path)
        self.assertEqual(store["briefings"]["2026-09-12"]["messageId"], "c")

    def test_build_orders_newest_first_and_resanitizes(self):
        lloyd.upsert("older", "2026-11-14T13:53:41Z", body_text="Sat body",
                     message_id="14", path=self.path)
        lloyd.upsert("newer", "2026-11-15T13:58:52Z",
                     body_html='<p>Hi<script>alert(1)</script></p><img src="https://x/p.gif">',
                     body_text="Sun body",
                     message_id="15", path=self.path)
        doc = lloyd.build(path=self.path)
        self.assertEqual(doc["count"], 2)
        self.assertEqual(doc["briefings"][0]["subject"], "newer")
        self.assertEqual(doc["briefings"][1]["subject"], "older")
        self.assertNotIn("<script", doc["briefings"][0]["bodyHtml"])
        self.assertNotIn("<img", doc["briefings"][0]["bodyHtml"])
        self.assertIn("Hi", doc["briefings"][0]["bodyHtml"])


class LandingTest(unittest.TestCase):
    def test_briefing_is_the_landing_tab(self):
        with open(INDEX, encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn('id="mBrief" class="on"', html)
        self.assertIn("/api/briefing", html)
        self.assertIn("briefing:'briefing'", html)
        self.assertIn("watch:'watch'", html)
        self.assertIn(":'briefing')", html)
        self.assertNotIn(":'watch')", html)
        self.assertIn("lloydbody", html)
        self.assertNotIn("What changed in price targets, sentiment, bitcoin treasuries", html)
        self.assertNotIn("briefSection('Price targets'", html)


if __name__ == "__main__":
    unittest.main()
