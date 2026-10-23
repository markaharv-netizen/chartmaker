"""Corporate Bitcoin Treasury Holdings (BTC Top 100).

The chart reads the committed snapshot btctop100.json. It does not fetch filings on
page load. Once a day, tools/refresh_btctop100.py re-reads primary documents for the
largest names and rewrites the snapshot:

  * SEC EDGAR 8-K / 6-K / 10-Q / 10-K text (Strategy, Strive, MARA, and the other
    names in WATCH). Not XBRL-only: weekly totals are in the 8-K narrative.
  * Metaplanet: the latest English TSE timely-disclosure PDF linked from
    metaplanet.jp (TDnet / xj-storage).

bitcointreasuries.net is not a source. A parsed total is applied only when its as-of
date is newer than the snapshot and the new amount is within 0.5x–2x of the current
figure. Anything else is flagged and left for a person. The other ~85 names stay on
the curated seed (the "BTCTC Top 100" tab of Mark's sheet) until a parser exists.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import time
import urllib.request

UA = "Chartmaker mark.a.harv@gmail.com"
ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "btctop100.json")
CACHE = os.path.join(ROOT, "cache", "btctop100")
MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
DATE = rf"({MONTH} \d{{1,2}}, \d{{4}})"
# Names the daily job is allowed to rewrite from a primary document.
WATCH = {
    "MSTR": {"cik": 1050446, "parser": "mstr"},
    "ASST": {"cik": 1920406, "parser": "asst"},
    "MARA": {"cik": 1507605, "parser": "prose"},
    "TSLA": {"cik": 1318605, "parser": "prose"},
    "RIOT": {"cik": 1167419, "parser": "prose"},
    "CLSK": {"cik": 827876, "parser": "prose"},
    "XXI": {"cik": 2070457, "parser": "prose"},
    "DJT": {"cik": 1849635, "parser": "prose"},
    "XYZ": {"cik": 1512673, "parser": "prose"},
    "SPCX": {"cik": 1181412, "parser": "prose"},
    "ABTC": {"cik": 1755953, "parser": "prose"},
    "COIN": {"cik": 1679788, "parser": "prose"},
    "3350.T": {"parser": "metaplanet"},
}
# Parsed and reported, never written. Hut 8's 10-Q consolidates American Bitcoin;
# the snapshot stores Hut excluding ABTC so the two rows are not double-counted.
REVIEW = {
    "HUT": {"cik": 1964789, "parser": "prose", "why": "10-Q consolidates American Bitcoin; snapshot is Hut ex-ABTC"},
    "BLSH": {"cik": 1872195, "parser": "prose", "why": "snapshot is the treasury-only figure, not total BTC (inventory, receivables, ETFs)"},
}
FORMS_8K = {"8-K", "8-K/A", "6-K", "6-K/A"}
FORMS_Q = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
PROSE = [
    (rf"as of {DATE},?\s+(?:we|the company|the group|it)\s+held\s+(?:approximately\s+|about\s+|an aggregate of\s+|a total of\s+)?([\d,]+(?:\.\d+)?)\s+bitcoins?\b", "dn"),
    (rf"at {DATE},?\s+(?:we|the company|the group)\s+held\s+(?:approximately\s+|about\s+|a total of\s+)?([\d,]+(?:\.\d+)?)\s+bitcoins?\b", "dn"),
    (rf"bringing (?:its|the|our) total holdings to ([\d,]+(?:\.\d+)?)\s+btc as of {DATE}", "nd"),
    (rf"total bitcoin holdings to ([\d,]+(?:\.\d+)?)\s+btc as of {DATE}", "nd"),
    (rf"total bitcoin holdings \({DATE}\)\s+([\d,]+(?:\.\d+)?)\s+btc\b", "dn"),
    (rf"held\s+(?:approximately\s+|about\s+|a total of\s+)?([\d,]+(?:\.\d+)?)\s+bitcoins?\s+as of\s+{DATE}", "nd"),
    (rf"bitcoin holdings of\s+(?:approximately\s+)?([\d,]+(?:\.\d+)?)\b(?:\s+btc|\s+bitcoins?)?\s+as of\s+{DATE}", "nd"),
    (rf"aggregate (?:btc |bitcoin )?holdings of\s+(?:approximately\s+)?([\d,]+(?:\.\d+)?)\b(?:\s+btc|\s+bitcoins?)?\s+as of\s+{DATE}", "nd"),
]
REJECT = re.compile(r"\b(purchased|acquired|bought|sold|mined|loaned|pledged|production)\b", re.I)
HELD = re.compile(r"\b(held|holdings|holds)\b", re.I)


def num(s):
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    v = float(t)
    if abs(v - round(v)) < 1e-6:
        return int(round(v))
    return round(v, 4)


def parse_date(s):
    try:
        return dt.datetime.strptime(re.sub(r"\s+", " ", s).strip(), "%B %d, %Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|tr|h\d|li|td|th|table)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw).replace("\xa0", " ").replace("\u2009", " ")
    return re.sub(r"\s+", " ", raw).strip()


def pdf_unescape(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in "nrtbf":
                out.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}[n]); i += 2; continue
            if n in "()\\":
                out.append(n); i += 2; continue
            if n.isdigit():
                octal, i = n, i + 2
                for _ in range(2):
                    if i < len(s) and s[i].isdigit():
                        octal += s[i]; i += 1
                    else:
                        break
                out.append(chr(int(octal, 8))); continue
            out.append(n); i += 2; continue
        out.append(s[i]); i += 1
    return "".join(out)


def pdf_to_text(data):
    """Stdlib text extract: concatenate the literal strings inside Tj/TJ (kerning numbers are not spaces)."""
    import zlib
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            dec = zlib.decompress(m.group(1)).decode("latin1")
        except Exception:
            continue
        for arr in re.findall(r"\[(.*?)\]\s*TJ", dec, re.S):
            parts = re.findall(r"\((?:\\.|[^\\)])*\)", arr)
            chunks.append("".join(pdf_unescape(p[1:-1]) for p in parts))
        for p in re.findall(r"\((?:\\.|[^\\)])*\)\s*Tj", dec):
            chunks.append(pdf_unescape(p[1:p.rfind(")")]))
    return re.sub(r"[ \t]+", " ", "\n".join(c for c in chunks if c.strip()))


def _hit(btc, asof, evidence):
    if not btc or btc <= 0 or not asof:
        return None
    return {"btc": btc, "asof": asof, "evidence": re.sub(r"\s+", " ", evidence).strip()[:300]}


def parse_mstr(flat):
    """Strategy's weekly 8-K table: Aggregate BTC Holdings is the integer after the period's purchase stats."""
    best = None
    for m in re.finditer(r"Aggregate BTC Holdings", flat):
        dates = re.findall(DATE, flat[max(0, m.start() - 500):m.start()])
        post = flat[m.end():m.end() + 800]
        mm = re.search(r"(\d[\d,]*)\s+\$\s+[\d,.]+\s+\$\s+[\d,]+\s+([\d,]{4,})\s+\$", post)
        if not dates or not mm:
            continue
        holdings = num(mm.group(2))
        purchased = num(mm.group(1))
        if not holdings or holdings < 1000 or (purchased and holdings < purchased):
            continue
        asof = parse_date(dates[-1])
        hit = _hit(holdings, asof, f"Aggregate BTC Holdings {holdings:,} as of {dates[-1]} (purchased {purchased:,} in the period)" if purchased else f"Aggregate BTC Holdings {holdings:,} as of {dates[-1]}")
        if hit and (best is None or hit["asof"] >= best["asof"]):
            best = hit
    return best


def parse_asst(flat):
    """Strive's 8-K holdings table: 'Bitcoin held <prior> <current> <change>' under two As-of dates."""
    best = None
    for m in re.finditer(r"Bitcoin held\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", flat, re.I):
        prev, cur, delta = num(m.group(1)), num(m.group(2)), num(m.group(3))
        if None in (prev, cur, delta) or cur < 100 or abs((cur - prev) - delta) > 1:
            continue
        pairs = [p for p in re.finditer(rf"As of {DATE}\s+As of {DATE}", flat) if p.end() <= m.start() and m.start() - p.end() < 900]
        if not pairs:
            continue
        asof_txt = pairs[-1].group(2)
        hit = _hit(cur, parse_date(asof_txt), f"Bitcoin held {prev:,} to {cur:,} (change {delta:,}) as of {asof_txt}")
        if hit and (best is None or hit["asof"] >= best["asof"]):
            best = hit
    return best


