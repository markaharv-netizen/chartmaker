#!/usr/bin/env python3
"""Build Chartmaker's daily briefing from the refresh files the app already stores.

The page reads GET /api/briefing, which calls build(). Nothing here hits the network,
and nothing schedules a new job. Refresh scripts append a snapshot when they write:

  python3 scripts/price_targets.py     # appends static/price_targets_history.json
  python3 scripts/refresh_btctop100.py # appends btctop100_history.json

Sentiment, the paper book, and Charts of the day already keep the prior point in
their own files, so the briefing reads those directly.

  python3 scripts/briefing.py          # one-screen summary
  python3 scripts/briefing.py --json   # the payload the page renders
  python3 scripts/briefing.py --self-test

Price targets compare the current sheet with the last snapshot taken before the
7-day window that ends on the snapshot's own as-of date. That is the same 7-day
rule the Price targets tab uses, anchored to the file rather than the clock.
Treasury compares the latest BTCTC snapshot with the one before it and ignores
holding drift under 1 BTC. A holding change whose company as-of date also
changed is an updated filing, not a same-day buy or sale. Sentiment compares
the board with the previous daily point in sentiment_history.json and flags a
name with fewer than 8 classified bull/bear items, the same thin cutoff as
the Over time chart.
"""
import argparse, datetime, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_catalog
from cotd_build import COTD_EXCLUDED

RECENT_DAYS = 7
THIN_ITEMS = 8
MIN_BTC = 1.0
SWING_LIMIT = 8
HISTORY_KEEP = 90

PRICE_FIELDS = (
    "base", "bear", "bull", "consensus", "analysts", "rating",
    "notes", "why", "previousDetail", "baseChangedAt", "previousBase",
)


def repo_of():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def parse_day(value):
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def label_day(day):
    if day is None:
        return ""
    if isinstance(day, str):
        day = parse_day(day)
    if day is None:
        return ""
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def money(value):
    if value is None:
        return "—"
    number = float(value)
    sign = "-" if number < 0 else ""
    return sign + "$" + f"{abs(number):,.2f}"


def num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def same_num(a, b):
    return num(a) == num(b)


def text(value):
    return " ".join(str(value or "").split())


def btc_text(value):
    number = float(value)
    if abs(number - round(number)) < 1e-6:
        return f"{int(round(number)):,}"
    text_value = f"{number:,.4f}".rstrip("0").rstrip(".")
    return text_value


def read(path):
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


# --- price targets -------------------------------------------------------

def snapshot_row(row):
    out = {"ticker": str(row.get("ticker") or "").strip().upper()}
    for key in PRICE_FIELDS:
        out[key] = row.get(key)
    if out["rating"]:
        out["rating"] = str(out["rating"]).strip().upper()
    return out


def snapshot_rows(doc):
    rows = []
    for row in (doc or {}).get("rows") or []:
        item = snapshot_row(row)
        if item["ticker"]:
            rows.append(item)
    return rows


def rows_map(rows):
    if isinstance(rows, dict):
        return rows
    return {row["ticker"]: row for row in rows or [] if row.get("ticker")}


def rows_signature(rows):
    return json.dumps(rows_map(rows), sort_keys=True, default=str)


def price_history_path(repo):
    return os.path.join(repo, "static", "price_targets_history.json")


def load_snapshots(path, stamp_key):
    doc = read(path) or {}
    snaps = []
    for snap in doc.get("snapshots") or []:
        if not isinstance(snap, dict) or not snap.get(stamp_key):
            continue
        snaps.append(snap)
    snaps.sort(key=lambda snap: str(snap.get(stamp_key)))
    return snaps


