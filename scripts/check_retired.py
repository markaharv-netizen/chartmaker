#!/usr/bin/env python3
"""Fail if a retired chart is still published.

Checks static/sheets/catalog.json, full PNGs, thumbnails, interactive data
bundles (including the data index), and the current Charts of the day edition.
Scope "cotd" rows are not retirements: they may stay in the library. A library
row (reason retired or excluded) must be absent from all of those places.

Usage:
  python3 scripts/check_retired.py
  python3 scripts/check_retired.py --repo /path/to/chartmaker
"""
import argparse, json, os, re, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import retired_charts

DATA_FILE = re.compile(r"^(\d+)(?:_([2-9]|[1-9]\d))?\.json$")
PNG_FILE = re.compile(r"^(\d+)(?:_([2-9]|[1-9]\d))?\.png$")
JPG_FILE = re.compile(r"^(\d+)\.jpg$")
GID_IN_URL = re.compile(r"[#&?]gid=(\d+)")


def _gid_of_card(card):
    if not isinstance(card, dict):
        return None, ""
    book = str(card.get("book") or "").strip()
    if card.get("gid") not in (None, ""):
        try:
            return int(card.get("gid")), book
        except (TypeError, ValueError):
            return None, book
    m = GID_IN_URL.search(str(card.get("sheet_url") or ""))
    if not m:
        return None, book
    return int(m.group(1)), book


def _scan_dir(folder, pattern, pairs, label, problems):
    if not os.path.isdir(folder):
        return
    book = os.path.basename(folder)
    for fn in sorted(os.listdir(folder)):
        m = pattern.fullmatch(fn)
        if not m:
            continue
        gid = int(m.group(1))
        if (book, gid) in pairs:
            kind = os.path.basename(os.path.dirname(folder))
            problems.append("%s: static/sheets/%s/%s/%s" % (label, kind, book, fn))


def _cotd_cards(doc):
    if not isinstance(doc, dict):
        return []
    rows = []
    for key in ("items", "picks", "random"):
        block = doc.get(key)
        if isinstance(block, list):
            rows.extend(block)
    return rows


def problems(repo, path=None):
    """Human-readable hits. Empty means the published tree is clean."""
    repo = os.path.abspath(repo)
    config = path or os.path.join(repo, "config", "retired_charts.json")
    rows = retired_charts.library_rows(config)
    pairs = {(row["book"], row["gid"]) for row in rows}
    found = []
    catalog_path = os.path.join(repo, "static", "sheets", "catalog.json")
    if os.path.isfile(catalog_path):
        with open(catalog_path, encoding="utf-8") as f:
            catalog = json.load(f)
        for tab in catalog.get("tabs") or []:
            if not isinstance(tab, dict):
                continue
            book = str(tab.get("book") or "")
            try:
                gid = int(tab.get("gid"))
            except (TypeError, ValueError):
                continue
            if (book, gid) in pairs:
                found.append("catalog: %s gid %s (%s)" % (book, gid, tab.get("tab") or ""))
            for chart in tab.get("chartList") or []:
                cid = chart.get("chartId") if isinstance(chart, dict) else None
                if cid is None:
                    continue
                for row in rows:
                    if row["book"] == book and row["gid"] == gid and row["chartId"] == cid:
                        found.append("catalog chartId: %s gid %s chartId %s" % (book, gid, cid))
    sheets = os.path.join(repo, "static", "sheets")
    for book in sorted({book for book, _gid in pairs}):
        _scan_dir(os.path.join(sheets, "full", book), PNG_FILE, pairs, "full png", found)
        _scan_dir(os.path.join(sheets, "thumbs", book), JPG_FILE, pairs, "thumb", found)
        _scan_dir(os.path.join(sheets, "data", book), DATA_FILE, pairs, "data", found)
    index_path = os.path.join(sheets, "data", "index.json")
    if os.path.isfile(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
        for row in index.get("charts") or []:
            if not isinstance(row, dict):
                continue
            try:
                key = (str(row.get("book") or ""), int(row.get("gid")))
            except (TypeError, ValueError):
                continue
            if key in pairs:
                found.append("data index: %s gid %s n %s" % (key[0], key[1], row.get("n")))
    today_path = os.path.join(repo, "static", "cotd", "today.json")
    edition = ""
    if os.path.isfile(today_path):
        with open(today_path, encoding="utf-8") as f:
            today = json.load(f)
        edition = str((today or {}).get("date") or "") if isinstance(today, dict) else ""
        for card in _cotd_cards(today):
            gid, book = _gid_of_card(card)
            if gid is None:
                continue
            if book and (book, gid) in pairs:
                found.append("cotd today.json: %s gid %s" % (book, gid))
            elif not book and any(pair[1] == gid for pair in pairs):
                found.append("cotd today.json: gid %s" % gid)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", edition):
        cards_path = os.path.join(repo, "static", "cotd", edition, "cards.json")
        if os.path.isfile(cards_path):
            with open(cards_path, encoding="utf-8") as f:
                cards = json.load(f)
            for card in _cotd_cards(cards):
                gid, book = _gid_of_card(card)
                if gid is None:
                    continue
                if (book, gid) in pairs:
                    found.append("cotd %s/cards.json: %s gid %s" % (edition, book, gid))
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit non-zero if a retired chart is still published.")
    ap.add_argument("--repo", default=retired_charts.ROOT)
    args = ap.parse_args(argv)
    hits = problems(args.repo)
    if hits:
        print("FAIL retired charts are still published (%d)" % len(hits))
        for hit in hits:
            print(hit)
        return 1
    print("PASS retired charts are absent from the catalog, images, data bundles, and today's edition")
    return 0


if __name__ == "__main__":
    sys.exit(main())
