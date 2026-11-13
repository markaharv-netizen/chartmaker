# Chartmaker
Browser app for X-ready stock/crypto charts (not for trading). Data: Yahoo Finance chart API via a tiny stdlib Python proxy (no pip installs).

Start: `cd /workspace/chartmaker && nohup python3 server.py 18790 > server.log 2>&1 &` then open http://localhost:18790
(Port 8790 is held by the box's egress tunnel, so the app runs on 18790.)

Use: tickers (comma-separated, e.g. `NBIS,CRWV,BTC-USD`), optional display names, range (1D/5D intraday, YTD, 1M, 6M, 1Y, 5Y, Max, Custom),
type (price line, % change indexed to start, candles + volume for one ticker, bar ranking of % return, ratio A/B with exactly two tickers), title, color, watermark toggle.
Line color on Charts, the one-series color on Financials, and bar color on Valuation share one list. Orange (`#F7931A`) is still the default and violet (`#8B5CF6`) is still the second choice. The list also includes the comparison palette (sky, green, red, ink, pink, teal) plus blue, olive, magenta, and brown. The control is one dropdown; Custom shows a color input and hex field only after that option is chosen. One series uses the picked color. Several series keep the old palette order for the first eight, then the extra hues. A saved view stores the hex, so an older orange or violet choice still applies. `?color=`, `?fcolor=`, and `?vcolor=` set the same hex. Dark themes brighten the named colors, and a custom hex is lightened or darkened only when it would vanish. Download PNG uses the color on screen.
"Download PNG" exports 1600x900 at 2x (3200x1800). Status bar shows "data as of" (ET); fetch failures show an error and render nothing.
Render on Charts, Heatmap, Treasury, and Valuation fetches fresh data (the separate Refresh button is gone). The Charts tab is the old Prices tab: `?tab=prices` and `?tab=charts` both open it, and `?tickers=` still does too.

## Treasury tab

One metric: **BTCTC top 100**. The table is Mark's sheet tab of that name in the workbook "My Bot's Sheets" (`1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU`, gid 73825060), not a BitcoinTreasuries.net scrape. The chart title is the sheet title (Corporate Bitcoin Treasury Holdings). Columns: rank, company, ticker, country, BTC, and Change. Change is the sheet's Change cell as displayed (a leading + or - stays); positive is green, negative is red, and an empty cell is an em dash. Each row's as-of date stays in the snapshot and is not a column. The footer total is the sum of BTC holdings. Holdings are the committed snapshot `btctop100.json`. The chart does not draw a USD column or a % of 21M column. `/api/treasury` still returns `usd` (BTC × a live Yahoo `BTC-USD` quote, or null) and `pctSupply` so existing readers of that response keep working; the weekday sync does not write those fields and must not put the columns back. The status line can still show the BTC spot price. Watermark is off unless the box is checked. Download PNG is the chart canvas.

Refresh the snapshot (then commit `btctop100.json`). The script reads the sheet with Google credentials, and falls back to a CSV export:

```
python3 scripts/refresh_btctop100.py
python3 scripts/refresh_btctop100.py --csv btctc100.csv
python3 scripts/refresh_btctop100.py --check
```

Credentials, first match: `GOOGLE_ACCESS_TOKEN` or `CHARTMAKER_GOOGLE_TOKEN`; or `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` (`CHARTMAKER_GOOGLE_*` names work too); or a service-account JSON in `GOOGLE_APPLICATION_CREDENTIALS` or `CHARTMAKER_GOOGLE_CREDENTIALS`. The sheet is private. `--csv` or `BTCTC_CSV` is the fallback.

Watchlist still updates only from its Refresh button. Financials has no quote-cache bypass. Financials and Valuation open with an empty ticker box and a blank chart until tickers are entered. Deep links: `?tickers=` and `&names=` (Charts), `?ftickers=` and `&fnames=` (Financials), `?vtickers=` and `&vnames=` (Valuation). Charts SMA is a multi-select menu (20, 50, 100, 200, 200W, plus a custom length).

Notes: YTD baseline = last close of prior year. 5Y uses weekly bars, Max monthly. Ratio aligns A to the latest B bar at or before each date.
Re-run samples: `python3 shoot.py` (uses /usr/bin/google-chrome).

## Financials mode (v2)
Toggle "Financials" at top. Data: SEC EDGAR XBRL companyfacts (primary source), ticker→CIK via company_tickers.json,
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

This browser also keeps `localStorage` key `chartmaker_sheet_favs` (v2: last synced items, revision, and clicks not yet confirmed). The first sync from a browser adds its local stars into the shared list and does not delete stars the shared list already has. A star that was explicitly removed (a tombstone) is not added back by that migration. Later clicks send only those clicks. A stale browser cannot replace the whole list. An add is ignored when a newer removal happened after the revision that browser last saw, and a remove is ignored when the star was added again after that revision. The cap is 200. Retired bot1 gids `1547332232`, `643299192`, `333185169`, `1024131985`, `1791345316`, `1370617918`, `292208014`, `2119398138`, `1584345646`, `1186940001`, `501919598`, `579853621`, `1308503061`, `748431671`, `254001772`, `1238930141`, `1835714359`, `492285771`, `220064271`, `79264442`, `1755343475`, `1067583608`, `1924976898`, `1978525586`, `1255387315`, `1522766504`, `1163669375`, `1576800449`, `1192979444`, `1912487083`, `664060481`, and `1778640335`, and bot experiments 2 gid `418894352`, are rejected. If the server cannot be reached, the star stays on this device, the page says it is not synced, and the click is retried from localStorage. A chart that leaves the catalog, or whose title no longer matches that chart number, is removed for everyone. A table favorite stores the tab name as its title, so renaming that tab removes it the same way.

Import the weekly export (files named like the tab, optional trailing `YYYY-MM-DD`, newest date then newest file time wins) on a machine that has the PNGs, then commit `static/sheets/full/`:

```
python3 scripts/import_full_pngs.py --from-dir /path/to/exported-pngs
```

Put each workbook in its own subfolder (`bot1`, the spreadsheet id, or the workbook title) to limit matches to that book. Repeat `--from-dir` for several folders. `--dry-run` prints matches without writing. `--no-optimize` copies the bytes unchanged. Otherwise the script uses `oxipng` or `optipng` when one is on PATH, otherwise `pngquant` (kept only when the file shrinks), otherwise Pillow's lossless optimize when Pillow is installed.

Refresh the tab list and thumbnails on a machine that can read the workbooks:

1. Save each spreadsheet's metadata JSON (`spreadsheets.get` / the Sheets `get_spreadsheet` response). This is the Monday part 3 dump. The field mask has to include chart position, not only titles: `sheets(properties,charts(chartId,spec.title,position))`. `properties` still supplies sheetId, title, index, and hidden. `position.overlayPosition.anchorCell` supplies rowIndex and columnIndex. Chart numbers follow that anchor (row, then column, then the original `charts[]` index), the same order the Monday PNG export uses (`pngChartsSorted_`). A zero row or column is often absent from the JSON; keep the anchorCell object in the dump anyway, and the missing index counts as 0. A one-chart tab does not need position.
2. `python3 scripts/sheets_catalog.py bot1.json bot2.json bot3.json`  
   This rewrites the tab list and **keeps** a thumbnail already recorded for a book and gid when that JPEG is still on disk. A multi-chart tab whose dump has no anchor on every chart keeps its existing catalog entry (`charts` and `chartList`) and the script prints a warning, so a title-only dump cannot renumber charts. A new multi-chart tab with no position is left out until the dump includes position. A one-chart tab is numbered n=1 without position. `EXCLUDED_TABS` in that script stays out of the rebuilt catalog: Bot experiments gid 333185169 ("87 TSLA beta to Nasdaq") even when that sheet tab still exists with no chart, and gid 1547332232 ("161 Nvidia P-S and P-E"), gid 643299192 ("202 Nvidia revenue by geography"), gid 1791345316 ("158 Korea & Taiwan vs S&P"), gid 1370617918 ("163 Supercore vs core CPI"), gid 1024131985 ("99 MSTR BTC per share"), gid 292208014 ("98 MSTR vs BTC drawdowns"), gid 2119398138 ("151 Big Tech capex vs Nvidia"), gid 1584345646 ("223 Micron revenue & margin"), gid 1186940001 ("226 OpenAI vs Anthropic revenue"), gid 501919598 ("263 Tariff pass-through"), gid 579853621 ("12 Breakeven vs CPI"), gid 1308503061 ("122 Bond vol vs stock vol"), gid 748431671 ("232 Quits vs wage growth"), and gid 254001772 ("162 Factory construction boom"), gid 1238930141 ("153 Big Tech capex intensity"), gid 1835714359 ("43 Nasdaq vs S&P ratio"), gid 492285771 ("109 Equal vs cap-weight S&P"), gid 220064271 ("135 Real coffee & cocoa"), gid 79264442 ("173 Continuing/initial claims"), gid 1755343475 ("239 Foreign vs native-born jobs"), and gid 1067583608 ("233 Older workers participation"), gid 1924976898 ("57 PPI vs CPI"), gid 1978525586 ("55 Shelter vs home prices"), gid 1255387315 ("115 Regional banks vs S&P"), gid 1522766504 ("128 Long bonds vs stocks DD"), gid 1163669375 ("186 Sahm rule recession gauge"), gid 1576800449 ("225 Nvidia inventory & DSO"), gid 1192979444 ("259 Financial conditions"), gid 1912487083 ("285 Ethereum L2s: Base leads"), gid 664060481 ("322 Microsoft quarterly FCF"), and gid 1778640335 ("324 Amazon quarterly FCF"), plus bot experiments 2 gid 418894352 ("81 CoreWeave margins"), even if a dump still lists those deleted tabs. Image import and thumbnail refresh skip the same gids, and `scripts/cotd_build.py` refuses to publish them. Import and thumbnail refresh also skip a PNG that is blank or near-uniform (the empty Google chart that says to add a series). Import skips a chart titled `Chart N` and a chart number that is no longer in that tab's `chartList`. Charts of the day does not draw an untitled `Chart N` chart, a blank PNG, or a `Chart N` card that has no image of its own. Charts of the day also refuses bot experiments 2 gid 645072508 ("69 Mark MSTR red dot tracker", BX2-069) through `COTD_EXCLUDED` in `scripts/cotd_build.py`. That tab stays in the catalog, the full PNG, and the thumbnail.
3. Point `scripts/sheets_thumbs.py` at a local folder of the weekly chart export (not at Render). The export names files after the tab, with the number sometimes zero-padded and a date on the end (`090 TSLA gross margin vs price 2026-10-21.png`). Duplicate names: the script keeps the newest date, then the newest file time.
   `python3 scripts/sheets_thumbs.py --from-dir /path/to/exported-pngs`
4. Commit `static/sheets/catalog.json` and `static/sheets/thumbs/`. Do not commit the source folder itself.
5. `python3 scripts/import_full_pngs.py --from-dir /path/to/exported-pngs`  
   Then commit `static/sheets/full/`. The script prints `matched N, unmatched M, ambiguous A`.

Until that export is on the machine, seed only the tabs that overlap today's Charts of the day:

`python3 scripts/sheets_thumbs.py --from-cotd`

Workbooks: Bot experiments `1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY`, bot experiments 2 `1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ`, My Bot's Sheets `1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU`. All three dumps are required unless you pass `--allow-partial`. The weekly PNG folders feed the sheet library, which Charts of the day copies previews from: `1SwZgYGwwHsoL_DXQUuQAYS6lhq5KxFe9`, `1mMUKaB_RbeZhtn5ntuKJV5xZOeo6LzGH`, `1AVfHRo2mJgBhbJoszHhe5vgOscfKhbzT`.

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

The **Daily briefing** tab is the page the app opens on: price targets, sentiment, BTC treasury holdings, the paper book's saved NAV, and charts of the day. `?tab=` still opens that tab (`?tab=watch` is Watchlist, `?tab=paper` is the paper book, `?tab=prices` and `?tab=charts` are Charts, and the same for the others). `?tickers=`, `?ftickers=`, and `?vtickers=` still open Charts, Financials, and Valuation.

`GET /api/briefing` builds the page from the files those tabs already use. No new scheduled job. Render does not call Google or X.

```
python3 scripts/briefing.py
python3 scripts/briefing.py --json
python3 scripts/briefing.py --self-test
```

- **Price targets.** Current `static/price_targets.json` against `static/price_targets_history.json`, the last snapshot from before the 7-day window that ends on the file's as-of date. `scripts/price_targets.py` appends a snapshot when base, street consensus, rating, notes, or why change. Commit `static/price_targets_history.json` with the price-target file.
- **Paper NAV.** The saved marks in `static/paper_trading.json`. This is the last marked close, not a live quote.
- **Sentiment.** `sentiment.json` against the previous date in `sentiment_history.json` → `daily`. A name with fewer than 8 classified bull and bear items is marked thin, same cutoff as the hollow dots on Over time.
- **BTC treasury.** Latest `btctop100.json` against the previous snapshot in `btctop100_history.json`. `scripts/refresh_btctop100.py` appends one snapshot per refresh, including each company's as-of date, note, and source. Commit `btctop100_history.json` with `btctop100.json`. Holding drift under 1 BTC is left out. A change whose as-of month also changed is listed under Updated filings, with both as-of dates and the sheet note, not as a one-day buy or sale. Same-period changes stay above that group. Companies that enter or leave the top 100 are listed.
- **Charts of the day.** `static/cotd/today.json`. Tabs in `EXCLUDED_TABS` or `COTD_EXCLUDED` are left out. The sheet catalog does not record how much each chart's underlying data moved at the weekly refresh, so the briefing does not rank charts by data change.

## Sentiment tab

Ranked bull-vs-bear board (like the "analyst sentiment" boards on X). The app only reads the static
`sentiment.json` (served at `/api/sentiment`); nothing is scraped on Render.

**Board tickers (21):** AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, **MU**, MSTR, COIN, BTC-USD, **ETH-USD**, CRWV, NBIS, IREN, CIFR, **ASST**, **SPCX**, **RKLB**, **ASTS**, **STRC**. Added 2026-11-02: STRC (Strategy's variable-rate preferred; README history queries below; news "Strategy STRC preferred stock"); removed 2026-11-02: INTC, APLD, WULF, AVGO. Added 2026-10-21: RKLB (Rocket Lab; news "Rocket Lab RKLB stock"), ASTS (AST SpaceMobile; news "AST SpaceMobile ASTS stock") — both tickers are unambiguous on X, so plain `RKLB`/`ASTS` queries; ETH-USD (Ethereum; crypto row like BTC-USD: `display` "ETH", `logoKey` "c:ETH", `kind` "crypto"; X queries use `Ethereum ...`; news "Ethereum price"), ASST (Strive; news "Strive ASST stock"), MU (Micron; X queries use `MU Micron ...` because bare "MU" is ambiguous; news "Micron stock"), SPCX (SpaceX, so every DCF-model stock — TSLA, SPCX, MSTR — is on the board; news "SpaceX SPCX stock"; plain query `SPCX SpaceX -pump -memecoin ...`). The daily input file must list all 21; they first appear on the board at the next daily refresh (no board snapshot is fabricated for them before that).

**Data pipeline (daily refresh)**
1. An agent with the X connector collects the last 7 days per ticker (≈7–9 calls/ticker):
   `search_posts_all` "`<T> -is:retweet -is:reply lang:en`" (relevancy, **up to 100 posts per search = 4 pages of 25**: the connector returns ≤ 25 per call, so follow `next_token` up to 4 pages, stop early when a page is thin/empty, dedupe by post id and combine the pages into one file per search), a second search (same 4-page pagination) adding
   `(bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued) -whatsapp -telegram -airdrop`,
   and `search_news` "`<company> stock`" (168h, 5). Note: X search strips `$`, so cashtags are plain tickers.
2. It labels each post/story **bull / bear / neutral** (spam, giveaways, generic lists and pattern-tutorial posts are neutral)
   and writes short, neutral-worded bull and bear headlines (no invented quotes) into
   `tools/sentiment_input/<YYYY-MM-DD>.json` (same shape as the 2026-10-18 file).
3. `python3 tools/build_sentiment.py tools/sentiment_input/<date>.json` does the math and writes
   `sentiment.json` + appends `sentiment_history.json`; commit both.

**Method**
- Weight per item: posts `1 + ln(1 + likes + 2·reposts + replies + 2·quotes)`, news stories `3`.
- Bull share = Σw(bull) ÷ Σw(bull + bear). Neutral items count toward "posts scanned" only.
- Band = 95% Wilson interval on the Kish effective sample size `(Σw)² / Σw²` (drawn hatched around the split).
- σ = (bull share − mean) ÷ stdev across the tickers in that run (cross-sectional), unless the ticker has an
  "Over time" history series with ≥ 20 earlier points; then σ = z-score of its **latest history point** vs its own earlier
  points (`sigma_basis: "own"`). Both sides of that z-score come from the same X-only rules-labeled series, so hand-labeled
  board levels and rules-labeled history levels are never mixed. Today MSTR, TSLA and BTC use "own"; the other 15 board names are cross-sectional.
- Bear headline is null (shows "No dominant bearish consensus…") when there are fewer than 2 bear items or the bear share is under 15%.
- Rows are sorted by bull share, descending (both in `sentiment.json` and re-sorted by the frontend at render time). The ticker box filters the dataset; it can't add tickers that aren't in the json.
- Crypto rows: give the input ticker `display`, `logoKey` (e.g. `"c:BTC"`, same crypto logo path as the heatmap) and `kind: "crypto"`;
  they pass through to `sentiment.json`. With more than 12 rows the board switches to a compact layout (no company-name line, short no-bear text) so 18+ rows fit the 1600×900 PNG.

### Over time (history) view

Sentiment tab → **View: Over time**. Weekly bull share (0–100%) per ticker with its 95% band shaded, a dashed 50% line,
and an optional dashed price line on a right axis (weekly closes from the normal `/api/chart` path with its usual fallbacks:
`$` for one ticker, % from start when several are picked). The picker lists only tickers that have history: weekly = **MSTR, TSLA, SPCX, SATA, STRC, BTC-USD, META**; daily (Points: Daily) = all 22 names. Hover shows window dates, bull %, band, bull/bear counts, posts and the close.
Hollow dots = thin week (< 8 bull+bear posts). Download PNG is 1600×900; view, picker and price toggle are saved per tab in views.
Data: `sentiment_history.json` → `series` (served at `/api/sentiment_history`); the board's own daily snapshots sit under `daily`.

**History is X-only and labeled by rules, not by hand.** It differs from the board on purpose:
- No news (the X news search can't be windowed to past weeks), so every history point is X posts only.
- 2 searches per ticker-window (the plain and opinion queries below), ≤ 25 posts each, deduped, relevancy-sorted.
- Posts are labeled by `tools/sentiment_lexicon.py` (opinion keywords with negation handling; price-move reports, TA level sheets,
  ticker lists, polls like "bullish or bearish?" and spam are neutral). Check against the 867 hand-labeled posts of
  2026-10-18 (`python3 tools/sentiment_lexicon.py`): 72% 3-way agreement, 84% agreement on bull vs bear when both call it
  polar, but per-ticker level only loosely tracks the hand labels (r ≈ 0.5, mean gap ≈ 20 pts). Read the lines for **direction and
  turns within a ticker**, not as levels comparable to the board bars or across tickers.
- Same weights, bull-share formula and Wilson/Kish band as the board (`build_sentiment.score`).
- Windows: 7 days ending Sunday 16:00 UTC (noon ET); a point is plotted at its window end date. Backfill: 26 weeks
  (windows ending 2026-04-19 … 2026-10-11) plus the Oct 11–18 week (MSTR/TSLA/BTC re-label the board snapshot's posts; SPCX/SATA/STRC used the 2 searches).
- SPCX listed on Nasdaq on Jun 12 2026 (pre-IPO posts were mostly perps/tokens), so its history starts with the window ending Jun 14 (19 points).
- META was added 2026-10-23: weekly backfill of the 27 windows ending 2026-04-19 … 2026-10-18 (54 X calls; plain query adds `(stock OR shares OR Zuckerberg)` because bare "meta" is a common word). Its rolling-7 points start with the next morning routine.
- SATA is **Strive's** (ASST) variable-rate preferred, not Strategy's; STRC is Strategy's.

Per-ticker queries (X strips `$`; times are `start_time`/`end_time` in UTC; `max_results` 25, `sort_order` relevancy,
`post.fields` public_metrics,created_at, `expansions` author_id, `user.fields` username):

| Ticker | plain query | opinion query |
|---|---|---|
| MSTR | `MSTR -is:retweet -is:reply lang:en` | `MSTR (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued OR mNAV) -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |
| TSLA | `TSLA -is:retweet -is:reply lang:en` | `TSLA (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued) -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |
| BTC-USD | `BTC Bitcoin -is:retweet -is:reply lang:en` | `Bitcoin (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued) -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |
| STRC | `STRC -is:retweet -is:reply lang:en` | `STRC (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued OR par OR yield) -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |
| SATA | `SATA (Strive OR ASST OR preferred OR dividend) -is:retweet -is:reply lang:en` | `SATA (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued OR par OR yield) -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |
| META | `META (stock OR shares OR Zuckerberg) -is:retweet -is:reply lang:en` | `META (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued) -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |
| SPCX | `SPCX SpaceX -pump -memecoin -is:retweet -is:reply lang:en` | `SPCX (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued) -pump -memecoin -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |

(The very first SATA window, ending Apr 19, used `SATA Strategy -is:retweet -is:reply lang:en` for the plain query.)

### Daily refresh routine (7:30 AM ET) — exact steps

Do both parts every morning. Budget ≈ 14 X calls for the history + ≈ 7–9 per board ticker (2 searches × up to 4 pages + 1 news; ≈ 150–190 total); stay under 30 calls/minute.

**A. History points for the 7 history tickers** (MSTR, TSLA, SPCX, SATA, STRC, BTC-USD, META):
1. Let `END` = now rounded down to the hour in UTC (e.g. `2026-10-19T11:00:00Z`) and `START` = `END` − 7 days.
2. For each ticker run the 2 searches from the table above with `start_time=START`, `end_time=END`, saving each result to a file
   (connector `write_to_file`). No news.
3. For each ticker:
   `python3 tools/sentiment_history.py ingest <TICKER> <START> <file_plain> <file_opinion> --end <END> --src x-rolling7 --q "<plain query>" --q "<opinion query>"`
   This writes `tools/sentiment_input/history/<TICKER>/<END date>.json` (rules-labeled posts; re-running the same day overwrites that day's file).
   Never hand-edit labels in history files: the series must stay one consistent labeler.
4. `python3 tools/sentiment_history.py build` → rewrites `series` in `sentiment_history.json` from every saved file.

**B. Board snapshot** (the 21 board tickers listed above, incl. ETH-USD, ASST, MU, SPCX, RKLB, ASTS, STRC): steps 1–3 of "Data pipeline" above, then
`python3 tools/build_sentiment.py tools/sentiment_input/<YYYY-MM-DD>.json` **after** A.4 (so MSTR/TSLA/BTC σ uses today's history point).
It writes `sentiment.json` and adds `daily[<date>]` to `sentiment_history.json` without touching `series`.

**A2. Daily points (all 22 names: the 21 board tickers + SATA)** — `series_daily`, shown with Over time → Points: Daily.
For yesterday's UTC day `D` (00:00–24:00Z), per ticker run ONE `search_posts_all` with the opinion query
`<SYM> (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued[ OR mNAV for MSTR | OR par OR yield for STRC/SATA]) [-pump -memecoin for SPCX] -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en`
(BTC-USD uses `Bitcoin`, ETH-USD uses `Ethereum`, MU uses `MU Micron`), `start_time=D`, `end_time=D+1`, max 25, relevancy, same fields as above; then
`python3 tools/sentiment_history.py ingest-daily <SYM> <D> <file> --q "<query>"` and re-run `build`. ≈ 22 calls/day.
**Backfill still open:** target start 2026-07-21 (`DAILY_START`); as of 2026-10-23 (backfill run 7) Aug 9 – Oct 22 are collected, so each ticker has
20 gap days (Jul 21 – Aug 8, plus the current day until the morning routine; RKLB also Oct 3 and SATA also Aug 9, whose searches returned 0 posts; `daily_gaps` in `sentiment_history.json`). Continue backwards with the same single search per
ticker-day (≈ 494 calls, i.e. one more nightly run of the 1,000/day X budget, ≤ 30/min). Never interpolate gaps. Daily points are thin
(1 search, often < 8 bull+bear posts → hollow dots); read them for turns, not levels.

**C.** Commit `sentiment.json`, `sentiment_history.json`, `tools/sentiment_input/<date>.json` and `tools/sentiment_input/history/` together.

**How daily points mix with the weekly backfill:** both are 7-day windows with the same 2 queries, labeler and math, so they are one
series: the backfill gives one point per week (windows ending Sundays, `src: "x-weekly"`), and from Oct 19 the routine adds one
overlapping rolling-7-day point per day (`src: "x-rolling7"`, plotted at its end date). Consecutive daily points share 6 of 7 days,
so they move smoothly; don't read day-to-day wiggles as new information. The weekly points are left as they are (never
recomputed from daily files). σ "own" compares the newest point with all earlier points (weekly + daily).
If a search errors or returns nothing, skip that ticker for the day (no point is better than an invented one) and note it in the commit message.
