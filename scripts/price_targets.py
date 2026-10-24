#!/usr/bin/env python3
"""Build Chartmaker's Price targets snapshot. Runs where the sheet can be read, not on Render.

Render has no Google credentials. The Price targets tab reads static/price_targets.json only.

Sheet: "DCF price target summary"
  https://docs.google.com/spreadsheets/d/10rRnxxpVrv5A2kUWrxiGSysG--S_wJmmBvckjtprnj0
  tab Sheet1, range A1:P11. Cells are formatted text ($ and %), which this script parses.

Usage:
  python3 scripts/price_targets.py                 # link CSV export (401 if the sheet is private)
  python3 scripts/price_targets.py --csv file.csv  # Sheet1 export saved locally
  python3 scripts/price_targets.py --seed          # email-image figures, when the sheet cannot be read
  python3 scripts/price_targets.py --self-test

The scenario row "SPCX – Elon case (scenario)" is skipped. why is not a sheet column:
existing why text in the JSON is kept, and a ticker with none yet gets the short line in WHY.
subtitleNote, ptChangeNote, and ratingNote already in the JSON are kept too.
"""
import argparse, csv, io, json, os, re, sys, datetime, urllib.request, urllib.error

SHEET_ID = "10rRnxxpVrv5A2kUWrxiGSysG--S_wJmmBvckjtprnj0"
SHEET_NAME = "DCF price target summary"
RANGE = "A1:P11"
TAB = "Sheet1"
EXPORTS = [
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0",
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={TAB}&range=A1:P11",
]
WHY = {
    "MSTR": "Bets priced in; at fair value",
    "META": "Bets priced in; near fair value",
    "MU": "HBM upside mostly priced in",
    "NVDA": "Just inside HOLD band",
    "SPCX": "Even with AI bets, price high",
    "ASTS": "Needs full constellation",
    "CRWV": "Debt still exceeds core EV",
    "RKLB": "Far above bet-adjusted value",
    "TSLA": "Far above bet-adjusted value",
}
DEFAULTS = {
    "title": "Steve's DCF Base Case vs Street Consensus vs Price",
    "subtitleNote": "Base now includes probability-weighted future bets",
    "ptChangeNote": "PT change vs Oct 21 update; increases mainly from adding probability-weighted future bets and Q3 refreshes.",
    "ratingNote": "Rating: BUY > +15% upside to Base, SELL < -15%, otherwise HOLD. Not investment advice.",
}
NOTE = (
    "Offline snapshot. Render does not call Google. Regenerate with scripts/price_targets.py. "
    "Dollar fields are numbers. Percent fields are fractions (0.051 = 5.1%). "
    "why is editable copy and is kept when the snapshot is regenerated. "
    "The SPCX Elon-case scenario row is omitted."
)

def parse_money(s):
    """'$1,561.00', '-$1.79', '+$54.31', '' -> float or None."""
    if s is None: return None
    t = str(s).strip()
    if t == "" or t.lower() in ("—", "-", "n/a", "na"): return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.replace("$", "").replace(",", "").replace(" ", "").strip("()")
    if t.startswith("+"): t = t[1:]
    if t.startswith("-"):
        neg = True; t = t[1:]
    if t == "": return None
    v = float(t)
    return -v if neg else v

def parse_pct(s):
    """'-19.4%' or '55.9%' -> fraction. Blank -> None."""
    if s is None: return None
    t = str(s).strip().replace(",", "").replace(" ", "")
    if t == "" or t.lower() in ("—", "-", "n/a", "na"): return None
    if t.endswith("%"): t = t[:-1]
    return float(t) / 100.0

def parse_change(s):
    """'+$54.31 (+73.3%)' or '+$23.40 (from $0)' -> (dollars, fraction or None, from_zero)."""
    raw = str(s or "").strip()
    from_zero = bool(re.search(r"from\s+\$0(?:\.0+)?\b", raw, re.I))
    md = re.search(r"[+-]?\$[\d,]+(?:\.\d+)?", raw)
    dollars = parse_money(md.group(0)) if md else None
    mp = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", raw)
    pct = float(mp.group(1)) / 100.0 if mp else None
    return dollars, pct, from_zero

