"""Full-size chart PNGs for the Sheets library.

The grid keeps the compressed JPEGs in static/sheets/thumbs/. View PNG and
Download PNG call /api/sheet-png, which returns the weekly Drive export
(names like "001 BTC vol vs VIX.png") and caches the bytes on disk.

Credential order, first complete match (same names as scripts/refresh_btctop100.py):
  GOOGLE_ACCESS_TOKEN or CHARTMAKER_GOOGLE_TOKEN
  GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN
    (CHARTMAKER_GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN also work)
  GOOGLE_SERVICE_ACCOUNT_JSON or CHARTMAKER_GOOGLE_SERVICE_ACCOUNT_JSON
    (the raw service-account key JSON; this is the one to set on Render)
  GOOGLE_APPLICATION_CREDENTIALS or CHARTMAKER_GOOGLE_CREDENTIALS
    (path to that same JSON file)

The service account (or OAuth token) needs
https://www.googleapis.com/auth/drive.readonly and Viewer on the three
chart-PNG folders. SHEETS_PNG_DIR, when it points at a directory, serves a
local export instead of Drive (tests and a machine that already has the files).

Render's blueprint does not include a Google credential. Without one, and
without SHEETS_PNG_DIR, the endpoint returns 503 and names
GOOGLE_SERVICE_ACCOUNT_JSON.
"""
import base64, hashlib, json, os, re, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import sheets_thumbs

# Weekly chart-PNG folders (Charts of the day uses the same three).
FOLDERS = (
    ("bot1", "1SwZgYGwwHsoL_DXQUuQAYS6lhq5KxFe9"),
    ("bot2", "1mMUKaB_RbeZhtn5ntuKJV5xZOeo6LzGH"),
    ("bot3", "1AVfHRo2mJgBhbJoszHhe5vgOscfKhbzT"),
)
FOLDER_IDS = {bid: fid for bid, fid in FOLDERS}
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
INDEX_TTL = 3600
MAX_BYTES = 15_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
BOOK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,24}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MISSING = (
    "Full-size chart PNGs are in a private Google Drive folder. "
    "Set GOOGLE_SERVICE_ACCOUNT_JSON to a service-account key with the Drive API enabled, "
    "and share the three chart PNG folders with that service account's client_email as Viewer."
)

_INDEX_LOCK = threading.Lock()
_TOKEN_LOCK = threading.Lock()
_KEY_LOCKS = {}
_KEY_LOCKS_GUARD = threading.Lock()
_INDEX = None  # {"at": float, "map": {(book, gid): hit}}
_TOKEN = {"access": "", "exp": 0, "kind": ""}
_CAT = {"mtime": None, "doc": None}
_LOCAL = {"root": "", "stamp": None, "chosen": None}
_TRANSPORT = None  # tests replace the HTTP call


class PngError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = int(status)


class _AuthExpired(Exception):
    pass


def env(*names):
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def reset_caches():
    """Drop in-memory index, token, and catalog. Tests and a credential change use this."""
    global _INDEX
    _INDEX = None
    _TOKEN["access"] = ""
    _TOKEN["exp"] = 0
    _TOKEN["kind"] = ""
    _CAT["mtime"] = None
    _CAT["doc"] = None
    _LOCAL["root"] = ""
    _LOCAL["stamp"] = None
    _LOCAL["chosen"] = None


def cache_root():
    raw = env("SHEETS_PNG_CACHE") or os.path.join(ROOT, "cache", "sheet-pngs")
    os.makedirs(raw, exist_ok=True)
    return os.path.realpath(raw)