def archive_price_targets(repo, doc=None, path=None):
    """Append the sheet snapshot when base, consensus, rating, or notes changed.

    A later refresh that rewrites the file with the same targets does not add
    another copy. The briefing still uses the live file's generated time as as-of.
    """
    path = path or price_history_path(repo)
    if doc is None:
        doc = load_json(os.path.join(repo, "static", "price_targets.json"))
    generated = doc.get("generated")
    if not generated:
        return None
    rows = snapshot_rows(doc)
    snaps = load_snapshots(path, "generated")
    if snaps and rows_signature(snaps[-1].get("rows")) == rows_signature(rows):
        return path
    snaps.append({"generated": generated, "rows": rows})
    snaps = snaps[-HISTORY_KEEP:]
    dump_json(path, {
        "note": (
            "Snapshots of Steve's base, bear, bull, street consensus, rating, notes, and why. "
            "scripts/price_targets.py appends one when those fields change. "
            "The daily briefing diffs the current file against the last snapshot before its 7-day window."
        ),
        "snapshots": snaps,
    })
    return path


_NEW_MODEL = re.compile(
    r"New model added\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", re.I)
_DATED_NOTE = re.compile(
    r"\b([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}):\s*(.+)", re.I)
_MONTHS = {name.lower(): i for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], 1)}
for _name, _i in list(_MONTHS.items()):
    _MONTHS.setdefault(_name[:3], _i)


def parse_written_day(phrase):
    match = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s+(\d{4})", str(phrase or ""))
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if not month:
        return None
    try:
        return datetime.date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def later_note(detail):
    """A dated clause that says the model was checked and the valuation did not move."""
    match = _DATED_NOTE.search(str(detail or ""))
    if not match:
        return None
    if not re.search(r"no input changed|valuation unchanged", match.group(2), re.I):
        return None
    return f"{match.group(1)}: update recorded; valuation unchanged."


def consensus_bits(row):
    if num(row.get("consensus")) is None:
        text_value = "no street consensus"
    else:
        text_value = money(row.get("consensus"))
    analysts = row.get("analysts")
    if isinstance(analysts, (int, float)) and not isinstance(analysts, bool):
        text_value += f" ({int(analysts)} analysts)"
    return text_value


def consensus_phrase(row):
    if num(row.get("consensus")) is None:
        return "no street consensus"
    return "consensus " + consensus_bits(row)


def new_model_lines(row):
    rating = row.get("rating") or "—"
    lines = [f"New model. Base {money(row.get('base'))}, {rating}, {consensus_phrase(row)}."]
    why = text(row.get("why"))
    if why:
        lines.append(why)
    note = later_note(row.get("previousDetail"))
    if note:
        lines.append(note)
    return lines


def update_lines(old, new):
    lines = []
    if not same_num(old.get("base"), new.get("base")):
        lines.append(f"Base {money(old.get('base'))} → {money(new.get('base'))}")
    if not same_num(old.get("bear"), new.get("bear")):
        lines.append(f"Bear {money(old.get('bear'))} → {money(new.get('bear'))}")
    if not same_num(old.get("bull"), new.get("bull")):
        lines.append(f"Bull {money(old.get('bull'))} → {money(new.get('bull'))}")
    consensus_moved = not same_num(old.get("consensus"), new.get("consensus"))
    analysts_moved = old.get("analysts") != new.get("analysts")
    if consensus_moved or analysts_moved:
        lines.append(f"Street consensus {consensus_bits(old)} → {consensus_bits(new)}")
    if (old.get("rating") or "") != (new.get("rating") or ""):
        lines.append(f"Rating {old.get('rating') or '—'} → {new.get('rating') or '—'}")
    if text(old.get("why")) != text(new.get("why")):
        lines.append("Why: " + (text(new.get("why")) or "cleared"))
    if text(old.get("notes")) != text(new.get("notes")):
        clip = text(new.get("notes"))
        if len(clip) > 160:
            clip = clip[:157].rstrip() + "…"
        lines.append("Notes updated." + (f" {clip}" if clip else ""))
    return lines


def event_day(row, fallback):
    """When this row's target last moved, from fields the sheet already stores."""
    detail = str((row or {}).get("previousDetail") or "")
    added = _NEW_MODEL.search(detail)
    if added and not parse_day((row or {}).get("baseChangedAt")):
        return parse_written_day(added.group(1)) or fallback
    dated = parse_day((row or {}).get("baseChangedAt"))
    if dated:
        return dated
    found = re.findall(r"\bat\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", detail)
    if found:
        return parse_written_day(found[-1]) or fallback
    return fallback


