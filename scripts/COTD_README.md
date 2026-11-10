# Charts of the day: daily refresh (for Flash)

The Chartmaker tab **Charts of the day** reads `static/cotd/today.json` (fetched with no-cache) and the PNGs in `static/cotd/YYYY-MM-DD/`. Each edition is 20 charts drawn uniformly at random from the sheet catalog, or every eligible chart when fewer than 20 remain. The builder does not rank charts and does not write notes. Updating the tab means committing a new edition to `main`. Render redeploys on push.

There is no picks file and no curation step. Do not choose charts from the news or from X, and do not write why-picked, why-it-matters, or what-to-know text.

## Daily steps (on the box)

1. **Sync:** `cd /workspace/chartmaker && timeout 30 git fetch origin && git reset --hard origin/main`
2. **Build:** `python3 scripts/cotd_build.py`
   - Run that from the chartmaker repo. No arguments.
   - The edition date is today in America/New_York. The same date, catalog, and history draw the same charts, so a retry the same day rewrites the same edition.
   - Eligible charts are real charts in `static/sheets/catalog.json` whose PNG is already in `static/sheets/full/<book>/<gid>.png` (chart 2 and later: `<gid>_2.png`, `<gid>_3.png`, …). A chartless tab, such as a table, is not eligible. A chart with no library PNG is not eligible.
   - The draw skips `sheets_catalog.EXCLUDED_TABS` (bot1 `333185169`, `1547332232`, `643299192`, `1791345316`, `1370617918`, `1024131985`, `292208014`, `2119398138`, `1584345646`, `1186940001`, `501919598`, `579853621`, and bot2 `418894352`) and `cotd_build.COTD_EXCLUDED` (bot2 `645072508`, tab "69 Mark MSTR red dot tracker", BX2-069). The red-dot tracker stays in the sheet catalog and is left off Charts of the day only. Add a newly retired tab by putting `("book", gid)` in `EXCLUDED_TABS`; the import, thumbnail, CotD, and favorites checks follow that set.
   - It also skips a chart that appeared in any of the previous 3 editions. It reads `static/cotd/<date>/cards.json` for D-1, D-2, and D-3. A match is book + gid + `chart_n` when both the new chart and the older card record `chart_n`, and book + gid when either side does not. A missing or unreadable `cards.json` excludes nothing for that day.
   - It draws 20 charts, or every eligible chart when fewer than 20 remain. It does not pad. If none are eligible it stops with an error and leaves `static/cotd/today.json` unchanged.
   - It checks that each image is not blank, fits it to 1600x900 on white, and writes `static/cotd/<date>/01.png` through `N.png`, `static/cotd/<date>/cards.json`, and `static/cotd/today.json`. Every card has `chart_n` (catalog `chartList` order). A tab with several charts can contribute more than one card, one per chart. Cards record the title and the source workbook. They do not record notes.
   - It keeps the last 7 editions.
   - Treat the Google sheets as read-only. Never edit them. Do not download chart PNGs for this refresh; the weekly library import already committed them.
3. **Check locally** (optional): run `VIEWS_STORE=browser python3 server.py 18799`, then open http://localhost:18799/ and go to the "Charts of the day" tab. The header counts the charts. Each card shows the chart, its title, and the workbook and tab.
4. **Commit and deploy:** run `git add static/cotd && git commit -m "Charts of the day <date>"`. Push to main through the usual route (format-patch, then a CloudAgent pushes it). Render redeploys in a few minutes.

`--date YYYY-MM-DD` builds that edition instead of today. The daily run does not pass it.