def _under(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    if os.path.commonpath([root, path]) != root:
        raise PngError("Refusing a path outside the cache.", 500)
    return path


def local_root():
    raw = env("SHEETS_PNG_DIR")
    if raw and os.path.isdir(raw):
        return os.path.realpath(raw)
    return ""


def catalog():
    path = os.path.join(ROOT, "static", "sheets", "catalog.json")
    mtime = os.path.getmtime(path)
    if _CAT["doc"] is None or _CAT["mtime"] != mtime:
        with open(path, encoding="utf-8") as f:
            _CAT["doc"] = json.load(f)
        _CAT["mtime"] = mtime
    return _CAT["doc"]


def lookup(book, gid):
    book = str(book or "").strip()
    if not BOOK_RE.fullmatch(book):
        raise PngError("Unknown sheet tab.", 404)
    try:
        gid = int(str(gid).strip())
    except (TypeError, ValueError):
        raise PngError("Unknown sheet tab.", 404)
    if gid < 0:
        raise PngError("Unknown sheet tab.", 404)
    for t in catalog().get("tabs") or []:
        try:
            tg = int(t.get("gid"))
        except (TypeError, ValueError):
            continue
        if t.get("book") == book and tg == gid:
            return t
    raise PngError("Unknown sheet tab.", 404)


def filename_for(tab):
    n = re.sub(r"[^\w]+", "_", str(tab or "")).strip("_") or "chart"
    return n + ".png"


def disposition(tab, download):
    kind = "attachment" if download else "inline"
    return '%s; filename="%s"' % (kind, filename_for(tab))


def etag_matches(header, etag):
    if not header or not etag:
        return False
    want = '"' + etag + '"'
    for part in str(header).split(","):
        part = part.strip()
        if part == "*" or part == want or part == etag:
            return True
        if part.startswith("W/") and part[2:].strip() == want:
            return True
    return False


def make_etag(hit):
    raw = "%s|%s|%s" % (hit.get("id") or hit.get("path") or "", hit.get("modifiedTime") or "", hit.get("size") or "")
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _request(url, data=None, headers=None, timeout=45):
    if _TRANSPORT:
        return _TRANSPORT(url, data=data, headers=headers, timeout=timeout)
    hdrs = {"User-Agent": "Chartmaker"}
    if headers:
        hdrs.update(headers)
    if data is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        body = e.read() if getattr(e, "fp", None) else b""
        return e.code, body, {}


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _service_account_token(info):
    email, key = info.get("client_email"), info.get("private_key")
    if not email or not key:
        raise PngError("GOOGLE_SERVICE_ACCOUNT_JSON is missing client_email or private_key.", 503)
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claim = _b64url(json.dumps({
        "iss": email,
        "scope": DRIVE_SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }, separators=(",", ":")).encode())
    signing = f"{header}.{claim}".encode()
    fd, pem = tempfile.mkstemp(prefix="sheetpng-sa-", suffix=".pem")
    try:
        os.write(fd, key.encode())
        os.close(fd)
        fd = None
        os.chmod(pem, 0o600)
        sig = subprocess.check_output(["openssl", "dgst", "-sha256", "-sign", pem], input=signing)
    except (OSError, subprocess.CalledProcessError) as e:
        raise PngError("Could not sign the service-account token (%s)." % e, 503)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.remove(pem)
        except OSError:
            pass
    assertion = signing.decode() + "." + _b64url(sig)
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    code, raw, _hdr = _request("https://oauth2.googleapis.com/token", data=body, timeout=30)
    if code != 200:
        raise PngError("Google rejected the service-account token (HTTP %s)." % code, 502)
    try:
        tok = json.loads(raw.decode() or "{}")
    except ValueError:
        tok = {}
    if not tok.get("access_token"):
        raise PngError("Google token response had no access_token.", 502)
    return tok["access_token"], int(tok.get("expires_in") or 3600)


def _refresh_access_token(client_id, client_secret, refresh_token):
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    code, raw, _hdr = _request("https://oauth2.googleapis.com/token", data=body, timeout=30)
    if code != 200:
        raise PngError("Google token refresh failed (HTTP %s)." % code, 502)
    try:
        tok = json.loads(raw.decode() or "{}")
    except ValueError:
        tok = {}
    if not tok.get("access_token"):
        raise PngError("Google refresh response had no access_token.", 502)
    return tok["access_token"], int(tok.get("expires_in") or 3600)


def _load_sa_info():
    raw = env("GOOGLE_SERVICE_ACCOUNT_JSON", "CHARTMAKER_GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            info = json.loads(raw)
        except ValueError:
            raise PngError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.", 503)
        if not isinstance(info, dict):
            raise PngError("GOOGLE_SERVICE_ACCOUNT_JSON is not a service-account key.", 503)
        return info
    path = env("GOOGLE_APPLICATION_CREDENTIALS", "CHARTMAKER_GOOGLE_CREDENTIALS")
    if not path:
        return None
    if not os.path.isfile(path):
        raise PngError("Google credential file was not found.", 503)
    try:
        info = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        raise PngError("Google credential file is not valid JSON.", 503)
    if not isinstance(info, dict):
        raise PngError("Google credential file is not a service-account key.", 503)
    return info


def _acquire():
    """Return (access_token, kind, expiry_epoch)."""
    direct = env("GOOGLE_ACCESS_TOKEN", "CHARTMAKER_GOOGLE_TOKEN")
    if direct:
        return direct, "access", time.time() + 3600
    cid = env("GOOGLE_CLIENT_ID", "CHARTMAKER_GOOGLE_CLIENT_ID")
    secret = env("GOOGLE_CLIENT_SECRET", "CHARTMAKER_GOOGLE_CLIENT_SECRET")
    refresh = env("GOOGLE_REFRESH_TOKEN", "CHARTMAKER_GOOGLE_REFRESH_TOKEN")
    if cid and secret and refresh:
        token, ttl = _refresh_access_token(cid, secret, refresh)
        return token, "refresh", time.time() + max(60, ttl - 60)
    info = _load_sa_info()
    if info:
        token, ttl = _service_account_token(info)
        return token, "service-account", time.time() + max(60, ttl - 60)
    raise PngError(MISSING, 503)


def get_token(force=False):
    with _TOKEN_LOCK:
        if not force and _TOKEN["access"] and time.time() < _TOKEN["exp"]:
            return _TOKEN["access"], _TOKEN["kind"]
        access, kind, exp = _acquire()
        _TOKEN["access"] = access
        _TOKEN["kind"] = kind
        _TOKEN["exp"] = exp
        return access, kind


def _drive_error(code):
    if code in (401, 403):
        return (
            "Google Drive refused the credential (HTTP %s). "
            "It needs the drive.readonly scope, and the three chart PNG folders "
            "must be shared with it as Viewer." % code
        )
    return "Google Drive returned HTTP %s." % code


def _authorized(token):
    return {"Authorization": "Bearer " + token, "User-Agent": "Chartmaker"}


def list_folder(token, folder_id):
    if not FILE_RE.fullmatch(folder_id):
        raise PngError("Chart folder id is not valid.", 500)
    files, page = [], None
    for _ in range(20):
        params = {
            "q": "'%s' in parents and trashed = false" % folder_id,
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
            "pageSize": "1000",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
        }
        if page:
            params["pageToken"] = page
        url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
        code, raw, _hdr = _request(url, headers=_authorized(token), timeout=45)
        if code == 401:
            raise _AuthExpired()
        if code != 200:
            raise PngError(_drive_error(code), 502)
        try:
            doc = json.loads(raw.decode() or "{}")
        except ValueError:
            raise PngError("Google Drive returned a listing that was not JSON.", 502)
        files.extend(doc.get("files") or [])
        page = doc.get("nextPageToken")
        if not page:
            return files
    raise PngError("Google Drive listing did not finish.", 502)


def is_png_file(f):
    name = str(f.get("name") or "")
    mime = str(f.get("mimeType") or "").lower()
    if mime.startswith("application/vnd.google-apps"):
        return False
    return mime == "image/png" or name.lower().endswith(".png")


def _epoch(s):
    s = str(s or "").strip()
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def _build_index(token):
    files = []
    for book, folder in FOLDERS:
        for f in list_folder(token, folder):
            if not is_png_file(f) or not f.get("id"):
                continue
            files.append({
                "path": f["id"],
                "name": f.get("name") or "",
                "parent": book,
                "mtime": _epoch(f.get("modifiedTime")),
                "explicit": None,
                "_meta": f,
            })
    doc = catalog()
    chosen, _unmatched, _ambiguous = sheets_thumbs.match_files(files, doc.get("books") or [], doc.get("tabs") or [])
    by_id = {f["path"]: f["_meta"] for f in files}
    mapping = {}
    for key, cands in chosen.items():
        meta = by_id.get(cands[0]["path"]) or {}
        if not meta.get("id"):
            continue
        mapping[key] = {
            "id": meta["id"],
            "name": meta.get("name") or "",
            "modifiedTime": meta.get("modifiedTime") or "",
            "size": str(meta.get("size") or ""),
        }
    return mapping


def _index_path():
    return _under(cache_root(), os.path.join(cache_root(), "index.json"))


def _save_index(mapping, at):
    entries = [{"book": b, "gid": g, **hit} for (b, g), hit in mapping.items()]
    raw = json.dumps({"at": at, "entries": entries}).encode()
    dest = _index_path()
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, dest)


def _load_index():
    path = _index_path()
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    at = doc.get("at")
    if not isinstance(at, (int, float)) or time.time() - float(at) >= INDEX_TTL:
        return None
    mapping = {}
    for row in doc.get("entries") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        try:
            key = (str(row["book"]), int(row["gid"]))
        except (KeyError, TypeError, ValueError):
            continue
        mapping[key] = {
            "id": row["id"],
            "name": row.get("name") or "",
            "modifiedTime": row.get("modifiedTime") or "",
            "size": str(row.get("size") or ""),
        }
    return {"at": float(at), "map": mapping}


def drive_index():
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX and time.time() - _INDEX["at"] < INDEX_TTL:
            return _INDEX["map"]
        loaded = _load_index()
        if loaded:
            _INDEX = loaded
            return _INDEX["map"]
        token, kind = get_token()
        try:
            mapping = _build_index(token)
        except _AuthExpired:
            if kind == "access":
                raise PngError("Google rejected the access token (HTTP 401).", 502)
            token, _kind = get_token(force=True)
            mapping = _build_index(token)
        at = time.time()
        _INDEX = {"at": at, "map": mapping}
        try:
            _save_index(mapping, at)
        except OSError:
            pass
        print("sheet-png: indexed %d charts" % len(mapping), flush=True)
        return mapping


def _key_lock(key):
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _cache_files(book, gid):
    root = cache_root()
    png = _under(root, os.path.join(root, book, "%s.png" % int(gid)))
    meta = _under(root, os.path.join(root, book, "%s.json" % int(gid)))
    return png, meta


def _read_cached(png_path, meta_path, hit):
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("id") != hit.get("id") or (meta.get("modifiedTime") or "") != (hit.get("modifiedTime") or ""):
        return None
    if str(meta.get("size") or "") != str(hit.get("size") or ""):
        return None
    try:
        with open(png_path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_BYTES or not data.startswith(PNG_MAGIC):
        return None
    return data


def _write_cached(png_path, meta_path, hit, data):
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    tmp = png_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, png_path)
    blob = json.dumps({
        "id": hit.get("id"),
        "modifiedTime": hit.get("modifiedTime") or "",
        "size": str(hit.get("size") or ""),
        "name": hit.get("name") or "",
    }).encode()
    tmp = meta_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, meta_path)