def price_target_section(doc, snapshots):
    """Changes in the 7 days ending on the price-targets file's as-of date."""
    if not isinstance(doc, dict):
        return {
            "asOf": None, "asOfLabel": "", "since": None, "sinceLabel": "",
            "href": "/?tab=pt", "source": "static/price_targets.json",
            "changes": [], "empty": True,
            "emptyMessage": "Price targets are not available (static/price_targets.json is missing).",
        }
    asof = parse_day(doc.get("generated"))
    current_rows = rows_map(snapshot_rows(doc))
    chain = [snap for snap in snapshots or [] if snap.get("generated")]
    if not chain or rows_signature(chain[-1].get("rows")) != rows_signature(list(current_rows.values())):
        if asof or current_rows:
            chain = list(chain) + [{"generated": (doc or {}).get("generated"), "rows": list(current_rows.values())}]
    since = None
    changes = []
    if asof and chain:
        start = asof - datetime.timedelta(days=RECENT_DAYS)
        baseline = None
        for snap in chain:
            day = parse_day(snap.get("generated"))
            if day is not None and day < start:
                baseline = snap
        if baseline is not None:
            since = parse_day(baseline.get("generated"))
            changes = diff_targets(rows_map(baseline.get("rows")), current_rows, chain, baseline)
        else:
            since = start
            changes = embedded_targets(current_rows, start, asof)
    elif asof:
        since = asof - datetime.timedelta(days=RECENT_DAYS)
        changes = embedded_targets(current_rows, since, asof)
    empty = not changes
    since_label = label_day(since) if since else "the previous snapshot"
    return {
        "asOf": asof.isoformat() if asof else None,
        "asOfLabel": label_day(asof),
        "since": since.isoformat() if since else None,
        "sinceLabel": label_day(since),
        "href": "/?tab=pt",
        "source": "static/price_targets.json",
        "changes": changes,
        "empty": empty,
        "emptyMessage": f"No price target changes since {since_label}." if empty and since_label else None,
    }


def diff_targets(old_rows, new_rows, chain, baseline):
    changes = []
    baseline_day = parse_day(baseline.get("generated"))
    for ticker in list(new_rows):
        new = new_rows[ticker]
        old = old_rows.get(ticker)
        if old is None:
            day = event_day(new, first_seen(chain, ticker, baseline_day))
            changes.append(target_change(ticker, "new", day, new_model_lines(new)))
            continue
        lines = update_lines(old, new)
        if lines:
            if not same_num(old.get("base"), new.get("base")):
                day = event_day(new, first_changed(chain, ticker, old, baseline_day))
            else:
                day = first_changed(chain, ticker, old, baseline_day)
            changes.append(target_change(ticker, "update", day, lines))
            continue
        # A scan line that only says "unchanged" is not a target, rating, or note change.
        note = later_note(new.get("previousDetail"))
        if note and note != later_note(old.get("previousDetail")):
            day = parse_written_day(note) or first_changed(chain, ticker, old, baseline_day)
            changes.append(target_change(ticker, "note", day, [note]))
    for ticker in old_rows:
        if ticker not in new_rows:
            changes.append(target_change(
                ticker, "removed", baseline_day,
                ["Removed from the price-target sheet."]))
    changes.sort(key=lambda item: (item.get("date") or "", item["ticker"]), reverse=True)
    return changes


def first_seen(chain, ticker, after):
    for snap in chain:
        day = parse_day(snap.get("generated"))
        if after and day and day <= after:
            continue
        if ticker in rows_map(snap.get("rows")):
            return day
    return None


def first_changed(chain, ticker, old, after):
    for snap in chain:
        day = parse_day(snap.get("generated"))
        if after and day and day <= after:
            continue
        row = rows_map(snap.get("rows")).get(ticker)
        if row and update_lines(old, row):
            return day
    return None


