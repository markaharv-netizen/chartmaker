"""Treasury tab: BitcoinTreasuries.net top 100 public companies by BTC held.

Holdings, rank, ticker and the as-of date come from the committed snapshot
btctop100.json. Refresh that file with scripts/refresh_btctop100.py.
USD value is BTC times a live Yahoo BTC-USD quote when one is available.
"""
import json, os
import heat

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "btctop100.json")
SUPPLY = 21_000_000

def load():
    with open(PATH) as f:
        return json.load(f)

def compute(fresh=False):
    db = load()
    companies = db.get("companies") or []
    if len(companies) != 100:
        raise ValueError(f"btctop100.json has {len(companies)} companies; expected 100")
    price, px_at, stale = None, None, []
    try:
        q = heat.quotes(["BTC-USD"], fresh, stale)
        d = q.get("BTC-USD") or {}
        price, px_at = d.get("regularMarketPrice"), d.get("regularMarketTime")
    except Exception:
        price = None
    supply = db.get("supply") or SUPPLY
    if supply != SUPPLY:
        raise ValueError("btctop100.json supply is not 21,000,000")
    rows = []
    for c in companies:
        btc = c.get("btc")
        if not isinstance(btc, (int, float)) or isinstance(btc, bool) or btc <= 0:
            raise ValueError("bad BTC holding for " + str(c.get("ticker") or c.get("name")))
        rows.append({
            "rank": c.get("rank"),
            "marker": c.get("marker"),
            "name": c.get("name"),
            "ticker": c.get("ticker"),
            "btc": btc,
            "usd": btc * price if price else None,
            "pctSupply": btc / supply * 100,
            "note": c.get("note") or None,
        })
    flags = []
    if not price:
        flags.append("no BTC-USD quote, so USD value is blank")
    elif stale:
        flags.append("BTC price is a cached Yahoo quote (live quote unavailable)")
    return {
        "metric": "btctop100",
        "label": "BTCTC top 100",
        "rows": rows,
        "btcPrice": price,
        "btcAsOf": px_at,
        "priceStale": bool(price) and bool(stale),
        "asOf": db.get("asOf"),
        "fetchedAt": db.get("fetchedAt"),
        "source": db.get("source"),
        "sourceName": db.get("sourceName") or "BitcoinTreasuries.net",
        "supply": supply,
        "flags": flags,
    }
