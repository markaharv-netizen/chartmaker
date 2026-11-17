#!/usr/bin/env python3
"""Export interactive chart data for the Sheets library.

Chartmaker draws these files in the browser. View PNG / Download PNG stay on
the committed full-size images. This script does not call Google unless
SHEETS_LIVE=1 and a caller asks for a live chart. The default path is a
saved dump, so CI and the Monday routine can run without credentials.

Dump shapes (any one of these):

  A bundle, which is what the pilot fixtures use:

    {
      "spreadsheet": <spreadsheets.get response>,
      "values":      <values.batchGet response>,
      "formatted":   <values.batchGet response, optional>,
      "sample":      true
    }

  Or the spreadsheets.get JSON plus a separate batchGet file:

    python3 scripts/sheets_data.py bot1.json --values bot1.values.json

The spreadsheets.get field mask has to include the chart spec, not only the title:

  sheets(properties,charts(chartId,spec,position))

properties still supplies sheetId, title, index, and hidden.
position.overlayPosition.anchorCell supplies the chart number, the same order
scripts/sheets_catalog.py and the Monday PNG export use. A one-chart tab does
not need position. A multi-chart tab with no anchor on every chart is left
unchanged (existing data files stay) and this script prints a warning.

values.batchGet should request every domain and series source range.
valueRenderOption UNFORMATTED_VALUE keeps numbers numeric. A second batchGet
with FORMATTED_VALUE (the "formatted" object) supplies category labels such
as "9/30/20". Dates may also arrive as Sheets serial numbers; a Date/Year
axis, or a DATE number format, turns those into YYYY-MM-DD and the axis title
is then dropped.

Output:

  static/sheets/data/<book>/<gid>.json
  static/sheets/data/<book>/<gid>_<n>.json    (chart 2 and later)
  static/sheets/data/index.json

Tabs in sheets_catalog.EXCLUDED_TABS or RETIRED_2026_11_17 are never written.
A data file already on disk for one of those tabs is deleted. The 2026-11-17
list lives in this file (not EXCLUDED_TABS) so it does not conflict with PR #63.

Supported basic charts: line, area, stepped area, column, bar (horizontal),
scatter, and combo (including a second axis). Pie, bubble, candlestick,
histogram, waterfall, treemap, org, and scorecard charts are skipped.

Usage:

  python3 scripts/sheets_data.py bot1.json bot2.json bot3.json
  python3 scripts/sheets_data.py --repo /workspace/chartmaker dump.json --values dump.values.json
  python3 scripts/sheets_data.py --write-samples
  python3 scripts/sheets_data.py --check
  python3 scripts/sheets_data.py --self-test

Live-on-open (not the default). Set SHEETS_LIVE=1 on the server and one of:

  GOOGLE_ACCESS_TOKEN or CHARTMAKER_GOOGLE_TOKEN
  GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN
    (CHARTMAKER_GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN also work)
  GOOGLE_APPLICATION_CREDENTIALS or CHARTMAKER_GOOGLE_CREDENTIALS
    (service-account JSON; the token is requested with spreadsheets.readonly)

Never commit those values. When the flag is off, or the fetch fails, the
browser uses the snapshot files and then the PNG.
"""
import argparse, datetime, json, math, os, re, sys, time, urllib.parse, urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import sheets_catalog

ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIRNAME = os.path.join("static", "sheets", "data")
BOOK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,24}$")
FILE_RE = re.compile(r"^(\d+)(?:_([2-9]|[1-9]\d))?\.json$")
CELL_RE = re.compile(r"^\$?([A-Za-z]+)\$?(\d+)$")
A1_RE = re.compile(
    r"^(?:'(?P<q>(?:[^']|'')*)'|\"(?P<d>[^\"]+)\"|(?P<u>[^'!]+))!(?P<a>\$?[A-Za-z]+\$?\d+)(?::(?P<b>\$?[A-Za-z]+\$?\d+))?$"
)
DROP_CATEGORY_TITLE = re.compile(
    r"^(date|dates|year|years|time|timestamp|x|x-axis|x axis)$", re.I
)
BTC_NAME = re.compile(r"bitcoin|\bbtc\b", re.I)
PALETTE = ["#F7931A", "#8B5CF6", "#0EA5E9", "#22C55E", "#EF4444", "#111827", "#EC4899", "#14B8A6"]
BASIC_TYPES = {"LINE", "AREA", "STEPPED_AREA", "COLUMN", "BAR", "SCATTER", "COMBO"}
UNSUPPORTED_KEYS = (
    ("pieChart", "pie"),
    ("bubbleChart", "bubble"),
    ("candlestickChart", "candlestick"),
    ("histogramChart", "histogram"),
    ("waterfallChart", "waterfall"),
    ("treemapChart", "treemap"),
    ("orgChart", "org"),
    ("scorecardChart", "scorecard"),
)
SERIES_TYPE = {
    "LINE": "line",
    "AREA": "area",
    "STEPPED_AREA": "area",
    "COLUMN": "column",
    "BAR": "bar",
    "SCATTER": "scatter",
}
INDEX_NOTE = (
    "Interactive chart data for the Sheets library. Built by scripts/sheets_data.py "
    "from a spreadsheets.get plus values.batchGet dump. The browser draws these files. "
    "View PNG still uses static/sheets/full/. Retired tabs in EXCLUDED_TABS and "
    "RETIRED_2026_11_17 are omitted."
)
# 2026-11-17 retirements. Kept here, not in sheets_catalog.EXCLUDED_TABS, so this
# branch does not conflict with PR #63. Monday's export must keep skipping these.
RETIRED_2026_11_17 = {
    ("bot1", 909478550),   # 215 Most profitable companies
    ("bot1", 1828414017),  # 185 r minus g
    ("bot1", 902803117),   # 182 Real fed funds rate
    ("bot1", 949971563),   # 209 Oracle cash burn
    ("bot1", 1234832613),  # 113 Discretionary vs Staples
    ("bot1", 727565635),   # 125 Junk vs IG spread ratio
    ("bot1", 673256883),   # 38 Copper-gold vs 10Y
    ("bot1", 254194955),   # 177 Real GDP US vs EU vs Japan
    ("bot1", 626951791),   # 199 Margin debt vs S&P
    ("bot1", 711603553),   # 323 Alphabet quarterly FCF
    ("bot2", 1149583871),  # 53 Dividends and buybacks vs legacy
    ("bot2", 2047295555),  # table tab retired 2026-11-17
    ("bot2", 84709656),    # table tab retired 2026-11-17
}

def tab_skipped(book, gid):
    """True for EXCLUDED_TABS or the 2026-11-17 retirement skip list."""
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return False
    if sheets_catalog.tab_excluded(book, gid):
        return True
    return (str(book), gid) in RETIRED_2026_11_17

LIVE_TTL = 300
_LIVE_CACHE = {}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def col_letters(index):
    n = int(index) + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def hex_to_color(value):
    h = str(value or "").lstrip("#")
    if len(h) != 6:
        return None
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def rgb_to_hex(color):
    if not isinstance(color, dict):
        return None
    src = color.get("rgbColor") if isinstance(color.get("rgbColor"), dict) else color
    if not isinstance(src, dict):
        return None
    if not any(k in src for k in ("red", "green", "blue")):
        return None

    def ch(key):
        try:
            x = float(src.get(key) or 0)
        except (TypeError, ValueError):
            return None
        if x <= 1:
            x = x * 255
        return max(0, min(255, int(round(x))))

    r, g, b = ch("red"), ch("green"), ch("blue")
    if None in (r, g, b):
        return None
    return "#%02X%02X%02X" % (r, g, b)


def series_color(series, index, name):
    explicit = rgb_to_hex(series.get("colorStyle")) or rgb_to_hex(series.get("color"))
    if explicit:
        return explicit
    if BTC_NAME.search(name or ""):
        return "#F7931A"
    return PALETTE[index % len(PALETTE)]


def clean_num(n):
    if n is None:
        return None
    if isinstance(n, bool):
        return None
    if isinstance(n, int):
        return n
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if math.isnan(n) or math.isinf(n):
        return None
    if abs(n - round(n)) < 1e-8:
        return int(round(n))
    return round(n, 4)


def parse_number(value):
    """Return (number or None, percent_string). percent_string means the text ended in %."""
    if value is None or value == "":
        return None, False
    if isinstance(value, bool):
        return None, False
    if isinstance(value, (int, float)):
        return clean_num(value), False
    text = str(value).strip()
    if not text or text in {"—", "-", "–", "−", "#N/A", "#DIV/0!", "#VALUE!", "#REF!"}:
        return None, False
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    text = text.replace("$", "").replace(",", "").replace(" ", "")
    neg = False
    if text.startswith("(") and text.endswith(")"):
        neg = True
        text = text[1:-1]
    try:
        n = float(text)
    except ValueError:
        return None, False
    if neg:
        n = -n
    return clean_num(n), percent


def strip_category_title(title):
    text = " ".join(str(title or "").split())
    if not text or DROP_CATEGORY_TITLE.fullmatch(text):
        return ""
    return text