def download_file(token, file_id):
    if not FILE_RE.fullmatch(file_id):
        raise PngError("Chart file id is not valid.", 502)
    url = "https://www.googleapis.com/drive/v3/files/%s?alt=media&supportsAllDrives=true" % urllib.parse.quote(file_id)
    code, raw, _hdr = _request(url, headers=_authorized(token), timeout=60)
    if code == 401:
        raise _AuthExpired()
    if code != 200:
        raise PngError(_drive_error(code), 502)
    if len(raw) > MAX_BYTES:
        raise PngError("Chart PNG is larger than the cache allows.", 502)
    if not raw.startswith(PNG_MAGIC):
        raise PngError("Drive file is not a PNG.", 502)
    return raw


def cached_or_download(tab, hit):
    book, gid = tab["book"], int(tab["gid"])
    png_path, meta_path = _cache_files(book, gid)
    got = _read_cached(png_path, meta_path, hit)
    if got is not None:
        return got
    with _key_lock((book, gid)):
        got = _read_cached(png_path, meta_path, hit)
        if got is not None:
            return got
        token, kind = get_token()
        try:
            data = download_file(token, hit["id"])
        except _AuthExpired:
            if kind == "access":
                raise PngError("Google rejected the access token (HTTP 401).", 502)
            token, _kind = get_token(force=True)
            data = download_file(token, hit["id"])
        try:
            _write_cached(png_path, meta_path, hit, data)
        except OSError:
            pass
        print("sheet-png: fetched %s/%s (%d bytes)" % (book, gid, len(data)), flush=True)
        return data


