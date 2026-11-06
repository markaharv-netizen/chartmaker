#!/usr/bin/env python3
"""Paper book sized by each name's paperWeight. Long and short. No new scheduler.

static/price_targets.json may carry paperWeight, a signed percent of NAV
(10 = 10% long, -5 = 5% short, 0 or missing = cash). The nightly price-targets
refresh copies that from the sheet column "Paper weight %".

This script reads that file, pulls daily opens and closes from the same Yahoo
chart endpoint as /api/chart, and writes static/paper_trading.json.

After any update to price_targets.json, from the repo root:

    python3 scripts/paper_trading.py

Then commit static/paper_trading.json. Running it again with the same weights
does not book a second trade.

Until a non-zero weight appears, the book is $100,000 in cash and the tab says
it is waiting for weights. The first time weights appear, those targets trade
at the next session open (today's open if this runs before 9:30 ET, otherwise
the following session). A later weight change resizes, opens, closes, or flips
that name at the next open. Rating is stored for the table and does not size
the book.

Gross exposure, the sum of absolute weights, is capped at 100%. Above that,
every weight is scaled down by the same factor. Short sales credit entry times
shares to cash and reserve those proceeds so they cannot fund another
position. NAV = cash + sum(signed shares * price), which is cash plus long
market value minus short market value. No fees.
"""
import argparse, copy, datetime, json, os, sys, time, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

CONFIG = {
    "startingCash": 100_000.0,
    "historyStart": "2026-11-01",
    "benchmark": "SPY",
    "benchmarkLabel": "S&P 500",
    "timezone": "America/New_York",
    "marketOpen": "09:30",
    "marketClose": "16:00",
    "grossCap": 100.0,
    "minTrade": 1.0,
}

NOTE = (
    "Paper book sized by paperWeight on static/price_targets.json. "
    "paperWeight is a signed percent of NAV (10 = 10% long, -5 = 5% short, 0 or missing = cash), "
    "copied from the sheet column \"Paper weight %\". "
    "Gross exposure is capped at 100%. No leverage, no fees. "
    "After any price_targets.json update: python3 scripts/paper_trading.py"
)


def summary_text(cfg):
    return (
        f"Virtual ${cfg['startingCash']:,.0f}. Each name is sized by its paper weight, "
        f"a signed percent of NAV (10 = 10% long, -5 = 5% short, 0 = cash). "
        f"Gross exposure is capped at {cfg['grossCap']:.0f}%. No leverage, no fees. "
        f"Weight changes trade at the next session open ({cfg['marketOpen']} America/New_York). "
        f"Compared with {cfg['benchmarkLabel']} ({cfg['benchmark']}). Not investment advice."
    )


def _tz(cfg=None):
    return ZoneInfo((cfg or CONFIG)["timezone"])


def _hhmm(text):
    hh, mm = str(text).split(":")
    return int(hh), int(mm)


def now_et(value=None, cfg=None):
    tz = _tz(cfg)
    if value is None:
        return datetime.datetime.now(tz)
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    s = str(value).strip()
    if len(s) == 10:
        return datetime.datetime.fromisoformat(s).replace(tzinfo=tz)
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def money(v):
    return round(float(v) + 1e-9, 2)


def q4(v):
    return float(f"{float(v):.4f}")


def not_before(now, cfg):
    """First date whose open is strictly after `now`. Before the open, that is today."""
    hh, mm = _hhmm(cfg["marketOpen"])
    d = now.date()
    if (now.hour, now.minute, now.second) >= (hh, mm, 0):
        d += datetime.timedelta(days=1)
    return d.isoformat()


def session_fillable(date, now, cfg):
    today = now.date().isoformat()
    if date < today:
        return True
    if date > today:
        return False
    hh, mm = _hhmm(cfg["marketOpen"])
    return (now.hour, now.minute, now.second) >= (hh, mm, 0)


