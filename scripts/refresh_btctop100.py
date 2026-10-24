#!/usr/bin/env python3
"""Rewrite btctop100.json from BitcoinTreasuries.net's public company list.

The homepage embeds a JSON snapshot (script#bt-query-snapshot) of
entities.listOverviews. This keeps the 100 public companies with the most
BTC, in that order, with the same rank markers the site paints (a company
can be listed and still have no ordinal rank).

USD value is not stored. The app multiplies BTC by a live BTC-USD quote.

Usage:
  python3 scripts/refresh_btctop100.py
  python3 scripts/refresh_btctop100.py --check   # validate the committed file, no network
"""
import argparse, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "btctop100.json")
PAGE = "https://bitcointreasuries.net/"
UA = "Chartmaker mark.a.harv@gmail.com"
SUPPLY = 21_000_000
TOP_N = 100


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", "replace"), r.headers


def snapshot(html):
    m = re.search(r'<script type="application/json" id="bt-query-snapshot">(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit("bitcointreasuries.net page has no bt-query-snapshot (layout changed)")
    data = json.loads(m.group(1))
    for q in data.get("queries") or []:
        if "entities.listOverviews" in q.get("hash", "") and isinstance(q.get("data"), list):
            return q["data"]
    raise SystemExit("bt-query-snapshot has no entities.listOverviews list")


def public_btc(entities):
    rows = []
    for e in entities:
        if e.get("type") != "PUBLIC_COMPANY":
            continue
        btc = None
        for h in e.get("holdings") or []:
            if h.get("asset") == "BTC" and h.get("balance"):
                btc = float(h["balance"])
        if not btc or btc <= 0:
            continue
        ticker = ((e.get("ticker") or {}).get("symbol") or "").strip()
        name = (e.get("name") or "").strip()
        if not name or not ticker:
            raise SystemExit(f"public company missing name or ticker: {e.get('uuid')}")
        rows.append({"uuid": e.get("uuid") or "", "name": name, "ticker": ticker, "btc": btc,
                     "country": ((e.get("country") or {}).get("alpha2") or "")})
    # The overview list is already ranked. A stable sort keeps tie order if that ever drifts.
    rows.sort(key=lambda r: -r["btc"])
    if len(rows) < TOP_N:
        raise SystemExit(f"only {len(rows)} public companies with BTC; expected at least {TOP_N}")
    return rows[:TOP_N]


class _RankParser(HTMLParser):
    """Companies the public table marks with 'no ordinal rank', and any explanation text nearby."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.uuids = []
        self._pending = False
        self._left = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("aria-label") == "Why this company has no ordinal rank":
            self._pending = True
            self._left = 12
        if self._pending and d.get("data-entity-card"):
            self.uuids.append(d["data-entity-card"])
            self._pending = False
        elif self._pending:
            self._left -= 1
            if self._left <= 0:
                self._pending = False


def unranked_uuids(html):
    p = _RankParser()
    try:
        p.feed(html)
    except Exception:
        pass
    # The homepage renders the same row twice (compact list and full table).
    out, seen = [], set()
    for u in p.uuids:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def explanations_from_js(html, headers):
    """Best-effort: the site hardcodes {uuid, marker, explanation} in a preloaded chunk."""
    pat = re.compile(r"make\(`([0-9a-f-]{36})`\).*?marker:`([^`]*)`,explanation:`([^`]*)`", re.S)
    found = {}
    for uuid, marker, note in pat.findall(html):
        found[uuid] = {"marker": marker or "*", "note": note.strip()}
    want = set(unranked_uuids(html))
    if want <= set(found):
        return found
    link = headers.get("Link") or headers.get("link") or ""
    paths = []
    for raw in re.findall(r"[./_a-zA-Z0-9-]*_app/immutable/chunks/[^>;\s]+\.js", link):
        paths.append("/" + raw.lstrip("./"))
    def pull(path):
        try:
            body, _ = fetch("https://bitcointreasuries.net" + path)
        except Exception:
            return ""
        return body if "explanation:`" in body else ""
    # The note lives in one small chunk. Scan the preloaded list in batches and stop when it shows up.
    for i in range(0, len(paths), 24):
        with ThreadPoolExecutor(12) as ex:
            bodies = list(ex.map(pull, paths[i:i + 24]))
        for body in bodies:
            if not body:
                continue
            for uuid, marker, note in pat.findall(body):
                found[uuid] = {"marker": marker or "*", "note": note.strip()}
        if want <= set(found):
            break
    return found


def assign_ranks(rows, unranked, notes):
    n = 0
    for r in rows:
        meta = notes.get(r["uuid"])
        if r["uuid"] in unranked or meta:
            r["rank"] = None
            r["marker"] = (meta or {}).get("marker") or "*"
            r["note"] = (meta or {}).get("note") or "Shown on BitcoinTreasuries.net without an ordinal rank."
        else:
            n += 1
            r["rank"] = n
            r["marker"] = None
            r["note"] = None
    return rows


def build():
    html, headers = fetch(PAGE)
    rows = public_btc(snapshot(html))
    unranked = [u for u in unranked_uuids(html) if any(r["uuid"] == u for r in rows)]
    notes = explanations_from_js(html, headers)
    assign_ranks(rows, set(unranked), notes)
    now = datetime.now(timezone.utc)
    doc = {
        "source": PAGE,
        "sourceName": "BitcoinTreasuries.net",
        "description": "Top 100 public companies by bitcoin held, as published by BitcoinTreasuries.net. USD value is BTC times a live BTC price and is not part of this file.",
        "asOf": now.strftime("%Y-%m-%d"),
        "fetchedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "supply": SUPPLY,
        "companies": [{k: r[k] for k in ("rank", "marker", "name", "ticker", "btc", "country", "uuid", "note")} for r in rows],
    }
    validate(doc)
    return doc


def validate(doc):
    cos = doc.get("companies")
    if not isinstance(cos, list) or len(cos) != TOP_N:
        raise SystemExit(f"expected {TOP_N} companies, got {len(cos) if isinstance(cos, list) else type(cos).__name__}")
    if doc.get("supply") != SUPPLY:
        raise SystemExit("supply must be 21000000")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc.get("asOf") or ""):
        raise SystemExit("asOf must be YYYY-MM-DD")
    prev = None
    ranks = []
    for i, c in enumerate(cos):
        for k in ("name", "ticker", "btc"):
            if c.get(k) in (None, ""):
                raise SystemExit(f"row {i} missing {k}")
        if not isinstance(c["btc"], (int, float)) or c["btc"] <= 0:
            raise SystemExit(f"row {i} bad btc")
        if prev is not None and c["btc"] > prev + 1e-9:
            raise SystemExit(f"row {i} is not in descending BTC order")
        prev = c["btc"]
        if c.get("rank") is None:
            if c.get("marker") != "*":
                raise SystemExit(f"row {i} unranked without marker *")
        else:
            if not isinstance(c["rank"], int) or c["rank"] < 1:
                raise SystemExit(f"row {i} bad rank")
            ranks.append(c["rank"])
    if ranks != list(range(1, len(ranks) + 1)):
        raise SystemExit(f"ordinal ranks are not 1..{len(ranks)} in order")
    return doc


def main():
    ap = argparse.ArgumentParser(description="Refresh the BitcoinTreasuries.net top 100 snapshot")
    ap.add_argument("--check", action="store_true", help="validate btctop100.json and do not download")
    args = ap.parse_args()
    if args.check:
        with open(OUT) as f:
            doc = json.load(f)
        validate(doc)
        print(f"ok {len(doc['companies'])} companies as of {doc['asOf']}")
        return
    doc = build()
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    btc = sum(c["btc"] for c in doc["companies"])
    star = [c["ticker"] for c in doc["companies"] if c.get("rank") is None]
    print(f"wrote {OUT}")
    print(f"as of {doc['asOf']}: {len(doc['companies'])} companies, {btc:,.0f} BTC, unranked: {', '.join(star) or 'none'}")
    for c in doc["companies"][:8]:
        rk = c["marker"] if c.get("rank") is None else c["rank"]
        print(f"  {rk:>3}  {c['ticker']:<10} {c['btc']:>12,.2f}  {c['name']}")


if __name__ == "__main__":
    sys.exit(main())