def _looks_like_serial(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return 20000 <= float(value) <= 80000


def serial_to_iso(value):
    base = datetime.datetime(1899, 12, 30)
    day = base + datetime.timedelta(days=float(value))
    return day.date().isoformat()


def _axis_wants_dates(axis):
    if not isinstance(axis, dict):
        return False
    title = " ".join(str(axis.get("title") or "").split())
    if DROP_CATEGORY_TITLE.fullmatch(title or ""):
        return True
    fmt = ((axis.get("format") or {}).get("numberFormat") or {})
    kind = str(fmt.get("type") or "").upper()
    return kind in {"DATE", "DATE_TIME", "TIME"}


def _number_format_type(axis):
    if not isinstance(axis, dict):
        return ""
    fmt = axis.get("format") or {}
    raw = fmt.get("numberFormat") or fmt.get("numberFormatType") or ""
    if isinstance(raw, dict):
        raw = raw.get("type") or ""
    return str(raw or "").upper()


def book_for(spreadsheet):
    sid = str((spreadsheet or {}).get("spreadsheetId") or "").strip()
    if not sid:
        raise ValueError("dump is missing spreadsheetId")
    if sid in sheets_catalog.BY_ID:
        bid, name = sheets_catalog.BY_ID[sid]
        return bid, name, sid
    name = ((spreadsheet.get("properties") or {}).get("title") or sid).strip()
    return "x" + sid[:8], name, sid


def parse_a1(range_name):
    """Return (sheet title, start col, start row) 0-based. None when the range is unusable."""
    text = str(range_name or "").strip()
    m = A1_RE.fullmatch(text)
    if not m:
        return None
    title = m.group("q") or m.group("d") or m.group("u") or ""
    title = title.replace("''", "'").strip()
    cell = CELL_RE.fullmatch(m.group("a") or "")
    if not cell:
        return None
    return title, col_index(cell.group(1)), int(cell.group(2)) - 1


def iter_sources(chart):
    spec = (chart or {}).get("spec") or {}
    basic = spec.get("basicChart") or {}
    for domain in basic.get("domains") or []:
        data = (domain or {}).get("domain") or {}
        for src in ((data.get("sourceRange") or {}).get("sources") or []):
            if isinstance(src, dict):
                yield src
    for series in basic.get("series") or []:
        data = (series or {}).get("series") or {}
        for src in ((data.get("sourceRange") or {}).get("sources") or []):
            if isinstance(src, dict):
                yield src


def grid_to_a1(title, src, row_cap=5000):
    r0 = int(src.get("startRowIndex") or 0)
    c0 = int(src.get("startColumnIndex") or 0)
    r1 = src.get("endRowIndex")
    c1 = src.get("endColumnIndex")
    if r1 is None:
        r1 = r0 + row_cap
    if c1 is None:
        c1 = c0 + 1
    r1 = min(int(r1), r0 + row_cap)
    c1 = max(int(c1), c0 + 1)
    if r1 <= r0:
        r1 = r0 + 1
    safe = str(title or "Sheet").replace("'", "''")
    return "'%s'!%s%d:%s%d" % (safe, col_letters(c0), r0 + 1, col_letters(c1 - 1), r1)


def value_ranges_of(doc):
    if not isinstance(doc, dict):
        return []
    if isinstance(doc.get("valueRanges"), list):
        return doc["valueRanges"]
    if isinstance(doc.get("values"), list):
        return doc["values"]
    return []


def build_cells(value_doc, title_to_id):
    cells = {}
    for block in value_ranges_of(value_doc):
        if not isinstance(block, dict):
            continue
        parsed = parse_a1(block.get("range"))
        if not parsed:
            continue
        title, c0, r0 = parsed
        sid = title_to_id.get(title)
        if sid is None and len(title_to_id) == 1:
            sid = next(iter(title_to_id.values()))
        if sid is None:
            continue
        for i, row in enumerate(block.get("values") or []):
            if not isinstance(row, list):
                continue
            for j, val in enumerate(row):
                cells[(sid, r0 + i, c0 + j)] = val
    return cells


def _sheet_rows(cells, sid):
    rows = [r for (s, r, _c) in cells if s == sid]
    return max(rows) + 1 if rows else 0


def _bounds(src, cells):
    sid = src.get("sheetId")
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return None
    r0 = int(src.get("startRowIndex") or 0)
    c0 = int(src.get("startColumnIndex") or 0)
    r1 = src.get("endRowIndex")
    c1 = src.get("endColumnIndex")
    if c1 is None:
        c1 = c0 + 1
    c1 = int(c1)
    if r1 is None:
        r1 = max(_sheet_rows(cells, sid), r0)
    return sid, r0, c0, int(r1), c1



def _src_shape(src, cells):
    """Return ('row'|'col'|None, sid, r0, c0, r1, c1)."""
    b = _bounds(src, cells)
    if not b:
        return None
    sid, r0, c0, r1, c1 = b
    rows, cols = r1 - r0, c1 - c0
    if rows <= 0 or cols <= 0:
        return None
    if rows == 1 and cols > 1:
        return ("row", sid, r0, c0, r1, c1)
    return ("col", sid, r0, c0, r1, c1)


def _first_source(data):
    sources = ((data or {}).get("sourceRange") or {}).get("sources") or []
    for src in sources:
        if isinstance(src, dict):
            return src
    return None


def _axis_by_position(basic):
    out = {}
    for axis in basic.get("axis") or []:
        if isinstance(axis, dict) and axis.get("position"):
            out[str(axis["position"]).upper()] = axis
    return out


def _category_position(chart_type):
    return "LEFT_AXIS" if chart_type == "BAR" else "BOTTOM_AXIS"


def _log_axis(axis):
    if not isinstance(axis, dict):
        return False
    if axis.get("logScale") is True:
        return True
    return str(axis.get("scaleType") or "").upper() in {"LOG", "LOG_SCALE"}


def _window(axis):
    if not isinstance(axis, dict):
        return None, None
    vw = axis.get("viewWindowOptions") or {}
    if not isinstance(vw, dict):
        return None, None
    mode = str(vw.get("viewWindowMode") or "").upper()
    lo = hi = None
    if mode == "EXPLICIT" or "viewWindowMin" in vw:
        if isinstance(vw.get("viewWindowMin"), (int, float)) and not isinstance(vw.get("viewWindowMin"), bool):
            lo = vw["viewWindowMin"]
    if mode == "EXPLICIT" or "viewWindowMax" in vw:
        if isinstance(vw.get("viewWindowMax"), (int, float)) and not isinstance(vw.get("viewWindowMax"), bool):
            hi = vw["viewWindowMax"]
    return lo, hi


def _labels_on(series):
    label = series.get("dataLabel") or {}
    if not isinstance(label, dict) or not label:
        return False
    kind = str(label.get("type") or "DATA").upper()
    return kind not in {"NONE", "DATA_LABEL_TYPE_UNSPECIFIED"}


def _category_label(raw, formatted, axis):
    if formatted not in (None, ""):
        text = " ".join(str(formatted).split())
        if text and text not in {"—", "-", "–"}:
            return text
    if _looks_like_serial(raw) and _axis_wants_dates(axis):
        return serial_to_iso(raw)
    if raw is None or raw == "":
        return None
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    if isinstance(raw, int) and not isinstance(raw, bool):
        return str(raw)
    text = " ".join(str(raw).split())
    return text or None


def _series_number(raw, formatted, percent_axis):
    if percent_axis and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if abs(float(raw)) <= 10:
            return clean_num(float(raw) * 100)
        return clean_num(raw)
    if isinstance(formatted, str) and formatted.strip().endswith("%"):
        n, _ = parse_number(formatted)
        return n
    if raw not in (None, ""):
        n, percent = parse_number(raw)
        if percent:
            return n
        return n
    if formatted not in (None, ""):
        n, _ = parse_number(formatted)
        return n
    return None


def _header_name(cells, sid, col, row):
    raw = cells.get((sid, row, col))
    if raw is None or raw == "":
        return ""
    text = " ".join(str(raw).split())
    return text


def _value_format(numbers, percent):
    if percent:
        return "percent"
    present = [n for n in numbers if isinstance(n, (int, float)) and not isinstance(n, bool)]
    if present and all(isinstance(n, int) or float(n).is_integer() for n in present):
        if max(abs(n) for n in present) >= 100:
            return "integer"
    return "number"


def unsupported(chart_id, kind, reason):
    return {
        "supported": False,
        "chartId": chart_id,
        "type": kind,
        "reason": reason,
    }



def _parse_category_date(label):
    text = " ".join(str(label or "").split())
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m", "%b %Y", "%b %d, %Y"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    m = re.fullmatch(r"Q([1-4])(?:-|'')?\s*(\d{2,4})", text, re.I)
    if m:
        year = int(m.group(2))
        if year < 100:
            year += 2000
        return datetime.datetime(year, int(m.group(1)) * 3, 1)
    if text.upper() == "QTD":
        return datetime.datetime(2099, 12, 31)
    return None


def _categories_are_descending(categories):
    """True when labels are a time series stored newest-first."""
    dates = []
    for lab in categories or []:
        d = _parse_category_date(lab)
        if d:
            dates.append(d)
    if len(dates) < 3:
        return False
    decreasing = all(dates[i] >= dates[i + 1] for i in range(len(dates) - 1))
    return decreasing and dates[0] > dates[-1]


def convert_chart(chart, cells, formatted_cells, sheet_title):
    spec = (chart or {}).get("spec") or {}
    chart_id = chart.get("chartId") if isinstance(chart, dict) else None
    if spec.get("threeDimensional") or ((spec.get("basicChart") or {}).get("threeDimensional")):
        return unsupported(chart_id, "3d", "3D charts are not supported yet")
    basic = spec.get("basicChart") if isinstance(spec.get("basicChart"), dict) else None
    if not basic:
        for key, kind in UNSUPPORTED_KEYS:
            if isinstance(spec.get(key), dict):
                return unsupported(chart_id, kind, "%s charts are not supported yet" % kind)
        return unsupported(chart_id, "unknown", "this chart has no basic chart spec")
    chart_type = str(basic.get("chartType") or "").upper()
    if chart_type not in BASIC_TYPES:
        return unsupported(chart_id, chart_type.lower() or "unknown", "chart type %s is not supported yet" % (chart_type or "UNKNOWN"))
    domain_src = _first_source(((basic.get("domains") or [{}])[0] or {}).get("domain"))
    if not domain_src:
        return unsupported(chart_id, SERIES_TYPE.get(chart_type, "unknown"), "chart has no domain range")
    bounds = _bounds(domain_src, cells)
    if not bounds:
        return unsupported(chart_id, SERIES_TYPE.get(chart_type, "unknown"), "chart domain sheet is missing")
    sid, r0, c0, r1, c1 = bounds
    try:
        header = int(basic.get("headerCount") or 0)
    except (TypeError, ValueError):
        header = 0
    if header < 0:
        header = 0
    row_oriented = (r1 - r0) == 1 and (c1 - c0) > 1
    if not row_oriented:
        data_r0 = r0 + header
        if data_r0 >= r1:
            return unsupported(chart_id, SERIES_TYPE.get(chart_type, "unknown"), "chart has no data rows")
    axes = _axis_by_position(basic)
    cat_pos = _category_position(chart_type)
    cat_axis = axes.get(cat_pos) or {}
    categories = []
    rows = []
    cat_cols = []
    if row_oriented:
        # Categories across a single row (Sheets often lays a small column chart this way).
        for c in range(c0, c1):
            raw = cells.get((sid, r0, c))
            fmt = formatted_cells.get((sid, r0, c))
            label = _category_label(raw, fmt, cat_axis)
            if not label:
                continue
            categories.append(label)
            cat_cols.append(c)
        if not categories:
            return unsupported(chart_id, SERIES_TYPE.get(chart_type, "unknown"), "chart has no category labels")
    else:
        for r in range(data_r0, r1):
            raw = cells.get((sid, r, c0))
            fmt = formatted_cells.get((sid, r, c0))
            label = _category_label(raw, fmt, cat_axis)
            if not label:
                continue
            categories.append(label)
            rows.append(r)
        if not categories:
            return unsupported(chart_id, SERIES_TYPE.get(chart_type, "unknown"), "chart has no category labels")
    raw_series = []
    for s in basic.get("series") or []:
        if not isinstance(s, dict):
            continue
        src = _first_source(s.get("series"))
        if not src:
            continue
        sb = _bounds(src, cells)
        if not sb:
            continue
        _ssid, sr0, sc0, sr1, sc1 = sb
        if row_oriented or ((sr1 - sr0) == 1 and (sc1 - sc0) > 1):
            raw_series.append((s, "row", sr0, sc0, sr1, sc1, True))
        else:
            width = max(1, sc1 - sc0)
            for offset in range(width):
                raw_series.append((s, "col", sr0, sc0 + offset, sr1, sc1, offset == 0))
    if not raw_series:
        return unsupported(chart_id, SERIES_TYPE.get(chart_type, "unknown"), "chart has no series range")
    built = []
    for i, (s, orient, sr0, sc0, sr1, sc1, primary) in enumerate(raw_series):
        if orient == "row":
            name = _header_name(cells, sid, sc0 - 1, sr0) if sc0 > 0 else ""
            if not name:
                name = _header_name(cells, sid, sc0, sr0) if header else ""
        else:
            name = _header_name(cells, sid, sc0, sr0) if header else ""
        if not name:
            name = "Series %d" % (i + 1)
        stype = str(s.get("type") or chart_type).upper()
        if stype not in SERIES_TYPE:
            stype = chart_type if chart_type in SERIES_TYPE else "LINE"
        mapped = SERIES_TYPE[stype]
        target = str(s.get("targetAxis") or "LEFT_AXIS").upper()
        axis_name = "right" if target == "RIGHT_AXIS" else "left"
        value_pos = "BOTTOM_AXIS" if chart_type == "BAR" and axis_name == "left" else ("RIGHT_AXIS" if axis_name == "right" else "LEFT_AXIS")
        value_axis = axes.get(value_pos) or {}
        percent = _number_format_type(value_axis) == "PERCENT"
        data = []
        saw_percent_text = False
        if orient == "row":
            for c in cat_cols:
                raw = cells.get((sid, sr0, c))
                fmt = formatted_cells.get((sid, sr0, c))
                if isinstance(raw, str) and raw.strip().endswith("%"):
                    saw_percent_text = True
                if isinstance(fmt, str) and fmt.strip().endswith("%"):
                    saw_percent_text = True
                data.append(_series_number(raw, fmt, percent))
        else:
            col = sc0
            for r in rows:
                raw = cells.get((sid, r, col))
                fmt = formatted_cells.get((sid, r, col))
                if isinstance(raw, str) and raw.strip().endswith("%"):
                    saw_percent_text = True
                if isinstance(fmt, str) and fmt.strip().endswith("%"):
                    saw_percent_text = True
                data.append(_series_number(raw, fmt, percent))
        if not any(v is not None for v in data):
            continue
        if saw_percent_text and not percent:
            percent = True
            data = []
            if orient == "row":
                for c in cat_cols:
                    data.append(_series_number(cells.get((sid, sr0, c)), formatted_cells.get((sid, sr0, c)), True))
            else:
                for r in rows:
                    data.append(_series_number(cells.get((sid, r, sc0)), formatted_cells.get((sid, r, sc0)), True))
        built.append({
            "name": name,
            "type": mapped,
            "axis": axis_name,
            "color": series_color(s if primary else {}, i, name),
            "data": data,
            "labels": _labels_on(s) if primary else False,
            "smooth": bool(basic.get("lineSmoothing")) and mapped == "line",
            "step": chart_type == "STEPPED_AREA" or stype == "STEPPED_AREA",
            "_percent": percent,
            "_value_axis": value_axis,
        })
    if not built:
        return unsupported(chart_id, SERIES_TYPE.get(chart_type, "unknown"), "chart has no numeric values")
    horizontal = chart_type == "BAR" or (built and all(s["type"] == "bar" for s in built))
    types = {s["type"] for s in built}
    right = any(s["axis"] == "right" for s in built)
    if chart_type == "COMBO" or len(types) > 1 or right:
        out_type = "combo" if (len(types) > 1 or chart_type == "COMBO") else ("line" if types == {"line"} else next(iter(types)))
        if len(types) > 1 or chart_type == "COMBO":
            out_type = "combo"
    else:
        out_type = next(iter(types))
    if horizontal and out_type == "column":
        out_type = "bar"
    stacked_raw = str(basic.get("stackedType") or "NOT_STACKED").upper()
    stacked = None
    if stacked_raw == "STACKED":
        stacked = "stacked"
    elif stacked_raw == "PERCENT_STACKED":
        stacked = "percent"
        totals = []
        for idx in range(len(categories)):
            total = 0
            for s in built:
                v = s["data"][idx]
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    total += v
            totals.append(total)
        for s in built:
            converted = []
            for idx, v in enumerate(s["data"]):
                total = totals[idx]
                if v is None or not total:
                    converted.append(None)
                else:
                    converted.append(clean_num(float(v) * 100 / total))
            s["data"] = converted
            s["_percent"] = True
    y_axes = []
    for axis_name, pos in (("left", "BOTTOM_AXIS" if horizontal else "LEFT_AXIS"), ("right", "RIGHT_AXIS")):
        members = [s for s in built if s["axis"] == axis_name]
        if not members:
            continue
        axis = members[0]["_value_axis"] if isinstance(members[0].get("_value_axis"), dict) else {}
        title = "" if pos == cat_pos else " ".join(str(axis.get("title") or "").split())
        if pos == cat_pos:
            title = ""
        percent = any(s.get("_percent") for s in members)
        numbers = [v for s in members for v in s["data"]]
        entry = {
            "title": title,
            "format": _value_format(numbers, percent),
            "log": _log_axis(axis),
        }
        lo, hi = _window(axis)
        # A PERCENT axis view window is in sheet fractions (1 = 100%). Data was scaled to points.
        if percent:
            if lo is not None and abs(float(lo)) <= 10:
                lo = clean_num(float(lo) * 100)
            if hi is not None and abs(float(hi)) <= 10:
                hi = clean_num(float(hi) * 100)
        if lo is not None:
            entry["min"] = lo
        if hi is not None:
            entry["max"] = hi
        y_axes.append(entry)
    if not y_axes:
        y_axes = [{"title": "", "format": "number", "log": False}]
    for s in built:
        s.pop("_percent", None)
        s.pop("_value_axis", None)
    x_title = "" if cat_pos else ""
    x_title = strip_category_title((axes.get(cat_pos) or {}).get("title"))
    legend = str(basic.get("legendPosition") or "").upper()
    if _categories_are_descending(categories):
        categories = list(reversed(categories))
        for s in built:
            s["data"] = list(reversed(s.get("data") or []))
    return {
        "supported": True,
        "chartId": chart_id,
        "title": sheets_catalog.chart_text(chart),
        "subtitle": " ".join(str(spec.get("subtitle") or "").split()),
        "type": out_type,
        "horizontal": bool(horizontal and out_type == "bar"),
        "stacked": stacked,
        "connectNulls": bool(basic.get("interpolateNulls")),
        "legend": legend not in {"NO_LEGEND", "NONE"},
        "categories": categories,
        "x": {"title": x_title},
        "y": y_axes,
        "series": built,
        "tab": sheet_title,
    }


def convert_bundle(bundle):
    """Return (charts, warnings). charts include supported False rows for skips."""
    if not isinstance(bundle, dict):
        raise ValueError("dump must be a JSON object")
    spreadsheet = bundle.get("spreadsheet") if isinstance(bundle.get("spreadsheet"), dict) else bundle
    if not isinstance(spreadsheet, dict) or not spreadsheet.get("sheets") and not spreadsheet.get("spreadsheetId"):
        raise ValueError("dump has no spreadsheet")
    book, workbook, _sid = book_for(spreadsheet)
    if not BOOK_RE.fullmatch(book):
        raise ValueError("workbook id is not safe to write: %r" % book)
    title_to_id = {}
    for sheet in spreadsheet.get("sheets") or []:
        props = (sheet or {}).get("properties") or {}
        title = str(props.get("title") or "").strip()
        gid = props.get("sheetId")
        if title and gid is not None:
            try:
                title_to_id[title] = int(gid)
            except (TypeError, ValueError):
                continue
    values = bundle.get("values") if isinstance(bundle.get("values"), dict) else {"valueRanges": spreadsheet.get("valueRanges") or []}
    formatted = bundle.get("formatted") if isinstance(bundle.get("formatted"), dict) else {}
    cells = build_cells(values, title_to_id)
    formatted_cells = build_cells(formatted, title_to_id)
    sample = bool(bundle.get("sample"))
    charts, warnings = [], []
    order = {bid: i for i, (bid, _name, _sid) in enumerate(sheets_catalog.BOOKS)}
    sheets = sorted(spreadsheet.get("sheets") or [], key=lambda s: ((s.get("properties") or {}).get("index") or 0))
    for sheet in sheets:
        props = (sheet or {}).get("properties") or {}
        if props.get("hidden"):
            continue
        tab = str(props.get("title") or "").strip()
        try:
            gid = int(props.get("sheetId"))
        except (TypeError, ValueError):
            continue
        if tab_skipped(book, gid):
            warnings.append("excluded %s %s (%s)" % (book, gid, tab))
            charts.append({
                "supported": False, "book": book, "gid": gid, "excluded": True,
                "reason": "retired tab", "tab": tab,
            })
            continue
        ordered = sheets_catalog.charts_sorted(sheet)
        if ordered is None:
            warnings.append(
                "%s gid %s (%s): %s charts and no position data; interactive export left unchanged"
                % (book, gid, tab, len(sheet.get("charts") or []))
            )
            continue
        for n, chart in enumerate(ordered, 1):
            converted = convert_chart(chart, cells, formatted_cells, tab)
            converted["book"] = book
            converted["gid"] = gid
            converted["n"] = n
            converted["workbook"] = workbook
            converted["tab"] = tab
            converted["source"] = "%s · %s" % (workbook, tab)
            if sample:
                converted["sample"] = True
            if not converted.get("title"):
                converted["title"] = tab if len(ordered) == 1 else "Chart %d" % n
            if not converted.get("supported"):
                warnings.append(
                    "%s gid %s chart %s: %s" % (book, gid, n, converted.get("reason") or "skipped")
                )
            charts.append(converted)
    charts.sort(key=lambda c: (order.get(c.get("book"), 99), c.get("gid") or 0, c.get("n") or 0))
    return charts, warnings


def data_rel(book, gid, n):
    n = int(n)
    suffix = "" if n <= 1 else "_%d" % n
    return "sheets/data/%s/%s%s.json" % (book, int(gid), suffix)


def _data_path(repo, book, gid, n):
    rel = data_rel(book, gid, n)
    root = os.path.realpath(os.path.join(repo, "static", "sheets", "data"))
    path = os.path.realpath(os.path.join(repo, "static", rel))
    if os.path.commonpath([root, path]) != root:
        raise ValueError("refusing path outside static/sheets/data")
    return path


def clear_gid(repo, book, gid):
    folder = os.path.join(repo, "static", "sheets", "data", str(book))
    if not os.path.isdir(folder):
        return []
    removed = []
    prefix_gid = str(int(gid))
    for fn in os.listdir(folder):
        m = FILE_RE.fullmatch(fn)
        if not m or m.group(1) != prefix_gid:
            continue
        path = os.path.join(folder, fn)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    return removed


def prune_excluded(repo):
    removed = []
    skipped = set(sheets_catalog.EXCLUDED_TABS) | set(RETIRED_2026_11_17)
    for book, gid in sorted(skipped):
        removed.extend(clear_gid(repo, book, gid))
    return removed


def _public_chart(chart, generated):
    keep = (
        "version", "generated", "book", "gid", "n", "chartId", "title", "subtitle",
        "type", "horizontal", "stacked", "connectNulls", "legend", "categories",
        "x", "y", "series", "source", "tab", "workbook", "sample", "supported",
    )
    out = {"version": 1, "generated": generated, "supported": True}
    for key in keep:
        if key in chart and key not in out:
            out[key] = chart[key]
    return out


def write_chart(repo, chart, generated):
    path = _data_path(repo, chart["book"], chart["gid"], chart["n"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = _public_chart(chart, generated)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def rebuild_index(repo, generated=None):
    root = os.path.join(repo, "static", "sheets", "data")
    os.makedirs(root, exist_ok=True)
    rows = []
    if os.path.isdir(root):
        for book in sorted(os.listdir(root)):
            folder = os.path.join(root, book)
            if not os.path.isdir(folder) or not BOOK_RE.fullmatch(book):
                continue
            for fn in sorted(os.listdir(folder)):
                parsed = FILE_RE.fullmatch(fn)
                if not parsed:
                    continue
                gid, n = int(parsed.group(1)), int(parsed.group(2) or 1)
                if tab_skipped(book, gid):
                    os.remove(os.path.join(folder, fn))
                    continue
                path = os.path.join(folder, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        doc = json.load(f)
                except (OSError, ValueError):
                    continue
                if not isinstance(doc, dict) or doc.get("supported") is False:
                    continue
                if str(doc.get("book")) != book or int(doc.get("gid") or -1) != gid or int(doc.get("n") or -1) != n:
                    continue
                rows.append({
                    "book": book,
                    "gid": gid,
                    "n": n,
                    "type": doc.get("type") or "",
                    "title": doc.get("title") or "",
                    "tab": doc.get("tab") or "",
                    "supported": True,
                    "file": data_rel(book, gid, n),
                })
    doc = {
        "generated": generated or _now(),
        "note": INDEX_NOTE,
        "charts": rows,
    }
    dest = os.path.join(root, "index.json")
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, dest)
    return doc


def export_bundles(bundles, repo):
    generated = _now()
    written, skipped = [], []
    warnings = []
    touched = set()
    held = set()
    for bundle in bundles:
        charts, notes = convert_bundle(bundle)
        warnings.extend(notes)
        by_gid = {}
        for chart in charts:
            by_gid.setdefault((chart.get("book"), chart.get("gid")), []).append(chart)
        for key, group in by_gid.items():
            book, gid = key
            if any(c.get("excluded") for c in group):
                clear_gid(repo, book, gid)
                continue
            if not group:
                continue
            # A held multi-chart tab is absent from `charts`. Presence means we can replace.
            clear_gid(repo, book, gid)
            touched.add(key)
            for chart in group:
                if not chart.get("supported"):
                    skipped.append("%s/%s/%s" % (book, gid, chart.get("n")))
                    continue
                written.append(write_chart(repo, chart, generated))
        # charts_sorted None never entered `charts`. Remember those gids so we don't claim we rewrote them.
        spreadsheet = bundle.get("spreadsheet") if isinstance(bundle.get("spreadsheet"), dict) else bundle
        try:
            book, _name, _sid = book_for(spreadsheet)
        except ValueError:
            continue
        for sheet in spreadsheet.get("sheets") or []:
            props = (sheet or {}).get("properties") or {}
            try:
                gid = int(props.get("sheetId"))
            except (TypeError, ValueError):
                continue
            if sheets_catalog.charts_sorted(sheet) is None:
                held.add((book, gid))
    prune_excluded(repo)
    index = rebuild_index(repo, generated)
    return {"written": written, "skipped": skipped, "warnings": warnings, "index": index, "held": sorted(held)}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def as_bundle(doc, values=None, formatted=None):
    if isinstance(doc, dict) and isinstance(doc.get("spreadsheet"), dict):
        bundle = {
            "spreadsheet": doc["spreadsheet"],
            "values": doc.get("values") if isinstance(doc.get("values"), dict) else (values or {"valueRanges": []}),
            "sample": bool(doc.get("sample")),
        }
        formatted_doc = doc.get("formatted") if isinstance(doc.get("formatted"), dict) else formatted
        if formatted_doc:
            bundle["formatted"] = formatted_doc
        return bundle
    bundle = {
        "spreadsheet": doc,
        "values": values if isinstance(values, dict) else {"valueRanges": (doc or {}).get("valueRanges") or []},
    }
    if isinstance(formatted, dict):
        bundle["formatted"] = formatted
    if isinstance(doc, dict) and doc.get("sample"):
        bundle["sample"] = True
    return bundle


def pair_inputs(dump_paths, value_paths, formatted_paths):
    dumps = [(p, load_json(p)) for p in dump_paths]
    values = [(p, load_json(p)) for p in value_paths]
    formatted = [(p, load_json(p)) for p in formatted_paths]
    used_v, used_f = set(), set()
    bundles = []
    for path, doc in dumps:
        if isinstance(doc, dict) and isinstance(doc.get("spreadsheet"), dict):
            bundles.append(as_bundle(doc))
            continue
        sid = str((doc or {}).get("spreadsheetId") or "")
        found_v = found_f = None
        for i, (vp, vdoc) in enumerate(values):
            if i in used_v:
                continue
            vsid = str((vdoc or {}).get("spreadsheetId") or "")
            if vsid == sid or (len(dumps) == 1 and len(values) == 1):
                found_v, used_v = vdoc, used_v | {i}
                break
        for i, (fp, fdoc) in enumerate(formatted):
            if i in used_f:
                continue
            fsid = str((fdoc or {}).get("spreadsheetId") or "")
            if fsid == sid or (len(dumps) == 1 and len(formatted) == 1):
                found_f, used_f = fdoc, used_f | {i}
                break
        if found_v is None and not (isinstance(doc, dict) and doc.get("valueRanges")):
            raise SystemExit(path + " has no cell values. Pass a bundle, or --values for that spreadsheet.")
        bundles.append(as_bundle(doc, found_v, found_f))
    if not bundles:
        raise SystemExit("no dumps given")
    return bundles


def _round(value, digits):
    if digits <= 0:
        return int(round(value))
    return round(float(value), digits)


def _month_index(ym):
    y, m = ym.split("-")
    return int(y) * 12 + int(m)


def _month_label(idx):
    y, m = divmod(idx, 12)
    if m == 0:
        y -= 1
        m = 12
    return "%04d-%02d" % (y, m)


def fill_months(keys, digits=0, wiggle=0, floor=None):
    pts = [(_month_index(ym), float(v)) for ym, v in keys]
    out = []
    for (i0, v0), (i1, v1) in zip(pts, pts[1:]):
        span = max(1, i1 - i0)
        for k in range(span):
            t = k / span
            val = v0 + (v1 - v0) * t
            if wiggle and not (v0 == 0 and v1 == 0):
                val += wiggle * ((((i0 + k) * 17) % 9) - 4) / 4.0
            if floor is not None:
                val = max(floor, val)
            out.append((_month_label(i0 + k), _round(val, digits)))
    last_i, last_v = pts[-1]
    out.append((_month_label(last_i), _round(last_v, digits)))
    return out


def fill_days(keys, step=7, digits=1, wiggle=0, floor=0):
    pts = [(datetime.datetime.strptime(d, "%Y-%m-%d"), float(v)) for d, v in keys]
    out = []
    seen = set()
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        days = max(1, (d1 - d0).days)
        n = max(1, int(round(days / float(step))))
        for k in range(n):
            t = k / float(n)
            day = d0 + datetime.timedelta(days=int(round(days * t)))
            val = v0 + (v1 - v0) * t
            if wiggle and not (v0 == 0 and v1 == 0):
                val += wiggle * ((((k + day.toordinal()) * 13) % 7) - 3) / 3.0
            if floor is not None:
                val = max(floor, val)
            label = day.strftime("%Y-%m-%d")
            if label in seen:
                continue
            seen.add(label)
            out.append((label, _round(val, digits)))
    last = pts[-1][0].strftime("%Y-%m-%d")
    if last not in seen:
        out.append((last, _round(pts[-1][1], digits)))
    return out


def _rows(headers, columns):
    width = len(headers)
    height = max(len(c) for c in columns)
    rows = [list(headers)]
    for i in range(height):
        rows.append([c[i] if i < len(c) else "" for c in columns])
    if len(rows[0]) != width:
        raise ValueError("header width")
    return rows


def _chart(chart_id, title, chart_type, domain, series, row, axes, legend="BOTTOM_LEGEND", subtitle="", header=1, stacked=None, smooth=False):
    basic = {
        "chartType": chart_type,
        "legendPosition": legend,
        "headerCount": header,
        "interpolateNulls": False,
        "lineSmoothing": smooth,
        "axis": axes,
        "domains": [{"domain": {"sourceRange": {"sources": [domain]}}}],
        "series": series,
        "stackedType": stacked or "NOT_STACKED",
    }
    spec = {"title": title, "basicChart": basic}
    if subtitle:
        spec["subtitle"] = subtitle
    return {
        "chartId": chart_id,
        "spec": spec,
        "position": {"overlayPosition": {"anchorCell": {"rowIndex": row, "columnIndex": 0}}},
    }


def _series(source, color, typ=None, axis="LEFT_AXIS", labels=False):
    item = {
        "series": {"sourceRange": {"sources": [source]}},
        "targetAxis": axis,
        "color": hex_to_color(color),
    }
    if typ:
        item["type"] = typ
    if labels:
        item["dataLabel"] = {"type": "DATA"}
    return item


def _grid(sheet_id, r0, c0, r1, c1):
    return {
        "sheetId": sheet_id,
        "startRowIndex": r0,
        "endRowIndex": r1,
        "startColumnIndex": c0,
        "endColumnIndex": c1,
    }


def _range(title, c0, r0, c1, r1, rows):
    return {"range": "'%s'!%s%d:%s%d" % (title.replace("'", "''"), col_letters(c0), r0 + 1, col_letters(c1), r1), "values": rows}


BTC_HOLDINGS = [
    ("9/30/20", 38250), ("12/31/20", 70469), ("3/31/21", 91326), ("6/30/21", 105085),
    ("9/30/21", 114042), ("12/31/21", 124391), ("3/31/22", 129218), ("6/30/22", 129699),
    ("9/30/22", 130000), ("12/31/22", 132500), ("3/31/23", 140006), ("6/30/23", 152800),
    ("9/30/23", 158245), ("12/31/23", 189150), ("3/31/24", 214278), ("6/30/24", 226331),
    ("9/30/24", 252220), ("12/31/24", 447470), ("3/31/25", 528185), ("6/30/25", 597325),
    ("9/30/25", 640031), ("12/31/25", 672500), ("3/31/26", 762099), ("6/30/26", 846000),
    ("9/30/26", 847666), ("11/16/26", 850364),
]
QQ_CHANGE = [
    ("Q4'20", 84.2), ("Q1'21", 29.6), ("Q2'21", 15.1), ("Q3'21", 8.5), ("Q4'21", 9.1),
    ("Q1'22", 3.9), ("Q2'22", 0.4), ("Q3'22", 0.2), ("Q4'22", 1.9), ("Q1'23", 5.7),
    ("Q2'23", 9.1), ("Q3'23", 3.6), ("Q4'23", 19.5), ("Q1'24", 13.3), ("Q2'24", 5.6),
    ("Q3'24", 11.4), ("Q4'24", 77.4), ("Q1'25", 18.0), ("Q2'25", 13.1), ("Q3'25", 7.1),
    ("Q4'25", 5.1), ("Q1'26", 13.3), ("Q2'26", 1.0), ("Q3'26", 0.2), ("QTD", 0.5),
]
SATS = [
    ("112,135", 91097), ("121,425", 98443), ("118,703", 102102), ("124,470", 107273),
    ("182,063", 158827), ("198,433", 176266), ("212,603", 190100), ("222,923", 199985),
    ("215,502", 194986), ("220,118", 201170), ("227,656", 210818), ("200,879", 187712),
    ("201,340", 186826),
]


def _mnav_points():
    return fill_days([
        ("2025-10-06", 1.50), ("2025-10-20", 1.40), ("2025-11-03", 1.30), ("2025-11-17", 1.18),
        ("2025-12-01", 1.16), ("2025-12-22", 1.12), ("2026-01-12", 1.02), ("2026-01-26", 1.06),
        ("2026-02-16", 1.20), ("2026-03-09", 1.18), ("2026-03-30", 1.16), ("2026-04-13", 1.10),
        ("2026-05-11", 1.30), ("2026-06-01", 1.24), ("2026-06-22", 1.10), ("2026-07-20", 1.00),
        ("2026-07-27", 1.04), ("2026-08-17", 1.02), ("2026-09-07", 1.12), ("2026-09-28", 1.18),
    ], step=7, digits=2, wiggle=0.012, floor=0.9)


def sample_bundles():
    """Pilot dumps transcribed from the committed PNGs. Not a live Sheets pull."""
    bot3 = "1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU"
    bot2 = "1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ"
    bot1 = "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY"
    debt = 1145735379
    mnav = _mnav_points()
    debt_values = [
        _range("MSTR Debt", 0, 0, 1, 1 + len(BTC_HOLDINGS), _rows(["Date", "Bitcoin"], [[a for a, _ in BTC_HOLDINGS], [b for _, b in BTC_HOLDINGS]])),
        _range("MSTR Debt", 3, 0, 4, 1 + len(QQ_CHANGE), _rows(["Quarter", "Q/Q change"], [[a for a, _ in QQ_CHANGE], [v / 100 for _, v in QQ_CHANGE]])),
        _range("MSTR Debt", 6, 0, 7, 1 + len(SATS), _rows(["Shares", "Sats per share"], [[a for a, _ in SATS], [b for _, b in SATS]])),
        _range("MSTR Debt", 9, 0, 10, 1 + len(mnav), _rows(["Date", "mNAV"], [[a for a, _ in mnav], [b for _, b in mnav]])),
    ]
    debt_fmt = [
        _range("MSTR Debt", 3, 0, 4, 1 + len(QQ_CHANGE), _rows(["Quarter", "Q/Q change"], [[a for a, _ in QQ_CHANGE], ["%s%%" % v for _, v in QQ_CHANGE]])),
    ]
    orange, black, gray = "#F7931A", "#202124", "#9AA0A6"
    blue, red, violet = "#4285F4", "#DB4437", "#8B5CF6"
    debt_sheet = {
        "properties": {"sheetId": debt, "title": "MSTR Debt", "index": 0},
        "charts": [
            _chart(501, "Strategy bitcoin on balance sheet", "COLUMN",
                   _grid(debt, 0, 0, 1 + len(BTC_HOLDINGS), 1),
                   [_series(_grid(debt, 0, 1, 1 + len(BTC_HOLDINGS), 2), orange, labels=True)],
                   0, [{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS", "title": "", "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0}}],
                   legend="NO_LEGEND"),
            _chart(502, "Strategy BTC Holdings Q/Q Change", "COLUMN",
                   _grid(debt, 0, 3, 1 + len(QQ_CHANGE), 4),
                   [_series(_grid(debt, 0, 4, 1 + len(QQ_CHANGE), 5), black, labels=True)],
                   28, [{"position": "BOTTOM_AXIS", "title": ""}, {"position": "LEFT_AXIS", "title": "", "format": {"numberFormat": {"type": "PERCENT"}}, "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0, "viewWindowMax": 1}}],
                   legend="NO_LEGEND"),
            _chart(503, "Strategy BTC/Share (in Sats)", "COLUMN",
                   _grid(debt, 0, 6, 1 + len(SATS), 7),
                   [_series(_grid(debt, 0, 7, 1 + len(SATS), 8), orange, labels=True)],
                   56, [{"position": "BOTTOM_AXIS", "title": ""}, {"position": "LEFT_AXIS", "title": "", "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0}}],
                   legend="NO_LEGEND"),
            _chart(504, "MSTR mNAV", "AREA",
                   _grid(debt, 0, 9, 1 + len(mnav), 10),
                   [_series(_grid(debt, 0, 10, 1 + len(mnav), 11), gray)],
                   84, [{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS", "title": "", "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0.8, "viewWindowMax": 1.8}}],
                   legend="NO_LEGEND",
                   subtitle="Before Jul 27: mNAV = (Enterprise value) / (BTC NAV); After Jul 27: mNAV = (MSTR market price) / (Net BTC per share ($))"),
        ],
    }
    lev_gid, res_gid = 1690294676, 49428948
    strategy = fill_days([
        ("2025-02-12", 17), ("2025-04-10", 23), ("2025-05-20", 16), ("2025-08-15", 17),
        ("2025-10-25", 20), ("2025-12-05", 29), ("2026-02-10", 26), ("2026-03-01", 34),
        ("2026-06-15", 44), ("2026-08-20", 33), ("2026-10-05", 31),
    ], step=7, digits=1, wiggle=0.6, floor=0)
    strive = fill_days([
        ("2025-02-12", 0), ("2025-11-15", 0), ("2025-12-01", 32), ("2026-01-20", 30),
        ("2026-02-15", 48), ("2026-04-15", 42), ("2026-06-10", 66), ("2026-08-15", 50),
        ("2026-09-20", 46), ("2026-10-05", 65),
    ], step=7, digits=1, wiggle=0.8, floor=0)
    # Align strive onto strategy dates. Missing early dates stay 0.
    strive_map = dict(strive)
    lev_labels = [d for d, _ in strategy]
    lev_s = [v for _, v in strategy]
    strive_items = list(strive_map.items())
    lev_a = []
    for d in lev_labels:
        prev = [v for sd, v in strive_items if sd <= d]
        lev_a.append(prev[-1] if prev else 0)
    reserve = [
        ("2025-12-01", 1450, 820), ("2026-01-01", 2200, 850), ("2026-02-01", 2250, 880),
        ("2026-03-01", 2250, 920), ("2026-04-01", 2250, 1100), ("2026-04-20", 2250, 1480),
        ("2026-05-10", 2250, 1500), ("2026-05-25", 2250, 1700), ("2026-06-05", 900, 1720),
        ("2026-06-20", 1400, 1750), ("2026-07-01", 2500, 1780), ("2026-07-20", 3750, 1760),
        ("2026-08-10", 4600, 1720), ("2026-08-25", 5100, 1680), ("2026-09-15", 4900, 1600),
        ("2026-09-28", None, None), ("2026-10-05", 4850, 1520),
    ]
    bot2_values = [
        _range("6 MSTR vs ASST leverage", 0, 0, 2, 1 + len(lev_labels), _rows(
            ["Date", "Strategy: debt + preferred ÷ bitcoin value (%)", "Strive: debt + preferred ÷ bitcoin value (%)"],
            [lev_labels, lev_s, lev_a])),
        _range("5 MSTR USD reserve vs dividends", 0, 0, 2, 1 + len(reserve), _rows(
            ["Date", "USD Reserve, USD m", "Annual preferred dividends + interest, USD m"],
            [[a for a, _, _ in reserve], [b if b is not None else "" for _, b, _ in reserve], [c if c is not None else "" for _, _, c in reserve]])),
    ]
    bot2_sheets = [
        {
            "properties": {"sheetId": lev_gid, "title": "6 MSTR vs ASST leverage", "index": 5},
            "charts": [_chart(
                601, "Strategy vs Strive leverage: Strategy 31% of its bitcoin, Strive 65%", "LINE",
                _grid(lev_gid, 0, 0, 1 + len(lev_labels), 1),
                [
                    _series(_grid(lev_gid, 0, 1, 1 + len(lev_labels), 2), orange),
                    _series(_grid(lev_gid, 0, 2, 1 + len(lev_labels), 3), blue),
                ],
                0,
                [{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS", "title": "%", "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0}}],
            )],
        },
        {
            "properties": {"sheetId": res_gid, "title": "5 MSTR USD reserve vs dividends", "index": 4},
            "charts": [_chart(
                602, "Strategy's USD reserve covers 37 months of dividends, vs 6 in May (Dec 2025–Oct 2026)", "LINE",
                _grid(res_gid, 0, 0, 1 + len(reserve), 1),
                [
                    _series(_grid(res_gid, 0, 1, 1 + len(reserve), 2), violet),
                    _series(_grid(res_gid, 0, 2, 1 + len(reserve), 3), orange),
                ],
                0,
                [{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS", "title": "USD millions", "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0}}],
            )],
        },
    ]
    vol_b = fill_months([
        ("2019-01", 75), ("2020-01", 55), ("2020-02", 80), ("2020-03", 198), ("2020-04", 95),
        ("2020-08", 48), ("2021-05", 105), ("2021-11", 70), ("2022-06", 82), ("2023-01", 42),
        ("2024-03", 55), ("2025-01", 38), ("2026-09", 28),
    ], digits=1, wiggle=4, floor=12)
    vol_v = fill_months([
        ("2019-01", 18), ("2020-01", 14), ("2020-02", 25), ("2020-03", 66), ("2020-04", 36),
        ("2020-08", 22), ("2022-06", 30), ("2023-01", 18), ("2024-08", 22), ("2026-09", 17),
    ], digits=1, wiggle=1.5, floor=10)
    # Same month labels. fill_months uses the same endpoints only if the key months match.
    # Rebuild VIX on BTC's month labels by holding the previous VIX point.
    vix_map = dict(vol_v)
    vol_labels = [d for d, _ in vol_b]
    vol_btc = [v for _, v in vol_b]
    vol_vix = []
    last = 15
    for d in vol_labels:
        if d in vix_map:
            last = vix_map[d]
        vol_vix.append(last)
    price = fill_months([
        ("2019-06", 8500), ("2019-12", 7200), ("2020-03", 5200), ("2020-12", 29000),
        ("2021-04", 55000), ("2021-11", 64000), ("2022-06", 20000), ("2022-11", 16500),
        ("2023-10", 34000), ("2024-03", 70000), ("2024-12", 96000), ("2025-08", 108000),
        ("2026-03", 74000), ("2026-10", 86000),
    ], digits=0, wiggle=1800, floor=4000)
    ma = fill_months([
        ("2019-06", 4200), ("2020-06", 8000), ("2021-06", 20000), ("2022-06", 30000),
        ("2023-06", 27000), ("2024-06", 45000), ("2025-06", 64000), ("2026-10", 72000),
    ], digits=0, wiggle=400, floor=3000)
    ma_map = dict(ma)
    px_labels = [d for d, _ in price]
    px = [v for _, v in price]
    px_ma = []
    last = 4200
    for d in px_labels:
        if d in ma_map:
            last = ma_map[d]
        px_ma.append(last)
    nominal = fill_months([
        ("2016-01", 1920), ("2018-01", 2700), ("2020-01", 3250), ("2020-03", 2580),
        ("2020-12", 3700), ("2021-12", 4700), ("2022-10", 3700), ("2024-12", 5900),
        ("2026-09", 7550),
    ], digits=0, wiggle=40, floor=1500)
    real = fill_months([
        ("2016-01", 2950), ("2018-01", 3650), ("2020-01", 4200), ("2020-03", 3300),
        ("2020-12", 4600), ("2021-12", 5550), ("2022-10", 4300), ("2024-12", 6400),
        ("2026-09", 7680),
    ], digits=0, wiggle=35, floor=2000)
    real_map = dict(real)
    sp_labels = [d for d, _ in nominal]
    sp_n = [v for _, v in nominal]
    sp_r = []
    last = 2950
    for d in sp_labels:
        if d in real_map:
            last = real_map[d]
        sp_r.append(last)
    g_vol, g_ma, g_sp = 1546024641, 1810821762, 1540677114
    bot1_values = [
        _range("01 BTC vol vs VIX", 0, 0, 2, 1 + len(vol_labels), _rows(
            ["Date", "BTC 30d realized vol (% ann.)", "VIX"], [vol_labels, vol_btc, vol_vix])),
        _range("04 BTC vs 200-week MA", 0, 0, 2, 1 + len(px_labels), _rows(
            ["Date", "BTC price (USD)", "200-week moving avg (USD)"], [px_labels, px, px_ma])),
        _range("09 Real S&P 500", 0, 0, 2, 1 + len(sp_labels), _rows(
            ["Date", "S&P 500 nominal (monthly avg)", "S&P 500 real (Aug 2026 dollars)"], [sp_labels, sp_n, sp_r])),
    ]
    bot1_sheets = [
        {"properties": {"sheetId": g_vol, "title": "01 BTC vol vs VIX", "index": 0}, "charts": [_chart(
            701, "Bitcoin is ~2.4x as volatile as stocks (BTC 30-day realized vol vs VIX)", "LINE",
            _grid(g_vol, 0, 0, 1 + len(vol_labels), 1),
            [_series(_grid(g_vol, 0, 1, 1 + len(vol_labels), 2), blue), _series(_grid(g_vol, 0, 2, 1 + len(vol_labels), 3), red)],
            0, [{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS", "title": "BTC 30d realized vol (% ann.) / VIX", "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0}}],
        )]},
        {"properties": {"sheetId": g_ma, "title": "04 BTC vs 200-week MA", "index": 3}, "charts": [_chart(
            702, "Bitcoin vs its 200-week moving average", "LINE",
            _grid(g_ma, 0, 0, 1 + len(px_labels), 1),
            [_series(_grid(g_ma, 0, 1, 1 + len(px_labels), 2), blue), _series(_grid(g_ma, 0, 2, 1 + len(px_labels), 3), red)],
            0, [{"position": "BOTTOM_AXIS", "title": "Year"}, {"position": "LEFT_AXIS", "title": "BTC price (USD) / 200-week moving avg (USD)", "logScale": True}],
        )]},
        {"properties": {"sheetId": g_sp, "title": "09 Real S&P 500", "index": 8}, "charts": [_chart(
            703, "The S&P 500 in today's dollars (CPI-adjusted)", "LINE",
            _grid(g_sp, 0, 0, 1 + len(sp_labels), 1),
            [
                _series(_grid(g_sp, 0, 1, 1 + len(sp_labels), 2), blue),
                _series(_grid(g_sp, 0, 2, 1 + len(sp_labels), 3), red),
            ],
            0, [{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS", "title": "S&P 500 nominal (monthly avg) / S&P 500 real (Aug 2026 dollars)", "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0}}],
        )]},
    ]
    def bundle(sid, title, sheets, values, formatted=None):
        doc = {
            "sample": True,
            "note": "Pilot sample transcribed from the committed chart PNGs so the page renders without Google credentials. The Monday export replaces it.",
            "spreadsheet": {"spreadsheetId": sid, "properties": {"title": title}, "sheets": sheets},
            "values": {"spreadsheetId": sid, "valueRanges": values},
        }
        if formatted:
            doc["formatted"] = {"spreadsheetId": sid, "valueRanges": formatted}
        return doc
    return [
        bundle(bot1, "Bot experiments", bot1_sheets, bot1_values),
        bundle(bot2, "bot experiments 2", bot2_sheets, bot2_values),
        bundle(bot3, "My Bot's Sheets", [debt_sheet], debt_values, debt_fmt),
    ]


def write_samples(repo):
    dest_dir = os.path.join(repo, "tests", "fixtures", "sheets_dumps")
    os.makedirs(dest_dir, exist_ok=True)
    bundles = sample_bundles()
    paths = []
    for bundle in bundles:
        sid = bundle["spreadsheet"]["spreadsheetId"]
        book = sheets_catalog.BY_ID[sid][0]
        path = os.path.join(dest_dir, book + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=1, ensure_ascii=False)
            f.write("\n")
        paths.append(path)
    result = export_bundles(bundles, repo)
    return paths, result


def check_data(repo):
    """Validate the snapshot. No network."""
    problems = []
    root = os.path.join(repo, "static", "sheets", "data")
    index_path = os.path.join(root, "index.json")
    if not os.path.isfile(index_path):
        return ["missing static/sheets/data/index.json"]
    index = load_json(index_path)
    seen = set()
    for row in index.get("charts") or []:
        book, gid, n = row.get("book"), row.get("gid"), row.get("n")
        if tab_skipped(book, gid):
            problems.append("index lists retired %s %s" % (book, gid))
        rel = row.get("file") or ""
        path = os.path.realpath(os.path.join(repo, "static", rel))
        data_root = os.path.realpath(root)
        if not rel.startswith("sheets/data/") or os.path.commonpath([data_root, path]) != data_root:
            problems.append("bad index path %s" % rel)
            continue
        if not os.path.isfile(path):
            problems.append("missing " + rel)
            continue
        doc = load_json(path)
        if doc.get("supported") is False:
            problems.append(rel + " is unsupported")
        if doc.get("x", {}).get("title", "").lower() in {"date", "year", "years"}:
            problems.append(rel + " still titles the x axis Date or Year")
        seen.add((book, int(gid), int(n)))
    skipped = set(sheets_catalog.EXCLUDED_TABS) | set(RETIRED_2026_11_17)
    for book, gid in skipped:
        folder = os.path.join(root, book)
        if not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            m = FILE_RE.fullmatch(fn)
            if m and int(m.group(1)) == int(gid):
                problems.append("retired data file still present: %s/%s" % (book, fn))
    return problems


def env_value(*names):
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def live_enabled():
    return os.environ.get("SHEETS_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}


def credential_status():
    """Which credential slots are filled. Names only, never values."""
    direct = bool(env_value("GOOGLE_ACCESS_TOKEN", "CHARTMAKER_GOOGLE_TOKEN"))
    oauth = all(env_value(a, b) for a, b in (
        ("GOOGLE_CLIENT_ID", "CHARTMAKER_GOOGLE_CLIENT_ID"),
        ("GOOGLE_CLIENT_SECRET", "CHARTMAKER_GOOGLE_CLIENT_SECRET"),
        ("GOOGLE_REFRESH_TOKEN", "CHARTMAKER_GOOGLE_REFRESH_TOKEN"),
    ))
    path = env_value("GOOGLE_APPLICATION_CREDENTIALS", "CHARTMAKER_GOOGLE_CREDENTIALS")
    file_ok = bool(path and os.path.isfile(path))
    file_missing = bool(path and not os.path.isfile(path))
    need = [
        "GOOGLE_ACCESS_TOKEN or CHARTMAKER_GOOGLE_TOKEN",
        "or GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN (CHARTMAKER_GOOGLE_* names work)",
        "or GOOGLE_APPLICATION_CREDENTIALS or CHARTMAKER_GOOGLE_CREDENTIALS (service-account JSON file)",
        "scope https://www.googleapis.com/auth/spreadsheets.readonly",
        "SHEETS_LIVE=1 on the server. Do not commit tokens.",
    ]
    return {
        "configured": bool(direct or oauth or file_ok),
        "accessToken": direct,
        "oauthRefresh": oauth,
        "serviceAccountFile": file_ok,
        "serviceAccountFileMissing": file_missing,
        "need": need,
    }


def _sid_for(book):
    for bid, _name, sid in sheets_catalog.BOOKS:
        if bid == book:
            return sid
    return ""


def _http_json(url, data=None, headers=None, timeout=45):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def _batch_get(spreadsheet_id, token, ranges, render):
    collected = []
    for i in range(0, len(ranges), 40):
        chunk = ranges[i:i + 40]
        query = urllib.parse.urlencode(
            [("ranges", r) for r in chunk] + [("valueRenderOption", render)],
            doseq=True,
        )
        url = "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet?%s" % (spreadsheet_id, query)
        doc = _http_json(url, headers={"Authorization": "Bearer " + token})
        collected.extend(doc.get("valueRanges") or [])
    return {"spreadsheetId": spreadsheet_id, "valueRenderOption": render, "valueRanges": collected}


def fetch_bundle(spreadsheet_id, token, gid=None):
    """Read chart specs and the ranges they plot. Caller supplies the bearer token."""
    fields = "spreadsheetId,properties.title,sheets(properties(sheetId,title,index,hidden),charts(chartId,spec,position))"
    url = "https://sheets.googleapis.com/v4/spreadsheets/%s?fields=%s" % (
        spreadsheet_id, urllib.parse.quote(fields, safe="(),")
    )
    meta = _http_json(url, headers={"Authorization": "Bearer " + token})
    titles = {}
    ranges = []
    for sheet in meta.get("sheets") or []:
        props = sheet.get("properties") or {}
        try:
            sheet_id = int(props.get("sheetId"))
        except (TypeError, ValueError):
            continue
        if gid is not None and sheet_id != int(gid):
            continue
        titles[sheet_id] = props.get("title") or "Sheet"
        if props.get("hidden"):
            continue
        for chart in sheet.get("charts") or []:
            for src in iter_sources(chart):
                if int(src.get("sheetId") or sheet_id) != sheet_id and src.get("sheetId") not in titles:
                    continue
                title = titles.get(int(src.get("sheetId") or sheet_id)) or titles[sheet_id]
                ranges.append(grid_to_a1(title, src))
    ranges = list(dict.fromkeys(ranges))
    if not ranges:
        raise RuntimeError("no chart ranges on that tab")
    return {
        "spreadsheet": meta,
        "values": _batch_get(spreadsheet_id, token, ranges, "UNFORMATTED_VALUE"),
        "formatted": _batch_get(spreadsheet_id, token, ranges, "FORMATTED_VALUE"),
    }


def _cache_get(key):
    hit = _LIVE_CACHE.get(key)
    if not hit:
        return None
    ts, doc = hit
    if time.time() - ts > LIVE_TTL:
        _LIVE_CACHE.pop(key, None)
        return None
    return doc


def live_chart_response(book, gid, n, fetch=None):
    """JSON body and HTTP status for GET /api/sheets/chart. Does not log secrets."""
    if not live_enabled():
        return {"live": False, "fallback": "static", "error": "SHEETS_LIVE is off"}, 404
    book = str(book or "").strip()
    try:
        gid_i = int(gid)
        n_i = int(n or 1)
    except (TypeError, ValueError):
        return {"error": "gid and n must be integers"}, 400
    if not BOOK_RE.fullmatch(book) or n_i < 1 or n_i > 99:
        return {"error": "book or chart number is not valid"}, 400
    if tab_skipped(book, gid_i):
        return {"live": False, "error": "retired tab", "fallback": "png"}, 410
    sid = _sid_for(book)
    if not sid:
        return {"error": "unknown workbook"}, 400
    status = credential_status()
    cached = _cache_get((book, gid_i, n_i))
    if cached:
        return cached, 200
    if not status["configured"]:
        return {
            "live": True,
            "configured": False,
            "fallback": "static",
            "error": "Google credentials are not configured",
            "need": status["need"],
        }, 503
    token = ""
    try:
        import refresh_btctop100 as gauth
        token, _how = gauth.access_token()
        if not token:
            return {
                "live": True, "configured": False, "fallback": "static",
                "error": "Google credentials are not configured", "need": status["need"],
            }, 503
        bundle = (fetch or fetch_bundle)(sid, token, gid_i)
        charts, _warnings = convert_bundle(bundle)
        match = next((c for c in charts if c.get("supported") and int(c.get("gid") or -1) == gid_i and int(c.get("n") or -1) == n_i), None)
        if not match:
            return {"live": True, "supported": False, "fallback": "png", "error": "no supported chart at that number"}, 404
        body = _public_chart(match, _now())
        body["live"] = True
        _LIVE_CACHE[(book, gid_i, n_i)] = (time.time(), body)
        return body, 200
    except Exception as exc:
        msg = str(exc)
        if token and token in msg:
            msg = msg.replace(token, "[redacted]")
        return {"live": True, "fallback": "static", "error": msg[:300]}, 502


def self_test():
    charts, warnings = convert_bundle(sample_bundles()[2])
    debt = [c for c in charts if c.get("gid") == 1145735379 and c.get("supported")]
    assert [c["n"] for c in debt] == [1, 2, 3, 4], [c["n"] for c in debt]
    assert debt[0]["series"][0]["data"][0] == 38250
    assert debt[0]["series"][0]["data"][-1] == 850364
    assert debt[0]["series"][0]["color"] == "#F7931A"
    assert debt[0]["x"]["title"] == ""
    assert debt[1]["series"][0]["data"][0] == 84.2
    assert debt[1]["y"][0]["format"] == "percent"
    assert debt[1]["y"][0].get("max") == 100
    retired = {
        "sample": False,
        "spreadsheet": {
            "spreadsheetId": "1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY",
            "properties": {"title": "Bot experiments"},
            "sheets": [{
                "properties": {"sheetId": 333185169, "title": "87 TSLA beta to Nasdaq", "index": 0},
                "charts": [_chart(1, "should not return", "LINE", _grid(333185169, 0, 0, 3, 1),
                                  [_series(_grid(333185169, 0, 1, 3, 2), "#F7931A")],
                                  0, [{"position": "BOTTOM_AXIS", "title": "Date"}, {"position": "LEFT_AXIS"}])],
            }],
        },
        "values": {"valueRanges": [_range("87 TSLA beta to Nasdaq", 0, 0, 1, 3, [["Date", "BTC"], ["2020-01-01", 1], ["2020-02-01", 2]])]},
    }
    retired_charts, retired_warnings = convert_bundle(retired)
    assert retired_charts and retired_charts[0].get("excluded"), retired_charts
    assert any("excluded" in w for w in retired_warnings)
    print("self-test ok (%s pilot charts in the MSTR Debt bundle)" % len(debt))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export interactive Sheets chart data from a metadata+values dump.")
    ap.add_argument("dumps", nargs="*", help="bundle JSON, or spreadsheets.get JSON paired with --values")
    ap.add_argument("--values", action="append", default=[], help="values.batchGet JSON for the preceding spreadsheet")
    ap.add_argument("--formatted", action="append", default=[], help="FORMATTED_VALUE batchGet JSON")
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--write-samples", action="store_true", help="write the pilot dumps and export them")
    ap.add_argument("--check", action="store_true", help="validate static/sheets/data without reading Google")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.check and not args.dumps and not args.write_samples:
        problems = check_data(args.repo)
        if problems:
            for item in problems:
                print("error: " + item)
            return 1
        print("interactive chart data ok")
        return 0
    if args.write_samples:
        paths, result = write_samples(args.repo)
        for path in paths:
            print("wrote " + path)
        print("exported %s charts" % len(result["written"]))
        for warning in result["warnings"]:
            print("warning: " + warning)
        return 0
    bundles = pair_inputs(args.dumps, args.values, args.formatted)
    result = export_bundles(bundles, args.repo)
    print("exported %s charts, skipped %s, index %s" % (
        len(result["written"]), len(result["skipped"]), len(result["index"]["charts"])))
    for warning in result["warnings"]:
        print("warning: " + warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