def parse_prev(detail):
    m = re.search(r"Prev(?:ious)? Base\s+\$([0-9,]+(?:\.\d+)?)", str(detail or ""), re.I)
    return parse_money(m.group(1)) if m else None

def parse_int(s):
    t = str(s or "").strip().replace(",", "")
    if not t: return None
    return int(float(t))

def parse_rating(s):
    t = str(s or "").strip().upper()
    for k in ("BUY", "HOLD", "SELL"):
        if t.startswith(k): return k
    return t or None

def is_scenario(ticker):
    t = str(ticker or "").lower().replace("–", "-").replace("—", "-").replace("−", "-")
    return "elon case" in t

def headers_ok(headers):
    h = [str(x or "").strip().lower() for x in headers]
    if len(h) < 16: return False
    return all((
        h[0] == "ticker",
        "base" in h[1],
        h[2].startswith("bear"),
        h[3].startswith("bull"),
        "rating" in h[4],
        "consensus" in h[5] and "vs" not in h[5],
        "analyst" in h[6],
        "source" in h[7] or "date" in h[7],
        h[8] == "price" or h[8].startswith("current price"),
        "as-of" in h[9] or "as of" in h[9],
        "base vs price" in h[10],
        "consensus vs price" in h[11],
        "base vs consensus" in h[12],
        h[13].startswith("note"),
        "change" in h[14],
        "previous" in h[15],
    ))

def q2(v):
    return None if v is None else float(f"{v:.2f}")

def q6(v):
    return None if v is None else float(f"{v:.6f}")

def consensus_label(sources):
    labs = []
    for s in sources:
        s = " ".join(str(s or "").split())
        if not s: continue
        if "stockanalysis" in s.lower(): name = "StockAnalysis"
        else: name = s.split(",")[0].split("(")[0].strip() or "consensus"
        m = re.search(r"fetched\s+([A-Z][a-z]{2,8}\s+\d{1,2})", s)
        lab = f"mean analyst target ({name}{', ' + m.group(1) if m else ''})"
        if lab not in labs: labs.append(lab)
    return " · ".join(labs) if labs else "mean analyst target"

def price_label(asofs):
    labs = []
    for s in asofs:
        raw = " ".join(str(s or "").split())
        m = re.search(r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})\s+close", raw)
        lab = (m.group(1) + " close") if m else raw
        if lab and lab not in labs: labs.append(lab)
    return labs[0] if len(labs) == 1 else "; ".join(labs)