def parse_prose(flat):
    """Dated 'held N bitcoin' sentences. Returns every hit; the caller drops purchases, sales, and mining."""
    out = []
    for pat, order in PROSE:
        for m in re.finditer(pat, flat, re.I):
            d, n = (m.group(1), m.group(2)) if order == "dn" else (m.group(2), m.group(1))
            nstart = m.start(2 if order == "dn" else 1)
            window = flat[max(0, nstart - 90):nstart]
            if REJECT.search(window) and not HELD.search(m.group(0)):
                continue
            hit = _hit(num(n), parse_date(d), m.group(0))
            if hit:
                out.append(hit)
    return out


def _dates_in(text):
    return [d for d in (parse_date(x) for x in re.findall(DATE, text)) if d]


def parse_units(flat):
    """10-Q phrasing the prose patterns miss: 'N units of Bitcoin' and a 'Bitcoin (Units) N' table row.
    Used only when the prose patterns find nothing. The as-of is the latest date in the preceding header."""
    out = []

    def add(btc, asof, evidence):
        hit = _hit(num(btc) if not isinstance(btc, (int, float)) else btc, asof, evidence)
        if hit:
            out.append(hit)

    for m in re.finditer(r"\bheld\s+([\d,]+(?:\.\d+)?)\s+units of bitcoins?\b", flat, re.I):
        dates = _dates_in(flat[max(0, m.start() - 220):m.start()])
        if dates:
            add(m.group(1), max(dates), m.group(0))
    for m in re.finditer(r"comprised of\s+([\d,]+(?:\.\d+)?)\s+units of bitcoins?\b", flat, re.I):
        dates = _dates_in(flat[max(0, m.start() - 280):m.start()])
        if dates:
            add(m.group(1), max(dates), m.group(0))
    for m in re.finditer(r"\bbitcoin(?:\s*\(units\))?\s+([\d,]+(?:\.\d+)?)\b", flat, re.I):
        prev = flat[max(0, m.start() - 400):m.start()]
        caption = prev[-260:]
        # A bare "Bitcoin 17,311" row is the investment table, not borrowed / collateral / customer crypto.
        if re.search(r"\(units\)", m.group(0), re.I):
            pass
        elif re.search(r"held for investment", caption, re.I):
            pass
        else:
            continue
        dates = _dates_in(prev)
        if dates:
            add(m.group(1), max(dates), m.group(0))
    return out