def session_complete(date, now, cfg):
    today = now.date().isoformat()
    if date < today:
        return True
    if date > today:
        return False
    hh, mm = _hhmm(cfg["marketClose"])
    return (now.hour, now.minute, now.second) >= (hh, mm, 0)


def blank_book(cfg=None):
    cfg = dict(CONFIG if cfg is None else {**CONFIG, **cfg})
    book = {
        "version": 2,
        "generated": None,
        "note": NOTE,
        "config": {
            "startingCash": float(cfg["startingCash"]),
            "historyStart": cfg.get("historyStart") or CONFIG["historyStart"],
            "benchmark": cfg["benchmark"],
            "benchmarkLabel": cfg["benchmarkLabel"],
            "timezone": cfg["timezone"],
            "marketOpen": cfg["marketOpen"],
            "marketClose": cfg["marketClose"],
            "grossCap": float(cfg["grossCap"]),
            "minTrade": float(cfg["minTrade"]),
            "summary": "",
        },
        "cash": float(cfg["startingCash"]),
        "started": False,
        "seenWeights": False,
        "waiting": True,
        "inception": None,
        "order": [],
        "positions": {},
        "applied": {},
        "pending": None,
        "trades": [],
        "marks": [],
        "spyInceptionOpen": None,
        "gross": {"requested": 0.0, "effective": 0.0, "scaled": False, "scale": 1.0, "cap": float(cfg["grossCap"]), "warning": ""},
    }
    book["config"]["summary"] = summary_text(book["config"])
    return book


def _import_positions(book):
    positions = book.get("positions")
    if isinstance(positions, list):
        order = book.get("order") or [pos.get("ticker") for pos in positions if pos.get("ticker")]
        book["positions"] = {pos["ticker"]: pos for pos in positions if pos.get("ticker")}
        book["order"] = [ticker for ticker in order if ticker in book["positions"]]
    elif not isinstance(positions, dict):
        book["positions"] = {}
    if not isinstance(book.get("applied"), dict):
        book["applied"] = {}