def parse_csv(text):
    """Return (rows, skipped_tickers). rows are in sheet order, scenario row removed."""
    rows_in = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows_in: sys.exit("CSV is empty")
    headers, body = rows_in[0], rows_in[1:]
    if not headers_ok(headers):
        sys.exit("CSV header is not Sheet1 A:P of the DCF price target summary (got: " + " | ".join(headers) + ")")
    out, skipped = [], []
    for i, raw in enumerate(body, start=2):
        if not any(str(c).strip() for c in raw): continue
        raw = list(raw) + [""] * (16 - len(raw))
        ticker = str(raw[0] or "").strip()
        if not ticker: continue
        if is_scenario(ticker):
            skipped.append(ticker); continue
        base, price = parse_money(raw[1]), parse_money(raw[8])
        consensus = parse_money(raw[5])
        if base is None or price is None or consensus is None:
            sys.exit(f"row {i} {ticker}: base, consensus, and price are required")
        chg, chg_pct, from_zero = parse_change(raw[14])
        prev = parse_prev(raw[15])
        if prev is None and from_zero: prev = 0.0
        rating = parse_rating(raw[4])
        if rating not in ("BUY", "HOLD", "SELL"):
            sys.exit(f"row {i} {ticker}: rating must be BUY, HOLD, or SELL (got {raw[4]!r})")
        out.append({
            "ticker": ticker,
            "base": q2(base),
            "bear": q2(parse_money(raw[2])),
            "bull": q2(parse_money(raw[3])),
            "rating": rating,
            "consensus": q2(consensus),
            "analysts": parse_int(raw[6]),
            "consensusSource": " ".join(str(raw[7] or "").split()),
            "price": q2(price),
            "priceAsOf": " ".join(str(raw[9] or "").split()),
            "baseVsPrice": q6(parse_pct(raw[10])),
            "consensusVsPrice": q6(parse_pct(raw[11])),
            "baseVsConsensus": q6(parse_pct(raw[12])),
            "notes": " ".join(str(raw[13] or "").split()),
            "baseChange": q2(chg),
            "baseChangePct": q6(chg_pct),
            "fromZero": bool(from_zero or prev == 0),
            "previousBase": q2(prev),
            "previousDetail": " ".join(str(raw[15] or "").split()),
        })
    seen = set()
    for r in out:
        if r["ticker"] in seen: sys.exit("duplicate ticker " + r["ticker"])
        seen.add(r["ticker"])
    if not out: sys.exit("no ticker rows after skipping the scenario row")
    return out, skipped

def apply_why(rows, old_rows):
    kept = {}
    for r in old_rows or []:
        t, w = str(r.get("ticker") or "").strip(), str(r.get("why") or "").strip()
        if t and w: kept[t] = w
    missing = []
    for r in rows:
        if r["ticker"] in kept: r["why"] = kept[r["ticker"]]
        elif r["ticker"] in WHY: r["why"] = WHY[r["ticker"]]
        else:
            r["why"] = ""; missing.append(r["ticker"])
    return missing

def editorial(old):
    out = {}
    for k, v in DEFAULTS.items():
        prev = old.get(k) if isinstance(old, dict) else None
        out[k] = prev.strip() if isinstance(prev, str) and prev.strip() else v
    return out

def build_doc(rows, old, seeded=False):
    ed = editorial(old if isinstance(old, dict) else {})
    missing = apply_why(rows, (old or {}).get("rows") if isinstance(old, dict) else None)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "generated": now,
        "seeded": bool(seeded),
        "title": ed["title"],
        "subtitleNote": ed["subtitleNote"],
        "ptChangeNote": ed["ptChangeNote"],
        "ratingNote": ed["ratingNote"],
        "ratingRule": {"buyAbove": 0.15, "sellBelow": -0.15},
        "consensusLabel": consensus_label([r.get("consensusSource") for r in rows]),
        "priceLabel": price_label([r.get("priceAsOf") for r in rows]),
        "source": {
            "name": SHEET_NAME,
            "spreadsheetId": SHEET_ID,
            "sheet": TAB,
            "range": RANGE,
            "url": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
        },
        "note": NOTE,
        "rows": rows,
    }
    if seeded:
        doc["consensusLabel"] = "mean analyst target (StockAnalysis, Oct 21)"
        doc["priceLabel"] = "Oct 21, 2026 close"
        doc["note"] = NOTE + " This file was seeded from the Oct 21 email table because the sheet could not be read."
    return doc, missing

