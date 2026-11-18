#!/usr/bin/env python3
"""Confirm a Chartmaker deploy is serving the expected commit.

Polls GET /version until the commit matches --expect-sha (a prefix match is
enough). When RENDER_API_KEY and a service id are set, it also reads
GET https://api.render.com/v1/services/<id>/deploys and logs the newest
deploy. The API key is never printed.

It then signs in with POST /login using CHARTMAKER_APP_PASSWORD, reads
/sheets/catalog.json (no /static prefix), checks a few full PNGs and
thumbnails, and checks that a sample of retired gids 404. Retired gids come
from config/retired_charts.json when that file is in the repo, otherwise from
sheets_catalog.EXCLUDED_TABS.

Usage:
  python3 scripts/check_deploy.py --expect-sha "$GITHUB_SHA"
  python3 scripts/check_deploy.py --base-url https://chartmaker-8pwa.onrender.com --expect-sha abcdef --timeout 1200
"""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_BASE = "https://chartmaker-8pwa.onrender.com"
POLL_SECONDS = 15
MIN_PREFIX = 7


class CheckFailed(Exception):
    pass


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body if isinstance(body, bytes) else bytes(body or b"")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_request(method, url, headers=None, body=None, timeout=30):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return Response(getattr(resp, "status", 200), resp.headers, resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers, exc.read())


def redact(text, secrets):
    out = str(text)
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, "[redacted]")
    return out


def commit_matches(live, expected):
    """True when either sha is a prefix of the other. Short values under 7 chars do not match."""
    live = (live or "").strip().lower()
    expected = (expected or "").strip().lower()
    if not live or not expected:
        return False
    if min(len(live), len(expected)) < MIN_PREFIX:
        return False
    return live.startswith(expected) or expected.startswith(live)


def header_values(headers, name):
    if headers is None:
        return []
    getter = getattr(headers, "get_all", None)
    if getter:
        return list(getter(name) or [])
    raw = headers.get(name) if hasattr(headers, "get") else None
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [raw]


def session_cookie(headers):
    for item in header_values(headers, "Set-Cookie"):
        part = str(item).split(";", 1)[0]
        key, _, value = part.partition("=")
        if key.strip() == "cm_session" and value.strip():
            return "cm_session=" + value.strip()
    return ""


def content_type(headers):
    values = header_values(headers, "Content-Type")
    return values[0] if values else ""


class Report:
    def __init__(self, secrets):
        self.secrets = [s for s in secrets if s]
        self.failures = []

    def say(self, kind, message):
        print("%s %s" % (kind, redact(message, self.secrets)), flush=True)

    def ok(self, message):
        self.say("PASS", message)

    def fail(self, message):
        self.failures.append(redact(message, self.secrets))
        self.say("FAIL", message)

    def info(self, message):
        self.say("..", message)


def poll_version(base, expect, timeout, request, sleep, now, report):
    url = base.rstrip("/") + "/version"
    deadline = now() + max(0, timeout)
    last = "no response yet"
    while True:
        try:
            res = request("GET", url, {"Accept": "application/json"}, None, 30)
            if res.status == 200:
                doc = json.loads(res.body.decode("utf-8"))
                if not isinstance(doc, dict) or "commit" not in doc:
                    last = "/version JSON has no commit"
                else:
                    live = doc.get("commit") or ""
                    report.info("version commit=%s" % (live or "(empty)"))
                    if commit_matches(live, expect):
                        report.ok("commit %s matches %s" % (live, expect))
                        return doc
                    last = "commit %s does not match %s" % (live or "(empty)", expect)
            else:
                last = "GET /version HTTP %s" % res.status
                report.info(last)
        except Exception as exc:
            last = "GET /version error: %s" % exc
            report.info(redact(last, report.secrets))
        if now() >= deadline:
            raise CheckFailed("timed out after %ss waiting for %s (%s)" % (timeout, expect, last))
        remaining = deadline - now()
        sleep(POLL_SECONDS if remaining > POLL_SECONDS else remaining)


def login(base, password, request):
    body = urllib.parse.urlencode({"password": password}).encode()
    res = request(
        "POST",
        base.rstrip("/") + "/login",
        {"Content-Type": "application/x-www-form-urlencoded"},
        body,
        30,
    )
    cookie = session_cookie(res.headers)
    if res.status not in (200, 303) or not cookie:
        raise CheckFailed("login failed (HTTP %s)" % res.status)
    return cookie


def fetch_json(base, path, cookie, request):
    res = request(
        "GET",
        base.rstrip("/") + "/" + path.lstrip("/"),
        {"Cookie": cookie, "Accept": "application/json"},
        None,
        60,
    )
    if res.status != 200:
        raise CheckFailed("%s HTTP %s" % (path, res.status))
    return json.loads(res.body.decode("utf-8"))


def catalog_ok(doc):
    tabs = doc.get("tabs") if isinstance(doc, dict) else None
    books = doc.get("books") if isinstance(doc, dict) else None
    if not isinstance(books, list) or not books:
        raise CheckFailed("catalog has no books")
    if not isinstance(tabs, list) or len(tabs) < 10:
        raise CheckFailed("catalog tab count %s is not sane" % (len(tabs) if isinstance(tabs, list) else "missing"))
    return len(tabs)


def sample_tabs(doc, n=3):
    out = []
    for tab in doc.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        thumb = str(tab.get("thumb") or "")
        book = str(tab.get("book") or "")
        gid = tab.get("gid")
        if not book or gid in (None, "") or ".." in thumb.split("/"):
            continue
        if not thumb.startswith("sheets/thumbs/"):
            continue
        out.append(tab)
        if len(out) >= n:
            break
    return out