def embedded_targets(rows, start, asof):
    """Used when no earlier snapshot exists. Reads dates already stored on each row."""
    changes = []
    for ticker, row in rows.items():
        added = parse_written_day(row.get("previousDetail")) if _NEW_MODEL.search(str(row.get("previousDetail") or "")) else None
        changed = parse_day(row.get("baseChangedAt"))
        day = added or changed
        if day is None or day < start or (asof and day > asof):
            note = later_note(row.get("previousDetail"))
            noted = parse_written_day(note) if note else None
            if not (note and noted and start <= noted <= (asof or noted)):
                continue
            changes.append(target_change(ticker, "note", noted, [note]))
            continue
        if added and (changed is None or added >= changed):
            changes.append(target_change(ticker, "new", added, new_model_lines(row)))
            continue
        if changed and num(row.get("base")) is not None and num(row.get("previousBase")) is not None and not same_num(row.get("base"), row.get("previousBase")):
            lines = [f"Base {money(row.get('previousBase'))} → {money(row.get('base'))}"]
            if row.get("rating"):
                lines.append(f"Rating {row['rating']}")
            changes.append(target_change(ticker, "update", changed, lines))
    changes.sort(key=lambda item: (item.get("date") or "", item["ticker"]), reverse=True)
    return changes


def target_change(ticker, kind, day, lines):
    return {
        "ticker": ticker,
        "kind": kind,
        "date": day.isoformat() if day else None,
        "dateLabel": label_day(day),
        "lines": lines,
        "href": "/?tab=pt",
    }


# --- paper book ----------------------------------------------------------

def paper_section(book):
    href = "/?tab=paper"
    if not isinstance(book, dict):
        return {"href": href, "source": "static/paper_trading.json", "empty": True,
                "message": "Paper trading book is not available."}
    generated = parse_day(book.get("generated"))
    if book.get("waiting") or not book.get("started"):
        return {
            "href": href,
            "source": "static/paper_trading.json",
            "asOf": generated.isoformat() if generated else None,
            "asOfLabel": label_day(generated),
            "started": False,
            "empty": True,
            "message": "Paper book has not started. It is waiting for weights.",
        }
    marks = sorted(
        [mark for mark in book.get("marks") or [] if parse_day(mark.get("date")) and mark.get("nav") is not None],
        key=lambda mark: mark["date"])
    start_cash = num((book.get("config") or {}).get("startingCash"))
    if not marks:
        nav = None
        message = "Paper book has started, but it has no saved marks yet."
        return {"href": href, "source": "static/paper_trading.json",
                "asOf": generated.isoformat() if generated else None, "asOfLabel": label_day(generated),
                "started": True, "nav": nav, "empty": True, "message": message}
    last = marks[-1]
    nav = float(last["nav"])
    mark_day = parse_day(last["date"])
    payload = {
        "href": href,
        "source": "static/paper_trading.json",
        "asOf": (generated or mark_day).isoformat(),
        "asOfLabel": label_day(generated or mark_day),
        "markDate": mark_day.isoformat(),
        "markLabel": label_day(mark_day),
        "nav": round(nav, 2),
        "started": True,
        "empty": False,
    }
    if start_cash:
        payload["sinceStart"] = round(nav - start_cash, 2)
        payload["sinceStartPct"] = (nav / start_cash) - 1
    if len(marks) >= 2:
        prev = marks[-2]
        prev_nav = float(prev["nav"])
        delta = nav - prev_nav
        payload["prevMarkDate"] = parse_day(prev["date"]).isoformat()
        payload["prevMarkLabel"] = label_day(parse_day(prev["date"]))
        payload["change"] = round(delta, 2)
        payload["changePct"] = (delta / prev_nav) if prev_nav else None
        payload["message"] = paper_message(payload)
    else:
        payload["message"] = f"Paper NAV {money(nav)} marked {label_day(mark_day)}. No earlier mark to compare."
    return payload


def paper_message(payload):
    nav = money(payload["nav"])
    mark = payload["markLabel"]
    change = payload.get("change")
    if change is None:
        return f"Paper NAV {nav} marked {mark}."
    prev = payload.get("prevMarkLabel")
    if abs(change) < 0.005:
        moved = f"unchanged from {prev}"
    else:
        direction = "up" if change > 0 else "down"
        pct = payload.get("changePct")
        pct_text = ""
        if pct is not None:
            pct_text = f" ({pct * 100:+.2f}%)"
        moved = f"{direction} {money(abs(change))}{pct_text} from {prev}"
    return f"Paper NAV {nav} on {mark}, {moved}."