def seed_rows():
    """Figures from the Oct 21 email table. PT change percents are the email's, not a later sheet scan."""
    raw = [
        ("MSTR", 153.35, 0.051, False, 239.27, 153.48, -0.001, 0.559, "HOLD"),
        ("META", 687.36, 0.085, False, 814.51, 697.52, -0.015, 0.168, "HOLD"),
        ("MU", 860.87, 0.021, False, 1561.00, 913.67, -0.058, 0.708, "HOLD"),
        ("NVDA", 189.36, 0.042, False, 328.72, 221.97, -0.147, 0.481, "HOLD"),
        ("SPCX", 128.42, 0.733, False, 222.71, 159.31, -0.194, 0.398, "SELL"),
        ("ASTS", 24.98, 2.304, False, 74.28, 40.90, -0.389, 0.816, "SELL"),
        ("CRWV", 23.40, None, True, 140.58, 61.82, -0.621, 1.274, "SELL"),
        ("RKLB", 15.47, 0.269, False, 102.26, 53.67, -0.712, 0.905, "SELL"),
        ("TSLA", 67.89, 1.493, False, 397.83, 386.47, -0.824, 0.029, "SELL"),
    ]
    rows = []
    for t, base, pct, zero, cons, px, bup, sup, rating in raw:
        prev = 0.0 if zero else q2(base / (1 + pct))
        rows.append({
            "ticker": t, "base": q2(base), "bear": None, "bull": None, "rating": rating,
            "consensus": q2(cons), "analysts": None,
            "consensusSource": "StockAnalysis (S&P Global), fetched Oct 21, 2026",
            "price": q2(px), "priceAsOf": "Oct 21, 2026 close, 4:00 PM ET (Yahoo Finance)",
            "baseVsPrice": q6(bup), "consensusVsPrice": q6(sup), "baseVsConsensus": None,
            "notes": "", "baseChange": q2(base if zero else base - prev),
            "baseChangePct": None if zero else q6(pct), "fromZero": zero,
            "previousBase": q2(prev), "previousDetail": "Seeded from the Oct 21 email table.",
        })
    return rows

def fetch_csv():
    last = "no response"
    for url in EXPORTS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Chartmaker price-targets"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                ctype = (r.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} for {url}"; continue
        except Exception as e:
            last = f"{type(e).__name__}: {e}"; continue
        head = data[:400].lstrip().lower()
        if "text/html" in ctype or head.startswith(b"<") or b"<html" in head:
            last = "export returned HTML, not CSV (sheet is probably private)"
            continue
        text = data.decode("utf-8-sig")
        if not text.splitlines() or "ticker" not in text.splitlines()[0].lower():
            last = "export did not start with a Ticker header"; continue
        return text
    sys.exit(
        "Could not read the Google Sheet (" + last + "). The workbook is not public.\n"
        "Export Sheet1 as CSV and run: python3 scripts/price_targets.py --csv file.csv\n"
        "Or write the email-image seed: python3 scripts/price_targets.py --seed"
    )

def load_old(path):
    try: return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError): return {}

def write_doc(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)

def dest_for(repo):
    return os.path.join(repo, "static", "price_targets.json")

