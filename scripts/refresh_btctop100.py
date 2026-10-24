#!/usr/bin/env python3
"""Rewrite btctop100.json from Mark's BTCTC Top 100 sheet.

Spreadsheet "My Bot's Sheets"
  https://docs.google.com/spreadsheets/d/1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU
  tab "BTCTC Top 100" (gid 73825060).

B1 is the chart title. Row 2 headers are Rank, Company, Ticker, Country,
Change, BTC Holdings, a blank column, As-of Date, Source, Source Link
(HYPERLINK formulas), Notes. Rows 3-102 are the 100 companies, already
sorted by BTC descending.

The sheet is private. This reads displayed cell values with Google
credentials from the environment (the same kind of env credentials the app
already uses for GitHub), and falls back to a CSV export when those
credentials are absent or the API call fails. Displayed values are what
the tab shows (BTC rounded the way the sheet rounds it, dates as formatted).
Source Link formulas are read separately so the URL is kept.

Credential order:
  GOOGLE_ACCESS_TOKEN or CHARTMAKER_GOOGLE_TOKEN
  GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN
    (CHARTMAKER_GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN also work)
  GOOGLE_APPLICATION_CREDENTIALS or CHARTMAKER_GOOGLE_CREDENTIALS
    (service-account JSON; signed with openssl)

USD value is not stored. The app multiplies BTC by a live BTC-USD quote.

Usage:
  python3 scripts/refresh_btctop100.py
  python3 scripts/refresh_btctop100.py --csv btctc100.csv
  python3 scripts/refresh_btctop100.py --check
  python3 scripts/refresh_btctop100.py --self-test
"""
import argparse, base64, csv, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "btctop100.json")
SHEET_ID = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
TAB = "BTCTC Top 100"
GID = 73825060
BOOK = "My Bot's Sheets"
SUPPLY = 21_000_000
TOP_N = 100
RANGE = "A1:L120"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid={GID}"
_LINK = re.compile(r"""^=HYPERLINK\(\s*"((?:[^"]|"")*)"\s*,\s*"((?:[^"]|"")*)"\s*\)$""", re.I)