def pick(hits, old_btc):
    """Latest as-of wins. Two different totals on that date are ambiguous unless one is the figure already on the snapshot."""
    if not hits:
        return None
    latest = max(h["asof"] for h in hits)
    top = [h for h in hits if h["asof"] == latest]
    uniq = []
    for h in top:
        if not any(abs(h["btc"] - u["btc"]) < 0.51 for u in uniq):
            uniq.append(h)
    if len(uniq) == 1:
        return uniq[0]
    same = [u for u in uniq if old_btc is not None and abs(u["btc"] - old_btc) < 0.51]
    if len(same) == 1:
        same[0]["also"] = [u["btc"] for u in uniq if u is not same[0]]
        return same[0]
    return {"ambiguous": True, "asof": latest, "options": [u["btc"] for u in uniq], "evidence": uniq[0]["evidence"]}


def parse_document(text, kind, old_btc):
    flat = text if kind == "metaplanet" else html_to_text(text)
    if kind == "metaplanet":
        flat = re.sub(r"\s+", " ", flat)
    hits = []
    if kind == "mstr":
        h = parse_mstr(flat)
        if h:
            hits.append(h)
    elif kind == "asst":
        h = parse_asst(flat)
        if h:
            hits.append(h)
    if kind in ("mstr", "asst") and hits:
        return hits[0]
    hits.extend(parse_prose(flat))
    chosen = pick(hits, old_btc)
    if chosen:
        return chosen
    return pick(parse_units(flat), old_btc)