def self_test():
    sample = """Ticker,My Base value/share,Bear,Bull,Rating (Base vs price),Street consensus PT (mean),# analysts,Consensus source / date,Current price,Price as-of (ET),Base vs price %,Consensus vs price %,Base vs consensus %,Notes,Base change vs previous update,Previous update detail
MSTR,"$1,153.35",$89.76,$266.37,HOLD,"$1,239.27",16,"StockAnalysis (S&P Global), fetched Oct 21, 2026",$153.48,"Oct 21, 2026 close, 4:00 PM ET (Yahoo Finance)",-0.1%,55.9%,-35.9%,note,+$8.55 (+5.9%),"Prev Base $144.80 -> $153.35 at Oct 22, 2026"
SPCX – Elon case (scenario),$272.08,,,BUY (scenario),$222.71,38,"StockAnalysis (S&P Global), fetched Oct 21, 2026",$159.31,"Oct 21, 2026 close, 4:00 PM ET (Yahoo Finance)",70.8%,39.8%,22.2%,scenario,$0.00 (no change),"Scenario value unchanged"
CRWV,$23.40,$0.00,$163.77,SELL,$140.58,41,"StockAnalysis (S&P Global), fetched Oct 21, 2026",$61.82,"Oct 21, 2026 close, 4:00 PM ET (Yahoo Finance)",-62.1%,127.4%,-83.4%,floors at $0,+$23.40 (from $0),"Prev Base $0.00 -> $23.40 at Oct 22, 2026"
SPCX,$128.42,-$1.79,$291.02,SELL,$222.71,38,"StockAnalysis (S&P Global), fetched Oct 21, 2026",$159.31,"Oct 21, 2026 close, 4:00 PM ET (Yahoo Finance)",-19.4%,39.8%,-42.3%,v3,+$54.31 (+73.3%),"Prev Base $74.11 -> $128.42 at Oct 22, 2026"
"""
    # MSTR base in this fixture is $1,153.35 so the comma parser is on the path. Real sheet base is $153.35.
    rows, skipped = parse_csv(sample)
    assert skipped and "elon case" in skipped[0].lower(), skipped
    assert [r["ticker"] for r in rows] == ["MSTR", "CRWV", "SPCX"], rows
    m, c, s = rows
    assert m["base"] == 1153.35 and m["consensus"] == 1239.27, m
    assert m["baseChange"] == 8.55 and abs(m["baseChangePct"] - 0.059) < 1e-9, m
    assert m["previousBase"] == 144.80 and m["fromZero"] is False, m
    assert m["bear"] == 89.76 and m["rating"] == "HOLD" and m["analysts"] == 16, m
    assert c["fromZero"] is True and c["previousBase"] == 0 and c["baseChangePct"] is None, c
    assert c["baseChange"] == 23.40 and c["base"] == 23.40, c
    assert s["bear"] == -1.79 and abs(s["baseVsPrice"] + 0.194) < 1e-9, s
    assert "Elon" not in "".join(r["ticker"] for r in rows)
    doc, missing = build_doc(rows, {"rows": [{"ticker": "MSTR", "why": "custom why"}], "ptChangeNote": "kept note"})
    assert doc["rows"][0]["why"] == "custom why", doc["rows"][0]
    assert doc["rows"][1]["why"] == WHY["CRWV"]
    assert doc["ptChangeNote"] == "kept note", doc["ptChangeNote"]
    assert missing == [], missing
    assert doc["priceLabel"] == "Oct 21, 2026 close", doc["priceLabel"]
    assert doc["consensusLabel"] == "mean analyst target (StockAnalysis, Oct 21)", doc["consensusLabel"]
    seeded, _ = build_doc(seed_rows(), {}, seeded=True)
    assert len(seeded["rows"]) == 9
    assert all(r["why"] for r in seeded["rows"])
    assert seeded["rows"][6]["ticker"] == "CRWV" and seeded["rows"][6]["fromZero"] is True
    assert not any(is_scenario(r["ticker"]) for r in seeded["rows"])
    print("self-test ok")

def main():
    ap = argparse.ArgumentParser(description="Build static/price_targets.json")
    ap.add_argument("--csv", help="Sheet1 CSV export (A1:P11). Used instead of the network export.")
    ap.add_argument("--seed", action="store_true", help="Write the email-image seed (no sheet).")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test(); return
    if a.csv and a.seed: sys.exit("pass only one of --csv and --seed")
    path = dest_for(a.repo)
    old = load_old(path)
    if a.seed:
        rows, skipped, seeded = seed_rows(), [], True
    else:
        text = open(a.csv, encoding="utf-8-sig").read() if a.csv else fetch_csv()
        rows, skipped = parse_csv(text)
        seeded = False
    doc, missing = build_doc(rows, old, seeded=seeded)
    write_doc(path, doc)
    print(f"wrote {path} ({len(rows)} tickers, seeded={seeded})")
    if skipped: print("skipped:", "; ".join(skipped))
    if missing: print("no why text for:", ", ".join(missing))
    for r in rows:
        chg = "new" if r["fromZero"] else ("" if r["baseChangePct"] is None else f"{r['baseChangePct']*100:+.1f}%")
        print(f"  {r['ticker']:5} base {r['base']:10.2f}  {chg:>8}  px {r['price']:10.2f}  {r['rating']:4}  {r['why']}")

if __name__ == "__main__":
    main()
