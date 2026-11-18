#!/usr/bin/env python3
"""Retire one workbook tab and drop it from the published library.

Reads and writes config/retired_charts.json. Dry-run is the default: it prints
the record and the files it would remove and does not write. Pass --apply to
add the record and delete the published copies.

Removal is the whole tab (book + gid). chartId is stored on the record when
you pass it. The catalog does not keep chartId, so a chart id does not leave
the rest of that tab in the library.

Today's Charts of the day cards for that gid are removed. Other cards keep
their filenames and numbers.

Usage:
  python3 scripts/retire_chart.py --book bot1 --gid 12345
  python3 scripts/retire_chart.py --book bot1 --gid 12345 --chart-id 99 --apply
"""
import argparse, datetime, json, os, re, sys
from zoneinfo import ZoneInfo

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import retired_charts

BOOKS = retired_charts.BOOKS
DATA_FILE = re.compile(r"^(\d+)(?:_([2-9]|[1-9]\d))?\.json$")
PNG_FILE = re.compile(r"^(\d+)(?:_([2-9]|[1-9]\d))?\.png$")
GID_IN_URL = re.compile(r"[#&?]gid=(\d+)")


def today_et():
    return datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _under(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    if os.path.commonpath([root, path]) != root:
        return None
    return path


def _write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _gid_of_card(card):
    if not isinstance(card, dict):
        return None
    if card.get("gid") not in (None, ""):
        try:
            return int(card.get("gid"))
        except (TypeError, ValueError):
            return None
    m = GID_IN_URL.search(str(card.get("sheet_url") or ""))
    if not m:
        return None
    return int(m.group(1))


def _card_matches(card, book, gid):
    found = _gid_of_card(card)
    if found != int(gid):
        return False
    card_book = str(card.get("book") or "").strip()
    if card_book and card_book != book:
        return False
    return True


def build_record(book, gid, chart_id, tab, chart_title, reason, drive_pngs, retired_on):
    if book not in BOOKS:
        raise SystemExit("book must be bot1, bot2, or bot3")
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        raise SystemExit("gid must be an integer")
    if gid < 0:
        raise SystemExit("gid must be an integer")
    if reason not in retired_charts.REASONS:
        raise SystemExit("reason must be retired or excluded")
    if chart_id is not None:
        try:
            chart_id = int(chart_id)
        except (TypeError, ValueError):
            raise SystemExit("chart-id must be an integer")
    pngs = []
    for name in drive_pngs or []:
        base = os.path.basename(str(name).strip())
        if not base or base in (".", "..") or "/" in str(name) or "\\" in str(name):
            raise SystemExit("drive png must be a file name, not a path")
        if base not in pngs:
            pngs.append(base)
    return {
        "book": book,
        "gid": gid,
        "chartId": chart_id,
        "tab": "" if not tab else str(tab),
        "chartTitle": None if not chart_title else str(chart_title),
        "retiredOn": retired_on,
        "reason": reason,
        "drivePngs": pngs,
        "scope": "library",
    }


def _matching_files(folder, gid, pattern):
    if not os.path.isdir(folder):
        return []
    found = []
    for fn in sorted(os.listdir(folder)):
        m = pattern.fullmatch(fn)
        if m and int(m.group(1)) == int(gid):
            found.append(os.path.join(folder, fn))
    return found


def asset_paths(repo, book, gid):
    """Published files for this tab. Paths stay inside the repo."""
    gid = int(gid)
    rels = []
    sheets = os.path.join(repo, "static", "sheets")
    for folder, pattern in (
        (os.path.join(sheets, "full", book), PNG_FILE),
        (os.path.join(sheets, "thumbs", book), re.compile(r"^(\d+)\.jpg$")),
        (os.path.join(sheets, "data", book), DATA_FILE),
    ):
        for path in _matching_files(folder, gid, pattern):
            if _under(repo, path):
                rels.append(os.path.relpath(path, repo))
    return rels


def catalog_hits(doc, book, gid):
    hits = []
    for tab in (doc or {}).get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        try:
            tab_gid = int(tab.get("gid"))
        except (TypeError, ValueError):
            continue
        if str(tab.get("book") or "") == book and tab_gid == int(gid):
            hits.append(tab)
    return hits


def _drop_cards(rows, book, gid):
    kept, removed = [], []
    for card in rows or []:
        if _card_matches(card, book, gid):
            removed.append(card)
        else:
            kept.append(card)
    return kept, removed


def cotd_plan(repo, book, gid):
    """Cards in today's edition that this retirement would drop, plus image paths."""
    today_path = os.path.join(repo, "static", "cotd", "today.json")
    if not os.path.isfile(today_path):
        return {"today": None, "cards_path": None, "removed": [], "images": []}
    with open(today_path, encoding="utf-8") as f:
        today = json.load(f)
    date = str((today or {}).get("date") or "")
    removed = []
    images = []
    if isinstance(today, dict):
        for key in ("items", "picks", "random"):
            _kept, gone = _drop_cards(today.get(key) if isinstance(today.get(key), list) else [], book, gid)
            removed.extend(gone)
    cards_path = os.path.join(repo, "static", "cotd", date, "cards.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None
    if cards_path and os.path.isfile(cards_path):
        with open(cards_path, encoding="utf-8") as f:
            cards = json.load(f)
        if isinstance(cards, dict):
            for key in ("items", "picks", "random"):
                _kept, gone = _drop_cards(cards.get(key) if isinstance(cards.get(key), list) else [], book, gid)
                removed.extend(gone)
    seen = set()
    for card in removed:
        image = str(card.get("image") or "")
        if not image or image in seen or ".." in image.split("/"):
            continue
        path = os.path.join(repo, "static", image)
        if _under(os.path.join(repo, "static", "cotd"), path):
            seen.add(image)
            if os.path.isfile(path):
                images.append(os.path.relpath(path, repo))
    return {"today": today_path, "cards_path": cards_path if cards_path and os.path.isfile(cards_path) else None,
            "removed": removed, "images": images}


def plan(repo, record, config_path=None):
    """Describe the write. Does not touch the disk."""
    book, gid = record["book"], int(record["gid"])
    config_path = retired_charts.config_path(config_path or os.path.join(repo, "config", "retired_charts.json"))
    existing = retired_charts.find(book, gid, record.get("chartId"), config_path) if os.path.isfile(config_path) else None
    catalog_path = os.path.join(repo, "static", "sheets", "catalog.json")
    catalog_tabs = []
    if os.path.isfile(catalog_path):
        with open(catalog_path, encoding="utf-8") as f:
            catalog_tabs = catalog_hits(json.load(f), book, gid)
    index_path = os.path.join(repo, "static", "sheets", "data", "index.json")
    index_rows = []
    if os.path.isfile(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
        for row in index.get("charts") or []:
            try:
                if row.get("book") == book and int(row.get("gid")) == gid:
                    index_rows.append(row)
            except (TypeError, ValueError):
                continue
    cotd = cotd_plan(repo, book, gid)
    return {
        "record": record,
        "already": existing is not None,
        "config": config_path,
        "assets": asset_paths(repo, book, gid),
        "catalog": [t.get("tab") or "" for t in catalog_tabs],
        "index": len(index_rows),
        "cotd_cards": len(cotd["removed"]),
        "cotd_images": cotd["images"],
    }


def _apply_catalog(repo, book, gid):
    path = os.path.join(repo, "static", "sheets", "catalog.json")
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    tabs = doc.get("tabs") if isinstance(doc.get("tabs"), list) else []
    kept = []
    removed = 0
    for tab in tabs:
        if _is_tab(tab, book, gid):
            removed += 1
        else:
            kept.append(tab)
    if removed:
        doc["tabs"] = kept
        _write_json(path, doc)
    return removed


def _is_tab(tab, book, gid):
    if not isinstance(tab, dict):
        return False
    try:
        return str(tab.get("book") or "") == book and int(tab.get("gid")) == int(gid)
    except (TypeError, ValueError):
        return False


def _apply_index(repo, book, gid):
    path = os.path.join(repo, "static", "sheets", "data", "index.json")
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    rows = doc.get("charts") if isinstance(doc.get("charts"), list) else []
    kept = []
    removed = 0
    for row in rows:
        try:
            hit = isinstance(row, dict) and row.get("book") == book and int(row.get("gid")) == int(gid)
        except (TypeError, ValueError):
            hit = False
        if hit:
            removed += 1
        else:
            kept.append(row)
    if removed:
        doc["charts"] = kept
        _write_json(path, doc)
    return removed


def _apply_cotd(repo, book, gid):
    """Drop today's cards for this gid. Leave every other card's filename alone."""
    today_path = os.path.join(repo, "static", "cotd", "today.json")
    if not os.path.isfile(today_path):
        return 0, []
    with open(today_path, encoding="utf-8") as f:
        today = json.load(f)
    removed_cards = []
    if isinstance(today, dict):
        changed = False
        for key in ("items", "picks", "random"):
            rows = today.get(key)
            if not isinstance(rows, list):
                continue
            kept, gone = _drop_cards(rows, book, gid)
            if gone:
                today[key] = kept
                removed_cards.extend(gone)
                changed = True
        if changed:
            _write_json(today_path, today)
    date = str((today or {}).get("date") or "") if isinstance(today, dict) else ""
    cards_path = os.path.join(repo, "static", "cotd", date, "cards.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else ""
    if cards_path and os.path.isfile(cards_path):
        with open(cards_path, encoding="utf-8") as f:
            cards = json.load(f)
        if isinstance(cards, dict):
            changed = False
            for key in ("items", "picks", "random"):
                rows = cards.get(key)
                if not isinstance(rows, list):
                    continue
                kept, gone = _drop_cards(rows, book, gid)
                if gone:
                    cards[key] = kept
                    removed_cards.extend(gone)
                    changed = True
            if changed:
                _write_json(cards_path, cards)
    images = []
    seen = set()
    for card in removed_cards:
        image = str(card.get("image") or "")
        if not image or image in seen or ".." in image.split("/"):
            continue
        seen.add(image)
        path = os.path.join(repo, "static", image)
        safe = _under(os.path.join(repo, "static", "cotd"), path)
        if safe and os.path.isfile(safe):
            os.remove(safe)
            images.append(os.path.relpath(safe, repo))
    return len(removed_cards), images


def apply(repo, record, config_path=None):
    """Add the record and delete published copies. Returns a short result dict."""
    config_path = retired_charts.config_path(config_path or os.path.join(repo, "config", "retired_charts.json"))
    if os.path.isfile(config_path):
        doc = retired_charts.load(config_path)
        rows = list(doc["charts"])
        note = doc.get("note")
    else:
        rows = []
        note = ""
    book, gid = record["book"], int(record["gid"])
    replaced = False
    stored = record
    for i, row in enumerate(rows):
        if row["book"] == book and row["gid"] == gid and row["chartId"] == record.get("chartId"):
            merged = dict(row)
            for key, value in record.items():
                if key in ("tab", "chartTitle") and not value:
                    continue
                if key == "drivePngs" and not value:
                    continue
                merged[key] = value
            rows[i] = merged
            stored = merged
            replaced = True
            break
    if not replaced:
        rows.append(record)
    retired_charts.save(rows, config_path, note=note)
    removed_files = []
    for rel in asset_paths(repo, book, gid):
        path = os.path.join(repo, rel)
        if os.path.isfile(path):
            os.remove(path)
            removed_files.append(rel)
    catalog_n = _apply_catalog(repo, book, gid)
    index_n = _apply_index(repo, book, gid)
    cotd_n, cotd_images = _apply_cotd(repo, book, gid)
    removed_files.extend(cotd_images)
    return {
        "record": stored,
        "replaced": replaced,
        "files": removed_files,
        "catalog": catalog_n,
        "index": index_n,
        "cotd": cotd_n,
    }


def format_plan(info, applied=None):
    rec = (applied or {}).get("record") or info["record"]
    lines = []
    verb = "updated" if applied and applied.get("replaced") else ("added" if applied else "would add")
    if info["already"] and not applied:
        verb = "already recorded; would refresh"
    lines.append("%s %s gid %s chartId %s (%s, %s)" % (
        verb, rec["book"], rec["gid"], rec["chartId"] if rec["chartId"] is not None else "null",
        rec["reason"], rec.get("tab") or "untitled tab"))
    lines.append("config: %s" % info["config"])
    if info["catalog"]:
        lines.append("catalog tabs: %s" % ", ".join(info["catalog"]))
    else:
        lines.append("catalog tabs: none")
    lines.append("asset files: %d" % len(info["assets"]))
    for rel in info["assets"]:
        lines.append("  " + rel)
    lines.append("data index rows: %d" % info["index"])
    lines.append("today's CotD cards: %d" % info["cotd_cards"])
    for rel in info["cotd_images"]:
        lines.append("  " + rel)
    if applied:
        lines.append("removed %d files, %d catalog tabs, %d index rows, %d CotD cards" % (
            len(applied["files"]), applied["catalog"], applied["index"], applied["cotd"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Retire a chart tab. Dry-run unless --apply.")
    ap.add_argument("--book", required=True)
    ap.add_argument("--gid", required=True, type=int)
    ap.add_argument("--chart-id", type=int, default=None)
    ap.add_argument("--tab", default="")
    ap.add_argument("--chart-title", default="")
    ap.add_argument("--reason", default="retired", choices=("retired", "excluded"))
    ap.add_argument("--drive-png", action="append", default=[])
    ap.add_argument("--date", default=None, help="YYYY-MM-DD. Default: today in America/New_York.")
    ap.add_argument("--repo", default=retired_charts.ROOT)
    ap.add_argument("--apply", action="store_true", help="Write the config and delete published files.")
    args = ap.parse_args(argv)
    retired_on = args.date or today_et()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", retired_on):
        ap.error("date must be YYYY-MM-DD")
    record = build_record(args.book, args.gid, args.chart_id, args.tab, args.chart_title,
                          args.reason, args.drive_png, retired_on)
    repo = os.path.abspath(args.repo)
    info = plan(repo, record)
    applied = apply(repo, record) if args.apply else None
    print(format_plan(info, applied))
    if not args.apply:
        print("dry-run only; pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