def _dir_stamp(root):
    latest = 0.0
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in names:
            try:
                latest = max(latest, os.path.getmtime(os.path.join(dirpath, fn)))
            except OSError:
                pass
    return latest


def local_index(root):
    stamp = _dir_stamp(root)
    if _LOCAL["root"] == root and _LOCAL["stamp"] == stamp and _LOCAL["chosen"] is not None:
        return _LOCAL["chosen"]
    found, _missing = sheets_thumbs.iter_dir(root)
    doc = catalog()
    chosen, _u, _a = sheets_thumbs.match_files(found, doc.get("books") or [], doc.get("tabs") or [])
    _LOCAL["root"] = root
    _LOCAL["stamp"] = stamp
    _LOCAL["chosen"] = chosen
    return chosen


def resolve_local(tab, root):
    cands = local_index(root).get((tab["book"], int(tab["gid"]))) or []
    if not cands:
        raise PngError("No full-size PNG for this tab.", 404)
    path = cands[0]["path"]
    path = _under(root, path)
    if not os.path.isfile(path):
        raise PngError("No full-size PNG for this tab.", 404)
    st = os.stat(path)
    hit = {"path": path, "modifiedTime": str(int(st.st_mtime)), "size": str(st.st_size)}
    etag = make_etag(hit)

    def read():
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or not data.startswith(PNG_MAGIC):
            raise PngError("Chart file is not a PNG.", 502)
        return data

    return etag, read