def decide(row, cand):
    """Pure update rule. cand is a parse result plus url/form/filed/source fields, or None."""
    base = {"ticker": row.get("ticker"), "btc": row.get("btc"), "asof": row.get("asof")}
    if not cand:
        return dict(base, action="none")
    if cand.get("ambiguous"):
        opts = ", ".join(f"{n:,}" if isinstance(n, int) else str(n) for n in cand.get("options") or [])
        return dict(base, action="flagged", check_detail=f"filing states more than one total on {cand.get('asof')}: {opts}; snapshot left unchanged")
    new, asof = cand.get("btc"), cand.get("asof")
    old, old_asof = row.get("btc"), row.get("asof") or ""
    if new is None or not asof:
        return dict(base, action="none")
    if asof < old_asof:
        return dict(base, action="older", check_detail=f"latest parsed total is {new:,} as of {asof}, older than snapshot {old_asof}")
    url = cand.get("url") or row.get("source_url")
    if asof == old_asof:
        if old is not None and abs(new - old) < 0.51:
            extra = ""
            if cand.get("also"):
                extra = " Filing also states " + ", ".join(f"{n:,}" if isinstance(n, int) else str(n) for n in cand["also"]) + "; snapshot figure is the one kept."
            return dict(base, action="confirmed", source_url=url, evidence=cand.get("evidence"), check_detail=("matches " + (cand.get("form") or cand.get("parser") or "filing") + " " + asof + "." + extra).strip())
        return dict(base, action="conflict", check_detail=f"filing says {new:,} on {asof}; snapshot says {old:,} on the same date; snapshot left unchanged")
    if old and not (0.5 * old <= new <= 2 * old):
        return dict(base, action="flagged", check_detail=f"newer filing says {new:,} as of {asof}, outside 0.5x–2x of {old:,}; not applied")
    change = new - old if isinstance(new, (int, float)) and isinstance(old, (int, float)) else None
    if isinstance(change, float) and abs(change - round(change)) < 1e-6:
        change = int(round(change))
    return dict(base, action="updated", btc=new, asof=asof, change=change, source_url=url,
                source=cand.get("source"), source_label=cand.get("source_label"), evidence=cand.get("evidence"),
                check_detail=f"updated from {old:,} as of {old_asof}")


def load():
    with open(PATH) as f:
        db = json.load(f)
    rows = [c for c in db.get("companies") or [] if isinstance(c.get("btc"), (int, float))]
    rows.sort(key=lambda c: (-float(c["btc"]), c.get("rank") or 10**9, c.get("ticker") or ""))
    for i, c in enumerate(rows, 1):
        c["rank"] = i
    out = dict(db)
    out["companies"] = rows
    out["count"] = len(rows)
    out["btc_total"] = int(round(sum(float(c["btc"]) for c in rows)))
    return out


def _write(db):
    rows = db["companies"]
    rows.sort(key=lambda c: (-float(c["btc"]), c.get("rank") or 10**9, c.get("ticker") or ""))
    for i, c in enumerate(rows, 1):
        c["rank"] = i
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, PATH)


# --- network (daily job only; the web page never calls this) ---