# --- sentiment -----------------------------------------------------------

def board_day(doc):
    asof = (doc or {}).get("asOf") or ""
    return parse_day(asof)


def sentiment_section(board, history):
    href = "/?tab=sent"
    tickers = (board or {}).get("tickers") if isinstance(board, dict) else None
    asof = board_day(board) if isinstance(board, dict) else None
    daily = {}
    if isinstance(history, dict):
        daily = history.get("daily") or {}
    days = sorted(day for day in daily if parse_day(day))
    previous = None
    if asof:
        prior = [day for day in days if parse_day(day) < asof]
        previous = prior[-1] if prior else None
    elif days:
        previous = days[-2] if len(days) > 1 else None
        if days:
            asof = parse_day(days[-1])
    base = {
        "asOf": asof.isoformat() if asof else None,
        "asOfLabel": label_day(asof),
        "previous": previous,
        "previousLabel": label_day(previous),
        "href": href,
        "source": "sentiment.json",
        "history": "sentiment_history.json",
        "thinBelow": THIN_ITEMS,
        "swings": [],
        "thin": [],
        "entered": [],
        "left": [],
    }
    if not tickers:
        base["empty"] = True
        base["emptyMessage"] = "Sentiment board is not available."
        return base
    if not previous:
        base["empty"] = True
        when = label_day(asof) if asof else "the latest board"
        base["emptyMessage"] = f"No earlier sentiment board to compare. Latest board is {when}."
        return base
    prior = daily.get(previous) or {}
    swings, thin, entered = [], [], []
    seen = set()
    for row in tickers:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        seen.add(symbol)
        classified = row.get("classified")
        if classified is None:
            classified = (row.get("n_bull") or 0) + (row.get("n_bear") or 0)
        bull = row.get("bull")
        prev = prior.get(symbol)
        item = {
            "symbol": symbol,
            "name": row.get("name") or symbol,
            "bull": bull,
            "previous": prev,
            "classified": classified,
            "nEff": row.get("n_eff"),
            "thin": classified is not None and classified < THIN_ITEMS,
            "href": "/?tab=sent&stickers=" + symbol,
        }
        if prev is None or bull is None:
            item["points"] = None
            item["kind"] = "new"
            (thin if item["thin"] else entered).append(item)
            continue
        item["points"] = round((float(bull) - float(prev)) * 100, 1)
        item["kind"] = "swing"
        (thin if item["thin"] else swings).append(item)
    left = [symbol for symbol in prior if symbol not in seen]
    swings.sort(key=lambda item: abs(item["points"]), reverse=True)
    thin.sort(key=lambda item: abs(item.get("points") or 0), reverse=True)
    extra = max(0, len(swings) - SWING_LIMIT)
    base["swings"] = swings[:SWING_LIMIT]
    base["moreSwings"] = extra
    base["thin"] = thin
    base["entered"] = entered
    base["left"] = sorted(left)
    base["empty"] = not swings and not thin and not entered
    if base["empty"]:
        base["emptyMessage"] = f"No sentiment change since {label_day(previous)}."
    else:
        base["emptyMessage"] = None
    return base


# --- treasury ------------------------------------------------------------

def treasury_history_path(repo):
    return os.path.join(repo, "btctop100_history.json")


def treasury_companies(doc):
    out = []
    for company in (doc or {}).get("companies") or []:
        ticker = str(company.get("ticker") or "").strip().upper()
        name = str(company.get("name") or "").strip()
        if company.get("btc") is None or not (ticker or name):
            continue
        out.append({
            "ticker": ticker,
            "name": name,
            "btc": company.get("btc"),
            "rank": company.get("rank"),
            "change": company.get("change"),
            "asOf": company.get("asOf"),
            "notes": company.get("notes"),
            "source": company.get("source"),
            "sourceLabel": company.get("sourceLabel"),
        })
    return out


