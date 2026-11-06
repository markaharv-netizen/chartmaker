#!/usr/bin/env python3
"""Paper book for Steve's ratings. Long only. No new scheduler.

The Price targets tab reads static/price_targets.json. This script reads that
file, pulls daily opens and closes from the same Yahoo chart endpoint as
/api/chart, and writes static/paper_trading.json.

Nightly, after price_targets.json is updated, from the repo root:

    python3 scripts/paper_trading.py

Then commit static/paper_trading.json with the price-targets update.

Rules live in CONFIG. BUY is a long of one equal-weight slot. HOLD and SELL
are cash. A move between HOLD and SELL does not trade. Leaving or entering
BUY trades at the next session open after the change is detected (the 9:30
ET open; a run at or after 9:30 waits for the following session). The
inception buys are the exception: they fill at the 2026-11-06 open, and
stay pending until that open is in the price history.

A name that shows up later takes one inception-sized slot from cash and
does not resize names already held. Cash below CONFIG["minCash"] is left
uninvested until a later rating change. No leverage, no fees, no shorts.
"""
import argparse, copy, datetime, json, os, sys, time, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

CONFIG = {
    "startingCash": 100_000.0,
    "inception": "2026-11-06",
    "benchmark": "SPY",
    "benchmarkLabel": "S&P 500",
    "timezone": "America/New_York",
    "marketOpen": "09:30",
    "marketClose": "16:00",
    "minCash": 1.0,
    "longRating": "BUY",
    "cashRatings": ("HOLD", "SELL"),
}

FROZEN = ("startingCash", "inception", "inceptionCount", "inceptionTickers", "slice")
NOTE = (
    "Long-only paper book of the ratings in static/price_targets.json. "
    "BUY buys one equal-weight slot at the next open. HOLD and SELL stay in cash. "
    "No shorts, no leverage, no fees. "
    "Nightly, after updating price_targets.json: python3 scripts/paper_trading.py"
)


