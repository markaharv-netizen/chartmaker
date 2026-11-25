# Chartmaker
Browser app for X-ready stock/crypto charts (not for trading). Data: Yahoo Finance chart API via a tiny stdlib Python proxy (no pip installs).

Start: `cd /workspace/chartmaker && nohup python3 server.py 18790 > server.log 2>&1 &` then open http://localhost:18790
(Port 8790 is held by the box's egress tunnel, so the app runs on 18790.)

Use: tickers (comma-separated, e.g. `NBIS,CRWV,BTC-USD`), optional display names, range (1D/5D intraday, YTD, 1M, 6M, 1Y, 5Y, Max, Custom),
type (price line, % change indexed to start, candles + volume for one ticker, bar ranking of % return, ratio A/B with exactly two tickers), title, color, watermark toggle.
Line color on Charts, the one-series color on Financials, and bar color on Valuation share one list. Orange (`#F7931A`) is still the default and violet (`#8B5CF6`) is still the second choice. The list also includes the comparison palette (sky, green, red, ink, pink, teal) plus blue, olive, magenta, and brown. The control is one dropdown; Custom shows a color input and hex field only after that option is chosen. One series uses the picked color. Several series keep the old palette order for the first eight, then the extra hues. A saved view stores the hex, so an older orange or violet choice still applies. `?color=`, `?fcolor=`, and `?vcolor=` set the same hex. Dark themes brighten the named colors, and a custom hex is lightened or darkened only when it would vanish. Download PNG uses the color on screen.
"Download PNG" exports 1600x900 at 2x (3200x1800). Status bar shows "data as of" (ET); fetch failures show an error and render nothing.
Render on Price, Heatmap, Treasury, and Valuation fetches fresh data (the separate Refresh button is gone). The top row is Daily briefing, Charts of the day, Sheets, a Chart dropdown, and a Views dropdown (the page still opens on Daily briefing). Chart (placeholder label Chart) holds Price, Financials, Valuation, and Watchlist. Views (placeholder label Views) holds Heatmap, BTC Treasury, Price targets, and Paper trading. Price is the old Charts/Prices view: `?tab=price`, `?tab=prices`, and `?tab=charts` all open it, and `?tickers=` still does too. `?tab=watch` and `?tab=watchlist` open Watchlist.

## Treasury tab

One metric: **BTCTC top 100**. The table is Mark's sheet tab of that name in the workbook "My Bot's Sheets" (`1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU`, gid 73825060), not a BitcoinTreasuries.net scrape. The chart title is the sheet title (Corporate Bitcoin Treasury Holdings). Columns: rank, company, ticker, country, BTC, and Change. Change is the sheet's Change cell as displayed (a leading + or - stays); positive is green, negative is red, and an empty cell is an em dash. Each row's as-of date stays in the snapshot and is not a column. The footer total is the sum of BTC holdings. Holdings are the committed snapshot `btctop100.json`. The chart does not draw a USD column or a % of 21M column. `/api/treasury` still returns `usd` (BTC × a live Yahoo `BTC-USD` quote, or null) and `pctSupply` so existing readers of that response keep working; the weekday sync does not write those fields and must not put the columns back. The status line can still show the BTC spot price. Watermark is off unless the box is checked. Download PNG is the chart canvas.

Refresh the snapshot (then commit `btctop100.json`). The script reads the sheet with Google credentials, and falls back to a CSV export:

```
python3 scripts/refresh_btctop100.py
python3 scripts/refresh_btctop100.py --csv btctc100.csv
python3 scripts/refresh_btctop100.py --check
```

Credentials, first match: `GOOGLE_ACCESS_TOKEN` or `CHARTMAKER_GOOGLE_TOKEN`; or `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` (`CHARTMAKER_GOOGLE_*` names work too); or a service-account JSON in `GOOGLE_APPLICATION_CREDENTIALS` or `CHARTMAKER_GOOGLE_CREDENTIALS`. The sheet is private. `--csv` or `BTCTC_CSV` is the fallback.

Watchlist still updates only from its Refresh button. Financials has no quote-cache bypass. Financials and Valuation open with an empty ticker box and a blank chart until tickers are entered. Deep links: `?tickers=` and `&names=` (Price), `?ftickers=` and `&fnames=` (Financials), `?vtickers=` and `&vnames=` (Valuation). Charts SMA is a multi-select menu (20, 50, 100, 200, 200W, plus a custom length).

Notes: YTD baseline = last close of prior year. 5Y uses weekly bars, Max monthly. Ratio aligns A to the latest B bar at or before each date.
Re-run samples: `python3 shoot.py` (uses /usr/bin/google-chrome).

## Financials mode (v2)
Pick Financials from the Chart dropdown. Data: SEC EDGAR XBRL companyfacts (primary source), ticker→CIK via company_tickers.json,
cached in `cache/` for 24h (User-Agent `Chartmaker mark.a.harv@gmail.com`). Code in `fin.py`, endpoint `/api/fin?symbol=&metric=&period=quarterly|annual|ttm&y1=&y2=`.
Metrics: revenue, gross/operating/net income, EPS diluted, OCF, capex, FCF (OCF−capex), cash, total debt, shares, R&D, gross/operating/net margin %.
Quarterly values missing from filings are derived from YTD differences (Q4 = FY − 9M); TTM = sum of 4 consecutive quarters. Derived cells are marked `*` in the table (hover for tag/form/derivation).
Gaps stay blank. Period labels are calendar-based (quarter/year in which the fiscal period mostly falls), so NVDA's FY ending Jan 2026 shows as 2025.
Shares/EPS are split-adjusted only when a later filing restates the same period at the split ratio. MSTR tags no period-end share count, so weighted-avg basic shares are used (labeled; Q4 blank).
Foreign filers: 20-F US-GAAP annual data only (non-USD currency shown); IFRS filers get a clear "not supported" message.
Re-run samples: `python3 shoot_fin.py`.

## Themes + saved views (v3)
Theme dropdown (top bar, with swatch preview): White (default), Off-white/cream #FAF7F2, Light gray #F5F6F8, Dark #0F1115, Navy #0B1A2E, Black #000000.
Themes set background, title/text/label/gridline/axis/watermark colors and apply to the PNG export; brand colors (orange, violet, green/red bars) are brightened slightly on dark themes.
Saved views: "Save view" stores mode, tickers, names, range/dates, metric, period, chart type, title, colors, theme, watermark. Pick from "Saved views" to load + re-render with fresh data;
With a view selected, Save view overwrites it right away (no prompt; status shows Saved “name”); with "— pick a view —" selected it asks for a name and saves a new view. Views are per tab. Rename and Delete. Stored server-side in `views.json` next to server.py (GET/POST `/api/views`).
views.json is never shipped in the zip, so updates don't overwrite saved views. Re-run theme samples: `python3 shoot_themes.py`.

## Hosting / password (v4)
Env vars: `APP_PASSWORD` (if set, every route incl. /api requires login; login page at /login, logout at /logout),
`SECRET_KEY` (HMAC key for the session cookie; random per start if unset), `VIEWS_STORE=browser` (saved views live in browser localStorage
instead of views.json), `PORT` (overrides CLI port; binds 0.0.0.0).
Session cookie: `cm_session`, signed HMAC-SHA256, HttpOnly, Secure, SameSite=Lax, 30 days. Login throttling: after 5 failures per IP, lockout 30s doubling up to 15 min.
With no APP_PASSWORD (local PC) behavior is unchanged. Render: `render.yaml` Blueprint (free plan, `python server.py`).
Saved views and shared favorites use the same store. With `GITHUB_TOKEN` set they are `views.json` on `VIEWS_BRANCH` (default `views-data`) via the GitHub Contents API, so a redeploy does not erase them. Without that token they are the local `views.json` file, which lasts only as long as that disk. `VIEWS_STORE=browser` keeps both in the browser and turns favorite sync off.

## Annotations, candles, Sheets library

### Event markers (Charts)
Tick **Annotations** on the row under the price controls. Markers are dashed lines on the shared time axis, so a multi-ticker chart gets one timeline (earnings belong to the **first** ticker). Bar-ranking charts ignore them. Labels are short pills inside the plot so they stay on the 1600×900 PNG; the optional note shows on hover, not on the image.

- **Earnings** — `/api/earnings` reads Yahoo `quoteSummary` (crumb, no API key): the last 4 reported quarters plus the next date. Yahoo's chart `events=earn` feed no longer returns a history. Older dates are custom markers. Crypto and some listings come back empty; the status line says so and the price chart still draws.
- **Halvings** — four historical Bitcoin halvings: 2012-11-28, 2016-07-09, 2020-05-11, 2024-04-20 UTC (block 840,000). No estimated future date.
- **Custom** — date, short label (24 characters), optional note. Stored on the Charts saved view as `ann` (same views store as everything else) and in this browser until another Charts view is loaded.

### Candles + volume (Charts)
Type **Candles + volume**. One ticker only; the option is disabled while several symbols are in the box. `/api/chart` returns `bars`: `[time_ms, open, high, low, close, volume]` beside the close-only `points`. Candle mode chooses the interval so bodies stay wide on the 1600px canvas: 1D is 15-minute regular-session bars (30-minute for 24h `-USD` pairs), 5D is one daily candle per trading session, and longer ranges step from daily to weekly to monthly when the finer bars would turn into hairlines. Body width scales with the free plot width, with a small gap, a floor, and a cap. Price line is unchanged (1D/5D stay 1-minute, including pre/after-hours). Candle mode does not draw extended-hours slivers or overnight gaps. Volume sits under the candles; green is close ≥ open. SMAs (daily closes; off for intraday bars), annotations, earnings markers, and Download PNG use the same 1600×900 canvas. The status line shows the interval actually drawn.

### Sheets library
The **Sheets** tab, next to Charts of the day, reads `static/sheets/catalog.json` for the tab list. Search by workbook, tab name, or chart title, then **Open in Sheets**. Render does not call Google.

With the search box empty, the list is text only. After you type a search, a result shows a chart preview when that tab has a thumbnail. Tabs without one still appear, and a missing file does not leave a broken image. Thumbnails are small JPEGs in `static/sheets/thumbs/` (at most 480×270). The grid keeps that JPEG.

**View PNG**, **Download PNG**, and the preview image open `static/sheets/full/<book>/<gid>.png` when that file is in the repo (chart 2 and later use `<gid>_2.png`, `<gid>_3.png`, …). If it is missing, those links open the thumbnail for chart 1. Later charts on the same tab have no thumbnail of their own.

**Favorites.** Each chart has a star (☆ / ★). A tab with several charts has one star per chart, keyed as `book/gid/chartNumber` (chart 1 is `<gid>.png`, chart 2 is `<gid>_2.png`), so two charts on one tab stay distinct. A tab with no chart, such as a table, is one favorite: `book/gid/1`, labeled with the tab name. That is the same file as the tab thumbnail and `<gid>.png`. Tabs that already have charts keep their existing numbers. Charts of the day shows a star when the card maps to one library chart or table. A random edition sets `chart_n` on every card, so the star is that chart, or `book/gid/1` for a table. `chart_n` on the card (1-based, catalog `chartList` order) is used first when that chart exists. With no `chart_n`, a single chart or a table maps to its one item, and a tab with several charts maps only when the card title matches exactly one chart. A stated `chart_n` that is not on the tab does not fall through to a different chart number. A card with no library tab, or a multi-chart tab with no `chart_n` and no unique title match, has no star. The page does not assume chart 1. **Favorites** on the Sheets tab lists the starred charts, including table tabs, with the same Open in Sheets, View PNG, and Download PNG actions. The "Has a chart" filter still hides table tabs, because a table is not a chart.

Favorites are one shared list for everyone who signs in. There are no per-person accounts. They are stored in the same `views.json` document as saved views (`favorites.rev`, `favorites.items`, and `favorites.removed` tombstones). On Render that file is the GitHub `views-data` branch when `GITHUB_TOKEN` is set, which is what makes the list survive a redeploy. Without the token, the list is the server's `views.json` and dies with an ephemeral disk. `VIEWS_STORE=browser` leaves stars in the browser only.

This browser also keeps `localStorage` key `chartmaker_sheet_favs` (v2: last synced items, revision, and clicks not yet confirmed). The first sync from a browser adds its local stars into the shared list and does not delete stars the shared list already has. A star that was explicitly removed (a tombstone) is not added back by that migration. Later clicks send only those clicks. A stale browser cannot replace the whole list. An add is ignored when a newer removal happened after the revision that browser last saw, and a remove is ignored when the star was added again after that revision. The cap is 200. Retired bot1 gids `1547332232`, `643299192`, `333185169`, `1024131985`, `1791345316`, `1370617918`, `292208014`, `2119398138`, `1584345646`, `1186940001`, `501919598`, `579853621`, `1308503061`, `748431671`, `254001772`, `1238930141`, `1835714359`, `492285771`, `220064271`, `79264442`, `1755343475`, `1067583608`, `1924976898`, `1978525586`, `1255387315`, `1522766504`, `1163669375`, `1576800449`, `1192979444`, `1912487083`, `664060481`, `1778640335`, `1476428159`, `1459587255`, `687897000`, `546391999`, `195161343`, `321339250`, and `2128724732`, `189736118`, `1981719475`, `519508812`, `45161413`, `567489150`, and `601460115`, `1024470894`, `1771590089`, `1566394978`, `780786735`, `248629477`, `1965360503`, `583946273`, and `327679061`, and bot experiments 2 gids `418894352`, `1794999542`, and `971499761`, are rejected. If the server cannot be reached, the star stays on this device, the page says it is not synced, and the click is retried from localStorage. A chart that leaves the catalog, or whose title no longer matches that chart number, is removed for everyone. A table favorite stores the tab name as its title, so renaming that tab removes it the same way.

Import the weekly export (files named like the tab, optional trailing `YYYY-MM-DD`, newest date then newest file time wins) on a machine that has the PNGs, then commit `static/sheets/full/`:

```
python3 scripts/import_full_pngs.py --from-dir /path/to/exported-pngs
```

Put each workbook in its own subfolder (`bot1`, the spreadsheet id, or the workbook title) to limit matches to that book. Repeat `--from-dir` for several folders. `--dry-run` prints matches without writing. `--no-optimize` copies the bytes unchanged. Otherwise the script uses `oxipng` or `optipng` when one is on PATH, otherwise `pngquant` (kept only when the file shrinks), otherwise Pillow's lossless optimize when Pillow is installed.

Refresh the tab list and thumbnails on a machine that can read the workbooks:

1. Save each spreadsheet's metadata JSON (`spreadsheets.get` / the Sheets `get_spreadsheet` response). This is the Monday part 3 dump. The field mask has to include chart position, not only titles: `sheets(properties,charts(chartId,spec.title,position))`. `properties` still supplies sheetId, title, index, and hidden. `position.overlayPosition.anchorCell` supplies rowIndex and columnIndex. Chart numbers follow that anchor (row, then column, then the original `charts[]` index), the same order the Monday PNG export uses (`pngChartsSorted_`). A zero row or column is often absent from the JSON; keep the anchorCell object in the dump anyway, and the missing index counts as 0. A one-chart tab does not need position.
2. `python3 scripts/sheets_catalog.py bot1.json bot2.json bot3.json`  
   This rewrites the tab list and **keeps** a thumbnail already recorded for a book and gid when that JPEG is still on disk. A multi-chart tab whose dump has no anchor on every chart keeps its existing catalog entry (`charts` and `chartList`) and the script prints a warning, so a title-only dump cannot renumber charts. A new multi-chart tab with no position is left out until the dump includes position. A one-chart tab is numbered n=1 without position. `EXCLUDED_TABS` in that script stays out of the rebuilt catalog: Bot experiments gid 333185169 ("87 TSLA beta to Nasdaq") even when that sheet tab still exists with no chart, and gid 1547332232 ("161 Nvidia P-S and P-E"), gid 643299192 ("202 Nvidia revenue by geography"), gid 1791345316 ("158 Korea & Taiwan vs S&P"), gid 1370617918 ("163 Supercore vs core CPI"), gid 1024131985 ("99 MSTR BTC per share"), gid 292208014 ("98 MSTR vs BTC drawdowns"), gid 2119398138 ("151 Big Tech capex vs Nvidia"), gid 1584345646 ("223 Micron revenue & margin"), gid 1186940001 ("226 OpenAI vs Anthropic revenue"), gid 501919598 ("263 Tariff pass-through"), gid 579853621 ("12 Breakeven vs CPI"), gid 1308503061 ("122 Bond vol vs stock vol"), gid 748431671 ("232 Quits vs wage growth"), and gid 254001772 ("162 Factory construction boom"), gid 1238930141 ("153 Big Tech capex intensity"), gid 1835714359 ("43 Nasdaq vs S&P ratio"), gid 492285771 ("109 Equal vs cap-weight S&P"), gid 220064271 ("135 Real coffee & cocoa"), gid 79264442 ("173 Continuing/initial claims"), gid 1755343475 ("239 Foreign vs native-born jobs"), and gid 1067583608 ("233 Older workers participation"), gid 1924976898 ("57 PPI vs CPI"), gid 1978525586 ("55 Shelter vs home prices"), gid 1255387315 ("115 Regional banks vs S&P"), gid 1522766504 ("128 Long bonds vs stocks DD"), gid 1163669375 ("186 Sahm rule recession gauge"), gid 1576800449 ("225 Nvidia inventory & DSO"), gid 1192979444 ("259 Financial conditions"), gid 1912487083 ("285 Ethereum L2s: Base leads"), gid 664060481 ("322 Microsoft quarterly FCF"), and gid 1778640335 ("324 Amazon quarterly FCF"), gid 1476428159 ("201 Nvidia data center revenue"), gid 1459587255 ("227 Private AI lab valuations"), gid 687897000 ("119 Emerging markets vs US"), gid 546391999 ("86 TSLA vs Toyota+GM+Ford"), gid 195161343 ("22 LTC & BCH in BTC"), gid 321339250 ("56 Food home vs away"), gid 2128724732 ("111 Value vs growth"), gid 189736118 ("191 Real gold price"), gid 1981719475 ("295 Tesla vs GM vs Ford margins"), gid 519508812 ("159 Uranium & nuclear vs S&P"), gid 45161413 ("45 Euro spreads vs Bunds"), gid 567489150 ("126 CCC vs BB spread"), and gid 601460115 ("204 Cloud operating margins"), gid 1024470894 ("206 Data center chip revenue"), gid 1771590089 ("198 Musk Inc vs mega caps"), gid 1566394978 ("256 Uncle Sam's borrowing cost"), gid 780786735 ("35 S&P 500 in gold"), gid 248629477 ("73 Bank loan-to-deposit"), gid 1965360503 ("337 US power sales by sector"), gid 583946273 ("184 Buffett indicator"), and gid 327679061 ("131 Refining crack spread"), plus bot experiments 2 gid 418894352 ("81 CoreWeave margins"), gid 1794999542 ("56 ROIC Tesla vs legacy and EV"), and gid 971499761 ("74 Neocloud quarterly revenue"), even if a dump still lists those deleted tabs. Image import and thumbnail refresh skip the same gids, and `scripts/cotd_build.py` refuses to publish them. Import and thumbnail refresh also skip a PNG that is blank or near-uniform (the empty Google chart that says to add a series). Import skips a chart titled `Chart N` and a chart number that is no longer in that tab's `chartList`. Charts of the day does not draw an untitled `Chart N` chart, a blank PNG, or a `Chart N` card that has no image of its own. Charts of the day also refuses bot experiments 2 gid 645072508 ("69 Mark MSTR red dot tracker", BX2-069) through `COTD_EXCLUDED` in `scripts/cotd_build.py`. That tab stays in the catalog, the full PNG, and the thumbnail.
3. Point `scripts/sheets_thumbs.py` at a local folder of the weekly chart export (not at Render). The export names files after the tab, with the number sometimes zero-padded and a date on the end (`090 TSLA gross margin vs price 2026-10-21.png`). Duplicate names: the script keeps the newest date, then the newest file time.
   `python3 scripts/sheets_thumbs.py --from-dir /path/to/exported-pngs`
4. Commit `static/sheets/catalog.json` and `static/sheets/thumbs/`. Do not commit the source folder itself.
5. `python3 scripts/import_full_pngs.py --from-dir /path/to/exported-pngs`  
   Then commit `static/sheets/full/`. The script prints `matched N, unmatched M, ambiguous A`.

Until that export is on the machine, seed only the tabs that overlap today's Charts of the day:

`python3 scripts/sheets_thumbs.py --from-cotd`

Workbooks: Bot experiments `1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY`, bot experiments 2 `1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ`, My Bot's Sheets `1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU`. All three dumps are required unless you pass `--allow-partial`. The weekly PNG folders feed the sheet library, which Charts of the day copies previews from: `1SwZgYGwwHsoL_DXQUuQAYS6lhq5KxFe9`, `1mMUKaB_RbeZhtn5ntuKJV5xZOeo6LzGH`, `1AVfHRo2mJgBhbJoszHhe5vgOscfKhbzT`.

## Interactive Sheets charts

The Sheets library can draw a chart in the browser from the workbook's own values: hover shows the point, and the date range zooms and pans. **View PNG** and **Download PNG** stay, including on Charts of the day, because a post on X still wants the flat image. A chart with no data file, or a type that is not supported yet, keeps the PNG and does not invent a picture.

Chartmaker uses a **data snapshot** (option A), not a fetch on every open. Render's service has no Google credential, and the page already does not call Google. The Monday routine writes JSON under `static/sheets/data/`. The browser reads that. A live fetch (option B) is implemented behind `SHEETS_LIVE` and is off unless that variable is set. Files in `static/sheets/data/` are a snapshot of the workbooks (not the `sample: true` transcriptions from the pilot). Monday's export replaces them.

Files:

- `static/sheets/data/<book>/<gid>.json` for chart 1, and `<gid>_2.json`, `<gid>_3.json`, … for the rest. The number is the catalog `chartList` `n`, the same number as the PNG.
- `static/sheets/data/index.json` lists the charts the Interactive button offers.
- `tests/fixtures/sheets_dumps/bot1.json`, `bot2.json`, and `bot3.json` are the saved `spreadsheets.get` + `values.batchGet` dumps the exporter reads with no credentials.

**Supported now:** line, area, stepped area, column, bar (horizontal), scatter, and combo, including a second axis and stacked or percent-stacked columns. The house palette is orange `#F7931A` then violet `#8B5CF6` when the sheet does not set a color. A series whose name is Bitcoin or BTC gets orange unless the spec has its own color. Fonts are Inter, Arial, sans-serif. An x-axis title of Date, Year, Years, or Time is dropped.

**Not supported yet:** pie, bubble, candlestick, histogram, waterfall, treemap, org, scorecard, and 3D charts. Those stay on the PNG. A native Sheets log scale is not in the public chart axis spec; the exporter honors `logScale: true` or `scaleType: LOG` on the axis when the dump includes it.

`sheets_catalog.EXCLUDED_TABS` never gets a data file. A file already on disk for one of those tabs is deleted. A multi-chart tab whose dump has no anchor on every chart is left unchanged, same rule as the catalog.

### Monday routine, part 3

The Sheets connector `get_spreadsheet` returns only chart titles (and can truncate a large workbook). It cannot supply the field mask `sheets(properties,charts(chartId,spec,position))`. Drive `export_file` also caps xlsx at 10 MB, which is below My Bot's Sheets.

The working no-browser path is: download each workbook as xlsx with Cursor `DownloadFile` (`connection: user-Google-drive`; this export is not 10 MB capped), keep the title-only inventory dump Part 3 already saves (complete `meta_bot1.json` / `meta_bot2.json` / `meta_bot3.json`, or last week's if this week's is truncated), then build bundles and the snapshot on the same machine that already runs the catalog (not on Render):

```
python3 scripts/xlsx_to_bundles.py \
  --xlsx bot1=bot1.xlsx bot2=bot2.xlsx bot3=bot3.xlsx \
  --meta bot1=meta_bot1.json bot2=meta_bot2.json bot3=meta_bot3.json \
  --catalog static/sheets/catalog.json \
  --out-dir dumps \
  --scripts-dir scripts

python3 scripts/sheets_catalog.py bot1.json bot2.json bot3.json
python3 scripts/sheets_data.py dumps/bot1.bundle.json dumps/bot2.bundle.json dumps/bot3.bundle.json
python3 scripts/sheets_data.py --check
```

`xlsx_to_bundles.py` is read-only. It honors `EXCLUDED_TABS` and skips the Python ETF draft (My Bot's Sheets gid `1608741572`). A Google chart that the xlsx export turned into a raster image, or a type that is not supported, is omitted and the PNG stays.

Commit `static/sheets/data/` with the catalog and the PNGs only when all three bundles exist, `--check` passes, and the export report is complete. If the export fails, keep last week's data files and report. `python3 scripts/sheets_data.py --write-samples` still regenerates the small fixture dumps under `tests/fixtures/`.

### Live on open (option B)

Leave this off until the Render service has a Google credential. Set `SHEETS_LIVE=1` and exactly one of these (never commit the values):

- `GOOGLE_ACCESS_TOKEN` or `CHARTMAKER_GOOGLE_TOKEN`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` (`CHARTMAKER_GOOGLE_CLIENT_ID`, `CHARTMAKER_GOOGLE_CLIENT_SECRET`, and `CHARTMAKER_GOOGLE_REFRESH_TOKEN` work too)
- `GOOGLE_APPLICATION_CREDENTIALS` or `CHARTMAKER_GOOGLE_CREDENTIALS`, a service-account JSON file

The token is requested with scope `https://www.googleapis.com/auth/spreadsheets.readonly`. `GET /api/sheets/chart?book=bot3&gid=1145735379&n=1` reads that chart and caches it for five minutes. The page tries the static file first and calls this route only when `SHEETS_LIVE` is on and the file is missing or the index has no entry. A missing credential, a failed fetch, or an unsupported type falls back to the snapshot and then to the PNG. The response never includes the token. Retired tabs return 410 and are not fetched.

With the flag on, every non-table chart shows Interactive, because the server can read the sheet on demand. With the flag off, the button appears only for charts in `index.json`.

### Pilot tabs

Nine charts, chosen from the catalog. The numbers are a transcription of the committed PNGs so the page renders without Google:

| Workbook | Tab | What it exercises |
| --- | --- | --- |
| My Bot's Sheets | MSTR Debt (`1145735379`) | four charts: columns, a percent column, sats per share, an area chart |
| bot experiments 2 | 6 MSTR vs ASST leverage (`1690294676`) | two lines; also on Charts of the day |
| bot experiments 2 | 5 MSTR USD reserve vs dividends (`49428948`) | two lines, violet and orange, with a gap |
| Bot experiments | 01 BTC vol vs VIX (`1546024641`) | two lines |
| Bot experiments | 04 BTC vs 200-week MA (`1810821762`) | two lines, log y-axis |
| Bot experiments | 09 Real S&P 500 (`1540677114`) | two lines |

## Charts of the day

The **Charts of the day** tab reads `static/cotd/today.json` and shows every item in that file. Each edition is a uniform random draw of 20 catalog items: every chart, and every chartless table tab. A tab with several charts contributes one item per chart. `scripts/cotd_build.py` writes up to 20 cards, fewer when fewer items are eligible, and does not pad. It refuses a gid in `sheets_catalog.EXCLUDED_TABS` or in `COTD_EXCLUDED` (bot experiments 2 gid 645072508, BX2-069, "69 Mark MSTR red dot tracker"). It also leaves out a chart titled `Chart N` or with no title, a blank or near-uniform PNG, and a `Chart N` card that has no image of its own. It writes `static/cotd/<date>/cards.json` and leaves out an item that appeared in any of the previous 3 days (book, gid, and `chart_n` when both sides have it; book and gid when either side does not). A missing `cards.json` for one of those days excludes nothing. Every card stores `chart_n` (1-based, catalog `chartList` order). A table is `chart_n` 1. The card shows the title and the source workbook, plus a chart image or a table preview when the library has one, and a link to the tab. It does not include a reason the item was chosen. The daily command is `python3 scripts/cotd_build.py`. The steps are in `scripts/COTD_README.md`.

## Price targets

The **Price targets** tab reads `static/price_targets.json`. Render does not call Google. The table is Steve's DCF base value per share against the street consensus mean and the price, sorted by base upside (high to low). Columns: Ticker, Steve's Base, Consensus, Price, Base Upside, Rating, Why. Steve's Base shows the dollar change versus the previous update, in parentheses, when `baseChangedAt` is within the last 7 days (green up, red down). A new ticker shows the price only. A blank Street consensus (an IPO-day name with no analyst coverage) is stored as null, and the Consensus column shows a dash.

Opening the tab loads quotes through the same `/api/chart` path as Charts and recomputes Price, base upside, and the rating from the price on screen. Rating is BUY above +15% upside to base, SELL below -15%, otherwise HOLD. A ticker whose quote fails keeps the snapshot price; the status line names the date in use (Yahoo close, or the snapshot date). Columns are as wide as their text, and the table is not stretched to the page. Download PNG is that same table. Watermark is off unless the box is checked.

`why` is not a sheet column. Edit it in the JSON. Regenerating the file keeps existing `why` lines, and the footer notes if they were edited.

Refresh the snapshot where the sheet can be read:

1. Export Sheet1 `A1:Q30` from [DCF price target summary](https://docs.google.com/spreadsheets/d/10rRnxxpVrv5A2kUWrxiGSysG--S_wJmmBvckjtprnj0) as CSV. The link export is 401 unless the sheet is shared. Column Q is optional: header `Paper weight %`.
2. `python3 scripts/price_targets.py --csv that.csv`
3. Commit `static/price_targets.json`.
4. `python3 scripts/paper_trading.py` and commit `static/paper_trading.json` in the same update. The paper book reads `paperWeight` from this file.

`python3 scripts/price_targets.py` tries that export first. `python3 scripts/price_targets.py --seed` writes the Oct 21 email figures when the sheet cannot be read. The scenario row `SPCX – Elon case (scenario)` is skipped. `python3 scripts/price_targets.py --self-test` checks the parser.

## Paper trading

The **Paper trading** tab sizes a virtual $100,000 book from `paperWeight` on each row of `static/price_targets.json`. That field is a signed percent of NAV: `10` is a 10% long, `-5` is a 5% short, and `0` or a missing field is cash. The rating column is shown for context and does not place trades. Open the tab at `/?tab=paper`.

The number comes from the DCF summary sheet column **Paper weight %** (column Q, after Previous update detail). `scripts/price_targets.py` reads it when the header is present and writes `paperWeight`. A missing column or a blank cell is stored as `0`. Values may include a percent sign (`10%`, `-5%`).

Until any weight is non-zero, the book stays in cash and the tab says it is waiting for weights. The first non-zero weights trade at the next session open after that update is seen: today's open when the script runs before 9:30 ET, otherwise the following session. To fill that first book at a session open that has already passed, run `python3 scripts/paper_trading.py --inception YYYY-MM-DD` once. The flag is ignored once the book has started, so the nightly run (no flag) keeps those fills and that inception date. A later change to a weight resizes, opens, closes, or flips that name at the next open. Running the script again with the same weights does not book another trade.

Gross exposure, the sum of the absolute weights, is capped at 100%. Above that, every weight is scaled by the same factor and the tab shows a warning. There is no leverage and no fee. A short credits entry times shares to cash; those proceeds are not free capital on top of the cap, because the short's market value is subtracted in NAV. NAV = cash + long market value − short market value. Short P&L is (entry − current) × shares. Daily closes are marked against SPY from the first trade's open.

`GET /api/paper` runs the same rules in memory through the Yahoo helper `/api/chart` uses, and does not write the file. Persist the book after every `price_targets.json` update:

```
python3 scripts/paper_trading.py
```

Then commit `static/paper_trading.json`. `python3 scripts/paper_trading.py --self-test` runs the trade tests. The stake, gross cap, and session clock are `CONFIG` at the top of `scripts/paper_trading.py`.

## Daily briefing

The **Daily briefing** tab is the page the app opens on. It is a reverse-chronological archive of Lloyd's 9 AM ET news briefing. The newest briefing is expanded; older ones are collapsed. `?tab=` still opens that view (`?tab=watch` and `?tab=watchlist` are Watchlist in the Chart dropdown, `?tab=paper` is Paper trading in the Views dropdown, `?tab=price`, `?tab=prices`, and `?tab=charts` are Price, and the same for the others). `?tickers=`, `?ftickers=`, and `?vtickers=` still open Price, Financials, and Valuation.

`GET /api/briefing` reads `data/lloyd_briefings.json`. Render does not call Gmail. A daily routine appends today's briefing and commits the file to main:

```
python3 scripts/add_lloyd_briefing.py \
  --subject "$SUBJECT" \
  --sent-at "$SENT_AT" \
  --body-file "$BODY_PATH" \
  --message-id "$MESSAGE_ID" \
  --commit --push
```

`--push` uses `CHARTMAKER_GITHUB_TOKEN` and never prints it. Upserts are keyed by the America/New_York calendar date of the send time: the same message is idempotent, and a later email that day replaces an earlier one. HTML is sanitized on write (no scripts, iframes, or remote images; unsubscribe and tracking links are dropped).

```
python3 scripts/briefing.py
python3 scripts/briefing.py --json
python3 scripts/briefing.py --self-test
```

Price targets and BTC Treasury live in the Views dropdown; Charts of the day stays on its own tab. The refresh scripts still append `static/price_targets_history.json` and `btctop100_history.json`.

## Sentiment tab (retired)

The Sentiment tab (bull-vs-bear board and history) was removed on 2026-11-21 at Mark's request. It is no longer in the app, and the Wednesday refresh is stopped. The Sheets library chart "176 Misery index vs sentiment" is unrelated and stays in the library.

## X Bookmarks tab

**X Bookmarks** (top row, after Sheets; `?tab=bookmarks`, `?tab=x-bookmarks`, `?tab=xbm`) lists Mark's X bookmarks (@thepowerfulhrv, id 127712777) in bookmark order. Each card has the author (name and @handle), the full post text (long posts use `note_post`; t.co links are expanded, media links dropped), the post date in ET, an "Open on X" link (`https://x.com/<handle>/status/<id>`), and likes, reposts, replies, quotes, bookmarks and views (views only when X returns them). Posts with media show a thumbnail (the photo, or `preview_image_url` for video and GIF) that was downloaded once, shrunk to a ≤360px JPEG, and is served by Chartmaker, not hotlinked from X. Search matches text, name or @handle; sort by recently bookmarked, newest/oldest post, most engagement (likes + reposts + replies + quotes + bookmarks), most likes or most views. The header shows the count, how many have media and when the data was last pulled.

Privacy: this repo is public, so bookmark data is never committed in plain form. `scripts/refresh_x_bookmarks.py` seals the whole bundle (posts + thumbnails) with a key derived from the app password (`scripts/x_bookmarks.py`: PBKDF2-SHA256, SHAKE-256 keystream, HMAC-SHA256 tag) and commits only `x_bookmarks.enc` to the data-only branch `x-bookmarks-data`. The server decrypts it in memory with `APP_PASSWORD` (or `XB_KEY`), caches it for 10 minutes, and serves `GET /api/x-bookmarks` and `GET /api/x-bookmarks/thumb?id=<post id>` only to signed-in browsers (401 otherwise). If the app password changes, re-run the refresh so the bundle is sealed with the new one. `XB_FILE=/path/x_bookmarks.enc` reads a local bundle instead of GitHub (dev and tests).

Refresh (on demand only; each pull spends Mark's X API credits, so no routine and no polling): an agent pulls through the user-X connector's `get_users_bookmarks` (id `127712777`, `max_results` 100, expansions `attachments.media_keys,author_id`, `media.fields` `url,preview_image_url,type`, `user.fields` `username,name,profile_image_url`, `tweet.fields` `created_at,public_metrics,attachments,author_id,note_post,entities`), following `meta.next_token` as `pagination_token` until it is absent, and saves each page to `/workspace/x-bookmarks/raw/pageNN.json`. Then:

```
python3 scripts/refresh_x_bookmarks.py --raw /workspace/x-bookmarks/raw/page*.json --api-calls <N> --publish
```

That needs `CHARTMAKER_APP_PASSWORD` and `CHARTMAKER_GITHUB_TOKEN` in the environment (never printed). No redeploy is needed; the server picks the new bundle up within 10 minutes (or `GET /api/x-bookmarks?fresh=1`). X only returns the ~800 most recent bookmarks; older ones are not reachable through the API. Bookmark folders are not pulled.
