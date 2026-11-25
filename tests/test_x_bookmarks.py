"""X Bookmarks tab: normalize, seal/open, refresh build, and the signed-in-only endpoints.

No test calls the X API, pbs.twimg.com, or GitHub.
"""
import base64, datetime, io, json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import server
import x_bookmarks as xb
import refresh_x_bookmarks as rx

PAGE = {
    "data": [
        {"id": "200", "author_id": "1", "created_at": "2026-11-25T21:00:15.000Z", "text": "Look https://t.co/aaa https://t.co/pic",
         "entities": {"urls": [{"url": "https://t.co/aaa", "expanded_url": "https://example.com/a"},
                               {"url": "https://t.co/pic", "expanded_url": "https://x.com/alice/status/200/photo/1", "media_key": "3_1"}]},
         "attachments": {"media_keys": ["3_1"]},
         "public_metrics": {"like_count": 5, "repost_count": 1, "reply_count": 0, "quote_count": 0, "bookmark_count": 2, "impression_count": 900}},
        {"id": "100", "author_id": "2", "created_at": "2025-01-02T15:00:00.000Z", "text": "short version…",
         "note_post": {"text": "the full long-form text of the post"}, "attachments": {"media_keys": ["13_9"]},
         "public_metrics": {"like_count": 50, "repost_count": 3, "reply_count": 1, "quote_count": 1, "bookmark_count": 9}},
        {"id": "100", "author_id": "2", "created_at": "2025-01-02T15:00:00.000Z", "text": "duplicate"},
    ],
    "includes": {
        "users": [{"id": "1", "name": "Alice", "username": "alice"}, {"id": "2", "name": "Bob", "username": "bob"}],
        "media": [{"media_key": "3_1", "type": "photo", "url": "https://pbs.twimg.com/media/abc.jpg"},
                  {"media_key": "13_9", "type": "video", "preview_image_url": "https://pbs.twimg.com/amplify_video_thumb/9/img/x.jpg"}],
    },
    "meta": {"result_count": 3},
}


def png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


class NormalizeTest(unittest.TestCase):
    def test_rows(self):
        items, sources = xb.normalize([PAGE])
        self.assertEqual([x["id"] for x in items], ["200", "100"])
        a, b = items
        self.assertEqual(a["text"], "Look https://example.com/a")
        self.assertEqual(a["url"], "https://x.com/alice/status/200")
        self.assertEqual(a["author"], {"id": "1", "name": "Alice", "username": "alice"})
        self.assertEqual(a["created_et"], "Nov 25, 2026 4:00 PM ET")
        self.assertEqual(a["metrics"]["impression_count"], 900)
        self.assertEqual(b["text"], "the full long-form text of the post")
        self.assertNotIn("impression_count", b["metrics"])
        self.assertEqual(b["media_type"], "video")
        self.assertEqual(sources, {"200": "https://pbs.twimg.com/media/abc.jpg",
                                   "100": "https://pbs.twimg.com/amplify_video_thumb/9/img/x.jpg"})

    def test_page_chain(self):
        self.assertTrue(xb.page_chain_complete([PAGE]))
        self.assertFalse(xb.page_chain_complete([{"meta": {"next_token": "x"}}]))
        self.assertFalse(xb.page_chain_complete([]))


class SealTest(unittest.TestCase):
    def test_round_trip_and_tamper(self):
        doc = {"items": [{"id": "1", "text": "secret bookmark"}]}
        blob = xb.seal(doc, "pw", iterations=10_000)
        self.assertNotIn(b"secret bookmark", blob)
        self.assertEqual(xb.open_sealed(blob, "pw"), doc)
        with self.assertRaises(xb.SealError):
            xb.open_sealed(blob, "wrong")
        bad = bytearray(blob); bad[50] ^= 1
        with self.assertRaises(xb.SealError):
            xb.open_sealed(bytes(bad), "pw")
        with self.assertRaises(xb.SealError):
            xb.seal(doc, "")

    def test_build_compresses_thumbs(self):
        calls = []
        def fake_fetch(src):
            calls.append(src)
            return png_bytes()
        now = datetime.datetime(2026, 11, 25, 23, 0, tzinfo=datetime.timezone.utc)
        bundle, failed = rx.build([PAGE], api_calls=1, fetch=fake_fetch, now=now)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(bundle["pulled_et"], "Nov 25, 2026 6:00 PM ET")
        self.assertTrue(all(x["thumb"] for x in bundle["items"]))
        jpg = xb.thumb_bytes(bundle, "200")
        self.assertTrue(jpg.startswith(b"\xff\xd8"))
        self.assertLessEqual(len(jpg), rx.THUMB_MAX)
        doc = xb.public_doc(bundle)
        self.assertEqual((doc["count"], doc["with_media"], doc["api_calls"]), (2, 2, 1))
        self.assertNotIn("thumbs", doc)

    def test_fetch_refuses_other_hosts(self):
        with self.assertRaises(ValueError):
            rx.fetch_image("https://evil.example.com/a.jpg")


class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="xbm-")
        bundle, _ = rx.build([PAGE], api_calls=1, fetch=lambda s: png_bytes())
        cls.path = os.path.join(cls.tmp, "x.enc")
        with open(cls.path, "wb") as f:
            f.write(xb.seal(bundle, "test-password", iterations=10_000))
        cls._saved = (server.PASSWORD, server.XB_FILE, dict(server._XB))
        server.PASSWORD, server.XB_FILE = "test-password", cls.path
        server._XB.update({"bundle": None, "at": 0})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        server.PASSWORD, server.XB_FILE = cls._saved[0], cls._saved[1]
        server._XB.clear(); server._XB.update(cls._saved[2])

    def req(self, path, cookie=None):
        r = urllib.request.Request(self.base + path, headers={"Cookie": cookie} if cookie else {})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def cookie(self):
        return f"{server.COOKIE}={server.make_token()}"

    def test_login_required(self):
        for p in ("/api/x-bookmarks", "/api/x-bookmarks/thumb?id=200", "/?tab=bookmarks"):
            code, _, body = self.req(p)
            self.assertEqual(code, 401, p)
            self.assertNotIn(b"Look", body)

    def test_signed_in(self):
        code, hdr, body = self.req("/api/x-bookmarks", self.cookie())
        self.assertEqual(code, 200)
        self.assertIn("no-store", hdr["Cache-Control"])
        doc = json.loads(body)
        self.assertEqual(doc["count"], 2)
        self.assertEqual(doc["items"][0]["url"], "https://x.com/alice/status/200")
        code, hdr, img = self.req("/api/x-bookmarks/thumb?id=200", self.cookie())
        self.assertEqual((code, hdr["Content-Type"]), (200, "image/jpeg"))
        self.assertTrue(img.startswith(b"\xff\xd8"))
        self.assertEqual(self.req("/api/x-bookmarks/thumb?id=999", self.cookie())[0], 404)
        self.assertEqual(self.req("/api/x-bookmarks/thumb?id=../x", self.cookie())[0], 400)

    def test_tab_in_page(self):
        html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
        self.assertIn('id="mXbm"', html)
        self.assertIn("'x-bookmarks':'xbm'", html)


class NoPlainDataInRepoTest(unittest.TestCase):
    def test_no_bookmark_files_committed(self):
        for dirpath, _, files in os.walk(ROOT):
            if ".git" in dirpath:
                continue
            for f in files:
                self.assertFalse(f.endswith(".enc") or f in ("bookmarks.json", "x_bookmarks.json"), os.path.join(dirpath, f))


if __name__ == "__main__":
    unittest.main()