def archive_treasury(doc, path):
    """Append one BTCTC snapshot. The same fetchedAt is not stored twice."""
    fetched = (doc or {}).get("fetchedAt")
    if not fetched:
        return None
    snaps = load_snapshots(path, "fetchedAt")
    if any(snap.get("fetchedAt") == fetched for snap in snaps):
        return path
    snaps.append({"fetchedAt": fetched, "companies": treasury_companies(doc)})
    snaps = snaps[-HISTORY_KEEP:]
    dump_json(path, {
        "note": (
            "Snapshots appended by scripts/refresh_btctop100.py. "
            "The daily briefing diffs the latest snapshot against the one before it. "
            "Each company keeps its as-of date, note, and source. "
            "Holding drift under 1 BTC is treated as sheet rounding and is not listed."
        ),
        "snapshots": snaps,
    })
    return path


def asof_month(value):
    """YYYY-MM for a holding as-of, or None. YYYY-MM and YYYY-MM-DD in that month match."""
    text_value = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", text_value):
        return text_value[:7]
    return None


def asof_changed(previous, current):
    """True when both as-of dates are known and they are not the same month."""
    old, new = asof_month(previous), asof_month(current)
    if not old or not new:
        return False
    return old != new


def short_asof(value):
    """'2026-11-09' -> 'Nov 9'. '2026-06' -> 'Jun 2026'. Empty stays empty."""
    text_value = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text_value):
        day = datetime.date.fromisoformat(text_value)
        return f"{day.strftime('%b')} {day.day}"
    if re.fullmatch(r"\d{4}-\d{2}", text_value):
        year, month = text_value.split("-")
        day = datetime.date(int(year), int(month), 1)
        return f"{day.strftime('%b')} {year}"
    return text_value


def holding_span(btc, asof):
    amount = btc_text(btc) if btc is not None else "—"
    label = short_asof(asof)
    return f"{amount} ({label})" if label else amount


def company_key(company):
    ticker = str(company.get("ticker") or "").strip().upper()
    if ticker:
        return "t:" + ticker
    return "n:" + str(company.get("name") or "").strip().lower()


