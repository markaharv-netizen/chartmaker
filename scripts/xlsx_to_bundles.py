#!/usr/bin/env python3
"""Build sheets_data.py dumps from a live xlsx export of the three workbooks.

Google's Sheets connector get_spreadsheet returns chartId+title only (and
truncates large workbooks). Drive export_file is capped at 10 MB. Cursor
DownloadFile can export each workbook as xlsx without that cap. This script
reads those xlsx files plus a title-only spreadsheets.get inventory (catalog
and/or a saved get dump) and writes bundle JSON:

  { "spreadsheet": <spreadsheets.get with full chart specs+positions>,
    "values":      <values.batchGet UNFORMATTED_VALUE>,
    "formatted":   <values.batchGet FORMATTED_VALUE> }

Usage:
  python3 xlsx_to_bundles.py \\
    --xlsx bot1=/path/bot1.xlsx bot2=/path/bot2.xlsx bot3=/path/bot3.xlsx \\
    --meta bot1=/path/meta_bot1.json bot2=... bot3=... \\
    --catalog /path/catalog.json \\
    --out-dir /path/dumps
"""
from __future__ import annotations

import argparse, datetime, json, os, re, sys, zipfile, posixpath, collections
from copy import deepcopy
from lxml import etree
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import to_excel

NS = {
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
A1_RE = re.compile(
    r"^(?:'(?P<q>(?:[^']|'')*)'|\"(?P<d>[^\"]+)\"|(?P<u>[^'!]+))!(?P<a>\$?[A-Za-z]+\$?\d+)(?::(?P<b>\$?[A-Za-z]+\$?\d+))?$"
)
CELL_RE = re.compile(r"^\$?([A-Za-z]+)\$?(\d+)$")
BOOKS = {
    "bot1": ("Bot experiments", "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"),
    "bot2": ("bot experiments 2", "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"),
    "bot3": ("My Bot's Sheets", "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"),
}
# Python ETF draft: native chart is not the published image.
PYTHON_ETF = {("bot3", 1608741572)}

TYPE_MAP = {
    "lineChart": "LINE",
    "areaChart": "AREA",
    "stockChart": "LINE",
}
# scatter, bar/col handled separately


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def col_letters(index):
    n = int(index) + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def norm_name(s):
    s = str(s or "")
    for ch in ("'", "'", "'", "`", "/", "\\", ",", "."):
        s = s.replace(ch, "")
    s = s.replace("&", "")
    return " ".join(s.split()).casefold()


def rels(z, path):
    d, b = posixpath.split(path)
    rp = f"{d}/_rels/{b}.rels"
    if rp not in z.namelist():
        return {}
    t = etree.fromstring(z.read(rp))
    out = {}
    for r in t.findall("pr:Relationship", NS):
        out[r.get("Id")] = (
            r.get("Type") or "",
            posixpath.normpath(posixpath.join(d, r.get("Target"))),
        )
    return out


def txt(el, xp):
    if el is None:
        return None
    r = el.find(xp, NS)
    return r.text if r is not None else None


def color(ser):
    for xp in ["c:spPr/a:ln/a:solidFill/a:srgbClr", "c:spPr/a:solidFill/a:srgbClr"]:
        e = ser.find(xp, NS)
        if e is not None and e.get("val"):
            return "#" + e.get("val")
    return None


def parse_a1_ref(text):
    text = str(text or "").strip()
    m = A1_RE.fullmatch(text)
    if not m:
        return None
    title = m.group("q") or m.group("d") or m.group("u") or ""
    title = title.replace("''", "'").strip()
    a = CELL_RE.fullmatch(m.group("a") or "")
    if not a:
        return None
    c0, r0 = col_index(a.group(1)), int(a.group(2)) - 1
    if m.group("b"):
        b = CELL_RE.fullmatch(m.group("b"))
        if not b:
            return None
        c1, r1 = col_index(b.group(1)), int(b.group(2)) - 1
    else:
        c1, r1 = c0, r0
    if r1 < r0:
        r0, r1 = r1, r0
    if c1 < c0:
        c0, c1 = c1, c0
    return title, c0, r0, c1 + 1, r1 + 1  # end exclusive


def hex_to_color(value):
    h = str(value or "").lstrip("#")
    if len(h) != 6:
        return None
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None
    return {"red": r / 255, "green": g / 255, "blue": b / 255}



def _val_rows(ser):
    parsed = parse_a1_ref(ser.get("val") or "")
    if not parsed:
        return None
    _t, _c0, r0, _c1, r1 = parsed
    return (r0, r1)


def fill_missing_cats(charts, sheet_name):
    """Some Google charts omit c:cat in the xlsx export. Reuse a sibling range."""
    catalog = []
    for ch in charts:
        for g in ch.get("groups") or []:
            for ser in g.get("series") or []:
                cat = parse_a1_ref(ser.get("cat") or "")
                rows = _val_rows(ser)
                if cat and rows:
                    catalog.append((rows, ser["cat"]))
    for ch in charts:
        for g in ch.get("groups") or []:
            for ser in g.get("series") or []:
                if parse_a1_ref(ser.get("cat") or ""):
                    continue
                rows = _val_rows(ser)
                chosen = None
                if rows:
                    for r, cat in catalog:
                        if r == rows or (r[0] <= rows[0] and r[1] >= rows[1]):
                            chosen = cat
                            break
                if not chosen and rows:
                    # same rows, column A of this tab
                    chosen = "'%s'!$A$%d:$A$%d" % (
                        str(sheet_name).replace("'", "''"),
                        rows[0] + 1,
                        rows[1],
                    )
                if chosen:
                    ser["cat"] = chosen

def parse_xlsx_charts(path):
    """Return {xlsx_sheet_name: [chart, ...]} with anchors, groups, axes."""
    z = zipfile.ZipFile(path)
    wb = etree.fromstring(z.read("xl/workbook.xml"))
    wr = {k: v[1] for k, v in rels(z, "xl/workbook.xml").items()}
    out = {}
    images = {}
    for s in wb.find("m:sheets", NS):
        name = s.get("name")
        sp = wr[s.get(RID)]
        sr = {k: v[1] for k, v in rels(z, sp).items()}
        sx = etree.fromstring(z.read(sp))
        dr = sx.find("m:drawing", NS)
        charts, pics = [], []
        if dr is not None:
            dp = sr[dr.get(RID)]
            drel = rels(z, dp)
            dx = etree.fromstring(z.read(dp))
            for anc in list(dx):
                frm = anc.find("xdr:from", NS)
                row = col = 0
                if frm is not None:
                    rt = frm.find("xdr:row", NS)
                    ct = frm.find("xdr:col", NS)
                    row = int(rt.text) if rt is not None and rt.text else 0
                    col = int(ct.text) if ct is not None and ct.text else 0
                chart_el = anc.find(".//c:chart", NS)
                if chart_el is None:
                    # raster fallback (some Sheets charts export only as images)
                    pics.append({"row": row, "col": col, "kind": "image"})
                    continue
                rid = chart_el.get(RID)
                typ, cp = drel.get(rid, (None, None))
                if not cp or cp not in z.namelist():
                    pics.append({"row": row, "col": col, "kind": "missing-xml"})
                    continue
                cx = etree.fromstring(z.read(cp))
                pa = cx.find(".//c:plotArea", NS)
                # Chart title only (not axis titles, which also live under c:title).
                title_el = cx.find("c:chart/c:title", NS)
                if title_el is None:
                    title_el = cx.find("./c:title", NS)
                title = "".join(t.text or "" for t in title_el.findall(".//a:t", NS)[:20]) if title_el is not None else ""
                axes = {}
                if pa is not None:
                    for ax in pa:
                        tag = etree.QName(ax).localname
                        if tag not in ("valAx", "catAx", "dateAx"):
                            continue
                        axid = ax.find("c:axId", NS)
                        axid = axid.get("val") if axid is not None else None
                        pos = ax.find("c:axPos", NS)
                        vmin = ax.find("c:scaling/c:min", NS)
                        vmax = ax.find("c:scaling/c:max", NS)
                        nf = ax.find("c:numFmt", NS)
                        axes[axid] = {
                            "kind": tag,
                            "pos": pos.get("val") if pos is not None else None,
                            "log": ax.find("c:scaling/c:logBase", NS) is not None,
                            "min": float(vmin.get("val")) if vmin is not None and vmin.get("val") else None,
                            "max": float(vmax.get("val")) if vmax is not None and vmax.get("val") else None,
                            "title": "".join(t.text or "" for t in ax.findall(".//c:title//a:t", NS)),
                            "fmt": nf.get("formatCode") if nf is not None else None,
                        }
                groups = []
                if pa is not None:
                    for g in pa:
                        tag = etree.QName(g).localname
                        if not tag.endswith("Chart"):
                            continue
                        grouping = g.find("c:grouping", NS)
                        bar_dir = g.find("c:barDir", NS)
                        grp = {
                            "type": tag,
                            "axIds": [a.get("val") for a in g.findall("c:axId", NS)],
                            "grouping": grouping.get("val") if grouping is not None else None,
                            "barDir": bar_dir.get("val") if bar_dir is not None else None,
                            "series": [],
                        }
                        for ser in g.findall("c:ser", NS):
                            grp["series"].append({
                                "name": txt(ser, "c:tx//c:f") or txt(ser, "c:tx//c:v"),
                                "cat": txt(ser, "c:cat//c:f") or txt(ser, "c:xVal//c:f"),
                                "val": txt(ser, "c:val//c:f") or txt(ser, "c:yVal//c:f"),
                                "color": color(ser),
                            })
                        groups.append(grp)
                charts.append({
                    "title": title,
                    "groups": groups,
                    "axes": axes,
                    "row": row,
                    "col": col,
                })
        fill_missing_cats(charts, name)
        # stable PNG-export order: row, then column
        charts.sort(key=lambda c: (c["row"], c["col"]))
        out[name] = charts
        images[name] = pics
    return out, images


def match_tab(xlsx_names, google_title):
    """Map a Google tab title to an xlsx sheet name (31-char / punctuation)."""
    names = list(xlsx_names)
    if google_title in names:
        return google_title
    if google_title[:31] in names:
        return google_title[:31]
    ng = norm_name(google_title)
    by_norm = {norm_name(n): n for n in names}
    if ng in by_norm:
        return by_norm[ng]
    ng31 = norm_name(google_title[:31])
    if ng31 in by_norm:
        return by_norm[ng31]
    cands = [n for n in names if norm_name(n).startswith(ng) or ng.startswith(norm_name(n))]
    if len(cands) == 1:
        return cands[0]
    return None


def load_inventory(meta_path, catalog_tabs, book):
    """Sheet list with gid, title, index, hidden, charts[{chartId,title}]."""
    sheets = []
    if meta_path and os.path.exists(meta_path):
        doc = json.load(open(meta_path, encoding="utf-8"))
        for s in doc.get("sheets") or []:
            props = s.get("properties") or {}
            title = str(props.get("title") or "").strip()
            if not title:
                continue
            try:
                gid = int(props.get("sheetId"))
            except (TypeError, ValueError):
                continue
            charts = []
            for ch in s.get("charts") or []:
                charts.append({
                    "chartId": ch.get("chartId"),
                    "title": ((ch.get("spec") or {}).get("title") or "").strip(),
                })
            sheets.append({
                "title": title,
                "gid": gid,
                "index": props.get("index") or 0,
                "hidden": bool(props.get("hidden")),
                "charts": charts,
                "grid": props.get("gridProperties") or {},
            })
    # add catalog tabs not in meta (shouldn't happen often)
    have = {sh["gid"] for sh in sheets}
    have_titles = {sh["title"] for sh in sheets}
    for t in catalog_tabs:
        if t.get("book") != book:
            continue
        gid = int(t["gid"])
        if gid in have:
            continue
        sheets.append({
            "title": t["tab"],
            "gid": gid,
            "index": 10_000 + gid % 1000,
            "hidden": False,
            "charts": [{"chartId": None, "title": c.get("title") or ""} for c in (t.get("chartList") or [])],
            "grid": {},
        })
        have.add(gid)
    sheets.sort(key=lambda s: s["index"])
    return sheets


def fmt_looks_date(fmt):
    if not fmt:
        return False
    f = str(fmt).lower()
    if "%" in f:
        return False
    return any(tok in f for tok in ("yy", "yyyy", "mm", "dd", "m/d", "d-"))


def fmt_looks_percent(fmt):
    return bool(fmt) and "%" in str(fmt)


def axis_format_obj(ax, want_date=False, want_percent=False):
    if want_percent or fmt_looks_percent(ax.get("fmt") if ax else None):
        typ = "PERCENT"
    elif want_date or fmt_looks_date(ax.get("fmt") if ax else None) or (ax and ax.get("kind") == "dateAx"):
        typ = "DATE"
    else:
        typ = None
    out = {}
    if typ:
        out["format"] = {"numberFormat": {"type": typ}}
    return out


def pos_to_google(pos, kind, chart_type):
    """Map OOXML axPos (l/r/b/t) to Sheets axis position."""
    p = (pos or "").lower()
    if chart_type == "BAR":
        # horizontal bar: categories on left, values on bottom
        if kind in ("catAx", "dateAx"):
            return "LEFT_AXIS"
        if p == "t":
            return "RIGHT_AXIS"  # unusual
        return "BOTTOM_AXIS"
    if kind in ("catAx", "dateAx"):
        return "BOTTOM_AXIS" if p in ("b", "", None) or p is None else ("TOP_AXIS" if p == "t" else "BOTTOM_AXIS")
    if p == "r":
        return "RIGHT_AXIS"
    return "LEFT_AXIS"


def group_chart_type(grp):
    tag = grp.get("type") or ""
    if tag == "barChart":
        if (grp.get("barDir") or "col") == "bar":
            return "BAR"
        return "COLUMN"
    if tag == "scatterChart":
        return "SCATTER"
    if tag == "ofPieChart" or tag == "pieChart":
        return "PIE"
    if tag == "doughnutChart":
        return "PIE"
    if tag == "bubbleChart":
        return "BUBBLE"
    if tag in TYPE_MAP:
        typ = TYPE_MAP[tag]
        # stepped area sometimes comes through as areaChart + grouping
        if tag == "areaChart" and (grp.get("grouping") or "") == "standard":
            return "AREA"
        return typ
    return tag.replace("Chart", "").upper() or "UNKNOWN"


def stacked_type(groups):
    for g in groups:
        gr = (g.get("grouping") or "").lower()
        if gr in ("percentstacked", "percentStacked"):
            return "PERCENT_STACKED"
        if gr == "stacked":
            return "STACKED"
    return "NOT_STACKED"


def xlsx_chart_to_spec(ch, title_to_id, default_gid, default_title, cells_note):
    """Build a Sheets API chart object (chartId filled later) from xlsx parse."""
    groups = ch.get("groups") or []
    if not groups or not any(g.get("series") for g in groups):
        return None, "no series in xlsx chart xml"
    types = []
    for g in groups:
        types.append(group_chart_type(g))
    unsupported = [t for t in types if t not in {"LINE", "AREA", "STEPPED_AREA", "COLUMN", "BAR", "SCATTER", "COMBO"}]
    if unsupported:
        return None, "unsupported type %s" % ",".join(sorted(set(unsupported)))
    unique = set(types)
    if len(unique) > 1:
        chart_type = "COMBO"
    else:
        chart_type = next(iter(unique))

    # Determine domain from first series cat range
    series_out = []
    domain_src = None
    needed = []  # (sheet_title_xlsx_or_google, c0,r0,c1,r1)
    header_cells = []  # literal names to inject

    def resolve_sheet(parsed_title):
        if not parsed_title:
            return default_title, default_gid
        # parsed title is xlsx name; map back if needed via default only when same tab
        gid = title_to_id.get(parsed_title)
        if gid is not None:
            return parsed_title, gid
        # try default
        return default_title, default_gid

    first_cat = None
    for gi, g in enumerate(groups):
        gtype = group_chart_type(g)
        # axis assignment: val axis of this group
        val_pos = "LEFT_AXIS"
        cat_ax = None
        val_ax = None
        axes = ch.get("axes") or {}
        for axid in g.get("axIds") or []:
            ax = axes.get(axid)
            if not ax:
                continue
            if ax["kind"] in ("catAx", "dateAx"):
                cat_ax = ax
            else:
                val_ax = ax
                val_pos = pos_to_google(ax.get("pos"), ax["kind"], chart_type if chart_type != "COMBO" else gtype)
        target = "RIGHT_AXIS" if val_pos == "RIGHT_AXIS" else "LEFT_AXIS"
        typ = gtype if gtype in {"LINE", "AREA", "STEPPED_AREA", "COLUMN", "BAR", "SCATTER"} else None
        for si, ser in enumerate(g.get("series") or []):
            cat = parse_a1_ref(ser.get("cat"))
            val = parse_a1_ref(ser.get("val"))
            if not val:
                continue
            if cat and first_cat is None:
                first_cat = cat
            sheet_t, gid = resolve_sheet(val[0])
            c0, r0, c1, r1 = val[1], val[2], val[3], val[4]
            # include header row if name is a one-cell formula on the row above
            name_ref = parse_a1_ref(ser.get("name") or "")
            header = 0
            if name_ref and name_ref[2] == r0 - 1 and r0 > 0:
                # expand start to header
                r0 = name_ref[2]
                header = 1
            elif (not name_ref) and ser.get("name") and r0 > 0:
                # literal name: inject a header cell
                r0 = r0 - 1
                header = 1
                header_cells.append((sheet_t, r0, c0, ser.get("name")))
            needed.append((sheet_t, c0, r0, c1, r1))
            # series name is often the cell to the left (row-oriented) or already included as a header row
            if c0 > 0:
                needed.append((sheet_t, c0 - 1, r0, c0, r1))
            if cat:
                needed.append((cat[0] and resolve_sheet(cat[0])[0] or sheet_t, cat[1], min(cat[2], r0), cat[3], cat[4]))
            entry = {
                "series": {
                    "sourceRange": {
                        "sources": [{
                            "sheetId": gid,
                            "startRowIndex": r0,
                            "endRowIndex": r1,
                            "startColumnIndex": c0,
                            "endColumnIndex": c1,
                        }]
                    }
                },
                "targetAxis": target,
            }
            if typ and chart_type == "COMBO":
                entry["type"] = typ
            col = hex_to_color(ser.get("color"))
            if col:
                entry["color"] = col
            series_out.append((header, entry, g, val_ax, cat_ax))

    if not series_out or not first_cat:
        return None, "no parseable series/domain ranges"

    # Domain: use first cat, expand to header if series did
    cat_title, dc0, dr0, dc1, dr1 = first_cat
    sheet_t, gid = resolve_sheet(cat_title)
    # If any series included a header, domain should too
    any_header = any(h for h, *_ in series_out)
    if any_header and dr0 > 0:
        # if cat started at data row, pull back one
        # only if series start is header
        ser_starts = [e["series"]["sourceRange"]["sources"][0]["startRowIndex"] for h, e, *_ in series_out]
        if min(ser_starts) < dr0:
            dr0 = min(ser_starts)
    domain = {
        "domain": {
            "sourceRange": {
                "sources": [{
                    "sheetId": gid,
                    "startRowIndex": dr0,
                    "endRowIndex": dr1,
                    "startColumnIndex": dc0,
                    "endColumnIndex": dc1,
                }]
            }
        }
    }
    needed.append((sheet_t, dc0, dr0, dc1, dr1))

    header_count = 1 if any_header or (dr0 < (parse_a1_ref((groups[0]["series"][0].get("cat") or "")) or (None,0,0,0,0))[2] if groups[0].get("series") else False) else 0
    # simpler: if domain start < first original cat start, header 1
    header_count = 1 if any_header else 0

    # Axes
    axis_list = []
    # category axis
    cat_ax = series_out[0][4] or {}
    cat_pos = "LEFT_AXIS" if chart_type == "BAR" else "BOTTOM_AXIS"
    cat_entry = {"position": cat_pos, "title": " ".join(str(cat_ax.get("title") or "").split())}
    cat_entry.update(axis_format_obj(cat_ax, want_date=True if (cat_ax or {}).get("kind") == "dateAx" or fmt_looks_date((cat_ax or {}).get("fmt")) else False))
    # If categories look like dates we still set DATE when fmt says so
    axis_list.append(cat_entry)

    seen_val = set()
    for header, entry, g, val_ax, _cat in series_out:
        target = entry.get("targetAxis") or "LEFT_AXIS"
        if target in seen_val:
            continue
        seen_val.add(target)
        ax = val_ax or {}
        item = {"position": target, "title": " ".join(str(ax.get("title") or "").split())}
        if ax.get("log"):
            item["logScale"] = True
        item.update(axis_format_obj(ax))
        vw = {}
        if ax.get("min") is not None:
            vw["viewWindowMode"] = "EXPLICIT"
            vw["viewWindowMin"] = ax["min"]
        if ax.get("max") is not None:
            vw.setdefault("viewWindowMode", "EXPLICIT")
            vw["viewWindowMax"] = ax["max"]
        if vw:
            item["viewWindowOptions"] = vw
        axis_list.append(item)

    basic = {
        "chartType": chart_type,
        "legendPosition": "BOTTOM_LEGEND",
        "domains": [domain],
        "series": [e for _h, e, *_ in series_out],
        "headerCount": header_count,
        "axis": axis_list,
        "stackedType": stacked_type(groups),
    }
    spec = {"title": " ".join(str(ch.get("title") or "").split()), "basicChart": basic}
    extra = {"needed": needed, "header_cells": header_cells, "row": ch["row"], "col": ch["col"]}
    return spec, extra


def load_sheet_values(xlsx_path, needed_by_xlsx_name):
    """Read unformatted + formatted cell values for needed ranges.

    needed_by_xlsx_name: {xlsx_sheet_name: [(c0,r0,c1,r1), ...]}
    Returns two dicts: raw[(name,r,c)] and fmt[(name,r,c)].
    """
    wb = load_workbook(xlsx_path, data_only=True, read_only=True)
    raw, fmt = {}, {}
    for name, ranges in needed_by_xlsx_name.items():
        if name not in wb.sheetnames:
            continue
        # bound box
        if not ranges:
            continue
        min_r = min(r0 for c0, r0, c1, r1 in ranges)
        max_r = max(r1 for c0, r0, c1, r1 in ranges)  # exclusive
        min_c = min(c0 for c0, r0, c1, r1 in ranges)
        max_c = max(c1 for c0, r0, c1, r1 in ranges)
        ws = wb[name]
        # read_only iter_rows is 1-based inclusive
        r_start = max(1, min_r + 1)
        r_end = max(r_start, max_r)
        c_start = max(1, min_c + 1)
        c_end = max(c_start, max_c)
        try:
            rows = ws.iter_rows(min_row=r_start, max_row=r_end, min_col=c_start, max_col=c_end)
        except Exception:
            rows = []
        for row in rows:
            for cell in row:
                if not getattr(cell, "row", None) or not getattr(cell, "column", None):
                    continue
                r = cell.row - 1
                c = cell.column - 1
                v = cell.value
                if v is None or v == "":
                    continue
                nf = cell.number_format or "General"
                if isinstance(v, datetime.datetime):
                    serial = to_excel(v)
                    raw[(name, r, c)] = serial
                    fmt[(name, r, c)] = v.strftime("%Y-%m-%d")
                elif isinstance(v, datetime.date):
                    serial = to_excel(datetime.datetime(v.year, v.month, v.day))
                    raw[(name, r, c)] = serial
                    fmt[(name, r, c)] = v.strftime("%Y-%m-%d")
                elif isinstance(v, bool):
                    raw[(name, r, c)] = v
                    fmt[(name, r, c)] = "TRUE" if v else "FALSE"
                elif isinstance(v, (int, float)):
                    raw[(name, r, c)] = v
                    if fmt_looks_percent(nf):
                        fmt[(name, r, c)] = ("%s%%" % (round(v * 100, 4) if abs(v) <= 10 else round(v, 4))).rstrip("0").rstrip(".") + ("" if False else "")
                        # tidy percent string
                        pct = v * 100 if abs(v) <= 10 else v
                        s = ("%f" % pct).rstrip("0").rstrip(".")
                        fmt[(name, r, c)] = s + "%"
                    elif fmt_looks_date(nf):
                        try:
                            fmt[(name, r, c)] = (datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(v))).strftime("%Y-%m-%d")
                        except Exception:
                            fmt[(name, r, c)] = v
                    else:
                        fmt[(name, r, c)] = v
                else:
                    raw[(name, r, c)] = str(v)
                    fmt[(name, r, c)] = str(v)
        # If a column is mostly dates, treat leftover Excel serials as dates too.
        cols = set(c for (n, r, c) in raw if n == name)
        for col in cols:
            date_rows = [
                r for (n, r, c) in fmt
                if n == name and c == col and isinstance(fmt[(n, r, c)], str)
                and len(fmt[(n, r, c)]) == 10 and fmt[(n, r, c)][4] == "-"
            ]
            num_rows = [
                r for (n, r, c) in raw
                if n == name and c == col and isinstance(raw[(n, r, c)], (int, float))
                and not isinstance(raw[(n, r, c)], bool)
            ]
            if len(date_rows) < 3:
                continue
            for r in num_rows:
                if (name, r, col) in fmt and isinstance(fmt[(name, r, col)], str) and len(fmt[(name, r, col)]) == 10:
                    continue
                v = raw[(name, r, col)]
                if 20000 <= float(v) <= 80000:
                    try:
                        fmt[(name, r, col)] = (
                            datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(v))
                        ).strftime("%Y-%m-%d")
                    except Exception:
                        pass
    wb.close()
    return raw, fmt


