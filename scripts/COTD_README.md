# Charts of the day: daily refresh (for Flash)

The Chartmaker tab **Charts of the day** reads `static/cotd/today.json` (fetched with no-cache) and the PNGs in `static/cotd/YYYY-MM-DD/`. Updating the tab means committing a new edition to `main`. Render redeploys on push.

## Daily steps (on the box)
1. **Sync:** `cd /workspace/chartmaker && timeout 30 git fetch origin && git reset --hard origin/main`
2. **Pick up to 20 charts** (target 20; ship fewer if that is the honest count — never pad) from the three sheets. Base the picks on today's news and X trends in Mark's areas: MSTR/Bitcoin, Tesla/robotaxi, SpaceX (SPCX), AI/neoclouds/data centers, and markets. Only pick charts with reasonably current data.
   - Bot experiments: `1m_YlKV3YiOLy4b94xcBu04wkqlc7dBeUhlufw9f6mGY`
   - bot experiments 2: `1wlWT_RRprgy3KrvWuBR49xr_LVxZAZCxSLZ_bBolbdQ`
   - My Bot's Sheets: `1BiV5QZti6uHTK1xBFDrTxcY9pimy0tR9o5YZb2fIqNU`
   - Treat the sheets as read-only. Never edit them.
3. **Get each chart's PNG onto the box.** The weekly refresh exports every chart to these Drive folders:
   - Bot experiments: `1SwZgYGwwHsoL_DXQUuQAYS6lhq5KxFe9`
   - bot experiments 2: `1mMUKaB_RbeZhtn5ntuKJV5xZOeo6LzGH`
   - My Bot's Sheets: `1AVfHRo2mJgBhbJoszHhe5vgOscfKhbzT`
   - Find the file with Drive `search_files` (`parentId = '<folder>'`). Duplicate names exist, so take the newest `modifiedTime`. Then run `DownloadFile` (connection `user-Google-drive`) to `/workspace/cotd_src/NN.png`.
   - If a chart has no fresh PNG, or it renders blank (bar+line COMBO charts fail in Apps Script `getAs`), use the fallback renderer: `/workspace/weekly-refresh-2026-10-19/diag/render_fallback.py`, which works from a Drive xlsx export of the book. You can also skip that chart.
   - Never sign in to Google in the box browser.
4. **Read the chart's data** with Sheets `read_range` or the xlsx export, and write the three texts from it:
   - **picked:** why it was picked today. Tie it to today's news.
   - **matters:** why it matters.
   - **know:** what you should know. Use the chart's actual latest values and dates, and flag stale data.
   - Each text is plain and factual, 2-3 sentences, with no invented numbers.
5. **Write the picks JSON** to `/workspace/cotd_src/picks.json`. Use the format in the `scripts/cotd_build.py` docstring. Each tab's `gid` comes from Sheets `get_spreadsheet`, as the tab's `sheetId`. Do not pick a gid in `sheets_catalog.EXCLUDED_TABS` (bot1 `333185169`, `1547332232`, `643299192`, `1791345316`, `1370617918`, `1024131985`, `292208014`, `2119398138`, `1584345646`, `1186940001`, `501919598`, `579853621`, and bot2 `418894352`) or in `cotd_build.COTD_EXCLUDED` (bot2 `645072508`, tab "69 Mark MSTR red dot tracker", BX2-069). The red-dot tracker stays in the sheet catalog and is left off Charts of the day only. `cotd_build.py` refuses those tabs (deleted charts must not be republished). Also do not pick a chart that appeared in any of the previous 3 editions. The builder reads `static/cotd/<date>/cards.json` for D-1, D-2, and D-3. A match is book + gid + `chart_n` when both the pick and the older card record `chart_n`, and book + gid when either side does not. A missing `cards.json` excludes nothing for that day. Add a newly retired tab by putting `("book", gid)` in `EXCLUDED_TABS`; the import, thumbnail, CotD, and favorites checks follow that set.
   - When the tab has more than one chart, set `chart_n` to the 1-based position of the panel in the PNG. That number is the catalog `chartList` order: chart 1 is `<gid>.png`, chart 2 is `<gid>_2.png`. Leave `chart_n` off when you do not know which panel it is. Do not put `1` there as a guess.
6. **Build:** `python3 scripts/cotd_build.py /workspace/cotd_src/picks.json`
   - It checks that each image isn't blank and fits it to 1600x900 on white.
   - It accepts 1 through 20 items (target 20) and writes every one to `static/cotd/<date>/01..N.png`, `static/cotd/<date>/cards.json`, and `static/cotd/today.json`. Fewer than 20 is allowed so a short list is never padded, including when the 3-day repeat rule removes candidates. More than 20 is an error. The script does not drop extra charts and does not fill the gap with a weaker chart.
   - It writes `chart_n` on a card when the pick includes a position that exists on that tab, when the tab has a single chart, when the tab is a table with no charts (`chart_n` 1, the same key as the tab thumbnail and `<gid>.png`), or when the card title matches exactly one chart. It omits `chart_n` when a multi-chart tab has no `chart_n` and no unique title match, and when a tab is not in the catalog unless the pick itself set `chart_n`. A `chart_n` that is not on a catalogued tab is an error. It does not guess chart 1.
   - It keeps the last 7 editions.
   - It stops with an error on any blank image, missing field, or excluded gid.
7. **Check locally** (optional): run `VIEWS_STORE=browser python3 server.py 18799`, then open http://localhost:18799/ and go to the "Charts of the day" tab.
8. **Commit and deploy:** run `git add static/cotd && git commit -m "Charts of the day <date>"`. Push to main through the usual route (format-patch, then a CloudAgent pushes it). Render redeploys in a few minutes.
