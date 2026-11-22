#!/usr/bin/env python3
"""Write static/sectors.json from Yahoo Finance assetProfile.sector.

The heatmap universe is the weekly share-count list in shares.json (~165
tickers). Each sector is the string Yahoo's quoteSummary assetProfile
reports (Technology, Financial Services, Consumer Cyclical, ...). A ticker
Yahoo does not classify — an ETF, a preferred, a foreign listing with no
profile — is stored as Unclassified. Bitcoin-treasury names keep whatever
sector Yahoo reports for that company; they are not given a separate bucket.

There is no share-count refresh script in this repo. The weekly job commits
shares.json directly. After it adds a ticker, run:

  python3 scripts/refresh_sectors.py --missing

That looks up only tickers that are not already in sectors.json. A Yahoo
failure keeps the previous file, prints a warning, and exits 0 so the
share-count refresh is not failed by a sector lookup. Pass --strict to
exit 1 instead.

  python3 scripts/refresh_sectors.py            # refresh every ticker
  python3 scripts/refresh_sectors.py --missing  # new tickers only (fail soft)
  python3 scripts/refresh_sectors.py --check
  python3 scripts/refresh_sectors.py --self-test
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SHARES = os.path.join(ROOT, "shares.json")
OUT = os.path.join(ROOT, "static", "sectors.json")
UNCLASSIFIED = "Unclassified"
UA = {"User-Agent": "Mozilla/5.0"}
EMPTY = {"", "n/a", "na", "none", "null", "other", "unclassified", "-"}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def share_symbols(path=SHARES):
    doc = load_json(path)
    return sorted(k for k in (doc.get("shares") or {}) if isinstance(k, str) and k.strip())


def load_sectors(path=OUT):
    try:
        doc = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    raw = doc.get("sectors") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip():
            out[k.strip()] = v.strip()
    return out


def clean_sector(value):
    """Yahoo's sector string, or None when the profile has no sector."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or text.lower() in EMPTY:
        return None
    return text


def yahoo_sector(sym, opener, crumb):
    """Return (sector or None, error or None). None sector with no error means Yahoo has no sector."""
    q = urllib.parse.urlencode({"modules": "assetProfile", "crumb": crumb})
    url = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/" + urllib.parse.quote(sym, safe="") + "?" + q
    try:
        with opener.open(urllib.request.Request(url, headers=UA), timeout=20) as r:
            doc = json.load(r)
    except Exception as e:
        return None, str(e)[:180]
    try:
        result = (doc.get("quoteSummary") or {}).get("result") or []
        sector = ((result[0].get("assetProfile") or {}).get("sector") if result else None)
    except Exception as e:
        return None, str(e)[:180]
    err = ((doc.get("quoteSummary") or {}).get("error") or {})
    if not result and err:
        return None, str(err.get("description") or err.get("code") or "no profile")[:180]
    return clean_sector(sector), None


def fetch_sectors(symbols, workers=8):
    """{sym: sector or None} for answers, plus a list of (sym, error) failures.

    A symbol in the dict with value None was answered and has no sector.
    A symbol only in the error list was not answered.
    """
    import heat
    crumb = heat.crumb()
    opener = heat._op
    found, failed = {}, []

    def one(sym):
        last = "no attempt"
        for attempt in range(3):
            sector, err = yahoo_sector(sym, opener, crumb)
            if err is None:
                return sym, sector, None
            last = err
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
        return sym, None, last

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, s) for s in symbols]
        for fut in as_completed(futs):
            sym, sector, err = fut.result()
            if err:
                failed.append((sym, err))
            else:
                found[sym] = sector
    return found, failed


def merge(symbols, previous, found, failed_syms):
    """Combine a lookup with the previous map.

    found: answered symbols, value None when Yahoo has no sector.
    failed_syms: lookups that did not answer; the previous sector is kept.
    """
    failed = set(failed_syms)
    out = {}
    for sym in symbols:
        if sym in found:
            out[sym] = found[sym] or UNCLASSIFIED
        else:
            prev = previous.get(sym)
            if sym in failed and isinstance(prev, str) and prev.strip():
                out[sym] = prev.strip()
            elif isinstance(prev, str) and prev.strip() and sym not in failed:
                out[sym] = prev.strip()
            else:
                out[sym] = UNCLASSIFIED
    return out


def document(sectors, asof=None):
    return {
        "_note": "GICS-style sector from Yahoo Finance quoteSummary assetProfile.sector, for the heatmap tickers in shares.json. "
                 "Unclassified is the bucket for a ticker Yahoo does not classify (ETF, preferred, or no profile). "
                 "Bitcoin-treasury names use the sector Yahoo reports. Refreshed by scripts/refresh_sectors.py.",
        "asof": asof or time.strftime("%Y-%m-%d"),
        "source": "Yahoo Finance quoteSummary assetProfile.sector",
        "unclassified": UNCLASSIFIED,
        "sectors": {k: sectors[k] for k in sorted(sectors)},
    }


def write_doc(doc, path=OUT):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def counts(sectors):
    n = {}
    for v in sectors.values():
        n[v] = n.get(v, 0) + 1
    return sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))