def check_image(base, cookie, path, request):
    res = request("GET", base.rstrip("/") + "/" + path.lstrip("/"), {"Cookie": cookie}, None, 60)
    ctype = content_type(res.headers).split(";", 1)[0].strip().lower()
    if res.status != 200 or not ctype.startswith("image/"):
        raise CheckFailed("%s HTTP %s content-type %s" % (path, res.status, ctype or "(none)"))
    if not res.body:
        raise CheckFailed("%s was an empty image" % path)


def retired_pairs(repo):
    """Library retirements. CotD-only rows stay in the library and are not sampled."""
    path = os.path.join(repo, "config", "retired_charts.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        pairs = []
        for row in doc.get("charts") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("scope") or "library") == "cotd":
                continue
            if str(row.get("reason") or "") not in ("retired", "excluded"):
                continue
            try:
                pairs.append((str(row["book"]), int(row["gid"])))
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(set(pairs))
    scripts = os.path.join(repo, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import sheets_catalog
    return sorted((str(book), int(gid)) for book, gid in sheets_catalog.EXCLUDED_TABS)


def render_summary(service_id, api_key, request):
    """One line about the newest deploy. Raises CheckFailed only for a transport bug we can redact."""
    url = "https://api.render.com/v1/services/%s/deploys?limit=5" % urllib.parse.quote(str(service_id), safe="")
    res = request(
        "GET",
        url,
        {"Authorization": "Bearer " + api_key, "Accept": "application/json"},
        None,
        30,
    )
    text = redact(res.body.decode("utf-8", "replace"), [api_key])
    if res.status != 200:
        return "Render API HTTP %s" % res.status
    try:
        data = json.loads(text)
    except ValueError:
        return "Render API returned non-JSON"
    newest = None
    if isinstance(data, list) and data:
        item = data[0]
        newest = item.get("deploy") if isinstance(item, dict) and isinstance(item.get("deploy"), dict) else item
    elif isinstance(data, dict):
        newest = data.get("deploy") if isinstance(data.get("deploy"), dict) else data
    if not isinstance(newest, dict):
        return "Render API returned no deploys"
    commit = newest.get("commit")
    if isinstance(commit, dict):
        commit = commit.get("id") or commit.get("sha") or ""
    status = newest.get("status") or "unknown"
    return "newest Render deploy status=%s commit=%s" % (status, commit or "(none)")


def run_check(base, expect, timeout, password, repo, service_id, api_key, request, sleep, now, samples=3, retired_sample=5):
    report = Report([password, api_key])
    try:
        poll_version(base, expect, timeout, request, sleep, now, report)
    except CheckFailed as exc:
        report.fail(str(exc))
        print("FAIL deploy")
        return 1
    if service_id and api_key:
        try:
            report.info(render_summary(service_id, api_key, request))
        except Exception as exc:
            report.info("Render API skipped: %s" % redact(exc, report.secrets))
    elif service_id or api_key:
        report.info("Render API skipped; set both RENDER_API_KEY and the service id")
    if not password:
        report.fail("CHARTMAKER_APP_PASSWORD is not set")
        print("FAIL deploy")
        return 1
    try:
        cookie = login(base, password, request)
        report.ok("logged in")
        catalog = fetch_json(base, "/sheets/catalog.json", cookie, request)
        count = catalog_ok(catalog)
        report.ok("catalog parses (%d tabs)" % count)
        tabs = sample_tabs(catalog, samples)
        if len(tabs) < 1:
            report.fail("catalog has no thumbnail to sample")
        for tab in tabs:
            book, gid = str(tab["book"]), int(tab["gid"])
            thumb = str(tab["thumb"])
            full = "sheets/full/%s/%d.png" % (book, gid)
            for path in (thumb, full):
                check_image(base, cookie, path, request)
                report.ok("%s 200 image" % path)
        pairs = retired_pairs(repo)
        if not pairs:
            report.fail("no retired gids to sample")
        else:
            for book, gid in pairs[:retired_sample]:
                path = "sheets/full/%s/%d.png" % (book, gid)
                res = request("GET", base.rstrip("/") + "/" + path, {"Cookie": cookie}, None, 30)
                if res.status != 404:
                    report.fail("retired %s gid %s returned HTTP %s" % (book, gid, res.status))
                else:
                    report.ok("retired %s gid %s 404" % (book, gid))
    except CheckFailed as exc:
        report.fail(str(exc))
    except Exception as exc:
        report.fail(redact(exc, report.secrets))
    if report.failures:
        print("FAIL deploy")
        return 1
    print("PASS deploy")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Poll /version and spot-check the live Chartmaker deploy.")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--expect-sha", required=True)
    ap.add_argument("--timeout", type=int, default=20 * 60, help="Seconds to wait for /version (default 1200, 20 minutes).")
    ap.add_argument("--service-id", default=os.environ.get("RENDER_SERVICE_ID") or "")
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args(argv)
    password = os.environ.get("CHARTMAKER_APP_PASSWORD") or ""
    api_key = os.environ.get("RENDER_API_KEY") or ""
    return run_check(
        args.base_url.rstrip("/"),
        args.expect_sha,
        args.timeout,
        password,
        args.repo,
        args.service_id.strip(),
        api_key,
        urllib_request,
        time.sleep,
        time.monotonic,
        samples=args.samples,
    )


if __name__ == "__main__":
    sys.exit(main())