def a1_range(title, c0, r0, c1, r1):
    safe = str(title).replace("'", "''")
    return "'%s'!%s%d:%s%d" % (safe, col_letters(c0), r0 + 1, col_letters(c1 - 1), r1)


def build_value_ranges(google_title, cells, xlsx_name, raw_or_fmt):
    """One valueRange covering the used box of this sheet from the cell dict."""
    pts = [(r, c, v) for (n, r, c), v in raw_or_fmt.items() if n == xlsx_name]
    if not pts:
        return None
    min_r = min(r for r, c, v in pts)
    max_r = max(r for r, c, v in pts)
    min_c = min(c for r, c, v in pts)
    max_c = max(c for r, c, v in pts)
    # also include injected cells under google title key? we store under xlsx name
    values = []
    lookup = {(r, c): v for r, c, v in pts}
    for r in range(min_r, max_r + 1):
        row = []
        for c in range(min_c, max_c + 1):
            row.append(lookup.get((r, c), ""))
        values.append(row)
    return {
        "range": a1_range(google_title, min_c, min_r, max_c + 1, max_r + 1),
        "values": values,
    }


def title_match(a, b):
    return norm_name(a) == norm_name(b) or norm_name(a).startswith(norm_name(b)) or norm_name(b).startswith(norm_name(a))


