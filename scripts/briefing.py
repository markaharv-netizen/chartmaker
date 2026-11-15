#!/usr/bin/env python3
"""Daily briefing helpers.

The Daily briefing tab shows a rolling archive of Lloyd's 9 AM ET news
briefings from data/lloyd_briefings.json (see scripts/lloyd_briefings.py).
GET /api/briefing calls build().

Refresh scripts still append price-target and treasury snapshots here, so
those history files stay current even though the tab no longer diffs them:

  python3 scripts/price_targets.py     # appends static/price_targets_history.json
  python3 scripts/refresh_btctop100.py # appends btctop100_history.json

  python3 scripts/briefing.py          # one-screen summary of the archive
  python3 scripts/briefing.py --json   # the payload the page renders
  python3 scripts/briefing.py --self-test
  python3 scripts/add_lloyd_briefing.py --help
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lloyd_briefings as lloyd

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


def read(path):
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


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
    another copy.
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
            "scripts/price_targets.py appends one when those fields change."
        ),
        "snapshots": snaps,
    })
    return path


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
            "Each company keeps its as-of date, note, and source."
        ),
        "snapshots": snaps,
    })
    return path


def build(repo=None):
    return lloyd.build(repo or repo_of())


def summary(doc):
    return lloyd.summary(doc)


def self_test():
    import unittest
    tests = os.path.join(repo_of(), "tests")
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTests(loader.discover(tests, pattern="test_briefing.py"))
    suite.addTests(loader.discover(tests, pattern="test_lloyd_briefings.py"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    print("self-test ok")


def main():
    parser = argparse.ArgumentParser(description="Build the Daily briefing payload from Lloyd's archive")
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