def _fetch(url, ttl=0, fresh=False):
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    path = os.path.join(CACHE, key)
    if not fresh and ttl and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity", "Accept": "*/*"})
    delay = 0.15
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            break
        except Exception as e:
            last = e
            time.sleep(delay)
            delay *= 2
    else:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        raise last
    with open(path, "wb") as f:
        f.write(raw)
    time.sleep(0.12)
    return raw


def _json(url, fresh=False):
    return json.loads(_fetch(url, ttl=0, fresh=fresh).decode("utf-8", "replace"))


def _recent(cik, forms, n, fresh):
    sub = _json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", fresh)
    rec = sub["filings"]["recent"]
    out = []
    for i, form in enumerate(rec["form"]):
        if form in forms:
            out.append({"form": form, "filed": rec["filingDate"][i], "acc": rec["accessionNumber"][i], "doc": rec["primaryDocument"][i]})
            if len(out) >= n:
                break
    return out


def _docs(cik, filing, fresh):
    acc = filing["acc"].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
    names = [filing["doc"]] if filing["doc"].lower().endswith((".htm", ".html")) else []
    if filing["form"] in FORMS_8K:
        try:
            idx = _json(f"{base}/index.json", fresh)
        except Exception:
            idx = {}
        items = (idx.get("directory") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        for it in items:
            name = it.get("name") or ""
            if re.search(r"(ex[-_]?99|exhibit[-_]?99)", name, re.I) and name.lower().endswith((".htm", ".html")) and name not in names:
                names.append(name)
    return [(name, f"{base}/{name}") for name in names[:3]]


def _annotate(hit, filing, url, parser):
    if not hit or hit.get("ambiguous"):
        if hit:
            hit.update(form=filing["form"], filed=filing["filed"], url=url, parser=parser)
        return hit
    if parser == "metaplanet":
        src, label = f"TSE timely disclosure ({filing['filed']})", "TSE disclosure"
    else:
        src, label = f"SEC Form {filing['form']} (filed {filing['filed']})", "SEC " + filing["form"].split("/")[0]
    hit.update(form=filing["form"], filed=filing["filed"], url=url, parser=parser, source=src, source_label=label)
    return hit


def check_sec(row, spec, fresh):
    cik, kind = spec["cik"], spec["parser"]
    filings = _recent(cik, FORMS_8K, 4, fresh) + _recent(cik, FORMS_Q, 1, fresh)
    seen, hits = set(), []
    for filing in filings:
        if filing["acc"] in seen:
            continue
        seen.add(filing["acc"])
        for name, url in _docs(cik, filing, fresh):
            try:
                raw = _fetch(url, ttl=12 * 3600, fresh=fresh).decode("utf-8", "replace")
            except Exception as e:
                hits.append({"error": f"{filing['form']} {filing['filed']} {name}: {e}"})
                continue
            hit = parse_document(raw, kind, row.get("btc"))
            if hit:
                hits.append(_annotate(hit, filing, url, kind))
    good = [h for h in hits if h.get("btc") or h.get("ambiguous")]
    if not good:
        err = next((h.get("error") for h in hits if h.get("error")), None)
        return None, err
    good.sort(key=lambda h: h.get("asof") or "", reverse=True)
    return good[0], None


def _meta_disclosures(fresh):
    raw = _fetch("https://metaplanet.jp/en/disclosures", ttl=0, fresh=fresh).decode("utf-8", "replace")
    text = raw.replace('\\"', '"').replace("\\/", "/")
    recs = re.findall(r'"date":"(\d{4}-\d{2}-\d{2})","title":"([^"]*)","filePath":"(https:[^"]+)","isEnglish":(true|false)', text)
    out = []
    for date, title, path, eng in recs:
        if eng != "true":
            continue
        t = title.lower()
        if ("bitcoin" in t or "btc" in t) and any(k in t for k in ("purchase", "holding", "acquisition")):
            out.append({"filed": date, "title": title, "url": path, "form": "TSE timely disclosure"})
    out.sort(key=lambda r: r["filed"], reverse=True)
    return out[:2], raw


def check_metaplanet(row, fresh):
    notices, page = _meta_disclosures(fresh)
    widget = None
    try:
        home = _fetch("https://metaplanet.jp/en", ttl=0, fresh=fresh).decode("utf-8", "replace")
        m = re.search(r"₿\s*([\d,]+)</span>\s*<span[^>]*>\s*BTC Holdings", home)
        if m:
            widget = num(m.group(1))
    except Exception:
        home = ""
    hits = []
    for n in notices:
        try:
            blob = _fetch(n["url"], ttl=12 * 3600, fresh=fresh)
        except Exception as e:
            hits.append({"error": str(e)})
            continue
        text = pdf_to_text(blob) if blob[:4] == b"%PDF" else blob.decode("utf-8", "replace")
        hit = parse_document(text, "metaplanet", row.get("btc"))
        if hit:
            hits.append(_annotate(hit, n, n["url"], "metaplanet"))
    good = [h for h in hits if h.get("btc") or h.get("ambiguous")]
    best = max(good, key=lambda h: h.get("asof") or "") if good else None
    note = None
    if widget is not None and row.get("btc") is not None and abs(widget - (best["btc"] if best and best.get("btc") else row["btc"])) >= 1:
        shown = best["btc"] if best and best.get("btc") else row["btc"]
        note = f"Metaplanet IR homepage shows {widget:,} BTC with no as-of date; dated disclosure figure {shown:,} kept"
    if best is None and not notices:
        note = (note + ". " if note else "") + "no recent English Bitcoin-purchase disclosure found on metaplanet.jp"
    return best, note


def _apply_row(row, decision, today):
    row["checked"] = today
    row["check"] = decision["action"]
    if decision.get("check_detail"):
        row["check_detail"] = decision["check_detail"]
    if decision["action"] == "confirmed":
        if decision.get("source_url"):
            row["source_url"] = decision["source_url"]
        if decision.get("evidence"):
            row["evidence"] = decision["evidence"]
    elif decision["action"] == "updated":
        if row.get("notes"):
            row["notes_prev"] = row["notes"]
        row["btc"] = decision["btc"]
        row["asof"] = decision["asof"]
        row["change"] = decision.get("change")
        if decision.get("source"):
            row["source"] = decision["source"]
        if decision.get("source_label"):
            row["source_label"] = decision["source_label"]
        if decision.get("source_url"):
            row["source_url"] = decision["source_url"]
        if decision.get("evidence"):
            row["evidence"] = decision["evidence"]
            row["notes"] = decision["evidence"]
        row["origin"] = "filing"
    return row


def refresh(fresh=False, write=True):
    with open(PATH) as f:
        db = json.load(f)
    by = {c["ticker"]: c for c in db["companies"]}
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    summary = {"confirmed": [], "updated": [], "older": [], "flagged": [], "conflict": [], "none": [], "review": [], "errors": []}
    for ticker, spec in {**WATCH, **REVIEW}.items():
        row = by.get(ticker)
        if not row:
            summary["errors"].append(f"{ticker}: not in snapshot")
            continue
        print(f"checking {ticker} ({spec['parser']})...", flush=True)
        try:
            if spec["parser"] == "metaplanet":
                cand, err = check_metaplanet(row, fresh)
            else:
                cand, err = check_sec(row, spec, fresh)
        except Exception as e:
            cand, err = None, str(e)
        if err and not (cand and (cand.get("btc") or cand.get("ambiguous"))):
            row["checked"] = today
            row["check"] = "error"
            row["check_detail"] = str(err)[:300]
            summary["errors"].append(f"{ticker}: {err}")
            continue
        decision = decide(row, cand)
        if ticker in REVIEW:
            decision = dict(decision, action="review", check_detail=(decision.get("check_detail") or decision["action"]) + f" — not applied ({spec['why']})")
            summary["review"].append(f"{ticker}: {decision['check_detail']}")
            row["checked"] = today
            row["check"] = "review"
            row["check_detail"] = decision["check_detail"]
            if cand and cand.get("url"):
                row["review_url"] = cand["url"]
            continue
        _apply_row(row, decision, today)
        bucket = decision["action"] if decision["action"] in summary else "none"
        detail = decision.get("check_detail") or ""
        summary[bucket].append(f"{ticker}: {row.get('btc')} as of {row.get('asof')}" + (f" — {detail}" if detail else ""))
    db["refreshed_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db["automated"] = sorted(WATCH)
    db["review_only"] = sorted(REVIEW)
    seeded = [c["ticker"] for c in db["companies"] if c["ticker"] not in WATCH]
    db["refresh_summary"] = {
        "confirmed": summary["confirmed"], "updated": summary["updated"], "older": summary["older"],
        "flagged": summary["flagged"], "conflict": summary["conflict"], "none": summary["none"],
        "review": summary["review"], "errors": summary["errors"], "seeded_count": len(seeded),
    }
    if write:
        _write(db)
    return db


def report(db):
    rows = sorted(db["companies"], key=lambda c: -float(c["btc"]))
    lines = [f"Corporate Bitcoin Treasury Holdings — snapshot refreshed {db.get('refreshed_at') or 'never'}"]
    lines.append(f"{len(rows)} companies, {int(round(sum(float(c['btc']) for c in rows))):,} BTC combined")
    lines.append("Top holdings:")
    for c in rows[:12]:
        ch = c.get("check") or "seed"
        lines.append(f"  {c['rank']:3} {c['ticker']:<10} {int(c['btc']):>10,}  as of {c.get('asof')}  [{ch}]  {c.get('source_label') or ''}")
    s = db.get("refresh_summary") or {}
    for key in ("updated", "confirmed", "flagged", "conflict", "review", "errors", "none", "older"):
        for item in s.get(key) or []:
            lines.append(f"{key}: {item}")
    lines.append(f"seeded (not auto-updated): {s.get('seeded_count', 'n/a')}")
    return "\n".join(lines)
