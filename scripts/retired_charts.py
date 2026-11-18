#!/usr/bin/env python3
"""Load config/retired_charts.json, the only retirement list.

Library rows (scope "library") stay out of the catalog, full PNGs, thumbnails,
interactive data, favorites, and Charts of the day. A scope "cotd" row stays in
the library and is left out of Charts of the day only. That row is not a retirement.

EXCLUDED_TABS, the dated RETIRED_* sets, and COTD_EXCLUDED are derived from this
file so older imports keep working.
"""
import json, os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_PATH = os.path.join(ROOT, "config", "retired_charts.json")
BOOKS = ("bot1", "bot2", "bot3")
REASONS = ("retired", "excluded")
SCOPES = ("library", "cotd")
_CACHE = {}


def config_path(path=None):
    return os.path.abspath(path or DEFAULT_PATH)


def load(path=None):
    """Return the document. Re-reads when the file's mtime changes."""
    path = config_path(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError as exc:
        raise OSError("retired charts config missing: %s" % path) from exc
    hit = _CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("charts"), list):
        raise ValueError("retired charts config needs a charts array")
    rows = [_normalize(row, i) for i, row in enumerate(doc["charts"])]
    _reject_duplicates(rows)
    loaded = {"note": doc.get("note") or "", "charts": rows, "path": path}
    _CACHE[path] = (mtime, loaded)
    return loaded


def invalidate(path=None):
    _CACHE.pop(config_path(path), None)


def charts(path=None):
    return load(path)["charts"]


def _normalize(row, index):
    if not isinstance(row, dict):
        raise ValueError("retired chart %s is not an object" % index)
    book = str(row.get("book") or "").strip()
    if book not in BOOKS:
        raise ValueError("retired chart %s has book %r" % (index, book))
    try:
        gid = int(row.get("gid"))
    except (TypeError, ValueError):
        raise ValueError("retired chart %s has no gid" % index)
    if gid < 0:
        raise ValueError("retired chart %s has gid %s" % (index, gid))
    chart_id = row.get("chartId")
    if chart_id is not None:
        try:
            chart_id = int(chart_id)
        except (TypeError, ValueError):
            raise ValueError("retired chart %s chartId is not an int" % index)
    reason = str(row.get("reason") or "").strip()
    scope = str(row.get("scope") or "library").strip()
    if reason not in REASONS:
        raise ValueError("retired chart %s reason %r" % (index, reason))
    if scope not in SCOPES:
        raise ValueError("retired chart %s scope %r" % (index, scope))
    retired_on = row.get("retiredOn")
    if retired_on is not None:
        retired_on = str(retired_on)
    pngs = row.get("drivePngs") or []
    if not isinstance(pngs, list) or any(not isinstance(p, str) for p in pngs):
        raise ValueError("retired chart %s drivePngs must be a list of names" % index)
    tab = row.get("tab")
    title = row.get("chartTitle")
    return {
        "book": book,
        "gid": gid,
        "chartId": chart_id,
        "tab": "" if tab is None else str(tab),
        "chartTitle": None if title in (None, "") else str(title),
        "retiredOn": retired_on,
        "reason": reason,
        "drivePngs": list(pngs),
        "scope": scope,
    }


def _reject_duplicates(rows):
    seen = set()
    for row in rows:
        key = (row["book"], row["gid"], row["chartId"])
        if key in seen:
            raise ValueError("duplicate retired chart %s/%s chartId %s" % key)
        seen.add(key)


def library_rows(path=None):
    """Rows that must not be in the sheet library. CotD-only rows are not included."""
    return [row for row in charts(path) if row["scope"] == "library"]


def cotd_rows(path=None):
    return [row for row in charts(path) if row["scope"] == "cotd"]


def _pairs(rows):
    return {(row["book"], int(row["gid"])) for row in rows}


def library_pairs(path=None):
    """(book, gid) set behind sheets_catalog.EXCLUDED_TABS."""
    return _pairs(library_rows(path))


def cotd_pairs(path=None):
    """(book, gid) set behind cotd_build.COTD_EXCLUDED."""
    return _pairs(cotd_rows(path))


def pairs_on(date, path=None):
    """Library rows retired on YYYY-MM-DD. Dated RETIRED_* sets are this view."""
    day = str(date)
    return _pairs(row for row in library_rows(path) if row.get("retiredOn") == day)


def tab_excluded(book, gid, path=None):
    """True when a catalog or image refresh must not keep this workbook tab."""
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return False
    return (str(book), gid) in library_pairs(path)


def cotd_excluded(book, gid, path=None):
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return False
    return (str(book), gid) in cotd_pairs(path)


def find(book, gid, chart_id=None, path=None):
    """The record for this tab, or for this chartId when one is given."""
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None
    if chart_id is not None:
        try:
            chart_id = int(chart_id)
        except (TypeError, ValueError):
            return None
    for row in charts(path):
        if row["book"] != str(book) or row["gid"] != gid:
            continue
        if chart_id is None or row["chartId"] == chart_id:
            return row
    return None


def save(doc_charts, path=None, note=None):
    """Write charts back. doc_charts are already normalized objects."""
    path = config_path(path)
    previous = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                previous = json.load(f)
        except (OSError, ValueError):
            previous = {}
    payload = {
        "note": note if note is not None else (previous.get("note") if isinstance(previous, dict) else "") or "",
        "charts": doc_charts,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    invalidate(path)
    return path