def weight_spec(rows, cap=100.0):
    """Requested paperWeight values, scaled so the sum of absolute weights is at most `cap`."""
    requested, ratings, order = {}, {}, []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        if not ticker or "elon case" in ticker.lower():
            continue
        if ticker in requested:
            raise ValueError("duplicate ticker " + ticker)
        try:
            weight = float(raw.get("paperWeight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        requested[ticker] = q4(weight)
        ratings[ticker] = str(raw.get("rating") or "").strip().upper() or None
        order.append(ticker)
    gross = sum(abs(w) for w in requested.values())
    cap = float(cap)
    scaled = gross > cap + 1e-9 and gross > 0
    scale = (cap / gross) if scaled else 1.0
    effective = {ticker: q4(weight * scale) for ticker, weight in requested.items()}
    # Keep the scaled gross on the cap when rounding drifts by a hair.
    if scaled:
        drift = q4(sum(abs(w) for w in effective.values()) - cap)
        if abs(drift) >= 0.0001 and effective:
            ticker = max(effective, key=lambda t: abs(effective[t]))
            sign = 1 if effective[ticker] >= 0 else -1
            effective[ticker] = q4(effective[ticker] - sign * drift)
    warning = ""
    if scaled:
        warning = (
            f"Requested gross weight is {gross:.2f}%, above the {cap:.0f}% cap. "
            f"Every weight is scaled by {scale:.4f} so gross exposure is {cap:.0f}%."
        )
    return {
        "requested": requested,
        "effective": effective,
        "ratings": ratings,
        "order": order,
        "grossRequested": q4(gross),
        "grossEffective": q4(min(gross, cap) if gross else 0.0),
        "scaled": scaled,
        "scale": scale,
        "cap": cap,
        "warning": warning,
        "any": any(abs(w) > 1e-6 for w in requested.values()),
    }


def _weights_equal(left, right):
    keys = set(left or {}) | set(right or {})
    for key in keys:
        if abs(float((left or {}).get(key) or 0) - float((right or {}).get(key) or 0)) > 1e-4:
            return False
    return True


def _shares(pos):
    if not pos or pos.get("shares") is None:
        return 0.0
    return float(pos["shares"])


def _blank_pos(ticker):
    return {
        "ticker": ticker,
        "rating": None,
        "side": "cash",
        "status": "cash",
        "shares": 0.0,
        "entryPrice": None,
        "entryDate": None,
        "realized": 0.0,
        "weight": 0.0,
        "weightRequested": 0.0,
    }


def _ensure(book, ticker):
    pos = book["positions"].get(ticker)
    if pos is None:
        pos = _blank_pos(ticker)
        book["positions"][ticker] = pos
        if ticker not in book["order"]:
            book["order"].append(ticker)
    return pos


def _log(book, **row):
    book["trades"].append({
        "id": len(book["trades"]) + 1,
        "date": row["date"],
        "ticker": row["ticker"],
        "action": row["action"],
        "rating": row.get("rating"),
        "price": row.get("price"),
        "shares": row.get("shares"),
        "notional": row.get("notional"),
        "realized": row.get("realized"),
        "reason": row.get("reason"),
    })


def _flatten(pos):
    pos["shares"] = 0.0
    pos["entryPrice"] = None
    pos["entryDate"] = None
    pos["side"] = "cash"
    pos["status"] = "cash"


def _close_qty(book, pos, qty, price, date, reason):
    sh = _shares(pos)
    qty = min(float(qty), abs(sh))
    if qty <= 1e-12:
        return
    entry = float(pos["entryPrice"])
    if sh > 0:
        pnl = qty * (price - entry)
        delta = -qty
        action = "sell"
    else:
        pnl = qty * (entry - price)
        delta = qty
        action = "cover"
    book["cash"] = money(book["cash"] - delta * price)
    pos["realized"] = money((pos.get("realized") or 0) + pnl)
    new_sh = sh + delta
    _log(book, date=date, ticker=pos["ticker"], action=action, rating=pos.get("rating"),
         price=price, shares=qty, notional=money(qty * price), realized=money(pnl), reason=reason)
    if abs(new_sh) < 1e-8:
        _flatten(pos)
    else:
        pos["shares"] = new_sh
        pos["side"] = "long" if new_sh > 0 else "short"
        pos["status"] = "open"


def _open_to(book, pos, target_shares, price, date, reason):
    sh = _shares(pos)
    delta = target_shares - sh
    if abs(delta) < 1e-12:
        return
    book["cash"] = money(book["cash"] - delta * price)
    if abs(sh) < 1e-12:
        pos["entryPrice"] = price
        pos["entryDate"] = date
    else:
        pos["entryPrice"] = (abs(sh) * float(pos["entryPrice"]) + abs(delta) * price) / abs(target_shares)
    pos["shares"] = target_shares
    pos["side"] = "long" if target_shares > 0 else "short"
    pos["status"] = "open"
    action = "buy" if target_shares > 0 else "short"
    _log(book, date=date, ticker=pos["ticker"], action=action, rating=pos.get("rating"),
         price=price, shares=abs(delta), notional=money(abs(delta) * price), realized=None, reason=reason)


def rebalance_ticker(book, ticker, target_mv, price, date, reason):
    """Move one name to a signed market value at `price`. Returns False if the price is missing."""
    if price is None or price <= 0:
        return False
    pos = _ensure(book, ticker)
    min_trade = float(book["config"]["minTrade"])
    sh = _shares(pos)
    cur_mv = sh * price
    if abs(sh) < 1e-12 and abs(target_mv) < min_trade:
        _flatten(pos)
        return True
    if abs(sh) > 1e-12 and abs(target_mv) >= min_trade and (sh > 0) != (target_mv > 0):
        _close_qty(book, pos, abs(sh), price, date, reason)
        _open_to(book, pos, target_mv / price, price, date, reason)
        return True
    if abs(target_mv) < min_trade:
        if abs(sh) > 1e-12:
            _close_qty(book, pos, abs(sh), price, date, reason)
        return True
    target_shares = target_mv / price
    if abs(sh) > 1e-12 and abs(target_shares) < abs(sh) - 1e-12:
        _close_qty(book, pos, abs(sh) - abs(target_shares), price, date, reason)
        return True
    if abs((target_shares - _shares(pos)) * price) < min_trade:
        return True
    _open_to(book, pos, target_shares, price, date, reason)
    return True


def _open_on(prices, symbol, date):
    for bar in prices.get(symbol) or []:
        if bar.get("date") == date and bar.get("open") not in (None, "") and bar["open"] > 0:
            return float(bar["open"])
    return None


def _close_on(prices, symbol, date):
    best = None
    for bar in sorted(prices.get(symbol) or [], key=lambda b: b.get("date") or ""):
        if not bar.get("date") or bar["date"] > date:
            break
        if bar.get("close") is not None:
            best = float(bar["close"])
    return best


def _dates_of(prices):
    found = set()
    for bars in (prices or {}).values():
        for bar in bars or []:
            if bar.get("date"):
                found.add(bar["date"])
    return sorted(found)


def portfolio_nav(book, price_of):
    """Cash plus signed market value. A short's negative shares subtract its market value."""
    if not book.get("started"):
        return float(book["config"]["startingCash"])
    nav = float(book["cash"])
    positions = book["positions"]
    seq = positions.values() if isinstance(positions, dict) else positions
    for pos in seq:
        sh = _shares(pos)
        if not sh:
            continue
        px = price_of(pos["ticker"])
        if px is None:
            px = pos.get("entryPrice")
        if px is None:
            continue
        nav += sh * float(px)
    return nav


def _desired(book, spec):
    names = list(spec["order"])
    for ticker, pos in book["positions"].items():
        if ticker not in spec["effective"] and abs(_shares(pos)) > 1e-12 and ticker not in names:
            names.append(ticker)
    return {ticker: q4(spec["effective"].get(ticker, 0.0)) for ticker in names}


def _sync_weights(book, spec, now):
    cfg = book["config"]
    book["gross"] = {
        "requested": spec["grossRequested"],
        "effective": spec["grossEffective"],
        "scaled": spec["scaled"],
        "scale": spec["scale"],
        "cap": spec["cap"],
        "warning": spec["warning"],
    }
    book["order"] = list(spec["order"])
    for ticker in spec["order"]:
        pos = _ensure(book, ticker)
        pos["rating"] = spec["ratings"].get(ticker)
        pos["weightRequested"] = spec["requested"].get(ticker, 0.0)
    if spec["any"]:
        book["seenWeights"] = True
    book["waiting"] = not book.get("seenWeights")
    if book["waiting"]:
        book["pending"] = None
        return
    desired = _desired(book, spec)
    pending = book.get("pending")
    if pending and _weights_equal(pending.get("weights") or {}, desired):
        return
    if book.get("started") and _weights_equal(desired, book.get("applied") or {}):
        book["pending"] = None
        return
    reason = "inception" if not book.get("started") else "weight"
    book["pending"] = {
        "executeOn": not_before(now, cfg),
        "detected": now.date().isoformat(),
        "detectedAt": now.isoformat(timespec="seconds"),
        "weights": desired,
        "reason": reason,
    }


def _needs_price(book, weights):
    needed = []
    for ticker, weight in weights.items():
        pos = book["positions"].get(ticker)
        if abs(weight) > 1e-6 or abs(_shares(pos)) > 1e-12:
            needed.append(ticker)
    return needed


def _try_fill(book, prices, date):
    pending = book.get("pending")
    if not pending:
        return False
    weights = pending["weights"]
    needed = _needs_price(book, weights)
    opens = {ticker: _open_on(prices, ticker, date) for ticker in needed}
    if any(px is None for px in opens.values()):
        return False
    nav = float(book["cash"])
    for ticker, pos in list(book["positions"].items()):
        sh = _shares(pos)
        if not sh:
            continue
        px = opens.get(ticker) or _open_on(prices, ticker, date)
        if px is None:
            return False
        nav += sh * px
    reason = pending.get("reason") or "weight"
    for ticker in needed:
        _ensure(book, ticker)
        if not rebalance_ticker(book, ticker, nav * float(weights[ticker]) / 100.0, opens[ticker], date, reason):
            return False
    book["applied"] = {ticker: q4(weight) for ticker, weight in weights.items()}
    book["pending"] = None
    book["started"] = True
    book["waiting"] = False
    if not book.get("inception"):
        book["inception"] = date
    if book.get("spyInceptionOpen") is None:
        spy = _open_on(prices, book["config"]["benchmark"], date)
        if spy is not None:
            book["spyInceptionOpen"] = spy
    # Drop names that are flat and no longer in the sheet.
    for ticker in list(book["positions"]):
        if ticker not in book["order"] and abs(_shares(book["positions"][ticker])) < 1e-12:
            book["positions"].pop(ticker, None)
    return True


def _refresh_display(book, spec):
    pending = book.get("pending") or {}
    target = pending.get("weights") or book.get("applied") or {}
    for ticker in list(book["order"]):
        pos = _ensure(book, ticker)
        pos["rating"] = spec["ratings"].get(ticker)
        pos["weightRequested"] = spec["requested"].get(ticker, 0.0)
        pos["weight"] = q4(float(target.get(ticker, spec["effective"].get(ticker, 0.0)) or 0))
        sh = _shares(pos)
        if abs(sh) > 1e-12:
            pos["side"] = "long" if sh > 0 else "short"
            pos["status"] = "open"
        elif pending and abs(float(pending.get("weights", {}).get(ticker) or 0)) > 1e-6:
            w = float(pending["weights"][ticker])
            pos["side"] = "long" if w > 0 else "short"
            pos["status"] = "pending"
            pos["shares"] = None
            pos["entryPrice"] = None
            pos["entryDate"] = None
        else:
            _flatten(pos)
            pos["weight"] = q4(float(target.get(ticker, 0) or 0))
            pos["weightRequested"] = spec["requested"].get(ticker, 0.0)


def _mark(book, prices, date):
    nav = portfolio_nav(book, lambda ticker: _close_on(prices, ticker, date))
    spy = _close_on(prices, book["config"]["benchmark"], date)
    return {"date": date, "nav": money(nav), "spyClose": spy}


def update_book(book, rows, prices, now, config=None):
    """Apply weight changes and append fills and daily close marks. Does not mutate `book`."""
    cfg = dict(CONFIG)
    if config:
        cfg.update(config)
    now = now_et(now, cfg)
    if not book or book.get("version") != 2:
        book = blank_book(cfg)
    else:
        book = copy.deepcopy(book)
        _import_positions(book)
        saved = book.get("config") or {}
        fresh = blank_book(cfg)["config"]
        fresh["startingCash"] = float(saved.get("startingCash") or fresh["startingCash"])
        fresh["historyStart"] = saved.get("historyStart") or fresh["historyStart"]
        book["config"] = fresh
        book["note"] = NOTE
        book["config"]["summary"] = summary_text(book["config"])
    spec = weight_spec(rows, book["config"]["grossCap"])
    _sync_weights(book, spec, now)
    prices = prices or {}
    dates = _dates_of(prices)
    fillable = lambda date: session_fillable(date, now, book["config"])
    complete = lambda date: session_complete(date, now, book["config"])
    last_mark = book["marks"][-1]["date"] if book["marks"] else None
    for date in dates:
        if last_mark and date <= last_mark:
            continue
        if book.get("pending") and book["pending"]["executeOn"] <= date and fillable(date):
            _try_fill(book, prices, date)
        # A session we still need for a fill stays unmarked so a later run can trade it.
        stuck = book.get("pending") and book["pending"]["executeOn"] <= date and fillable(date)
        if stuck:
            continue
        if book.get("started") and book.get("inception") and date >= book["inception"] and complete(date):
            if not any(mark["date"] == date for mark in book["marks"]):
                book["marks"].append(_mark(book, prices, date))
    _refresh_display(book, spec)
    book["waiting"] = not book.get("seenWeights")
    book["config"]["summary"] = summary_text(book["config"])
    return book


def export_book(book):
    out = copy.deepcopy(book)
    positions = out.get("positions") or {}
    if isinstance(positions, dict):
        order = list(out.get("order") or [])
        for ticker in positions:
            if ticker not in order:
                order.append(ticker)
        out["order"] = [ticker for ticker in order if ticker in positions]
        out["positions"] = [positions[ticker] for ticker in out["order"]]
    return out


def sessions_from_ms_bars(bars, tzname=None):
    tz = ZoneInfo(tzname or CONFIG["timezone"])
    by_date = {}
    for bar in bars or []:
        if not bar or len(bar) < 5:
            continue
        opened, closed = bar[1], bar[4]
        if opened is None and closed is None:
            continue
        date = datetime.datetime.fromtimestamp(bar[0] / 1000, tz).date().isoformat()
        by_date[date] = {"date": date, "open": opened, "close": closed}
    return [by_date[date] for date in sorted(by_date)]


def sessions_from_chart(payload, tzname=None):
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return []
    row = result[0]
    meta = row.get("meta") or {}
    tz = ZoneInfo(tzname or meta.get("exchangeTimezoneName") or CONFIG["timezone"])
    stamps = row.get("timestamp") or []
    quote = ((row.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    closes = quote.get("close") or []
    by_date = {}
    for i, stamp in enumerate(stamps):
        opened = opens[i] if i < len(opens) else None
        closed = closes[i] if i < len(closes) else None
        if opened is None and closed is None:
            continue
        date = datetime.datetime.fromtimestamp(int(stamp), tz).date().isoformat()
        by_date[date] = {"date": date, "open": opened, "close": closed}
    return [by_date[date] for date in sorted(by_date)]


def fetch_sessions(symbol, start_date, cfg=None):
    cfg = cfg or CONFIG
    tz = _tz(cfg)
    start = datetime.datetime.fromisoformat(start_date).replace(tzinfo=tz)
    period1 = int(start.timestamp()) - 10 * 86400
    period2 = int(time.time()) + 2 * 86400
    query = urllib.parse.urlencode({
        "interval": "1d",
        "includePrePost": "false",
        "period1": period1,
        "period2": period2,
    })
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol) + "?" + query
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)
    sessions = sessions_from_chart(payload, cfg["timezone"])
    if not sessions:
        error = ((payload.get("chart") or {}).get("error")) or "no bars"
        raise ValueError(f"No price points for {symbol}: {error}")
    meta = (((payload.get("chart") or {}).get("result") or [{}])[0].get("meta") or {})
    last = meta.get("regularMarketPrice")
    return sessions, last


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_book(path):
    try:
        book = load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(book, dict):
        return None
    _import_positions(book)
    return book


def _dump(book):
    body = export_book(book)
    body["generated"] = None
    return json.dumps(body, indent=2, sort_keys=False)


def write_book(path, book):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = export_book(book)
    body["generated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)
    return body


def repo_of(script_path=None):
    here = os.path.dirname(os.path.abspath(script_path or __file__))
    return os.path.dirname(here)


def build_prices(symbols, start_date, offline=None, cfg=None):
    if offline is not None:
        return {symbol: list(offline.get(symbol) or []) for symbol in symbols}, {}
    prices, last = {}, {}
    for symbol in symbols:
        try:
            sessions, px = fetch_sessions(symbol, start_date, cfg)
        except Exception as exc:
            print(f"warning: {symbol}: {exc}", file=sys.stderr)
            prices[symbol] = []
            continue
        prices[symbol] = sessions
        if px is not None:
            last[symbol] = px
    return prices, last


def run_once(targets_path, out_path, now=None, offline=None, write=True):
    doc = load_json(targets_path)
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    book = load_book(out_path)
    cfg = book["config"] if book else CONFIG
    start = cfg.get("historyStart") or cfg.get("inception") or CONFIG["historyStart"]
    symbols = []
    for row in rows or []:
        ticker = str((row or {}).get("ticker") or "").strip().upper()
        if ticker and ticker not in symbols:
            symbols.append(ticker)
    benchmark = cfg.get("benchmark") or CONFIG["benchmark"]
    if benchmark not in symbols:
        symbols.append(benchmark)
    if book and isinstance(book.get("positions"), dict):
        for ticker in book["positions"]:
            if ticker not in symbols:
                symbols.insert(0, ticker)
    prices, _last = build_prices(symbols, start, offline=offline, cfg=CONFIG)
    moment = now if isinstance(now, datetime.datetime) else now_et(now)
    updated = update_book(book, rows, prices, moment)
    changed = True
    if os.path.exists(out_path):
        try:
            previous = load_book(out_path)
            changed = _dump(previous) != _dump(updated)
        except (OSError, ValueError):
            changed = True
    if write and changed:
        write_book(out_path, updated)
    return updated, changed


def describe(book):
    lines = [book["config"].get("summary") or ""]
    if book.get("waiting"):
        lines.append("waiting for weights")
    if book.get("gross", {}).get("warning"):
        lines.append(book["gross"]["warning"])
    pending = book.get("pending")
    when = pending["executeOn"] if pending else "—"
    lines.append(
        f"cash {book['cash']:.2f}  started {bool(book.get('started'))}  "
        f"pending {when}  trades {len(book.get('trades') or [])}  marks {len(book.get('marks') or [])}"
    )
    positions = book["positions"]
    seq = [positions[t] for t in book.get("order") or [] if t in positions] if isinstance(positions, dict) else positions
    for pos in seq:
        shares = "—" if pos.get("shares") is None else f"{pos['shares']:.4f}"
        lines.append(
            f"  {pos['ticker']:6} {str(pos.get('rating') or '—'):4} {str(pos.get('side') or '—'):5} "
            f"w {float(pos.get('weight') or 0):8.2f}  {str(pos.get('status') or '—'):8} shares {shares}"
        )
    return "\n".join(lines)


def self_test():
    import unittest
    tests = os.path.join(repo_of(), "tests")
    suite = unittest.defaultTestLoader.discover(tests, pattern="test_paper_trading.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    print("self-test ok")


def main():
    parser = argparse.ArgumentParser(description="Update static/paper_trading.json from paperWeight and Yahoo daily bars")
    parser.add_argument("--repo", default=repo_of())
    parser.add_argument("--targets", help="defaults to static/price_targets.json")
    parser.add_argument("--out", help="defaults to static/paper_trading.json")
    parser.add_argument("--prices", help="offline sessions JSON, {TICKER: [{date, open, close}]}")
    parser.add_argument("--as-of", help="clock in America/New_York (ISO). Default: now")
    parser.add_argument("--dry-run", action="store_true", help="compute the book and print it, do not write")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    repo = args.repo
    targets = args.targets or os.path.join(repo, "static", "price_targets.json")
    out = args.out or os.path.join(repo, "static", "paper_trading.json")
    offline = load_json(args.prices) if args.prices else None
    now = now_et(args.as_of) if args.as_of else now_et()
    updated, changed = run_once(targets, out, now=now, offline=offline, write=not args.dry_run)
    print(describe(updated))
    if args.dry_run:
        print("dry-run (not written)")
    elif changed:
        print(f"wrote {out}")
    else:
        print(f"unchanged {out}")


if __name__ == "__main__":
    main()