def env(*names):
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def http_json(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def service_account_token(path):
    info = json.load(open(path, encoding="utf-8"))
    email, key = info.get("client_email"), info.get("private_key")
    if not email or not key:
        raise RuntimeError("service account JSON has no client_email or private_key")
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claim = b64url(json.dumps({
        "iss": email,
        "scope": "https://www.googleapis.com/auth/spreadsheets.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }, separators=(",", ":")).encode())
    signing = f"{header}.{claim}".encode()
    fd, pem = tempfile.mkstemp(prefix="btctc-sa-", suffix=".pem")
    try:
        os.write(fd, key.encode())
        os.close(fd)
        fd = None
        os.chmod(pem, 0o600)
        sig = subprocess.check_output(["openssl", "dgst", "-sha256", "-sign", pem], input=signing)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.remove(pem)
        except OSError:
            pass
    assertion = signing.decode() + "." + b64url(sig)
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    tok = http_json("https://oauth2.googleapis.com/token", data=body, timeout=30)
    if not tok.get("access_token"):
        raise RuntimeError("service account token response had no access_token")
    return tok["access_token"]


def refresh_access_token(client_id, client_secret, refresh_token):
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    tok = http_json("https://oauth2.googleapis.com/token", data=body, timeout=30)
    if not tok.get("access_token"):
        raise RuntimeError("refresh response had no access_token")
    return tok["access_token"]


def access_token():
    """Return (token, how) or (None, reason)."""
    direct = env("GOOGLE_ACCESS_TOKEN", "CHARTMAKER_GOOGLE_TOKEN")
    if direct:
        return direct, "access token"
    cid = env("GOOGLE_CLIENT_ID", "CHARTMAKER_GOOGLE_CLIENT_ID")
    secret = env("GOOGLE_CLIENT_SECRET", "CHARTMAKER_GOOGLE_CLIENT_SECRET")
    refresh = env("GOOGLE_REFRESH_TOKEN", "CHARTMAKER_GOOGLE_REFRESH_TOKEN")
    if cid and secret and refresh:
        return refresh_access_token(cid, secret, refresh), "oauth refresh token"
    path = env("GOOGLE_APPLICATION_CREDENTIALS", "CHARTMAKER_GOOGLE_CREDENTIALS")
    if path:
        if not os.path.isfile(path):
            raise RuntimeError("credential file not found: " + path)
        return service_account_token(path), path
    return None, "no Google credentials in the environment"


def fetch_values(token, render):
    rng = urllib.parse.quote(f"'{TAB}'!{RANGE}", safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{rng}"
        "?valueRenderOption=" + render
    )
    doc = http_json(url, headers={"Authorization": "Bearer " + token}, timeout=45)
    rows = doc.get("values")
    if not rows:
        raise RuntimeError("sheet range came back empty")
    width = max(len(r) for r in rows)
    return [list(r) + [""] * (width - len(r)) for r in rows]


def fetch_sheet(token):
    """Displayed values, with HYPERLINK formulas kept in the Source Link column."""
    grid = fetch_values(token, "FORMATTED_VALUE")
    try:
        formulas = fetch_values(token, "FORMULA")
    except Exception:
        return grid
    _, headers = find_header(grid)
    if "source link" not in headers:
        return grid
    link = headers.index("source link")
    for i, row in enumerate(formulas):
        if i >= len(grid) or link >= len(row):
            continue
        if str(row[link] or "").lstrip().upper().startswith("=HYPERLINK"):
            grid[i][link] = row[link]
    return grid


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.reader(f)]


def parse_num(cell):
    s = str(cell or "").strip().replace(",", "").replace(" ", "")
    if s == "" or s in ("—", "-", "–"):
        return None
    return float(s)


def parse_date(cell):
    s = str(cell or "").strip()
    if not s:
        raise ValueError("missing as-of date")
    # The sheet shows a few dates as YYYY-MM (SOS is 2026-06). Keep that text.
    if re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", s):
        return s
    if re.fullmatch(r"\d+(?:\.0+)?", s):
        n = int(float(s))
        if not 20000 <= n <= 80000:
            raise ValueError("date serial out of range: " + s)
        return (datetime(1899, 12, 30) + timedelta(days=n)).strftime("%Y-%m-%d")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError("bad as-of date: " + s)


def parse_link(cell):
    s = str(cell or "").strip()
    if not s:
        return None, None
    m = _LINK.fullmatch(s)
    if not m:
        return None, s
    url = m.group(1).replace('""', '"').strip()
    label = m.group(2).replace('""', '"').strip()
    if url and not url.startswith("https://"):
        raise ValueError("source link is not https: " + url[:80])
    return (url or None), (label or None)


def json_num(v):
    if abs(v - round(v)) < 1e-8:
        return int(round(v))
    return round(v, 8)


def find_header(grid):
    for i, row in enumerate(grid[:6]):
        h = [norm(c) for c in row]
        if "rank" in h and "company" in h and "ticker" in h and any(x.startswith("btc") for x in h):
            return i, h
    raise SystemExit("sheet has no header row (Rank, Company, Ticker, BTC Holdings)")


def cell(row, idx):
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def title_from(grid, header_i):
    for row in grid[:header_i]:
        for c in row:
            t = " ".join(str(c or "").split())
            if t:
                return t
    raise SystemExit("sheet title cell is empty")


def build(grid):
    header_i, headers = find_header(grid)
    def col(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None
    ix = {
        "rank": col("rank"),
        "name": col("company"),
        "ticker": col("ticker"),
        "country": col("country"),
        "change": col("change"),
        "btc": col("btc holdings", "btc"),
        "asOf": col("as of date", "as of"),
        "source": col("source"),
        "link": col("source link"),
        "notes": col("notes"),
    }
    missing = [k for k in ("rank", "name", "ticker", "country", "btc", "asOf") if ix[k] is None]
    if missing:
        raise SystemExit("header missing " + ", ".join(missing))
    title = title_from(grid, header_i)
    companies = []
    for row in grid[header_i + 1:]:
        if not any(str(c or "").strip() for c in row):
            continue
        rank_raw = str(cell(row, ix["rank"])).strip()
        name = " ".join(str(cell(row, ix["name"])).split())
        if not rank_raw and not name:
            continue
        try:
            rank = int(float(rank_raw))
        except ValueError:
            raise SystemExit(f"bad rank {rank_raw!r} for {name or 'a row'}")
        ticker = str(cell(row, ix["ticker"])).strip()
        country = str(cell(row, ix["country"])).strip().upper()
        btc = parse_num(cell(row, ix["btc"]))
        if not name or not ticker:
            raise SystemExit(f"rank {rank} missing company or ticker")
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise SystemExit(f"{ticker} country must be a 2-letter code, got {country!r}")
        if btc is None or btc <= 0:
            raise SystemExit(f"{ticker} has no BTC holding")
        try:
            as_of = parse_date(cell(row, ix["asOf"]))
        except ValueError as e:
            raise SystemExit(f"{ticker}: {e}")
        url, label = parse_link(cell(row, ix["link"]))
        change = parse_num(cell(row, ix["change"])) if ix["change"] is not None else None
        notes = " ".join(str(cell(row, ix["notes"])).split()) or None
        source = " ".join(str(cell(row, ix["source"])).split()) or None
        companies.append({
            "rank": rank,
            "name": name,
            "ticker": ticker,
            "country": country,
            "change": json_num(change) if change is not None else None,
            "btc": json_num(btc),
            "asOf": as_of,
            "source": source,
            "sourceLabel": label,
            "sourceUrl": url,
            "notes": notes,
        })
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "title": title,
        "source": SHEET_URL,
        "sourceName": BOOK,
        "spreadsheetId": SHEET_ID,
        "sheet": TAB,
        "gid": GID,
        "description": (
            "Top 100 corporate bitcoin holdings from the BTCTC Top 100 tab of My Bot's Sheets. "
            "USD value is BTC times a live BTC price and is not part of this file."
        ),
        "fetchedAt": now,
        "supply": SUPPLY,
        "companies": companies,
    }
    validate(doc)
    return doc


def validate(doc):
    if not str(doc.get("title") or "").strip():
        raise SystemExit("title is empty")
    if doc.get("supply") != SUPPLY:
        raise SystemExit("supply must be 21000000")
    if doc.get("sheet") != TAB or doc.get("spreadsheetId") != SHEET_ID:
        raise SystemExit("snapshot is not the BTCTC Top 100 sheet")
    cos = doc.get("companies")
    if not isinstance(cos, list) or len(cos) != TOP_N:
        raise SystemExit(f"expected {TOP_N} companies, got {len(cos) if isinstance(cos, list) else type(cos).__name__}")
    prev = None
    for i, c in enumerate(cos):
        for k in ("name", "ticker", "country", "asOf", "btc"):
            if c.get(k) in (None, ""):
                raise SystemExit(f"row {i} missing {k}")
        if c.get("rank") != i + 1:
            raise SystemExit(f"row {i} rank is {c.get('rank')}, expected {i + 1}")
        if not isinstance(c["btc"], (int, float)) or isinstance(c["btc"], bool) or c["btc"] <= 0:
            raise SystemExit(f"row {i} bad btc")
        if not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", c.get("asOf") or ""):
            raise SystemExit(f"row {i} asOf must be YYYY-MM or YYYY-MM-DD")
        if not re.fullmatch(r"[A-Z]{2}", c.get("country") or ""):
            raise SystemExit(f"row {i} bad country")
        if prev is not None and c["btc"] > prev + 1e-6:
            raise SystemExit(f"row {i} ({c.get('ticker')}) is not in descending BTC order")
        prev = c["btc"]
        url = c.get("sourceUrl")
        if url and not str(url).startswith("https://"):
            raise SystemExit(f"row {i} source link is not https")
    return doc


def write_doc(doc):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, OUT)


def self_test():
    formula = """ ,Corporate Bitcoin Treasury Holdings
,Rank,Company,Ticker,Country,Change,BTC Holdings,,As-of Date,Source,Source Link,Notes
,1,Strategy,MSTR,US,,848517,,46313,SEC Form 8-K,"=HYPERLINK(""https://www.sec.gov/Archives/edgar/data/1050446/mstr.htm"",""SEC 8-K"")",Bought more.
,2,Example Co,EX,JP,20,"1,234.5",,2026-06-30,A filing,Press release,"""
    assert parse_date(46313) == "2026-10-18", parse_date(46313)
    assert parse_date("46203") == "2026-06-30"
    assert parse_date("2026-10-18") == "2026-10-18"
    assert parse_date("2026-06") == "2026-06"
    url, label = parse_link('=HYPERLINK("https://example.com/a","SEC 8-K")')
    assert url == "https://example.com/a" and label == "SEC 8-K", (url, label)
    assert parse_num("848,517") == 848517
    assert json_num(848517.0) == 848517 and json_num(63.7) == 63.7
    rows = list(csv.reader(formula.splitlines()))
    header_i, headers = find_header(rows)
    assert header_i == 1 and "btc holdings" in headers and title_from(rows, header_i) == "Corporate Bitcoin Treasury Holdings"
    try:
        build(rows)
    except SystemExit as e:
        assert "expected 100" in str(e), e
    else:
        raise SystemExit("short fixture should have been rejected")
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser(description="Refresh the BTCTC Top 100 snapshot from Mark's sheet")
    ap.add_argument("--check", action="store_true", help="validate btctop100.json and do not download")
    ap.add_argument("--csv", help="CSV export to use when the sheet cannot be read")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    if args.check:
        with open(OUT, encoding="utf-8") as f:
            doc = json.load(f)
        validate(doc)
        btc = sum(c["btc"] for c in doc["companies"])
        print(f"ok {len(doc['companies'])} companies, {doc['title']!r}, {btc:,.0f} BTC, fetched {doc.get('fetchedAt')}")
        return
    grid, how = None, None
    err = None
    try:
        token, how = access_token()
    except Exception as e:
        token, how, err = None, None, str(e)
    if token:
        try:
            grid = fetch_sheet(token)
            how = "sheet via " + str(how)
        except Exception as e:
            err = str(e)
            grid = None
    elif not err:
        err = how
    if grid is None:
        path = args.csv or env("BTCTC_CSV")
        if not path:
            raise SystemExit(
                "Could not read the Google Sheet (" + str(err) + ").\n"
                "Set Google credentials (see the script docstring) or pass a CSV export:\n"
                "  python3 scripts/refresh_btctop100.py --csv btctc100.csv"
            )
        grid = load_csv(path)
        how = "csv " + path + ((" after sheet error: " + err) if err and token else "")
    doc = build(grid)
    write_doc(doc)
    btc = sum(c["btc"] for c in doc["companies"])
    print(f"wrote {OUT}")
    print(f"{how}: {doc['title']!r}, {len(doc['companies'])} companies, {btc:,.0f} BTC")
    for c in doc["companies"][:5]:
        print(f"  {c['rank']:>3}  {c['ticker']:<12} {c['country']}  {c['btc']:>12}  {c['asOf']}  {c['name']}")
    print(f"  {doc['companies'][-1]['rank']:>3}  {doc['companies'][-1]['ticker']:<12} {doc['companies'][-1]['btc']}")


if __name__ == "__main__":
    sys.exit(main())
