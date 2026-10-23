"""Treasury mode: mNAV, amplification, BTC/share for BTC treasury companies.

Market cap, price, shares, and BTC-USD come from Yahoo. BTC holdings, and debt
or preferred when a filing states them, come from primary documents:

- MSTR BTC: latest Strategy SEC 8-K column "Aggregate BTC Holdings" (the 8-K
  itself, or an exhibit when that is where the table is filed)
- MSTR debt: latest SEC 10-Q/10-K LongTermDebt
- MSTR preferred: that filing's TemporaryEquityLiquidationPreference, plus face
  value of preferred sold in later 8-Ks, minus STRC shares repurchased in later
  8-Ks times the $100 stated amount the 10-Q gives STRC
- ASST BTC and preferred stated amount: Strive SEC 8-Ks ("Bitcoin held", SATA
  shares, SATS stated amount once a later 8-K reports the closing)
- 3350.T BTC: the latest Metaplanet "Notice of Additional Purchase of Bitcoin"
  on metaplanet.jp (the PDF states the holdings and the as-of date)
- SWC.L BTC: the company's treasury-data.aspx feed (sum of btcChange)
- SWC.L debt and preferred: the company's analytics page, from the labeled
  "Total Debt Outstanding" and "Total Preferred" terms

BitcoinTreasuries.net and other treasury trackers are a quality bar for what a
primary-source figure should look like. They are not fetched and not used as a
feed. A figure that fails the parser or a wide sanity check stays on the last
accepted value and is flagged. Last resort is treasury.json, then the committed
treasury_snapshot.json from the previous successful refresh.
"""
import json, os, re, sys, time, threading, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import fin, heat

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "treasury.json")
SNAP = os.path.join(HERE, "treasury_snapshot.json")
CDIR = os.path.join(HERE, "cache")
LIVE = os.path.join(CDIR, "treasury_live.json")
FDIR = os.path.join(CDIR, "treasury_filings")
TTL = 6 * 3600
RETRY = 15 * 60
SEC_UA = "Chartmaker mark.a.harv@gmail.com"
WEB_UA = "Mozilla/5.0 (compatible; Chartmaker/1.0; +mark.a.harv@gmail.com)"
SWC_XML = "https://www.smarterwebcompany.co.uk/_private/treasury-data.aspx"
SWC_PAGE = "https://www.smarterwebcompany.co.uk/bitcoin-treasury/analytics-/"
META_DISCLOSURES = "https://metaplanet.jp/en/disclosures"
META_HOME = "https://metaplanet.jp/en"
# STRC stated amount, from Strategy's 10-Q ("$100 per share stated amount"). The
# weekly 8-K reports shares repurchased, not the face retired. STRC is the only
# series this is applied to. Other series are left on the last-known figure.
STRC_FACE = 100.0
ASST_PAR = 100.0  # SATA stated amount per share; SATS states the same $100 in the 8-K footnote
FORMS = fin.FORMS
MONTHS = {m: i for i, m in enumerate("January February March April May June July August September October November December".split(), 1)}
_LOCK = threading.Lock()
_SEC_LOCK = threading.Lock()
_SEC_NEXT = [0.0]
_MEM = {"t": 0.0, "doc": None}
_SUB = {}

def load():
    with open(PATH) as f: return json.load(f)

def _http(url, ua=SEC_UA, timeout=30):
    if "sec.gov" in url:
        with _SEC_LOCK:
            wait = _SEC_NEXT[0] - time.time()
            if wait > 0: time.sleep(wait)
            _SEC_NEXT[0] = time.time() + 0.12
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read()

def _clean(n):
    if n is None: return None
    x = float(n)
    return int(round(x)) if abs(x - round(x)) < 1e-6 else round(x, 6)

def _num(s):
    if s is None: return None
    t = str(s).strip().replace(",", "").replace("$", "").replace("£", "").replace("€", "")
    t = re.sub(r"\s*\(\d+\)\s*$", "", t).strip()
    if not t or t in ("—", "-", "–", "−"): return None
    if t[0] == "(" and t[-1] == ")": t = "-" + t[1:-1]
    try: return float(t)
    except ValueError: return None

def _usdate(s):
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})", s or "")
    if not m: return None
    return f"{int(m.group(3)):04d}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"

def _sec_url(cik, accn, doc):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}"

def _strip(html):
    if isinstance(html, bytes): html = html.decode("utf-8", "replace")
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&#160;", " ").replace("&amp;", "&").replace("&#163;", "£").replace("&pound;", "£")
    text = text.replace("&rsquo;", "'").replace("&lsquo;", "'").replace("&rdquo;", '"').replace("&ldquo;", '"').replace("&mdash;", "—").replace("&ndash;", "-").replace("&quot;", '"')
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if int(m.group(1)) < 128512 else " ", text)
    return re.sub(r"\s+", " ", text)

