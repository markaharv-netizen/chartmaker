"""SEC EDGAR XBRL financials (stdlib only). Primary data from companyfacts; nothing invented."""
import json, os, time, urllib.request, datetime as dt
UA = "Chartmaker mark.a.harv@gmail.com"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"}
D = dt.date.fromisoformat

def _get(url, fname):
    os.makedirs(CACHE_DIR, exist_ok=True); p = os.path.join(CACHE_DIR, fname)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        with open(p, "rb") as f: return json.load(f)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: raw = r.read()
    except Exception:
        if os.path.exists(p):  # stale cache beats nothing
            with open(p, "rb") as f: return json.load(f)
        raise
    with open(p, "wb") as f: f.write(raw)
    return json.loads(raw)

def cik_of(ticker):
    t = ticker.upper().replace(".", "-")
    for v in _get("https://www.sec.gov/files/company_tickers.json", "company_tickers.json").values():
        if v["ticker"].upper() == t: return v["cik_str"], v["title"]
    raise ValueError(f"{ticker}: not found in SEC ticker list (ETFs, crypto and many non-US tickers are not SEC filers)")

# metric -> (kind, unit, [tags in priority order]); kind: flow (additive duration), inst (balance sheet), avg (non-additive duration)
TAGS = {
 "revenue": ("flow", "USD", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet", "RevenuesNetOfInterestExpense"]),
 "gross_profit": ("flow", "USD", ["GrossProfit"]),
 "operating_income": ("flow", "USD", ["OperatingIncomeLoss"]),
 "net_income": ("flow", "USD", ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"]),
 "eps_diluted": ("flow", "USD/shares", ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]),
 "ocf": ("flow", "USD", ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
 "capex": ("flow", "USD", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherPropertyPlantAndEquipment"]),
 "rnd": ("flow", "USD", ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]),
 "cash": ("inst", "USD", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"]),
 "debt_lt": ("inst", "USD", ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "DebtInstrumentCarryingAmount"]),
 "debt_ltnc": ("inst", "USD", ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "ConvertibleNotesPayableNoncurrent"]),
 "debt_ltc": ("inst", "USD", ["LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]),
 "debt_st": ("inst", "USD", ["ShortTermBorrowings", "CommercialPaper"]),
 "shares_out": ("inst", "shares", ["CommonStockSharesOutstanding"]),
 "shares_wavg": ("avg", "shares", ["WeightedAverageNumberOfSharesOutstandingBasic"]),
}
METRICS = {  # id: (label, unit, note)
 "revenue": ("Revenue", "$", ""), "gross_profit": ("Gross profit", "$", ""), "operating_income": ("Operating income", "$", ""),
 "net_income": ("Net income", "$", ""), "eps_diluted": ("EPS (diluted)", "$/sh", "TTM EPS = sum of 4 quarterly EPS (approx.)"),
 "ocf": ("Operating cash flow", "$", ""), "capex": ("Capex", "$", "Purchases of property & equipment"),
 "fcf": ("Free cash flow", "$", "FCF = operating cash flow − capex"), "cash": ("Cash", "$", "Cash & equivalents"),
 "total_debt": ("Total debt", "$", "Long-term debt incl. current portion + short-term borrowings/commercial paper where tagged; excludes leases unless the company tags them together"),
 "shares": ("Shares outstanding", "sh", "Balance-sheet shares; if not tagged, weighted-avg basic shares (marked). Split-adjusted only where a later filing restates the same period at the split ratio"),
 "rnd": ("R&D", "$", ""), "gross_margin": ("Gross margin", "%", "Gross profit / revenue"),
 "operating_margin": ("Operating margin", "%", "Operating income / revenue"), "net_margin": ("Net margin", "%", "Net income / revenue"),
}

def facts(cik):
    return _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", f"CIK{cik:010d}.json")

def _raw(f, kind, unit, tags):
    """Merge tags per period (first tag wins per period). key = (start|None, end). Latest filing wins (restatements)."""
    g = f["facts"].get("us-gaap", {}); out = {}
    for pri, tag in enumerate(tags):
        units = g.get(tag, {}).get("units", {})
        if unit == "USD" and "USD" not in units:  # foreign filers may report in EUR, JPY, ...
            unit_alt = next((u for u in units if len(u) == 3 and u.isupper()), None)
        else: unit_alt = unit
        for x in (units.get(unit_alt) or []):
            if x.get("form") not in FORMS: continue
            k = (x.get("start"), x["end"])
            if (kind == "inst") != (k[0] is None): continue
            cur = out.get(k)
            if cur is None or pri < cur["pri"] or (pri == cur["pri"] and x["filed"] > cur["filed"]):
                out[k] = {"val": x["val"], "filed": x["filed"], "form": x["form"], "pri": pri, "tag": tag, "cur": unit_alt}
    return out

def _splits(f, tags, unit):
    """Stock splits evidenced in the filings themselves: the same period re-reported in a later filing at a clean
    integer multiple, confirmed by at least 2 different periods. Returns [(first restating filing date, ratio)]."""
    g = f["facts"].get("us-gaap", {}); found = {}
    for tag in tags:
        vers = {}
        for x in (g.get(tag, {}).get("units", {}).get(unit) or []):
            if x.get("form") in FORMS and x["val"]: vers.setdefault((x.get("start"), x["end"]), []).append((x["filed"], x["val"]))
        for per, v in vers.items():
            v.sort()
            for (f1, a), (f2, b) in zip(v, v[1:]):
                if unit == "shares" and min(abs(a), abs(b)) < 1e5: continue
                if unit != "shares" and min(abs(a), abs(b)) < 0.25: continue
                q = b / a if unit == "shares" else a / b
                if q >= 1.9 and abs(q - round(q)) / q < 0.01:
                    found.setdefault(round(q), []).append((f2, per))
    out = []
    for r, lst in found.items():
        lst.sort(); i = 0
        while i < len(lst):
            grp = [x for x in lst[i:] if _days(lst[i][0], x[0]) < 400]
            if len({p for _, p in grp}) >= 2: out.append((lst[i][0], r))
            i += len(grp)
    return sorted(out)

def _adjust(pts, splits, unit):
    for p in pts.values():
        fac = 1
        for f2, r in splits:
            if p.get("filed", "9999") < f2: fac *= r
        if fac != 1:
            p["val"] = p["val"] * fac if unit == "shares" else p["val"] / fac
            p["derived"] = "; ".join(x for x in [p["derived"], f"split-adjusted ×{fac} (split seen in later restated filings)"] if x)
    return pts

def _days(s, e): return (D(e) - D(s)).days
def _isq(d): return 80 <= d <= 100
def _isy(d): return 350 <= d <= 380

def _quarters(raw, additive=True):
    """Quarterly flows: direct 3-month facts, else derived from YTD differences (Q2=H1-Q1, Q3=9M-H1, Q4=FY-9M or FY-Q1-Q2-Q3)."""
    q = {}
    for (s, e), v in raw.items():
        if _isq(_days(s, e)): q[e] = {"start": s, "end": e, "val": v["val"], "derived": v.get("derived", ""), "form": v["form"], "tag": v["tag"], "cur": v.get("cur"), "filed": v.get("filed")}
    if not additive: return q
    for _ in range(4):  # iterate so derived quarters can feed further derivations
        for (s, e), v in raw.items():
            n = _days(s, e)
            if n <= 100 or n > 380 or e in q: continue
            # same-start YTD fact ending ~one quarter earlier
            for (s2, e2), v2 in raw.items():
                if s2 == s and _isq(_days(e2, e)) and _days(s2, e2) > 20:
                    q[e] = {"start": e2, "end": e, "val": v["val"] - v2["val"], "form": v["form"], "tag": v["tag"], "cur": v.get("cur"), "filed": v.get("filed"),
                            "derived": ("Q4 = FY − 9M YTD" if _isy(n) else "YTD difference")}
                    break
            else:
                if _isy(n):  # FY minus three quarters inside the year
                    inside = sorted([x for x in q.values() if D(x["start"]) >= D(s) - dt.timedelta(5) and D(x["end"]) < D(e) - dt.timedelta(20)], key=lambda x: x["end"])
                    if len(inside) == 3 and _isq(_days(inside[-1]["end"], e)):
                        q[e] = {"start": inside[-1]["end"], "end": e, "val": v["val"] - sum(x["val"] for x in inside),
                                "form": v["form"], "tag": v["tag"], "cur": v.get("cur"), "filed": v.get("filed"), "derived": "Q4 = FY − Q1−Q3"}
    return q

def _annual(raw):
    return {e: {"start": s, "end": e, "val": v["val"], "derived": v.get("derived", ""), "form": v["form"], "tag": v["tag"], "cur": v.get("cur"), "filed": v.get("filed")} for (s, e), v in raw.items() if _isy(_days(s, e))}

def _ttm(q):
    out = {}; qs = sorted(q.values(), key=lambda x: x["end"])
    for i in range(3, len(qs)):
        w = qs[i - 3:i + 1]
        if all(abs(_days(w[j]["end"], w[j + 1]["end"]) - 91) <= 12 for j in range(3)):
            out[w[-1]["end"]] = {"start": w[0]["start"], "end": w[-1]["end"], "val": sum(x["val"] for x in w),
                                 "derived": "TTM = sum of 4 quarters" + ("; incl. derived quarter" if any(x["derived"] for x in w) else ""), "form": w[-1]["form"], "tag": w[-1]["tag"], "cur": w[-1].get("cur")}
    return out

def _fy_ends(f):
    ends = set()
    for t in ["NetIncomeLoss", "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "NetCashProvidedByUsedInOperatingActivities", "ProfitLoss"]:
        for x in f["facts"].get("us-gaap", {}).get(t, {}).get("units", {}).get("USD", []):
            if x.get("start") and _isy(_days(x["start"], x["end"])) and x.get("form") in FORMS: ends.add(x["end"])
    return ends

def _base(f, m, period):
    kind, unit, tags = TAGS[m]; raw = _raw(f, kind, unit, tags)
    if unit in ("shares", "USD/shares"):
        sp = _splits(f, tags, unit)
        if sp:
            raw = {k: dict(v) for k, v in raw.items()}
            for k, v in raw.items(): v["derived"] = ""
            _adjust(raw, sp, unit)
    if kind == "inst":
        pts = {e: {"start": None, "end": e, "val": v["val"], "derived": v.get("derived", ""), "form": v["form"], "tag": v["tag"], "cur": v.get("cur"), "filed": v.get("filed")} for (_, e), v in raw.items()}
        if period == "annual":
            fy = _fy_ends(f); pts = {e: v for e, v in pts.items() if e in fy or v["form"].startswith(("10-K", "20-F", "40-F"))}
        return pts  # TTM for balance-sheet items = quarter-end value
    if kind == "avg":
        return _annual(raw) if period == "annual" else _quarters(raw, additive=False)
    if period == "annual": return _annual(raw)
    q = _quarters(raw)
    return _ttm(q) if period == "ttm" else q

def _combine(a, b, fn, note):
    out = {}
    for e in a:
        if e in b and (a[e]["start"] == b[e]["start"] or a[e]["start"] is None):
            v = fn(a[e]["val"], b[e]["val"])
            if v is None: continue
            out[e] = dict(a[e], val=v, derived="; ".join(dict.fromkeys(x for x in [note, a[e]["derived"], b[e]["derived"]] if x)))
    return out

def metric_series(f, m, period):
    if m in ("revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "ocf", "capex", "rnd", "cash"):
        return _base(f, m, period)
    if m == "fcf": return _combine(_base(f, "ocf", period), _base(f, "capex", period), lambda a, b: a - b, "")
    if m.endswith("_margin"):
        num = {"gross_margin": "gross_profit", "operating_margin": "operating_income", "net_margin": "net_income"}[m]
        return _combine(_base(f, num, period), _base(f, "revenue", period), lambda a, b: a / b * 100 if b else None, "")
    if m == "total_debt":
        lt, nc, c, st = (_base(f, k, period) for k in ("debt_lt", "debt_ltnc", "debt_ltc", "debt_st"))
        out = {}
        for e in set(lt) | set(nc):
            if e in lt: v, note = lt[e]["val"], lt[e]["tag"]
            else:
                v, note = nc[e]["val"] + (c[e]["val"] if e in c else 0), nc[e]["tag"] + (" + " + c[e]["tag"] if e in c else "")
            if e in st: v += st[e]["val"]; note += " + " + st[e]["tag"]
            base = lt.get(e) or nc.get(e)
            out[e] = dict(base, val=v, derived="", tag=note)
        return out
    if m == "shares":
        s = _base(f, "shares_out", period)
        if s: return s
        w = _base(f, "shares_wavg", period)
        return {e: dict(v, derived="weighted-avg basic shares (period-end count not tagged)") for e, v in w.items()}
    raise ValueError("unknown metric " + m)

def _label(e, period):
    d = D(e) - dt.timedelta(days=45)
    if period == "annual":
        d = D(e) - dt.timedelta(days=182); return str(d.year)
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"

def series(ticker, m, period, y1=None, y2=None):
    cik, title = cik_of(ticker); f = facts(cik)
    g = f["facts"].get("us-gaap")
    if not g:
        if "ifrs-full" in f["facts"]:
            raise ValueError(f"{ticker}: files under IFRS (20-F/40-F foreign filer); this app only maps US-GAAP tags, so no data shown")
        raise ValueError(f"{ticker}: no US-GAAP financial data in SEC XBRL")
    pts = metric_series(f, m, period)
    rows = []
    for e in sorted(pts):
        p = pts[e]; lab = _label(e, period)
        yr = int(lab[:4])
        if (y1 and yr < y1) or (y2 and yr > y2): continue
        rows.append({"end": e, "start": p["start"], "label": lab, "value": p["val"], "derived": p["derived"], "form": p["form"], "tag": p["tag"], "cur": p.get("cur")})
    # one row per label (keep latest end) to avoid odd overlaps such as 53-week years
    ded = {}
    for r in rows: ded[r["label"]] = r
    rows = list(ded.values())
    msg = ""
    foreign = any(r["form"].startswith(("20-F", "40-F")) for r in rows)
    if foreign: msg = f"{ticker}: foreign filer (20-F/40-F) — annual data only, no quarterly filings"
    if not rows and period != "annual" and any(x.get("form", "").startswith(("20-F", "40-F")) for t in g.values() for u in t.get("units", {}).values() for x in u[:50]):
        msg = f"{ticker}: foreign filer (20-F/40-F) — no quarterly SEC data; try Annual"
    elif not rows: msg = f"{ticker}: no {METRICS[m][0]} data for this period/range in SEC filings"
    return {"ticker": ticker.upper(), "name": f.get("entityName") or title, "cik": cik, "metric": m, "period": period,
            "currency": next((r["cur"] for r in rows if r.get("cur") and r["cur"] not in ("shares", "USD/shares")), "USD"), "rows": rows, "message": msg, "foreign": foreign, "note": METRICS[m][2]}