def treasury_section(doc, snapshots):
    href = "/?tab=tre"
    fetched = (doc or {}).get("fetchedAt") if isinstance(doc, dict) else None
    asof = parse_day(fetched)
    companies = treasury_companies(doc or {})
    chain = list(snapshots or [])
    if fetched and not any(snap.get("fetchedAt") == fetched for snap in chain):
        chain.append({"fetchedAt": fetched, "companies": companies})
    chain = [snap for snap in chain if snap.get("fetchedAt")]
    chain.sort(key=lambda snap: snap["fetchedAt"])
    base = {
        "asOf": fetched,
        "asOfLabel": label_day(asof),
        "href": href,
        "source": "btctop100.json",
        "minBtc": MIN_BTC,
        "changes": [],
        "filings": [],
        "entered": [],
        "left": [],
    }
    if len(chain) < 2:
        prior = None
    else:
        prior = chain[-2]
    latest = chain[-1] if chain else None
    if prior is None:
        base["empty"] = True
        when = label_day(asof) if asof else "the latest treasury file"
        base["emptyMessage"] = (
            f"No earlier treasury snapshot to compare. Holdings as of {when}. "
            "The next BTCTC refresh records changes against this snapshot."
        )
        base["previous"] = None
        base["previousLabel"] = None
        base["filings"] = []
        return base
    previous_day = parse_day(prior.get("fetchedAt"))
    base["previous"] = prior.get("fetchedAt")
    base["previousLabel"] = label_day(previous_day)
    old = {company_key(company): company for company in prior.get("companies") or []}
    if fetched and latest and latest.get("fetchedAt") == fetched:
        latest_companies = companies
    else:
        latest_companies = (latest or {}).get("companies") or []
    new = {company_key(company): company for company in latest_companies}
    changes, entered, left = [], [], []
    for key, company in new.items():
        before = old.get(key)
        if before is None:
            entered.append({
                "ticker": company.get("ticker"),
                "name": company.get("name"),
                "btc": company.get("btc"),
                "rank": company.get("rank"),
                "href": href,
            })
            continue
        if abs(float(company["btc"]) - float(before["btc"])) + 1e-9 < MIN_BTC:
            continue
        delta = float(company["btc"]) - float(before["btc"])
        filing = asof_changed(before.get("asOf"), company.get("asOf"))
        changes.append({
            "ticker": company.get("ticker"),
            "name": company.get("name"),
            "btc": company.get("btc"),
            "previous": before.get("btc"),
            "delta": delta,
            "rank": company.get("rank"),
            "previousRank": before.get("rank"),
            "change": company.get("change"),
            "asOf": company.get("asOf"),
            "previousAsOf": before.get("asOf"),
            "asOfLabel": short_asof(company.get("asOf")),
            "previousAsOfLabel": short_asof(before.get("asOf")),
            "holdingLine": f"{holding_span(before.get('btc'), before.get('asOf'))} to {holding_span(company.get('btc'), company.get('asOf'))}",
            "filingUpdate": filing,
            "note": company.get("notes") or "",
            "source": company.get("source") or "",
            "sourceLabel": company.get("sourceLabel") or "",
            "href": href,
        })
    for key, company in old.items():
        if key not in new:
            left.append({
                "ticker": company.get("ticker"),
                "name": company.get("name"),
                "btc": company.get("btc"),
                "rank": company.get("rank"),
                "href": href,
            })
    # Same-period moves stay above updated filings so a stale as-of replacement
    # (MARA's June total replaced by a November derived figure) is not the headline.
    changes.sort(key=lambda item: (item["filingUpdate"], -abs(item["delta"])))
    entered.sort(key=lambda item: item.get("rank") or 999)
    left.sort(key=lambda item: item.get("rank") or 999)
    base["changes"] = [item for item in changes if not item["filingUpdate"]]
    base["filings"] = [item for item in changes if item["filingUpdate"]]
    base["entered"] = entered
    base["left"] = left
    base["empty"] = not changes and not entered and not left
    if base["empty"]:
        base["emptyMessage"] = (
            f"No BTC holding changes of {int(MIN_BTC)} BTC or more, and no companies entered or left the top 100, "
            f"since {label_day(previous_day)}."
        )
    else:
        base["emptyMessage"] = None
    return base


# --- charts of the day ---------------------------------------------------

_SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_GID = re.compile(r"[#&?]gid=(\d+)")
_IMAGE = re.compile(r"^cotd/\d{4}-\d{2}-\d{2}/\d{2}\.png$")


def chart_excluded(item):
    url = str(item.get("sheet_url") or "")
    gid_match = _GID.search(url)
    id_match = _SHEET_ID.search(url)
    if not gid_match or not id_match:
        return False
    gid = int(gid_match.group(1))
    pair = sheets_catalog.BY_ID.get(id_match.group(1))
    if not pair:
        return False
    book = pair[0]
    if sheets_catalog.tab_excluded(book, gid):
        return True
    return (book, gid) in COTD_EXCLUDED


def charts_section(doc):
    href = "/?tab=cotd"
    if not isinstance(doc, dict):
        return {
            "href": href, "source": "static/cotd/today.json", "items": [], "empty": True,
            "emptyMessage": "Charts of the day are not available (static/cotd/today.json is missing).",
        }
    day = parse_day(doc.get("date"))
    items = []
    for item in doc.get("items") or []:
        if not isinstance(item, dict) or chart_excluded(item):
            continue
        url = item.get("sheet_url") or ""
        safe_url = url if str(url).startswith("https://") else href
        image = item.get("image") or ""
        safe_image = image if _IMAGE.match(str(image)) else ""
        items.append({
            "title": item.get("title") or item.get("tab") or "Chart",
            "sheet": item.get("sheet") or "",
            "tab": item.get("tab") or "",
            "image": safe_image,
            "href": safe_url,
            "tabHref": href,
            "kind": item.get("kind") or "chart",
        })
    empty = not items
    when = label_day(day) if day else "this edition"
    return {
        "asOf": day.isoformat() if day else None,
        "asOfLabel": label_day(day),
        "theme": text(doc.get("theme")),
        "href": href,
        "source": "static/cotd/today.json",
        "items": items,
        "empty": empty,
        "emptyMessage": f"No charts of the day for {when}." if empty else None,
    }