def summary_text(cfg):
    n = cfg.get("inceptionCount")
    slot = ""
    if n:
        slot = f" Inception slots: 1/{n} of ${cfg['startingCash']:,.0f} each."
    cash = " and ".join(cfg.get("cashRatings") or ())
    return (
        f"Virtual ${cfg['startingCash']:,.0f}, long only, no fees and no leverage. "
        f"Starts at the {cfg['inception']} open. "
        f"{cfg['longRating']} buys one equal-weight slot; {cash} leave that slot in cash. "
        f"A change into or out of {cfg['longRating']} trades at the next session open "
        f"({cfg['marketOpen']} America/New_York). "
        f"Compared with {cfg['benchmarkLabel']} ({cfg['benchmark']})."
        f"{slot} Not investment advice."
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


def normalize_rating(value, cfg=None):
    cfg = cfg or CONFIG
    t = str(value or "").strip().upper()
    if not t:
        return None
    if t == cfg["longRating"] or t.startswith(cfg["longRating"] + " ") or t.startswith(cfg["longRating"] + "("):
        return cfg["longRating"]
    for name in cfg["cashRatings"]:
        if t == name or t.startswith(name + " ") or t.startswith(name + "("):
            return name
    return None


def wants_long(rating, cfg):
    return rating == cfg["longRating"]


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
    cfg = dict(CONFIG if cfg is None else cfg)
    cfg.setdefault("cashRatings", CONFIG["cashRatings"])
    book = {
        "version": 1,
        "generated": None,
        "note": NOTE,
        "config": {
            "startingCash": float(cfg["startingCash"]),
            "inception": cfg["inception"],
            "benchmark": cfg["benchmark"],
            "benchmarkLabel": cfg["benchmarkLabel"],
            "timezone": cfg["timezone"],
            "marketOpen": cfg["marketOpen"],
            "marketClose": cfg["marketClose"],
            "minCash": float(cfg["minCash"]),
            "longRating": cfg["longRating"],
            "cashRatings": list(cfg["cashRatings"]),
            "inceptionCount": None,
            "inceptionTickers": [],
            "slice": None,
            "weight": None,
            "summary": "",
        },
        "cash": float(cfg["startingCash"]),
        "order": [],
        "positions": {},
        "ratings": {},
        "pending": [],
        "trades": [],
        "marks": [],
        "spyInceptionOpen": None,
        "started": False,
    }
    book["config"]["summary"] = summary_text(book["config"])
    return book


def _apply_config(book, cfg):
    """Refresh operational knobs. Inception size stays on the book once set."""
    saved = book.get("config") or {}
    merged = blank_book(cfg)["config"]
    for key in FROZEN:
        if saved.get(key) not in (None, [], ""):
            merged[key] = saved[key]
    if merged.get("inceptionCount") and not merged.get("slice"):
        merged["slice"] = float(merged["startingCash"]) / int(merged["inceptionCount"])
    if merged.get("inceptionCount"):
        merged["weight"] = f"1/{int(merged['inceptionCount'])}"
    merged["summary"] = summary_text(merged)
    book["config"] = merged
    book["note"] = NOTE
    return book


def parse_targets(rows, cfg):
    out, seen = [], set()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        if not ticker or "elon case" in ticker.lower():
            continue
        rating = normalize_rating(raw.get("rating"), cfg)
        if rating is None:
            continue
        if ticker in seen:
            raise ValueError("duplicate ticker " + ticker)
        seen.add(ticker)
        out.append((ticker, rating))
    if not out:
        raise ValueError("price targets have no BUY, HOLD, or SELL rows")
    return out


def _held(pos):
    return bool(pos and pos.get("status") == "open" and pos.get("side") == "long" and (pos.get("shares") or 0) > 0)


def _blank_pos(ticker, rating, cfg):
    n = int(cfg["inceptionCount"])
    slot = float(cfg["slice"])
    return {
        "ticker": ticker,
        "rating": rating,
        "side": "long" if wants_long(rating, cfg) else "cash",
        "status": "pending",
        "entryDate": None,
        "entryPrice": None,
        "shares": None,
        "notional": 0.0,
        "targetNotional": slot,
        "targetWeight": 1.0 / n,
        "realized": 0.0,
    }


def _take_pending(book, ticker):
    kept, found = [], None
    for order in book["pending"]:
        if order["ticker"] == ticker and found is None:
            found = order
        elif order["ticker"] != ticker:
            kept.append(order)
    book["pending"] = kept
    return found


def _queue(book, ticker, rating, action, execute_on, now, reason):
    book["pending"].append({
        "ticker": ticker,
        "rating": rating,
        "action": action,
        "detected": now.date().isoformat(),
        "detectedAt": now.isoformat(timespec="seconds"),
        "executeOn": execute_on,
        "reason": reason,
    })


def _needed_action(want_long, held):
    if want_long and not held:
        return "buy"
    if not want_long and held:
        return "sell"
    if not want_long and not held:
        return "cash"
    return None


def _reconcile(book, ticker, rating, now, is_new):
    cfg = book["config"]
    pos = book["positions"].get(ticker)
    if pos is None:
        pos = _blank_pos(ticker, rating, cfg)
        book["positions"][ticker] = pos
        if ticker not in book["order"]:
            book["order"].append(ticker)
    existing = _take_pending(book, ticker)
    prev = book["ratings"].get(ticker)
    held = _held(pos)
    want_long = wants_long(rating, cfg)
    action = _needed_action(want_long, held)
    inception_name = ticker in set(cfg.get("inceptionTickers") or [])
    rating_changed = prev is not None and prev != rating

    if (not book.get("started")) and inception_name and not held:
        action = "buy" if want_long else "cash"
        if existing and existing["action"] == action and existing.get("reason") == "inception":
            existing["rating"] = rating
            book["pending"].append(existing)
        else:
            _queue(book, ticker, rating, action, cfg["inception"], now, "inception")
        pos["status"] = "pending"
        pos["side"] = "long" if want_long else "cash"
        pos["shares"] = None
        pos["notional"] = 0.0
        pos["entryPrice"] = None
        pos["entryDate"] = None
        pos["rating"] = rating
        book["ratings"][ticker] = rating
        return

    if action == "buy" and pos.get("status") == "unfunded" and not is_new and not rating_changed:
        pos["rating"] = rating
        book["ratings"][ticker] = rating
        return

    if action is None:
        # Already long, and the rating is still BUY. Drop a stale sell if the rating flipped back.
        pos["rating"] = rating
        pos["side"] = "long"
        pos["status"] = "open"
        book["ratings"][ticker] = rating
        return

    if action == "cash" and not is_new and not held and pos.get("status") in ("open", "unfunded"):
        # HOLD <-> SELL, a settled cash slot, or a buy that could not be funded. No trade.
        pos["rating"] = rating
        pos["side"] = "cash"
        if pos.get("status") == "open" and pos.get("shares") is None:
            pos["shares"] = 0
        book["ratings"][ticker] = rating
        return

    if existing and existing["action"] == action:
        existing["rating"] = rating
        book["pending"].append(existing)
        pos["rating"] = rating
        book["ratings"][ticker] = rating
        return

    reason = "added" if is_new else "rating"
    execute = not_before(now, cfg)
    _queue(book, ticker, rating, action, execute, now, reason)
    pos["rating"] = rating
    book["ratings"][ticker] = rating
    if action == "buy" and pos.get("status") != "open":
        pos["status"] = "pending"
        pos["side"] = "long"
    elif action == "cash":
        pos["status"] = "pending"
        pos["side"] = "cash"


def _remove_ticker(book, ticker, now):
    pos = book["positions"].get(ticker)
    existing = _take_pending(book, ticker)
    if _held(pos):
        if existing and existing["action"] == "sell":
            existing["reason"] = "removed"
            book["pending"].append(existing)
        else:
            cfg = book["config"]
            _queue(book, ticker, pos.get("rating") or cfg["longRating"], "sell", not_before(now, cfg), now, "removed")
        book["ratings"][ticker] = "OUT"
        return
    book["positions"].pop(ticker, None)
    book["ratings"].pop(ticker, None)
    if ticker in book["order"]:
        book["order"].remove(ticker)


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


def _apply(book, order, price, date):
    cfg = book["config"]
    ticker = order["ticker"]
    pos = book["positions"].get(ticker)
    if pos is None:
        return True
    action = order["action"]
    if action == "sell":
        if not _held(pos):
            return True
        if price is None or price <= 0:
            return False
        pnl = pos["shares"] * (price - pos["entryPrice"])
        book["cash"] = money(book["cash"] + pos["notional"] + pnl)
        realized = money(pnl)
        pos["realized"] = money((pos.get("realized") or 0) + pnl)
        _log(book, date=date, ticker=ticker, action="sell", rating=pos.get("rating"),
             price=price, shares=pos["shares"], notional=pos["notional"], realized=realized,
             reason=order.get("reason"))
        pos["shares"] = 0
        pos["notional"] = 0.0
        pos["entryPrice"] = None
        pos["entryDate"] = date
        pos["side"] = "cash"
        pos["status"] = "open"
        if book["ratings"].get(ticker) == "OUT":
            book["positions"].pop(ticker, None)
            book["ratings"].pop(ticker, None)
            if ticker in book["order"]:
                book["order"].remove(ticker)
        return True
    if action == "buy":
        if _held(pos):
            return True
        if price is None or price <= 0:
            return False
        deploy = money(min(float(pos["targetNotional"]), book["cash"]))
        if deploy < float(cfg["minCash"]):
            pos["status"] = "unfunded"
            pos["side"] = "cash"
            pos["shares"] = 0
            pos["notional"] = 0.0
            pos["entryPrice"] = None
            pos["entryDate"] = date
            pos["rating"] = order["rating"]
            _log(book, date=date, ticker=ticker, action="unfunded", rating=order["rating"],
                 price=price, shares=0, notional=0, realized=None, reason=order.get("reason"))
            return True
        shares = deploy / price
        book["cash"] = money(book["cash"] - deploy)
        pos["status"] = "open"
        pos["side"] = "long"
        pos["rating"] = order["rating"]
        pos["shares"] = shares
        pos["notional"] = deploy
        pos["entryPrice"] = price
        pos["entryDate"] = date
        _log(book, date=date, ticker=ticker, action="buy", rating=order["rating"],
             price=price, shares=shares, notional=deploy, realized=None, reason=order.get("reason"))
        return True
    # cash settle: the slot stays in cash. No shares, no short.
    pos["status"] = "open"
    pos["side"] = "cash"
    pos["rating"] = order["rating"]
    pos["shares"] = 0
    pos["notional"] = 0.0
    pos["entryPrice"] = None
    pos["entryDate"] = date
    _log(book, date=date, ticker=ticker, action="cash", rating=order["rating"],
         price=None, shares=0, notional=0, realized=None, reason=order.get("reason"))
    return True


def _dates_of(prices):
    found = set()
    for bars in (prices or {}).values():
        for bar in bars or []:
            if bar.get("date"):
                found.add(bar["date"])
    return sorted(found)


def _first_open(prices, symbol, start, fillable):
    bars = [b for b in (prices.get(symbol) or []) if b.get("date") and b.get("open") not in (None, "")]
    bars.sort(key=lambda b: b["date"])
    for bar in bars:
        if bar["date"] < start:
            continue
        if not fillable(bar["date"]):
            continue
        if bar["open"] is None or bar["open"] <= 0:
            continue
        return bar
    return None


def _exec_point(order, prices, dates, fillable):
    if order["action"] in ("buy", "sell"):
        bar = _first_open(prices, order["ticker"], order["executeOn"], fillable)
        if not bar:
            return None, None
        return bar["date"], bar["open"]
    for date in dates:
        if date >= order["executeOn"] and fillable(date):
            return date, None
    return None, None


def _close_on(prices, symbol, date):
    best = None
    bars = sorted((prices.get(symbol) or []), key=lambda b: b.get("date") or "")
    for bar in bars:
        if not bar.get("date") or bar["date"] > date:
            break
        if bar.get("close") is not None:
            best = bar["close"]
    return best


def portfolio_nav(book, price_of):
    """Cash plus long market value. Shorts are not a state this book can be in."""
    if not book.get("started"):
        return float(book["config"]["startingCash"])
    nav = float(book["cash"])
    positions = book["positions"]
    seq = positions.values() if isinstance(positions, dict) else positions
    for pos in seq:
        if not _held(pos):
            continue
        px = price_of(pos["ticker"])
        if px is None:
            px = pos["entryPrice"]
        nav += float(pos["notional"]) + float(pos["shares"]) * (float(px) - float(pos["entryPrice"]))
    return nav


def _mark(book, prices, date):
    nav = portfolio_nav(book, lambda ticker: _close_on(prices, ticker, date))
    spy = _close_on(prices, book["config"]["benchmark"], date)
    return {"date": date, "nav": money(nav), "spyClose": None if spy is None else float(spy)}


def update_book(book, rows, prices, now, config=None):
    """Apply rating changes and append fills and daily close marks. Does not mutate `book`."""
    cfg = dict(CONFIG)
    if config:
        cfg.update(config)
    now = now_et(now, cfg)
    if book is None:
        book = blank_book(cfg)
    else:
        book = copy.deepcopy(book)
        _import_positions(book)
    _apply_config(book, cfg)
    cfg = book["config"]
    parsed = parse_targets(rows, cfg)
    if not cfg.get("inceptionCount"):
        cfg["inceptionCount"] = len(parsed)
        cfg["inceptionTickers"] = [ticker for ticker, _rating in parsed]
        cfg["slice"] = float(cfg["startingCash"]) / float(cfg["inceptionCount"])
        cfg["weight"] = f"1/{cfg['inceptionCount']}"
        cfg["summary"] = summary_text(cfg)
    current = {ticker for ticker, _rating in parsed}
    known = set(book["ratings"]) | set(book["positions"])
    for ticker, rating in parsed:
        _reconcile(book, ticker, rating, now, is_new=ticker not in known)
    for ticker in list(book["ratings"]):
        if ticker not in current:
            _remove_ticker(book, ticker, now)

    dates = _dates_of(prices)
    fillable = lambda date: session_fillable(date, now, cfg)
    complete = lambda date: session_complete(date, now, cfg)
    if book.get("spyInceptionOpen") is None:
        bar = _first_open(prices or {}, cfg["benchmark"], cfg["inception"], fillable)
        if bar:
            book["spyInceptionOpen"] = float(bar["open"])
    last_mark = book["marks"][-1]["date"] if book["marks"] else None
    visit = [date for date in dates if date >= cfg["inception"]]
    for date in visit:
        if last_mark and date <= last_mark:
            continue
        kept = []
        for order in book["pending"]:
            when, price = _exec_point(order, prices or {}, dates, fillable)
            if when != date:
                kept.append(order)
                continue
            if _apply(book, order, price, date):
                book["started"] = True
            else:
                kept.append(order)
        book["pending"] = kept
        if book.get("started") and complete(date) and not any(mark["date"] == date for mark in book["marks"]):
            book["marks"].append(_mark(book, prices or {}, date))
    cfg["summary"] = summary_text(cfg)
    return book


def _import_positions(book):
    positions = book.get("positions")
    if isinstance(positions, list):
        order = book.get("order") or [pos["ticker"] for pos in positions if pos.get("ticker")]
        book["positions"] = {pos["ticker"]: pos for pos in positions if pos.get("ticker")}
        book["order"] = [ticker for ticker in order if ticker in book["positions"]]
    elif positions is None:
        book["positions"] = {}


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
    """Return (sessions_by_symbol, last_price_by_symbol)."""
    if offline is not None:
        prices = {symbol: list(offline.get(symbol) or []) for symbol in symbols}
        return prices, {}
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
    start = (cfg or CONFIG)["inception"]
    symbols = []
    for row in rows or []:
        ticker = str((row or {}).get("ticker") or "").strip().upper()
        if ticker and ticker not in symbols:
            symbols.append(ticker)
    benchmark = (book or {}).get("config", {}).get("benchmark") or CONFIG["benchmark"]
    if benchmark not in symbols:
        symbols.append(benchmark)
    if book:
        for ticker in list(book.get("positions") or {}):
            if ticker not in symbols:
                symbols.insert(0, ticker)
    prices, _last = build_prices(symbols, start, offline=offline, cfg=CONFIG)
    updated = update_book(book, rows, prices, now_et(now) if not isinstance(now, datetime.datetime) else now)
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
    cfg = book["config"]
    lines = [cfg.get("summary") or summary_text(cfg)]
    lines.append(
        f"cash {book['cash']:.2f}  started {bool(book.get('started'))}  "
        f"pending {len(book.get('pending') or [])}  trades {len(book.get('trades') or [])}  "
        f"marks {len(book.get('marks') or [])}"
    )
    positions = book["positions"]
    seq = [positions[t] for t in book.get("order") or [] if t in positions] if isinstance(positions, dict) else positions
    for pos in seq:
        pending = next((p for p in book.get("pending") or [] if p["ticker"] == pos["ticker"]), None)
        flag = f"  pending {pending['action']} {pending['executeOn']}" if pending else ""
        shares = "—" if pos.get("shares") is None else f"{pos['shares']:.4f}"
        lines.append(
            f"  {pos['ticker']:6} {str(pos.get('rating') or '—'):4} {str(pos.get('side') or '—'):5} "
            f"{str(pos.get('status') or '—'):9} shares {shares}{flag}"
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
    parser = argparse.ArgumentParser(description="Update static/paper_trading.json from price targets and Yahoo daily bars")
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
