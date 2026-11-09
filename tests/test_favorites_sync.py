"""Shared favorites: one list for every signed-in browser, with tombstones and a durable store.

File mode is the server's views.json. GitHub mode is the same document the saved views
already use (Contents API, sha retry). These tests never call the real GitHub API.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server
from test_cotd_build import Chrome

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONE = "bot1/1186940001/1"
OTHER = "bot1/1965360503/1"
RETIRED = "bot1/1547332232/1"


def post(base, body, headers=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(base + "/api/favorites", data=data, method="POST", headers={
        "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            doc = json.loads(raw)
        except ValueError:
            doc = {"error": raw[:200]}
        return e.code, doc


def get(base, headers=None):
    req = urllib.request.Request(base + "/api/favorites", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            doc = json.loads(raw)
        except ValueError:
            doc = {"error": raw[:200]}
        return e.code, doc


class SyncStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fav-sync-")
        self._saved = (server.VIEWS, server.GH_TOKEN, server.VIEWS_STORE, server.PASSWORD, dict(server._VS))
        server.VIEWS = os.path.join(self.tmp, "views.json")
        server.GH_TOKEN = ""
        server.VIEWS_STORE = "server"
        server.PASSWORD = ""
        server._VS = {"doc": None, "sha": None, "at": 0}

    def tearDown(self):
        server.VIEWS, server.GH_TOKEN, server.VIEWS_STORE, server.PASSWORD, server._VS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def keys(self, doc):
        return [item["k"] for item in doc["items"]]

    def test_file_is_on_disk_and_merge_keeps_views(self):
        server.store_update(lambda d: {**d, "views": [{"name": "Keep me", "mode": "prices"}]})
        first = server.favorites_write({"migrate": [{"k": ONE, "title": "OpenAI"}], "ops": []})
        self.assertEqual(self.keys(first), [ONE])
        self.assertEqual(first["sync"], "file")
        self.assertTrue(first["shared"])
        self.assertIn("no separate accounts", first["scope"].lower())
        on_disk = json.load(open(server.VIEWS, encoding="utf-8"))
        self.assertEqual(on_disk["views"][0]["name"], "Keep me")
        self.assertEqual(on_disk["favorites"]["items"][0]["k"], ONE)
        server._VS = {"doc": None, "sha": None, "at": 0}
        again = server.favorites_read()
        self.assertEqual([x["k"] for x in again["items"]], [ONE])

    def test_migration_unions_and_tombstones_block_stale_adds(self):
        server.favorites_write({"ops": [{"op": "add", "k": ONE, "title": "A"}], "baseRev": 0})
        removed = server.favorites_write({"ops": [{"op": "remove", "k": ONE}], "baseRev": 1})
        self.assertEqual(self.keys(removed), [])
        stale = server.favorites_write({"migrate": [{"k": ONE, "title": "A"}, {"k": OTHER, "title": "B"}]})
        self.assertEqual(self.keys(stale), [OTHER])
        self.assertIn({"k": ONE, "reason": "removed"}, stale["skipped"])
        replay = server.favorites_write({"ops": [{"op": "add", "k": ONE, "title": "A"}], "baseRev": 0})
        self.assertNotIn(ONE, self.keys(replay))
        brought_back = server.favorites_write({"ops": [{"op": "add", "k": ONE, "title": "A"}], "baseRev": replay["rev"]})
        self.assertIn(ONE, self.keys(brought_back))

    def test_stale_remove_does_not_drop_a_newer_star(self):
        server.favorites_write({"ops": [{"op": "add", "k": ONE, "title": "A"}], "baseRev": 0})
        server.favorites_write({"ops": [{"op": "remove", "k": ONE}], "baseRev": 1})
        current = server.favorites_read()
        restored = server.favorites_write({"ops": [{"op": "add", "k": ONE, "title": "A"}], "baseRev": current["rev"]})
        stale = server.favorites_write({"ops": [{"op": "remove", "k": ONE}], "baseRev": 1})
        self.assertIn(ONE, self.keys(stale))
        self.assertIn({"k": ONE, "reason": "newer"}, stale["skipped"])
        self.assertGreater(restored["rev"], 1)

    def test_concurrent_adds_both_stick_and_retired_is_rejected(self):
        errors = []

        def add(key):
            try:
                server.favorites_write({"ops": [{"op": "add", "k": key, "title": key}], "baseRev": 0})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=add, args=(key,)) for key in ("bot1/10/1", "bot1/11/2")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self.keys(server.favorites_public(server.favorites_read()))), ["bot1/10/1", "bot1/11/2"])
        retired = server.favorites_write({"ops": [{"op": "add", "k": RETIRED, "title": "no"}, {"op": "add", "k": "bot1/1024131985/1", "title": "no"}], "baseRev": 0})
        self.assertNotIn(RETIRED, self.keys(retired))
        self.assertNotIn("bot1/1024131985/1", self.keys(retired))
        self.assertTrue(all(row["reason"] == "retired" for row in retired["skipped"]))
        on_disk = json.dumps(json.load(open(server.VIEWS, encoding="utf-8")))
        self.assertNotIn("1547332232", on_disk)
        self.assertNotIn("1024131985", on_disk)

    def test_table_key_is_stored_beside_a_chart_key(self):
        table = "bot2/1714490525/1"
        out = server.favorites_write({"ops": [
            {"op": "add", "k": table, "title": "9 MSTR preferreds table"},
            {"op": "add", "k": ONE, "title": "OpenAI"},
        ], "baseRev": 0})
        self.assertEqual(self.keys(out), [table, ONE])
        self.assertEqual(out["skipped"], [])
        on_disk = json.load(open(server.VIEWS, encoding="utf-8"))
        items = on_disk["favorites"]["items"]
        self.assertEqual([(x["k"], x["title"]) for x in items], [
            (table, "9 MSTR preferreds table"),
            (ONE, "OpenAI"),
        ])
        again = server.favorites_read()
        self.assertEqual([x["k"] for x in again["items"]], [table, ONE])

    def test_github_retry_does_not_drop_the_other_writers_star(self):
        state = {"sha": "aaa", "puts": 0, "doc": {
            "views": [{"name": "Keep me", "mode": "prices"}], "watchlist": None,
            "favorites": {"rev": 1, "items": [{"k": OTHER, "title": "Y", "rev": 1}], "removed": []}}}

        def fake(method, body=None):
            if method == "GET":
                return {"content": base64.b64encode(json.dumps(state["doc"]).encode()).decode(), "sha": state["sha"]}
            state["puts"] += 1
            if state["puts"] == 1:
                state["doc"]["favorites"]["items"].append({"k": "bot1/42/1", "title": "Z", "rev": 2})
                state["doc"]["favorites"]["rev"] = 2
                state["sha"] = "bbb"
                raise urllib.error.HTTPError("https://api.github.com/repos/example/contents/views.json", 409, "conflict", None, None)
            if body.get("sha") != state["sha"]:
                raise urllib.error.HTTPError("https://api.github.com/repos/example/contents/views.json", 409, "conflict", None, None)
            state["doc"] = json.loads(base64.b64decode(body["content"]))
            state["sha"] = "ccc"
            self.assertIn("[skip ci]", body["message"])
            self.assertNotIn("token", json.dumps(body).lower())
            return {"content": {"sha": "ccc"}}

        server.GH_TOKEN = "not-a-real-token"
        server._gh = fake
        try:
            out = server.favorites_write({"ops": [{"op": "add", "k": ONE, "title": "A"}], "baseRev": 1})
        finally:
            server.GH_TOKEN = ""
        self.assertEqual(out["sync"], "github")
        self.assertEqual(sorted(self.keys(out)), sorted([OTHER, "bot1/42/1", ONE]))
        self.assertEqual(state["doc"]["views"][0]["name"], "Keep me")
        self.assertGreaterEqual(state["puts"], 2)

    def test_auth_csrf_and_unavailable_storage(self):
        server.PASSWORD = "test-password"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        try:
            status, doc = get(base)
            self.assertEqual(status, 401)
            self.assertEqual(doc["error"], "login required")
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
            opener = urllib.request.build_opener(NoRedirect)
            try:
                opener.open(urllib.request.Request(base + "/login", data=b"password=test-password", method="POST"), timeout=10)
                self.fail("login did not redirect")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 303)
                cookie = e.headers.get("Set-Cookie", "").split(";", 1)[0]
            self.assertTrue(cookie.startswith("cm_session="), cookie)
            kept = server.VIEWS
            status, doc = post(base, {"ops": [{"op": "add", "k": ONE, "title": "A"}], "baseRev": 0}, {"Cookie": cookie})
            self.assertEqual(status, 200, doc)
            self.assertEqual(self.keys(doc), [ONE])
            status, doc = post(base, {"ops": [{"op": "remove", "k": ONE}], "baseRev": 1}, {
                "Cookie": cookie, "Origin": "https://evil.example"})
            self.assertEqual(status, 403, doc)
            self.assertIn(ONE, self.keys(server.favorites_public(server.favorites_read())))
            status, _doc = post(base, {"ops": []}, {"Cookie": cookie, "Sec-Fetch-Site": "cross-site"})
            self.assertEqual(status, 403)
            server.VIEWS = os.path.join(self.tmp, "blocked")
            os.mkdir(server.VIEWS)
            status, doc = post(base, {"ops": [{"op": "add", "k": OTHER, "title": "B"}], "baseRev": 0}, {"Cookie": cookie})
            self.assertEqual(status, 502, doc)
            self.assertEqual(doc["error"], "storage unavailable")
            server.VIEWS = kept
            server._VS = {"doc": None, "sha": None, "at": 0}
            self.assertEqual(self.keys(server.favorites_public(server.favorites_read())), [ONE])
            self.assertFalse(os.path.isfile(os.path.join(self.tmp, "blocked")))
        finally:
            httpd.shutdown()
            httpd.server_close()
            server.PASSWORD = ""


class TwoBrowserSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not chrome:
            raise unittest.SkipTest("google-chrome is required")
        cls.tmp = tempfile.mkdtemp(prefix="fav-browsers-")
        cls._saved = (server.VIEWS, server.GH_TOKEN, server.VIEWS_STORE, server.PASSWORD, dict(server._VS))
        server.VIEWS = os.path.join(cls.tmp, "views.json")
        server.GH_TOKEN = ""
        server.VIEWS_STORE = "server"
        server.PASSWORD = ""
        server._VS = {"doc": None, "sha": None, "at": 0}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/" % cls.httpd.server_address[1]
        cls.chrome = chrome
        cls.a = Chrome(chrome)
        cls.b = Chrome(chrome)
        cls.a.__enter__()
        cls.b.__enter__()

    @classmethod
    def tearDownClass(cls):
        for browser in (getattr(cls, "a", None), getattr(cls, "b", None)):
            if browser:
                browser.__exit__(None, None, None)
        httpd = getattr(cls, "httpd", None)
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if hasattr(cls, "_saved"):
            server.VIEWS, server.GH_TOKEN, server.VIEWS_STORE, server.PASSWORD, server._VS = cls._saved
        if hasattr(cls, "tmp"):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def wait(self, cdp, expr, ok, timeout=20):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = cdp.evaluate(expr)
            except Exception as exc:
                last = str(exc)
            else:
                if ok(last):
                    return last
            time.sleep(0.15)
        raise AssertionError("timeout %s last=%r" % (expr, last))

    def ready(self, cdp):
        cdp.call("Page.navigate", {"url": self.url})
        self.wait(cdp, "document.readyState==='complete' && typeof favSync==='function'", lambda v: v is True)
        cdp.evaluate("localStorage.clear()")
        cdp.call("Page.reload", {"ignoreCache": True})
        self.wait(cdp, "document.readyState==='complete' && typeof favSync==='function'", lambda v: v is True)
        self.wait(cdp, "(document.body && document.body.innerText) || ''", lambda t: True)

    def open_chart(self, cdp):
        cdp.evaluate("document.getElementById('mSheets').click()")
        self.wait(cdp, "document.getElementById('favlocal') && document.getElementById('favlocal').dataset.sync", lambda v: v == "file")
        cdp.evaluate("""(()=>{const i=document.getElementById('shq');i.value='226 OpenAI vs Anthropic revenue';i.dispatchEvent(new Event('input'));})()""")
        self.wait(cdp, "!!document.querySelector('button.fav[data-fav=%s]')" % json.dumps(ONE), lambda v: v is True)

    def test_star_on_a_shows_on_b_and_unstar_comes_back(self):
        self.ready(self.a.cdp)
        self.ready(self.b.cdp)
        self.open_chart(self.a.cdp)
        scope = self.a.cdp.evaluate("document.getElementById('favlocal').textContent")
        self.assertIn("no separate accounts", scope.lower())
        self.assertIn("views.json", scope)
        self.a.cdp.evaluate("document.querySelector('button.fav[data-fav=%s]').click()" % json.dumps(ONE))
        self.wait(self.a.cdp, "document.querySelector('button.fav[data-fav=%s]').getAttribute('aria-pressed')" % json.dumps(ONE), lambda v: v == "true")
        self.wait(self.a.cdp, "(document.getElementById('favmsg')||{}).textContent||''", lambda t: "not synced" not in t and "could not sync" not in t.lower())
        on_disk = json.load(open(server.VIEWS, encoding="utf-8"))
        self.assertIn(ONE, [item["k"] for item in on_disk["favorites"]["items"]])
        server._VS = {"doc": None, "sha": None, "at": 0}
        self.open_chart(self.b.cdp)
        self.wait(self.b.cdp, "document.querySelector('button.fav[data-fav=%s]').getAttribute('aria-pressed')" % json.dumps(ONE), lambda v: v == "true")
        self.b.cdp.evaluate("document.querySelector('button.fav[data-fav=%s]').click()" % json.dumps(ONE))
        self.wait(self.a.cdp, "document.querySelector('button.fav[data-fav=%s]').getAttribute('aria-pressed')" % json.dumps(ONE), lambda v: v == "false", timeout=15)
        self.a.cdp.call("Page.reload", {"ignoreCache": True})
        self.wait(self.a.cdp, "document.readyState==='complete' && typeof favSync==='function'", lambda v: v is True)
        self.open_chart(self.a.cdp)
        self.wait(self.a.cdp, "document.querySelector('button.fav[data-fav=%s]').getAttribute('aria-pressed')" % json.dumps(ONE), lambda v: v == "false")
        on_disk = json.load(open(server.VIEWS, encoding="utf-8"))
        self.assertNotIn(ONE, [item["k"] for item in on_disk["favorites"]["items"]])


if __name__ == "__main__":
    unittest.main()