def resolve_drive(tab):
    if tab["book"] not in FOLDER_IDS:
        raise PngError("No chart folder is configured for this workbook.", 404)
    hit = drive_index().get((tab["book"], int(tab["gid"])))
    if not hit:
        raise PngError("No full-size PNG for this tab.", 404)
    etag = make_etag(hit)
    return etag, lambda: cached_or_download(tab, hit)


def serve(book, gid, download=False, if_none_match=""):
    """Return (status, headers, body) for one chart PNG."""
    tab = lookup(book, gid)
    root = local_root()
    if root:
        etag, read = resolve_local(tab, root)
    else:
        etag, read = resolve_drive(tab)
    cache = [("ETag", '"' + etag + '"'), ("Cache-Control", "private, max-age=3600")]
    if etag_matches(if_none_match, etag):
        return 304, cache, b""
    body = read()
    headers = [
        ("Content-Type", "image/png"),
        ("Content-Length", str(len(body))),
        *cache,
        ("Content-Disposition", disposition(tab.get("tab"), download)),
    ]
    return 200, headers, body


def _patch_env(updates):
    old = {}
    for k, v in updates.items():
        old[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return old


def _restore_env(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


_CRED_KEYS = [
    "GOOGLE_ACCESS_TOKEN", "CHARTMAKER_GOOGLE_TOKEN",
    "GOOGLE_CLIENT_ID", "CHARTMAKER_GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET", "CHARTMAKER_GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN", "CHARTMAKER_GOOGLE_REFRESH_TOKEN",
    "GOOGLE_SERVICE_ACCOUNT_JSON", "CHARTMAKER_GOOGLE_SERVICE_ACCOUNT_JSON",
    "GOOGLE_APPLICATION_CREDENTIALS", "CHARTMAKER_GOOGLE_CREDENTIALS",
    "SHEETS_PNG_DIR", "SHEETS_PNG_CACHE",
]


def self_test():
    import shutil
    global _TRANSPORT
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert "/api/sheet-png?book=" in html
    assert 'src="${E(im)}"' in html
    assert 'href="${E(im)}" target="_blank" rel="noopener">View PNG' not in html
    assert "(n||'chart')+'.png'" in html
    assert "Download PNG" in html

    doc = catalog()
    books, tabs = doc["books"], doc["tabs"]
    files = [
        {"path": "newid", "name": "001 BTC vol vs VIX.png", "parent": "bot1", "mtime": 200, "explicit": None},
        {"path": "oldid", "name": "001 BTC vol vs VIX.png", "parent": "bot1", "mtime": 100, "explicit": None},
        {"path": "wrongbook", "name": "001 BTC vol vs VIX.png", "parent": "bot2", "mtime": 999, "explicit": None},
    ]
    chosen, _u, _a = sheets_thumbs.match_files(files, books, tabs)
    win = chosen[("bot1", 1546024641)][0]
    assert win["path"] == "newid" and win["how"] == "tab", win
    assert all(c["path"] != "wrongbook" for cands in chosen.values() for c in cands)

    blank = {k: None for k in _CRED_KEYS}
    old = _patch_env(blank)
    tmp = tempfile.mkdtemp(prefix="sheetpng-")
    try:
        reset_caches()
        try:
            serve("bot1", "1546024641")
            raise AssertionError("expected missing-credential error")
        except PngError as e:
            assert e.status == 503 and "GOOGLE_SERVICE_ACCOUNT_JSON" in str(e), e
        try:
            serve("bot1", "999999999")
            raise AssertionError("expected unknown tab")
        except PngError as e:
            assert e.status == 404, e

        # Local export folder: the real weekly filename, served as image/png.
        tiny = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        book_dir = os.path.join(tmp, "bot1")
        os.makedirs(book_dir)
        png_path = os.path.join(book_dir, "001 BTC vol vs VIX.png")
        with open(png_path, "wb") as f:
            f.write(tiny)
        _patch_env({"SHEETS_PNG_DIR": tmp, "SHEETS_PNG_CACHE": os.path.join(tmp, "cache")})
        reset_caches()
        status, hdrs, body = serve("bot1", "1546024641", download=True)
        assert status == 200 and body == tiny, status
        h = dict(hdrs)
        assert h["Content-Type"] == "image/png"
        assert h["Content-Disposition"].startswith("attachment;")
        assert h["Content-Disposition"].endswith('filename="01_BTC_vol_vs_VIX.png"'), h["Content-Disposition"]
        status, _h, body = serve("bot1", "1546024641", if_none_match=h["ETag"])
        assert status == 304 and body == b"", status
        try:
            serve("nope", "1")
            raise AssertionError("bad book should 404")
        except PngError as e:
            assert e.status == 404

        # Drive API: newest file wins, bytes are cached, the second view does not re-download.
        calls = {"list": 0, "media": 0, "auth": []}
        listed = {
            FOLDER_IDS["bot1"]: [{
                "id": "fileNEWER", "name": "001 BTC vol vs VIX.png", "mimeType": "image/png",
                "modifiedTime": "2026-11-02T11:58:03.070Z", "size": "128",
            }, {
                "id": "fileOLDER", "name": "001 BTC vol vs VIX.png", "mimeType": "image/png",
                "modifiedTime": "2026-10-01T00:00:00.000Z", "size": "128",
            }],
        }

        def transport(url, data=None, headers=None, timeout=45):
            auth = (headers or {}).get("Authorization") or ""
            assert auth == "Bearer test-token", auth
            assert url.startswith("https://www.googleapis.com/"), url
            if "/files/fileNEWER" in url and "alt=media" in url:
                calls["media"] += 1
                return 200, tiny, {}
            if "/files/fileOLDER" in url:
                raise AssertionError("downloaded the older duplicate")
            if url.startswith("https://www.googleapis.com/drive/v3/files?"):
                calls["list"] += 1
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                parent = ""
                for part in (q.get("q") or [""])[0].split(" in parents"):
                    parent = part.strip().strip("'")
                    break
                body = json.dumps({"files": listed.get(parent, [])}).encode()
                return 200, body, {}
            raise AssertionError("unexpected url " + url)

        _TRANSPORT = transport
        _patch_env({"SHEETS_PNG_DIR": None, "GOOGLE_ACCESS_TOKEN": "test-token",
                    "SHEETS_PNG_CACHE": os.path.join(tmp, "drive-cache")})
        reset_caches()
        status, hdrs, body = serve("bot1", "1546024641")
        assert status == 200 and body == tiny
        assert calls["media"] == 1 and calls["list"] == 3, calls
        etag = dict(hdrs)["ETag"]
        status, _h, body = serve("bot1", "1546024641", if_none_match=etag)
        assert status == 304 and body == b""
        status, _h, body = serve("bot1", "1546024641")
        assert status == 200 and body == tiny and calls["media"] == 1, calls
        # A newer Drive modifiedTime invalidates the disk cache after the index expires.
        listed[FOLDER_IDS["bot1"]][0]["modifiedTime"] = "2026-11-03T00:00:00.000Z"
        global _INDEX
        _INDEX = None
        index_file = os.path.join(os.environ["SHEETS_PNG_CACHE"], "index.json")
        os.remove(index_file)
        status, _h, body = serve("bot1", "1546024641")
        assert status == 200 and calls["media"] == 2, calls
        try:
            serve("bot2", "1")
            raise AssertionError("bot2 gid 1 is not a tab")
        except PngError as e:
            assert e.status == 404

        # Service-account JWT requests drive.readonly and the signature verifies.
        key_path = os.path.join(tmp, "sa.pem")
        pub_path = os.path.join(tmp, "sa.pub")
        subprocess.check_call(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", key_path],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["openssl", "pkey", "-in", key_path, "-pubout", "-out", pub_path],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        info = {"client_email": "sheets@example.iam.gserviceaccount.com", "private_key": open(key_path).read()}
        captured = {}

        def token_transport(url, data=None, headers=None, timeout=45):
            assert url == "https://oauth2.googleapis.com/token", url
            form = urllib.parse.parse_qs(data.decode())
            assertion = form["assertion"][0]
            signing, sig_b64 = assertion.rsplit(".", 1)
            pad = "=" * (-len(sig_b64) % 4)
            sig = base64.urlsafe_b64decode(sig_b64 + pad)
            payload = signing.split(".", 1)[1]
            ppad = "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload + ppad))
            captured["claims"] = claims
            sig_path = os.path.join(tmp, "sig.bin")
            open(sig_path, "wb").write(sig)
            verified = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", pub_path, "-signature", sig_path],
                input=signing.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if verified.returncode != 0:
                raise AssertionError(verified.stderr.decode() or "signature did not verify")
            return 200, json.dumps({"access_token": "ya29.test", "expires_in": 3600}).encode(), {}

        _TRANSPORT = token_transport
        _patch_env({"GOOGLE_ACCESS_TOKEN": None, "GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(info)})
        reset_caches()
        access, kind = get_token()
        assert access == "ya29.test" and kind == "service-account"
        assert captured["claims"]["scope"] == DRIVE_SCOPE
        assert captured["claims"]["iss"] == info["client_email"]
    finally:
        _TRANSPORT = None
        _restore_env(old)
        reset_caches()
        shutil.rmtree(tmp, ignore_errors=True)
    # HTTP route, using the local export so the handler and the login gate agree.
    argv = sys.argv
    sys.argv = [argv[0]]
    try:
        import server
    finally:
        sys.argv = argv
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    old = _patch_env({"SHEETS_PNG_DIR": tmp, "SHEETS_PNG_CACHE": os.path.join(tmp, "cache2")})
    # tmp was removed; rebuild a tiny export for the live handler.
    try:
        os.makedirs(os.path.join(tmp, "bot1"), exist_ok=True)
        with open(os.path.join(tmp, "bot1", "001 BTC vol vs VIX.png"), "wb") as f:
            f.write(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        reset_caches()
        url = "http://127.0.0.1:%d/api/sheet-png?book=bot1&gid=1546024641" % port
        with urllib.request.urlopen(url, timeout=10) as r:
            got = r.read()
            assert r.status == 200
            assert r.headers["Content-Type"] == "image/png"
            assert got.startswith(PNG_MAGIC)
        req = urllib.request.Request(url + "&download=1")
        with urllib.request.urlopen(req, timeout=10) as r:
            assert "attachment" in r.headers["Content-Disposition"]
            assert r.headers["Content-Disposition"].endswith('filename="01_BTC_vol_vs_VIX.png"')
    finally:
        httpd.shutdown()
        _restore_env(old)
        reset_caches()
        shutil.rmtree(tmp, ignore_errors=True)
    print("self-test ok")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Serve or test full-size Sheets chart PNGs.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
    else:
        ap.error("pass --self-test")
