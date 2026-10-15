"""Treasury mode: mNAV, amplification, BTC/share for BTC treasury companies.
Holdings/debt/pref from editable treasury.json; market cap, price, shares from Yahoo; BTC price from Yahoo BTC-USD."""
import json, os
import heat
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "treasury.json")
def load():
    with open(PATH) as f: return json.load(f)
def compute(tickers=None, fresh=False):
    db = load(); by = {c["ticker"].upper(): c for c in db["companies"]}
    syms = [s.strip().upper() for s in (tickers or "").split(",") if s.strip()] or list(by)
    q = heat.quotes(syms + ["BTC-USD"], fresh)
    btc = (q.get("BTC-USD") or {}).get("regularMarketPrice")
    if not btc: raise ValueError("No live BTC price from Yahoo")
    fx = heat.fx_to_usd([(q.get(s) or {}).get("currency") for s in syms] + [c.get(k) for c in by.values() for k in ("debt_cur", "pref_cur")], fresh)
    rows, flags = [], []
    for s in syms:
        c, d = by.get(s, {}), q.get(s) or {}
        cur = d.get("currency") or "USD"; maj, k = heat.MINOR.get(cur, (cur, 1)); r = fx.get(maj)
        mcap = d["marketCap"] * r if d.get("marketCap") and r else None
        usd = lambda v, cu: v * 1e6 * fx[cu] if v is not None and cu in fx else None
        row = {"symbol": s, "name": c.get("name") or d.get("shortName") or s, "btc": c.get("btc"), "shares": d.get("sharesOutstanding"),
               "mcapUSD": mcap, "debtUSD": usd(c.get("debt_m"), c.get("debt_cur", "USD")), "prefUSD": usd(c.get("pref_m"), c.get("pref_cur", "USD")),
               "source": c.get("source"), "note": c.get("debt_note")}
        nav = row["btc"] * btc if row["btc"] else None; row["navUSD"] = nav
        row["mnav"] = mcap / nav if mcap and nav else None
        row["amp"] = (row["debtUSD"] + row["prefUSD"]) / nav if nav and row["debtUSD"] is not None and row["prefUSD"] is not None else None
        row["btcPerShare"] = row["btc"] / row["shares"] if row["btc"] and row["shares"] else None
        miss = [n for n, v in (("BTC holdings", row["btc"]), ("debt", c.get("debt_m")), ("preferred", c.get("pref_m")), ("Yahoo market cap", mcap), ("Yahoo shares", row["shares"])) if v is None]
        if not d: miss.append("Yahoo quote")
        if miss: flags.append(f"{s}: missing {', '.join(miss)}")
        rows.append(row)
    return {"rows": rows, "flags": flags, "btcPrice": btc, "compiled": db.get("compiled"),
            "asOf": max([(q.get(s) or {}).get("regularMarketTime") or 0 for s in syms] or [0])}