def assign_chart_ids(xlsx_charts, inventory_charts):
    """Pair xlsx charts with inventory (chartId, title) by title then order."""
    remaining = list(inventory_charts)
    assigned = []
    used = set()
    for i, ch in enumerate(xlsx_charts):
        cid = None
        # exact / fuzzy title
        for j, inv in enumerate(remaining):
            if j in used:
                continue
            if inv.get("title") and ch.get("title") and title_match(inv["title"], ch["title"]):
                cid = inv.get("chartId")
                used.add(j)
                break
        assigned.append(cid)
    # fill leftovers by remaining order
    leftovers = [inv for j, inv in enumerate(remaining) if j not in used]
    li = 0
    for i, cid in enumerate(assigned):
        if cid is None and li < len(leftovers):
            assigned[i] = leftovers[li].get("chartId")
            li += 1
    return assigned


def load_excluded(catalog_py_hint):
    sys.path.insert(0, catalog_py_hint)
    import sheets_catalog
    import sheets_data
    return set(sheets_catalog.EXCLUDED_TABS), sheets_catalog, set(sheets_data.RETIRED_2026_11_17)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", nargs="+", required=True, help="book=/path.xlsx")
    ap.add_argument("--meta", nargs="+", default=[], help="book=/path.json title-only get dumps")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--scripts-dir", default="/workspace/cm_interactive_data/scripts")
    args = ap.parse_args(argv)

    xlsx = dict(x.split("=", 1) for x in args.xlsx)
    meta = dict(x.split("=", 1) for x in args.meta)
    catalog = json.load(open(args.catalog, encoding="utf-8"))
    excluded, sc, retiring = load_excluded(args.scripts_dir)
    skip_gids = set(excluded) | set(retiring) | set(PYTHON_ETF)

    os.makedirs(args.out_dir, exist_ok=True)
    report = {"books": {}, "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    for book, (book_name, sid) in BOOKS.items():
        if book not in xlsx:
            continue
        print("Parsing", book, xlsx[book], flush=True)
        xcharts, ximages = parse_xlsx_charts(xlsx[book])
        inv = load_inventory(meta.get(book), catalog.get("tabs") or [], book)
        title_to_id = {sh["title"]: sh["gid"] for sh in inv}
        # also index xlsx names mapped later

        sheets_out = []
        value_needed = {}  # xlsx_name -> list of boxes
        header_inject = []  # (xlsx_name, r, c, value)
        stats = collections.Counter()
        skips = []
        exported_charts = 0

        # Also export tabs that are only in xlsx (new tabs): try inventory by matched name
        inv_by_title = {sh["title"]: sh for sh in inv}
        matched_xlsx = set()

        for sh in inv:
            title, gid = sh["title"], sh["gid"]
            rec = {
                "properties": {
                    "sheetId": gid,
                    "title": title,
                    "index": sh["index"],
                    "sheetType": "GRID",
                    "gridProperties": sh.get("grid") or {"rowCount": 1000, "columnCount": 26},
                    **({"hidden": True} if sh.get("hidden") else {}),
                }
            }
            pair = (book, gid)
            xname = match_tab(xcharts.keys(), title)
            if xname:
                matched_xlsx.add(xname)

            if sh.get("hidden"):
                stats["hidden_tabs"] += 1
                sheets_out.append(rec)
                continue
            if pair in skip_gids:
                reason = "retiring" if pair in retiring else ("python_etf" if pair in PYTHON_ETF else "excluded")
                n_inv = len(sh.get("charts") or [])
                stats[reason + "_tabs"] += 1
                stats[reason + "_charts"] += n_inv or len(xcharts.get(xname) or [])
                skips.append({"book": book, "gid": gid, "tab": title, "reason": reason, "charts": n_inv})
                # omit charts so sheets_data won't write them
                rec["charts"] = []
                sheets_out.append(rec)
                continue

            xlist = list(xcharts.get(xname) or []) if xname else []
            # If tab has no xlsx match
            if not xname:
                n_inv = len(sh.get("charts") or [])
                if n_inv:
                    stats["unmatched_tab"] += 1
                    stats["skipped_no_xlsx_tab"] += n_inv
                    skips.append({"book": book, "gid": gid, "tab": title, "reason": "tab not in xlsx", "charts": n_inv})
                else:
                    stats["table_or_chartless"] += 1
                rec["charts"] = []
                sheets_out.append(rec)
                continue

            # Build specs
            chart_objs = []
            ids = assign_chart_ids(xlist, sh.get("charts") or [])
            # catalog titles for skip reporting
            inv_titles = [c.get("title") or "" for c in (sh.get("charts") or [])]
            used_inv = set()
            for i, ch in enumerate(xlist):
                # Map xlsx sheet name -> google title for title_to_id
                # title_to_id is google titles; parse_a1 of xlsx uses xlsx names.
                # Build a local map that resolves both.
                local_map = dict(title_to_id)
                local_map[xname] = gid
                local_map[title] = gid
                spec, extra = xlsx_chart_to_spec(ch, local_map, gid, title, None)
                if spec is None:
                    stats["skipped_bad_spec"] += 1
                    skips.append({"book": book, "gid": gid, "tab": title, "reason": extra, "xlsx_title": ch.get("title")})
                    continue
                needed = extra["needed"]
                for sheet_t, c0, r0, c1, r1 in needed:
                    # needed sheet titles may be google or xlsx; store under xlsx name for this tab's own ranges
                    key = xname if sheet_t in (title, xname, "") else match_tab(xcharts.keys(), sheet_t) or xname
                    value_needed.setdefault(key, []).append((c0, r0, c1, r1))
                for sheet_t, r, c, val in extra.get("header_cells") or []:
                    key = xname if sheet_t in (title, xname, "") else match_tab(xcharts.keys(), sheet_t) or xname
                    header_inject.append((key, r, c, val))
                cid = ids[i]
                obj = {
                    "chartId": cid if cid is not None else (1000000 + gid % 100000 + i),
                    "spec": spec,
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": gid,
                                "rowIndex": ch["row"],
                                "columnIndex": ch["col"],
                            }
                        }
                    },
                }
                chart_objs.append(obj)
                exported_charts += 1
                stats["exported"] += 1

            # inventory charts not represented in xlsx
            x_titles = [norm_name(c.get("title")) for c in xlist]
            for invc in sh.get("charts") or []:
                t = invc.get("title") or ""
                if t and not any(title_match(t, c.get("title") or "") for c in xlist):
                    # might still have been assigned by order; only flag if count short
                    pass
            n_inv = len(sh.get("charts") or [])
            n_img = len(ximages.get(xname) or [])
            if n_inv > len(chart_objs):
                missing = n_inv - len(chart_objs)
                stats["skipped_missing_in_xlsx"] += missing
                skips.append({
                    "book": book, "gid": gid, "tab": title,
                    "reason": "xlsx export missing %d chart(s) (often raster-only)" % missing,
                    "catalog_charts": n_inv, "xlsx_charts": len(chart_objs), "xlsx_images": n_img,
                    "inventory_titles": inv_titles,
                    "xlsx_titles": [c.get("title") for c in xlist],
                })
            rec["charts"] = chart_objs
            if not sh.get("charts") and not chart_objs:
                stats["table_or_chartless"] += 1
            sheets_out.append(rec)

        # New xlsx tabs not in inventory
        for xname, xlist in xcharts.items():
            if xname in matched_xlsx:
                continue
            if not xlist:
                stats["xlsx_only_chartless"] += 1
                continue
            stats["xlsx_only_unmapped"] += 1
            stats["skipped_xlsx_only"] += len(xlist)
            skips.append({"book": book, "tab": xname, "reason": "xlsx tab not in inventory (no gid)", "charts": len(xlist)})

        print("  loading values for", len(value_needed), "sheets", flush=True)
        raw, fmt = load_sheet_values(xlsx[book], value_needed)
        for name, r, c, val in header_inject:
            raw.setdefault((name, r, c), val)
            fmt.setdefault((name, r, c), val)

        # Build valueRanges keyed by google title
        xname_to_google = {}
        for sh in inv:
            xn = match_tab(xcharts.keys(), sh["title"])
            if xn:
                xname_to_google[xn] = sh["title"]

        value_ranges, formatted_ranges = [], []
        for xname in value_needed:
            gtitle = xname_to_google.get(xname, xname)
            vr = build_value_ranges(gtitle, raw, xname, raw)
            fr = build_value_ranges(gtitle, fmt, xname, fmt)
            if vr:
                value_ranges.append(vr)
            if fr:
                formatted_ranges.append(fr)

        bundle = {
            "spreadsheet": {
                "spreadsheetId": sid,
                "properties": {"title": book_name},
                "sheets": sheets_out,
            },
            "values": {"valueRanges": value_ranges},
            "formatted": {"valueRanges": formatted_ranges},
        }
        outp = os.path.join(args.out_dir, "%s.bundle.json" % book)
        with open(outp, "w", encoding="utf-8") as f:
            json.dump(bundle, f)
        print("  wrote", outp, "sheets", len(sheets_out), "exported_charts", exported_charts, "valueRanges", len(value_ranges), flush=True)
        report["books"][book] = {
            "file": outp,
            "sheets": len(sheets_out),
            "exported_charts": exported_charts,
            "stats": dict(stats),
            "skips": skips,
        }

    report_path = os.path.join(args.out_dir, "export_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("REPORT", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