# --- assemble ------------------------------------------------------------

def build(repo=None):
    repo = repo or repo_of()
    targets = read(os.path.join(repo, "static", "price_targets.json"))
    target_history = load_snapshots(price_history_path(repo), "generated")
    paper = read(os.path.join(repo, "static", "paper_trading.json"))
    board = read(os.path.join(repo, "sentiment.json"))
    history = read(os.path.join(repo, "sentiment_history.json"))
    treasury = read(os.path.join(repo, "btctop100.json"))
    treasury_history = load_snapshots(treasury_history_path(repo), "fetchedAt")
    charts = read(os.path.join(repo, "static", "cotd", "today.json"))
    return {
        "priceTargets": price_target_section(targets, target_history),
        "paper": paper_section(paper),
        "sentiment": sentiment_section(board, history),
        "treasury": treasury_section(treasury, treasury_history),
        "charts": charts_section(charts),
    }


def summary(doc):
    lines = []
    targets = doc["priceTargets"]
    lines.append(f"Price targets as of {targets.get('asOfLabel') or '—'} (since {targets.get('sinceLabel') or '—'})")
    if targets.get("empty"):
        lines.append("  " + targets["emptyMessage"])
    for change in targets.get("changes") or []:
        lines.append(f"  {change['ticker']} {change.get('dateLabel') or ''} [{change['kind']}]")
        for line in change["lines"]:
            lines.append("    " + line)
    paper = doc["paper"]
    lines.append("Paper: " + (paper.get("message") or ""))
    sent = doc["sentiment"]
    lines.append(f"Sentiment as of {sent.get('asOfLabel') or '—'} vs {sent.get('previousLabel') or '—'}")
    if sent.get("empty"):
        lines.append("  " + sent["emptyMessage"])
    for swing in sent.get("swings") or []:
        lines.append(f"  {swing['symbol']} {swing['points']:+.1f} pts ({swing['classified']} items)")
    for swing in sent.get("thin") or []:
        points = "n/a" if swing.get("points") is None else f"{swing['points']:+.1f} pts"
        lines.append(f"  thin {swing['symbol']} {points} ({swing['classified']} items)")
    tre = doc["treasury"]
    lines.append(f"Treasury as of {tre.get('asOfLabel') or '—'} vs {tre.get('previousLabel') or '—'}")
    if tre.get("empty"):
        lines.append("  " + tre["emptyMessage"])
    for change in tre.get("changes") or []:
        lines.append(f"  {change['ticker']} {change['delta']:+.2f} BTC")
    for change in tre.get("filings") or []:
        lines.append(f"  filing {change['ticker']} {change.get('holdingLine')}")
    for company in tre.get("entered") or []:
        lines.append(f"  entered {company['ticker']}")
    for company in tre.get("left") or []:
        lines.append(f"  left {company['ticker']}")
    charts = doc["charts"]
    lines.append(f"Charts of the day {charts.get('asOfLabel') or '—'} ({len(charts.get('items') or [])})")
    if charts.get("empty"):
        lines.append("  " + charts["emptyMessage"])
    return "\n".join(lines)


def self_test():
    import unittest
    tests = os.path.join(repo_of(), "tests")
    suite = unittest.defaultTestLoader.discover(tests, pattern="test_briefing.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    print("self-test ok")


def main():
    parser = argparse.ArgumentParser(description="Build the daily briefing from Chartmaker refresh files")
    parser.add_argument("--repo", default=repo_of())
    parser.add_argument("--json", action="store_true", help="print the full briefing JSON")
    parser.add_argument("--write", metavar="PATH", help="write the briefing JSON to PATH")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    doc = build(args.repo)
    if args.write:
        dump_json(args.write, doc)
        print(f"wrote {args.write}")
    if args.json:
        json.dump(doc, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    print(summary(doc))


if __name__ == "__main__":
    main()