def refresh(symbols, previous, found, failed, only_missing=False):
    """Return (sectors or None, warnings). None means leave the file alone."""
    warnings = []
    failed_syms = [s for s, _ in failed]
    for sym, err in failed:
        warnings.append(f"{sym}: {err}")
    if symbols and not found and failed:
        warnings.append("Yahoo sector lookup failed; sectors.json left unchanged")
        return None, warnings
    if only_missing:
        # A new ticker that Yahoo did not answer is left out so the next --missing retries it.
        # A new ticker Yahoo answered (including "no sector") is recorded.
        merged = dict(previous)
        for sym in symbols:
            if sym in previous:
                continue
            if sym in found:
                merged[sym] = found[sym] or UNCLASSIFIED
            else:
                warnings.append(f"{sym}: not written (lookup failed)")
        if merged == previous:
            return None, warnings
        return merged, warnings
    return merge(symbols, previous, found, failed_syms), warnings


def run(path_shares, path_out, only_missing, strict, fetch):
    symbols = share_symbols(path_shares)
    previous = load_sectors(path_out)
    targets = [s for s in symbols if not only_missing or s not in previous]
    warnings = []
    if not targets:
        print(f"sectors.json already covers all {len(symbols)} tickers")
        return 0
    try:
        found, failed = fetch(targets)
    except Exception as e:
        warnings.append(f"Yahoo sector lookup failed: {e}")
        found, failed = {}, [(s, str(e)[:180]) for s in targets]
    sectors, more = refresh(symbols if not only_missing else targets, previous, found, failed, only_missing=only_missing)
    warnings.extend(more)
    for line in warnings:
        print("warning:", line)
    if sectors is None:
        if strict and warnings:
            return 1
        return 0
    # Full refresh keeps every universe ticker. Missing-only keeps prior entries too.
    if not only_missing:
        for sym in symbols:
            sectors.setdefault(sym, UNCLASSIFIED)
        sectors = {s: sectors[s] for s in symbols}
    else:
        for sym, val in previous.items():
            sectors.setdefault(sym, val)
    write_doc(document(sectors), path_out)
    print(f"wrote {path_out} ({len(sectors)} tickers)")
    for name, n in counts(sectors):
        print(f"  {n:4d}  {name}")
    if strict and any(s for s, _ in failed):
        return 1
    return 0


def check(path_shares=SHARES, path_out=OUT):
    symbols = share_symbols(path_shares)
    sectors = load_sectors(path_out)
    missing = [s for s in symbols if s not in sectors]
    bad = [s for s in symbols if s in sectors and not clean_sector(sectors[s]) and sectors[s] != UNCLASSIFIED]
    if missing or bad:
        print("missing:", ", ".join(missing))
        print("bad:", ", ".join(bad))
        return 1
    print(f"ok {len(symbols)} tickers")
    for name, n in counts({s: sectors[s] for s in symbols}):
        print(f"  {n:4d}  {name}")
    return 0


def self_test():
    prev = {"AAPL": "Technology", "NEW": "Financial Services"}
    found = {"MSFT": "Technology", "ETF": None}
    failed = [("GONE", "429")]
    # full refresh: answered names update, empty becomes Unclassified, failure keeps previous when present
    full, warns = refresh(["AAPL", "MSFT", "ETF", "GONE", "ZZ"], {"AAPL": "Technology", "GONE": "Energy"}, found, failed, only_missing=False)
    assert full["AAPL"] == "Technology", full
    assert full["MSFT"] == "Technology", full
    assert full["ETF"] == UNCLASSIFIED, full
    assert full["GONE"] == "Energy", full
    assert full["ZZ"] == UNCLASSIFIED, full
    assert any("GONE" in w for w in warns)
    # total failure does not replace the file
    none, warns = refresh(["AAPL"], prev, {}, [("AAPL", "down")], only_missing=False)
    assert none is None, none
    assert any("unchanged" in w for w in warns)
    # missing-only writes an answered new ticker and skips a failed one
    added, warns = refresh(["NEW2", "NEW3"], {"AAPL": "Technology"}, {"NEW2": "Healthcare"}, [("NEW3", "timeout")], only_missing=True)
    assert added["AAPL"] == "Technology" and added["NEW2"] == "Healthcare", added
    assert "NEW3" not in added, added
    assert any("NEW3" in w for w in warns)
    # missing-only with nothing new leaves the file alone
    same, _ = refresh([], {"AAPL": "Technology"}, {}, [], only_missing=True)
    assert same is None
    # empty Yahoo sector on a new ticker is Unclassified, not dropped
    empty, _ = refresh(["ETF"], {}, {"ETF": None}, [], only_missing=True)
    assert empty["ETF"] == UNCLASSIFIED
    assert clean_sector("  Technology ") == "Technology"
    assert clean_sector("Other") is None
    assert clean_sector("") is None
    doc = document({"MSFT": "Technology", "AAPL": "Technology"})
    assert list(doc["sectors"]) == ["AAPL", "MSFT"]
    assert doc["unclassified"] == UNCLASSIFIED
    print("self-test ok")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Refresh static/sectors.json from Yahoo assetProfile.sector")
    p.add_argument("--missing", action="store_true", help="only look up tickers not already in sectors.json; fail soft")
    p.add_argument("--strict", action="store_true", help="exit 1 when a lookup fails")
    p.add_argument("--check", action="store_true", help="every shares.json ticker has a sector; no network")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.check:
        return check()
    # --missing is the weekly hook: never fail the share-count refresh unless --strict.
    return run(SHARES, OUT, args.missing, args.strict, fetch_sectors)


if __name__ == "__main__":
    sys.exit(main())