def _cell(td):
    t = re.sub(r"(?is)<br\s*/?>", " ", td)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&#160;", " ").replace("&nbsp;", " ").replace("&#8212;", "—").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()

def _grids(html):
    if isinstance(html, bytes): html = html.decode("utf-8", "replace")
    grids = []
    for table in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html):
        grid = []
        for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table):
            row, col = {}, 0
            tds = re.findall(r"(?is)<td[^>]*>.*?</td>", tr)
            if not tds: continue
            for td in tds:
                m = re.search(r'(?i)\bcolspan="(\d+)"', td)
                span = int(m.group(1)) if m else 1
                text = _cell(td)
                while col in row: col += 1
                for k in range(span): row[col + k] = text
                col += span
            if any(row.values()): grid.append(row)
        if grid: grids.append(grid)
    return grids

def _uniq(row):
    """Cell texts in column order, collapsing a colspan that repeated one string."""
    out, last = [], None
    for c in sorted(row):
        t = row[c]
        if t != last: out.append(t)
        last = t
    return [t for t in out if t]

def _pdf_text(data):
    """Concatenate literal strings in Flate-compressed PDF content streams. Stdlib only."""
    if isinstance(data, str): data = data.encode("latin1", "replace")
    parts = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try: dec = __import__("zlib").decompress(m.group(1))
        except Exception: continue
        for s in re.findall(rb"\((?:\\.|[^\\)])*\)", dec):
            raw = s[1:-1].replace(b"\\n", b"\n").replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            parts.append(raw.decode("latin1", "replace"))
    return "".join(parts)

def parse_mstr_btc(html):
    """Latest Aggregate BTC Holdings in the document. None if the column is absent."""
    found = []
    for grid in _grids(html):
        header_i, cols = None, []
        for i, row in enumerate(grid):
            for c, t in row.items():
                if t.strip() == "Aggregate BTC Holdings" or t.startswith("Aggregate BTC Holdings"):
                    cols.append(c); header_i = i if header_i is None else header_i
        if not cols: continue
        head = " ".join(" ".join(_uniq(r)) for r in grid[:3])
        m = re.search(r"as of\s+([A-Z][a-z]+ \d{1,2}, \d{4})", head, re.I)
        asof = _usdate(m.group(1)) if m else None
        for r in grid[(header_i or 0) + 1:(header_i or 0) + 4]:
            for c in cols:
                n = _num(r.get(c, ""))
                if n and n >= 10000 and abs(n - round(n)) < 1e-6:
                    found.append({"btc": int(round(n)), "btcAsOf": asof})
                    break
    if not found: return None
    return max(enumerate(found), key=lambda iv: (iv[1]["btcAsOf"] or "", iv[0]))[1]

def parse_mstr_activity(html):
    """Preferred face sold (Notional Value, $ millions) and shares repurchased, by series and period."""
    out = []
    for grid in _grids(html):
        head = " ".join(" ".join(_uniq(r)) for r in grid[:2])
        m = re.search(r"During Period ([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})", head)
        if not m: continue
        start, end = _usdate(m.group(1)), _usdate(m.group(2))
        if "Notional Value" in head and "Shares Sold" in head: kind = "issued"
        elif "Shares Repurchased" in head: kind = "repurchased"
        else: continue
        bucket = {}
        for row in grid:
            cells = _uniq(row)
            if not cells: continue
            name = re.sub(r"\s*\(\d+\)\s*$", "", cells[0]).strip()
            sm = re.match(r"(STR[A-Z]+)\s+Stock\b", name)
            if not sm: continue
            rest = [c for c in cells[1:] if c.strip() not in ("$", "")]
            if kind == "issued":
                notional = _num(rest[1]) if len(rest) > 1 else None
                if notional: bucket[sm.group(1).upper()] = notional
            else:
                shares = _num(rest[0]) if rest else None
                if shares: bucket[sm.group(1).upper()] = shares
        if start and end and bucket:
            out.append({"start": start, "end": end, "kind": kind, "by": bucket})
    return out

