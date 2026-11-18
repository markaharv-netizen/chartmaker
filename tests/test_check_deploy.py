"""Deploy verification: public /version and a mocked check_deploy run."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_deploy
import server

SHA = "0123456789abcdef0123456789abcdef01234567"
API_KEY = "rnd_secret_key_do_not_print"


class HeaderMap(dict):
    def get_all(self, name):
        value = self.get(name)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


def response(status, body, headers=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return check_deploy.Response(status, HeaderMap(headers or {}), raw)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FallbackTest(unittest.TestCase):
    def test_missing_config_uses_excluded_tabs(self):
        import sheets_catalog
        # A checkout that has config/retired_charts.json must still exercise the
        # fallback. Point at a tree with the scripts and no config file.
        with tempfile.TemporaryDirectory() as tmp:
            os.symlink(os.path.join(ROOT, "scripts"), os.path.join(tmp, "scripts"))
            pairs = check_deploy.retired_pairs(tmp)
        self.assertEqual(set(pairs), {(str(book), int(gid)) for book, gid in sheets_catalog.EXCLUDED_TABS})
        self.assertNotIn(("bot2", 645072508), pairs)
        self.assertIn(("bot1", 333185169), pairs)


class MatchTest(unittest.TestCase):
    def test_prefix_match_either_way(self):
        self.assertTrue(check_deploy.commit_matches(SHA, SHA[:12]))
        self.assertTrue(check_deploy.commit_matches(SHA[:12], SHA))
        self.assertFalse(check_deploy.commit_matches(SHA, "012345"))
        self.assertFalse(check_deploy.commit_matches("", SHA))
        self.assertFalse(check_deploy.commit_matches(SHA, "deadbeefdead"))


class PollTest(unittest.TestCase):
    def test_second_response_matches(self):
        calls = {"n": 0}

        def request(method, url, headers, body, timeout):
            calls["n"] += 1
            commit = "stale-commit-value" if calls["n"] == 1 else SHA
            return response(200, {"commit": commit, "service": "chartmaker"})

        clock = Clock()
        report = check_deploy.Report([])
        doc = check_deploy.poll_version("https://example.test", SHA[:10], 60, request, clock.sleep, clock, report)
        self.assertEqual(doc["commit"], SHA)
        self.assertEqual(calls["n"], 2)
        self.assertGreater(clock.t, 0)

    def test_timeout_is_a_failure(self):
        def request(method, url, headers, body, timeout):
            return response(200, {"commit": "ffffffffffffffffffffffffffffffffffffffff"})

        clock = Clock()
        report = check_deploy.Report([])
        with self.assertRaises(check_deploy.CheckFailed):
            check_deploy.poll_version("https://example.test", SHA, 20, request, clock.sleep, clock, report)
        self.assertGreaterEqual(clock.t, 20)


class FakeDeploy:
    def __init__(self, catalog_tabs, retired_gid=999, retired_status=404, echo_key=False):
        self.catalog_tabs = catalog_tabs
        self.retired_gid = retired_gid
        self.retired_status = retired_status
        self.echo_key = echo_key
        self.seen = []

    def __call__(self, method, url, headers, body, timeout):
        self.seen.append((method, url, dict(headers or {}), body))
        if url.endswith("/version"):
            return response(200, {"commit": SHA, "service": "chartmaker"})
        if url.endswith("/login") and method == "POST":
            return response(303, b"", {"Set-Cookie": "cm_session=session-token; Max-Age=10; Path=/"})
        if url.endswith("/sheets/catalog.json"):
            self._cookie(headers)
            return response(200, {"books": [{"id": "bot1"}], "tabs": self.catalog_tabs})
        if "api.render.com" in url:
            auth = (headers or {}).get("Authorization") or ""
            if not auth.startswith("Bearer "):
                raise AssertionError("missing bearer token")
            payload = [{"deploy": {"status": "live", "commit": {"id": SHA}}}]
            raw = json.dumps(payload)
            if self.echo_key:
                raw = raw + API_KEY
            return response(200, raw.encode())
        if url.endswith("/sheets/full/bot1/%d.png" % self.retired_gid):
            self._cookie(headers)
            return response(self.retired_status, b"missing", {"Content-Type": "text/html"})
        if url.endswith(".png") or url.endswith(".jpg"):
            self._cookie(headers)
            return response(200, b"\x89PNG", {"Content-Type": "image/png"})
        return response(500, b"unexpected " + url.encode())

    def _cookie(self, headers):
        if (headers or {}).get("Cookie") != "cm_session=session-token":
            raise AssertionError("missing session cookie")


def tab(gid=10):
    return {
        "book": "bot1",
        "gid": gid,
        "tab": "sample %d" % gid,
        "thumb": "sheets/thumbs/bot1/%d.jpg" % gid,
    }


def many_tabs():
    return [tab(i) for i in range(1, 13)]


def retired_repo(gid=999):
    tmp = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(tmp.name, "config"))
    with open(os.path.join(tmp.name, "config", "retired_charts.json"), "w", encoding="utf-8") as f:
        json.dump({"charts": [{
            "book": "bot1", "gid": gid, "chartId": None, "tab": "gone",
            "chartTitle": None, "retiredOn": "2026-11-18", "reason": "retired",
            "drivePngs": [], "scope": "library",
        }, {
            "book": "bot2", "gid": 645072508, "chartId": None, "tab": "red dot",
            "chartTitle": "Mark's Red Dot Tracker: MSTR", "retiredOn": None,
            "reason": "excluded", "drivePngs": [], "scope": "cotd",
        }]}, f)
    return tmp


class RunTest(unittest.TestCase):
    def test_pass_with_render_api_and_retired_404(self):
        repo = retired_repo()
        self.addCleanup(repo.cleanup)
        fake = FakeDeploy(many_tabs())
        code = check_deploy.run_check(
            "https://example.test", SHA, 5, "app-password", repo.name,
            "srv-test", API_KEY, fake, lambda _s: None, Clock(), samples=2, retired_sample=1,
        )
        self.assertEqual(code, 0)
        urls = [url for _m, url, _h, _b in fake.seen]
        self.assertTrue(any("api.render.com/v1/services/srv-test/deploys" in url for url in urls))
        self.assertTrue(any(url.endswith("/sheets/full/bot1/999.png") for url in urls))
        self.assertFalse(any("645072508" in url for url in urls))

    def test_retired_200_fails_and_the_key_is_not_printed(self):
        import io
        from contextlib import redirect_stdout
        repo = retired_repo()
        self.addCleanup(repo.cleanup)
        fake = FakeDeploy(many_tabs(), retired_status=200, echo_key=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = check_deploy.run_check(
                "https://example.test", SHA[:8], 0, "app-password", repo.name,
                "srv-test", API_KEY, fake, lambda _s: None, Clock(), samples=1, retired_sample=1,
            )
        text = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("returned HTTP 200", text)
        self.assertNotIn(API_KEY, text)
        self.assertNotIn("app-password", text)

    def test_missing_password_fails_before_login(self):
        seen = []

        def request(method, url, headers, body, timeout):
            seen.append(url)
            if url.endswith("/version"):
                return response(200, {"commit": SHA})
            raise AssertionError("should not request " + url)

        code = check_deploy.run_check(
            "https://example.test", SHA, 0, "", ROOT, "", "", request, lambda _s: None, Clock(),
        )
        self.assertEqual(code, 1)
        self.assertEqual(seen, ["https://example.test/version"])

    def test_thin_catalog_fails(self):
        def request(method, url, headers, body, timeout):
            if url.endswith("/version"):
                return response(200, {"commit": SHA})
            if url.endswith("/login"):
                return response(303, b"", {"Set-Cookie": "cm_session=t; Path=/"})
            if url.endswith("/sheets/catalog.json"):
                return response(200, {"books": [{"id": "bot1"}], "tabs": [tab()]})
            return response(404, b"")

        code = check_deploy.run_check(
            "https://example.test", SHA, 0, "pw", ROOT, "", "", request, lambda _s: None, Clock(),
        )
        self.assertEqual(code, 1)


class VersionEndpointTest(unittest.TestCase):
    def test_version_is_public_and_has_no_secrets(self):
        saved = server.PASSWORD
        env = {
            "RENDER_GIT_COMMIT": SHA,
            "RENDER_SERVICE_NAME": "chartmaker",
            "APP_PASSWORD": "app-secret",
            "SECRET_KEY": "secret-key",
            "GITHUB_TOKEN": "ghp_should_not_leak",
            "RENDER_API_KEY": API_KEY,
        }
        previous = {key: os.environ.get(key) for key in env}
        server.PASSWORD = "app-secret"
        os.environ.update(env)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/version" % port, timeout=5) as res:
                body = res.read()
                doc = json.loads(body.decode())
            self.assertEqual(doc, {"commit": SHA, "service": "chartmaker"})
            text = body.decode()
            for secret in ("app-secret", "secret-key", "ghp_should_not_leak", API_KEY):
                self.assertNotIn(secret, text)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen("http://127.0.0.1:%d/sheets/catalog.json" % port, timeout=5)
            self.assertEqual(caught.exception.code, 401)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            server.PASSWORD = saved
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