def parse_asst_table(html):
    """Latest 'Bitcoin held' and SATA/SATS share counts from a Strive holdings 8-K. None if absent."""
    for grid in _grids(html):
        dates = []
        for row in grid:
            found = [_usdate(t) for t in _uniq(row) if "as of" in t.lower() and _usdate(t)]
            if len(found) >= 2: dates = found; break
        if len(dates) < 2: continue
        btc, shares = None, {}
        for row in grid:
            cells = _uniq(row)
            if not cells: continue
            label = cells[0]
            if len(label) > 80: continue
            nums = [n for t in cells[1:] if (n := _num(t)) is not None]
            if re.match(r"Bitcoin held\b", label, re.I) and len(nums) >= 2:
                best = max(zip(dates, nums), key=lambda p: p[0])
                btc = {"btc": _clean(best[1]), "btcAsOf": best[0]}
            m = re.match(r"(SATA|SATS) Stock\b", label, re.I)
            if m and len(nums) >= 1:
                best = max(zip(dates, nums), key=lambda p: p[0])
                shares[m.group(1).upper()] = _clean(best[1])
        if btc: return btc, shares
    return None

def parse_sats_offering(html):
    """Pending SATS stated amount from the holdings 8-K footnote, if SATS was not yet outstanding."""
    text = _strip(html)
    m = re.search(r"([\d,]+) shares of its [\d.]+% Series A Perpetual SATS Preferred Stock \(\$([\d,.]+) million aggregate stated amount\)", text, re.I)
    if not m or "no shares of sats stock were outstanding" not in text.lower(): return None
    settle = re.search(r"scheduled to settle on ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    return {"shares": _clean(_num(m.group(1))), "millions": _clean(_num(m.group(2))), "settle": _usdate(settle.group(1)) if settle else None}

def _closing(html):
    t = _strip(html).lower()
    return "closing of the offering" in t or "closing of its upsized initial public offering" in t

def _no_debt(html):
    return re.search(r"we don.?t have any debt|we do not have any debt", _strip(html), re.I) is not None

def parse_swc_btc(xml_bytes):
    root = ET.fromstring(xml_bytes)
    node = root.find("btc")
    if node is None: return None
    total, dates = Decimal(0), []
    for e in node.findall("entry"):
        total += Decimal(e.findtext("btcChange") or "0")
        if e.findtext("entryDate"): dates.append(e.findtext("entryDate"))
    if not dates: return None
    return {"btc": _clean(total), "btcAsOf": max(dates)}

def parse_swc_cap(html):
    """Debt and preferred from the labeled NAV formula. None if the sentence is not on the page."""
    text = _strip(html)
    phrase = "Bitcoin Value + Treasury Cash Balance + Dividend Cash Balance - Total Debt Outstanding - Total Preferred"
    i = text.find(phrase)
    if i < 0: return None
    m = re.search(r"£\s*([\d,]+(?:\.\d+)?)\s*\+\s*£\s*([\d,]+(?:\.\d+)?)\s*\+\s*£\s*([\d,]+(?:\.\d+)?)\s*-\s*£\s*([\d,]+(?:\.\d+)?)\s*-\s*£\s*([\d,]+(?:\.\d+)?)", text[i:i + 1200])
    if not m: return None
    return {"debt_m": _clean(_num(m.group(4)) / 1e6), "pref_m": _clean(_num(m.group(5)) / 1e6)}

def parse_meta_btc(text):
    """Holdings sentence from a Metaplanet purchase notice. None if it is not that notice."""
    m = re.search(r"total Bitcoin holdings to ([\d,]+) BTC as of ([A-Z][a-z]+ \d{1,2}, \d{4})", text or "")
    if not m: return None
    return {"btc": _clean(_num(m.group(1))), "btcAsOf": _usdate(m.group(2))}

def parse_meta_home(html):
    if isinstance(html, bytes): html = html.decode("utf-8", "replace")
    m = re.search(r"₿\s*([\d,]+)</span>\s*<span[^>]*>\s*BTC Holdings", html)
    if not m: return None
    return _clean(_num(m.group(1)))

def _accept(flags, ticker, label, new, old, lo, hi):
    if new is None: return False
    if not (lo <= new <= hi):
        flags.append(f"{ticker} {label}: {_clean(new)} rejected (outside {lo:g}–{hi:g}); kept last-known")
        return False
    if old:
        ratio = new / old
        if not (0.2 <= ratio <= 5):
            flags.append(f"{ticker} {label}: {_clean(new)} rejected (last-known {_clean(old)}); kept last-known")
            return False
    return True

def _submissions(cik, fresh=False):
    now = time.time()
    with _LOCK:
        hit = _SUB.get(cik)
        if hit and not fresh and now - hit[0] < TTL: return hit[1]
    doc = json.loads(_http(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    with _LOCK: _SUB[cik] = (now, doc)
    return doc

def _recent_8k(cik, since, cap=80, fresh=False):
    """8-Ks filed on or after `since` (YYYY-MM-DD), newest first. truncated if `cap` cut the list short of `since`."""
    rec = _submissions(cik, fresh)["filings"]["recent"]
    out = []
    for i, form in enumerate(rec["form"]):
        if form not in ("8-K", "8-K/A"): continue
        filed = rec["filingDate"][i]
        if filed < since: return out, False
        acc = rec["accessionNumber"][i]
        out.append({"filed": filed, "acc": acc, "doc": rec["primaryDocument"][i],
                    "url": _sec_url(cik, acc, rec["primaryDocument"][i])})
        if len(out) >= cap: return out, True
    return out, False

def _cached(name):
    os.makedirs(FDIR, exist_ok=True)
    p = os.path.join(FDIR, name)
    if os.path.exists(p) and os.path.getsize(p) > 400:
        with open(p, "rb") as f: return f.read()
    return None

def _store(name, raw):
    if not raw or len(raw) < 400: return
    try:
        os.makedirs(FDIR, exist_ok=True)
        p = os.path.join(FDIR, name)
        with open(p + ".tmp", "wb") as f: f.write(raw)
        os.replace(p + ".tmp", p)
    except OSError: pass

def _get_cached(url, name, ua=SEC_UA, timeout=30):
    hit = _cached(name)
    if hit is not None: return hit
    raw = _http(url, ua, timeout)
    _store(name, raw)
    return raw

def _filing_docs(cik, meta):
    """Primary 8-K and exhibit HTML. XBRL viewer files (R1.htm) are skipped."""
    acc = meta["acc"].replace("-", "")
    docs = [(meta["url"], f"{acc}_{meta['doc']}")]
    try:
        idx = json.loads(_get_cached(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/index.json", f"{acc}_index.json"))
    except Exception:
        idx = {}
    for it in (idx.get("directory") or {}).get("item") or []:
        name = it.get("name") or ""
        low = name.lower()
        if not low.endswith((".htm", ".html")): continue
        if "index" in low or re.match(r"r\d+\.htm", low): continue
        if "ex" not in low: continue
        url = _sec_url(cik, meta["acc"], name)
        if all(url != u for u, _ in docs): docs.append((url, f"{acc}_{name}"))
    out = []
    for url, name in docs:
        try: out.append((url, _get_cached(url, name)))
        except Exception: continue
    return out

def _fact(facts, tag):
    rows = [x for x in facts["facts"]["us-gaap"][tag]["units"]["USD"] if "start" not in x and x.get("form") in FORMS]
    if not rows: raise KeyError(tag)
    return max(rows, key=lambda r: (r["end"], r["filed"], 1 if r.get("frame") else 0))

def _accn_url(cik, accn, fresh=False):
    rec = _submissions(cik, fresh)["filings"]["recent"]
    for i, a in enumerate(rec["accessionNumber"]):
        if a == accn: return _sec_url(cik, accn, rec["primaryDocument"][i])
    return _sec_url(cik, accn, "")

def _mstr(c, base, fresh):
    flags, out = [], {}
    cik = int(c.get("sec_cik") or 1050446)
    old = {**{k: c.get(k) for k in ("btc", "debt_m", "pref_m")}, **base}
    quarter_end, liq, liq_url = None, None, None
    try:
        if fresh: facts = json.loads(_http(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
        else: facts = fin.facts(cik)
        debt = _fact(facts, "LongTermDebt")
        debt_m = debt["val"] / 1e6
        if _accept(flags, "MSTR", "debt $M", debt_m, old.get("debt_m"), 100, 100000):
            out.update(debt_m=_clean(debt_m), debt_cur="USD", debtAsOf=debt["end"],
                       debtSrc=f"SEC {debt['form']} LongTermDebt filed {debt['filed']}",
                       debtUrl=_accn_url(cik, debt["accn"], fresh))
        pref = _fact(facts, "TemporaryEquityLiquidationPreference")
        liq, quarter_end = pref["val"] / 1e6, pref["end"]
        liq_url = _accn_url(cik, pref["accn"], fresh)
        liq_filed = pref["filed"]
    except Exception:
        flags.append("MSTR debt/preferred: SEC companyfacts unavailable; kept last-known")
        liq_filed = None
    since = quarter_end or "2026-06-30"
    try: filings, truncated = _recent_8k(cik, since, 80, fresh)
    except Exception as e:
        flags.append(f"MSTR: SEC submissions unavailable ({type(e).__name__}); kept last-known holdings")
        return out, flags
    btc_best, activity = None, {}
    for meta in reversed(filings):
        for url, html in _filing_docs(cik, meta):
            got = parse_mstr_btc(html)
            if got and (btc_best is None or (got["btcAsOf"] or "", meta["filed"]) >= (btc_best["btcAsOf"] or "", btc_best["filed"])):
                btc_best = {**got, "url": url, "filed": meta["filed"]}
            for ev in parse_mstr_activity(html):
                slot = activity.setdefault((ev["start"], ev["end"]), {"issued": {}, "repurchased": {}})
                slot[ev["kind"]].update(ev["by"])
    if not btc_best:
        flags.append("MSTR BTC: no 8-K with Aggregate BTC Holdings; kept last-known")
        return out, flags
    if _accept(flags, "MSTR", "BTC", btc_best["btc"], old.get("btc"), 50000, 5000000):
        out.update(btc=btc_best["btc"], btcAsOf=btc_best["btcAsOf"], btcUrl=btc_best["url"],
                   btcSrc=f"SEC 8-K filed {btc_best['filed']}")
    if liq is None or not quarter_end: return out, flags
    if truncated:
        flags.append("MSTR preferred: 8-K history since the 10-Q was truncated; kept last-known liquidation preference")
        return out, flags
    issued_m, strc_shares, other, through = 0.0, 0.0, [], quarter_end
    for (start, end), slot in activity.items():
        if not start or not end or start <= quarter_end: continue
        issued_m += sum(slot["issued"].values())
        for series, shares in slot["repurchased"].items():
            if series == "STRC": strc_shares += shares
            else: other.append(series)
        if end > through: through = end
    if other:
        flags.append("MSTR preferred: 8-K repurchased " + ", ".join(sorted(set(other))) + " and only STRC has a $100 stated amount in this parser; kept last-known")
        return out, flags
    face_m = strc_shares * STRC_FACE / 1e6
    pref_m = liq + issued_m - face_m
    if _accept(flags, "MSTR", "pref $M", pref_m, old.get("pref_m"), 1000, 80000):
        out.update(pref_m=_clean(pref_m), pref_cur="USD", prefAsOf=through, prefUrl=liq_url,
                   prefSrc=(f"SEC 10-Q liquidation preference as of {quarter_end} filed {liq_filed}"
                            + (f", plus ${_clean(issued_m):g}M face issued" if issued_m else "")
                            + (f", minus {int(strc_shares):,} STRC × ${STRC_FACE:.0f} stated amount through {through}" if strc_shares else "")))
    return out, flags

def _asst(c, base, fresh):
    flags, out = [], {}
    cik = int(c.get("sec_cik") or 1920406)
    old = {**{k: c.get(k) for k in ("btc", "debt_m", "pref_m")}, **base}
    filings, _ = _recent_8k(cik, "2026-01-01", 12, fresh)
    docs = []
    for meta in filings:
        for url, html in _filing_docs(cik, meta):
            docs.append((meta, url, html))
    parsed = None
    for meta, url, html in docs:
        got = parse_asst_table(html)
        if got: parsed = (meta, url, html, got); break
    if not parsed: raise RuntimeError("no Strive 8-K with a Bitcoin held table")
    hold_meta, hold_url, hold_html, (btc, shares) = parsed
    if _accept(flags, "ASST", "BTC", btc["btc"], old.get("btc"), 1000, 1000000):
        out.update(btc=btc["btc"], btcAsOf=btc["btcAsOf"], btcSrc=f"SEC 8-K filed {hold_meta['filed']}", btcUrl=hold_url)
    par = float(c.get("pref_par") or ASST_PAR)
    offer = parse_sats_offering(hold_html)
    closed = None
    if offer and "SATS" not in shares:
        for meta, url, html in docs:
            if meta["filed"] >= hold_meta["filed"] and meta["acc"] != hold_meta["acc"] and _closing(html):
                closed = (meta, url); break
    pref, parts, asof, pref_url = 0.0, [], btc.get("btcAsOf"), hold_url
    if "SATA" in shares:
        pref += shares["SATA"] * par / 1e6
        parts.append(f"SATA {shares['SATA']:,.0f} × ${par:.0f}")
    if "SATS" in shares:
        pref += shares["SATS"] * par / 1e6
        parts.append(f"SATS {shares['SATS']:,.0f} × ${par:.0f}")
    elif offer and closed:
        pref += offer["millions"]
        parts.append(f"SATS ${offer['millions']:g}M stated amount, closing filed {closed[0]['filed']}")
        asof, pref_url = closed[0]["filed"], closed[1]
    if parts and _accept(flags, "ASST", "pref $M", pref, old.get("pref_m"), 100, 20000):
        out.update(pref_m=_clean(pref), pref_cur="USD", prefAsOf=asof, prefUrl=pref_url, prefSrc="SEC 8-K " + " + ".join(parts))
    for meta, url, html in docs:
        if meta["filed"] >= hold_meta["filed"] and _no_debt(html):
            if _accept(flags, "ASST", "debt $M", 0, old.get("debt_m"), 0, 20000):
                out.update(debt_m=0, debt_cur="USD", debtAsOf=meta["filed"], debtUrl=url,
                           debtSrc=f"SEC 8-K filed {meta['filed']} (company states it has no debt)")
            break
    return out, flags

def _meta_notices(html):
    """English 'Notice of Additional Purchase of Bitcoin' entries, newest first, from the disclosures page."""
    if isinstance(html, bytes): html = html.decode("utf-8", "replace")
    i = html.find('{\\"disclosures\\":[')
    if i < 0: return []
    try: text = html[i:i + 2500000].encode("utf-8").decode("unicode_escape")
    except Exception: return []
    pat = re.compile(r'\{"id":"[^"]+","date":"(?P<date>[^"]+)","title":"(?P<title>(?:\\.|[^"\\])*)","filePath":"(?P<url>https:[^"]+)","isEnglish":true')
    rows = [m.groupdict() for m in pat.finditer(text) if m.group("title").startswith("Notice of Additional Purchase of Bitcoin")]
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows

def _metaplanet(c, base, fresh):
    flags, out = [], {}
    old = {**{k: c.get(k) for k in ("btc", "debt_m", "pref_m")}, **base}
    page = _http(META_DISCLOSURES, WEB_UA, 40)
    got, notice = None, None
    for notice in _meta_notices(page)[:4]:
        try:
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", notice["url"].rsplit("/", 1)[-1])[:80]
            pdf = _get_cached(notice["url"], "meta_" + name, WEB_UA, 40)
            got = parse_meta_btc(_pdf_text(pdf))
        except Exception:
            got = None
        if got: break
    if not got:
        try:
            home = parse_meta_home(_http(META_HOME, WEB_UA, 25))
        except Exception:
            home = None
        if home and _accept(flags, "3350.T", "BTC", home, old.get("btc"), 1000, 1000000):
            flags.append("3350.T BTC: purchase-notice PDF unavailable; figure is the company homepage, which does not state an as-of date")
            out.update(btc=home, btcAsOf=None, btcSrc="Metaplanet homepage metaplanet.jp/en", btcUrl=META_HOME)
        else:
            raise RuntimeError("no Metaplanet purchase notice with a Bitcoin holdings sentence")
    elif _accept(flags, "3350.T", "BTC", got["btc"], old.get("btc"), 1000, 1000000):
        out.update(btc=got["btc"], btcAsOf=got["btcAsOf"], btcUrl=notice["url"],
                   btcSrc=f"Metaplanet notice filed {notice['date']}")
        try:
            home = parse_meta_home(_http(META_HOME, WEB_UA, 25))
        except Exception:
            home = None
        if home is not None and home != got["btc"]:
            flags.append(f"3350.T BTC: homepage shows {_clean(home)}; chart uses the {notice['date']} notice ({_clean(got['btc'])} as of {got['btcAsOf']})")
    return out, flags

def _swc(c, base, fresh):
    flags, out = [], {}
    old = {**{k: c.get(k) for k in ("btc", "debt_m", "pref_m")}, **base}
    btc = parse_swc_btc(_http(SWC_XML, WEB_UA, 25))
    if not btc: raise RuntimeError("Smarter Web feed had no btc entries")
    if _accept(flags, "SWC.L", "BTC", btc["btc"], old.get("btc"), 100, 100000):
        out.update(btc=btc["btc"], btcAsOf=btc["btcAsOf"], btcSrc="Smarter Web treasury feed", btcUrl=SWC_XML)
    try: cap = parse_swc_cap(_http(SWC_PAGE, WEB_UA, 25))
    except Exception: cap = None
    if not cap:
        flags.append("SWC.L debt/pref: analytics page unavailable; kept last-known")
    else:
        asof = btc.get("btcAsOf")
        if _accept(flags, "SWC.L", "debt £M", cap["debt_m"], old.get("debt_m"), 0, 500):
            out.update(debt_m=cap["debt_m"], debt_cur="GBP", debtAsOf=asof, debtSrc="Smarter Web analytics", debtUrl=SWC_PAGE)
        if _accept(flags, "SWC.L", "pref £M", cap["pref_m"], old.get("pref_m"), 0, 500):
            out.update(pref_m=cap["pref_m"], pref_cur="GBP", prefAsOf=asof, prefSrc="Smarter Web analytics", prefUrl=SWC_PAGE)
    return out, flags

_REFRESH = {"MSTR": _mstr, "ASST": _asst, "3350.T": _metaplanet, "SWC.L": _swc}

def _read_json(path):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return None

def _disk():
    doc = _read_json(LIVE)
    if doc and doc.get("tickers"): return doc
    doc = _read_json(SNAP)
    if doc and doc.get("tickers"): return doc
    return {"tickers": {}}

def _save(doc):
    try:
        os.makedirs(CDIR, exist_ok=True)
        with open(LIVE + ".tmp", "w") as f: json.dump(doc, f)
        os.replace(LIVE + ".tmp", LIVE)
    except OSError: pass

def _write_snapshot(doc):
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "_note": "Last successful primary-source refresh (python3 treasury.py --refresh). The server prefers a live refetch once this is older than 6 hours, then this file, then treasury.json. Do not hand-edit.",
           "tickers": doc.get("tickers") or {}}
    with open(SNAP + ".tmp", "w") as f: json.dump(out, f, indent=2)
    os.replace(SNAP + ".tmp", SNAP)
    return out

def overlay(db, fresh=False, syms=None):
    """{ticker: live fields} for the requested symbols. Per-ticker TTL 6h; a failed fetch keeps the last success and is retried after 15 min.

    The lock covers the in-memory cache only. Fetches run outside it: a worker that
    calls back into a locked helper must not wait on the thread that is waiting for it.
    """
    now = time.time()
    with _LOCK:
        doc = _MEM["doc"] if _MEM["doc"] is not None and (not fresh) and now - _MEM["t"] < 30 else None
    if doc is None: doc = _disk()
    doc.setdefault("tickers", {})
    by = {c["ticker"].upper(): c for c in db["companies"]}
    want = [t for t in (syms or list(by)) if t in by]
    need = []
    for t in want:
        ent = doc["tickers"].get(t) or {}
        age = now - ent.get("t", 0)
        due = fresh or not ent or (ent.get("error") and age > RETRY) or (not ent.get("error") and age > TTL)
        if due and t in _REFRESH: need.append(t)
    if need:
        bases = {t: dict((doc["tickers"].get(t) or {}).get("fields") or {}) for t in need}
        def run(t):
            try: fields, fl = _REFRESH[t](by[t], bases[t], fresh)
            except Exception as e: return t, {}, [f"{t}: primary source failed ({type(e).__name__}: {e}); kept last-known"], True
            soft = any("unavailable" in x or "failed" in x or "kept last-known" in x for x in fl)
            return t, fields, fl, soft
        with ThreadPoolExecutor(min(4, len(need))) as ex:
            done = list(ex.map(run, need))
        for t, fields, fl, err in done:
            prev = (doc["tickers"].get(t) or {}).get("fields") or {}
            merged = dict(prev); merged.update({k: v for k, v in fields.items() if v is not None or k.endswith("AsOf")})
            doc["tickers"][t] = {"t": now, "error": bool(err) or not merged, "fields": merged, "flags": fl}
        _save(doc)
    with _LOCK:
        _MEM["t"], _MEM["doc"] = now, doc
    out, flags = {}, []
    for t in want:
        ent = doc["tickers"].get(t) or {}
        if ent.get("fields"): out[t] = dict(ent["fields"], liveAt=ent.get("t"))
        flags.extend(ent.get("flags") or [])
        if t not in _REFRESH: flags.append(f"{t}: treasury.json only (no primary-source parser)")
        elif not ent.get("fields") and not ent.get("flags"): flags.append(f"{t}: treasury.json {db.get('compiled')} (primary source unavailable)")
    return out, flags

def _pick(ov, c, key, jkey=None):
    jkey = jkey or key
    if ov.get(key) is not None: return ov[key]
    return c.get(jkey)

def compute(tickers=None, fresh=False):
    db = load(); by = {c["ticker"].upper(): c for c in db["companies"]}
    syms = [s.strip().upper() for s in (tickers or "").split(",") if s.strip()] or list(by)
    try: ov, flags = overlay(db, fresh, syms)
    except Exception as e:
        ov, flags = {}, [f"holdings refresh failed ({type(e).__name__}); using treasury.json"]
    stale = []
    q = heat.quotes(syms + ["BTC-USD"], fresh, stale)
    btc = (q.get("BTC-USD") or {}).get("regularMarketPrice")
    if not btc: raise ValueError("No live BTC price from Yahoo")
    curs = [(q.get(s) or {}).get("currency") for s in syms]
    for s in syms:
        c, o = by.get(s, {}), ov.get(s) or {}
        curs += [o.get("debt_cur") or c.get("debt_cur"), o.get("pref_cur") or c.get("pref_cur")]
    fx = heat.fx_to_usd(curs, fresh)
    rows = []
    for s in syms:
        c, d, o = by.get(s, {}), q.get(s) or {}, ov.get(s) or {}
        cur = d.get("currency") or "USD"; maj, k = heat.MINOR.get(cur, (cur, 1)); r = fx.get(maj)
        shares, ssrc = d.get("sharesOutstanding"), "Yahoo"
        if not shares and c.get("shares"): shares, ssrc = c["shares"], f"treasury.json ({c.get('shares_source')}, {c.get('shares_asof')})"
        if d.get("marketCap") and r: mcap, msrc = d["marketCap"] * r, "Yahoo market cap"
        elif shares and d.get("regularMarketPrice") and r: mcap, msrc = d["regularMarketPrice"] * k * shares * r, "price x shares"
        else: mcap, msrc = None, None
        held = _pick(o, c, "btc")
        debt_m, debt_cur = _pick(o, c, "debt_m"), (o.get("debt_cur") or c.get("debt_cur") or "USD")
        pref_m, pref_cur = _pick(o, c, "pref_m"), (o.get("pref_cur") or c.get("pref_cur") or "USD")
        fb = f"treasury.json {db.get('compiled')}"
        btc_src, btc_asof = (o.get("btcSrc"), o.get("btcAsOf")) if o.get("btc") is not None else (fb, db.get("compiled"))
        debt_src, debt_asof = (o.get("debtSrc"), o.get("debtAsOf")) if o.get("debt_m") is not None else (fb, db.get("compiled"))
        pref_src, pref_asof = (o.get("prefSrc"), o.get("prefAsOf")) if o.get("pref_m") is not None else (fb, db.get("compiled"))
        usd = lambda v, cu: v * 1e6 * fx[cu] if v is not None and cu in fx else None
        row = {"symbol": s, "name": c.get("name") or d.get("shortName") or s, "btc": held, "shares": shares, "sharesSrc": ssrc if shares else None, "mcapSrc": msrc, "via": d.get("via", "v7"),
               "mcapUSD": mcap, "debtUSD": usd(debt_m, debt_cur), "prefUSD": usd(pref_m, pref_cur),
               "source": btc_src, "note": c.get("debt_note"),
               "btcAsOf": btc_asof, "btcSrc": btc_src, "btcUrl": o.get("btcUrl"),
               "debtAsOf": debt_asof, "debtSrc": debt_src, "debtUrl": o.get("debtUrl"),
               "prefAsOf": pref_asof, "prefSrc": pref_src, "prefUrl": o.get("prefUrl")}
        for label, src in (("BTC", btc_src), ("debt", debt_src), ("preferred", pref_src)):
            if src and str(src).startswith("treasury.json"): flags.append(f"{s} {label}: {src}")
        nav = row["btc"] * btc if row["btc"] else None; row["navUSD"] = nav
        row["mnav"] = mcap / nav if mcap and nav else None
        row["amp"] = (row["debtUSD"] + row["prefUSD"]) / nav if nav and row["debtUSD"] is not None and row["prefUSD"] is not None else None
        row["btcPerShare"] = row["btc"] / row["shares"] if row["btc"] and row["shares"] else None
        miss = [n for n, v in (("BTC holdings", row["btc"]), ("debt", debt_m), ("preferred", pref_m), ("Yahoo market cap", mcap), ("shares", row["shares"])) if v is None]
        if not d: miss.append("Yahoo quote")
        if miss: flags.append(f"{s}: missing {', '.join(miss)}")
        rows.append(row)
    if stale: flags.append("served last cached quote (Yahoo unavailable): " + ", ".join(stale))
    return {"rows": rows, "flags": flags, "stale": stale, "via": sorted({r["via"] for r in rows}), "mcapSrc": sorted({r["mcapSrc"] for r in rows if r["mcapSrc"]}), "btcPrice": btc, "compiled": db.get("compiled"),
            "basis": "company filings and disclosures",
            "asOf": max([(q.get(s) or {}).get("regularMarketTime") or 0 for s in syms] or [0])}

def main(argv):
    db = load()
    fresh = "--refresh" in argv or "--fresh" in argv
    ov, flags = overlay(db, fresh=fresh)
    if "--refresh" in argv:
        _write_snapshot(_disk())
    print(f"basis: company filings and disclosures")
    for t, fields in ov.items():
        print(f"{t}: BTC {fields.get('btc')} as of {fields.get('btcAsOf')} ({fields.get('btcSrc')})")
        print(f"    debt {fields.get('debt_m')} {fields.get('debt_cur')} ({fields.get('debtSrc')})")
        print(f"    pref {fields.get('pref_m')} {fields.get('pref_cur')} ({fields.get('prefSrc')})")
    for fl in flags: print("FLAG", fl)
    missing = [c["ticker"] for c in db["companies"] if not (ov.get(c["ticker"].upper()) or {}).get("btc")]
    return 1 if missing else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
