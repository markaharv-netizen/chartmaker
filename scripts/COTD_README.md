# Charts of the day: daily refresh (for Flash)

The Chartmaker tab **Charts of the day** reads `static/cotd/today.json` (fetched with no-cache) and the PNGs in `static/cotd/YYYY-MM-DD/`. Updating the tab means committing a new edition to `main`. Render redeploys on push.

## Daily steps (on the box)
1. **Sync:** `cd /workspace/chartmaker && timeout 30 git fetch origin && git reset --hard origin/main`
2. **Pick 10 charts** from the three sheets. Base the picks on today's news and X trends in Mark's areas: MSTR/Bitcoin, Tesla/robotaxi, SpaceX (SPCX), AI/neoclouds/data centers, and markets. Only pick charts with reasonably current data.
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
5. **Write the picks JSON** to `/workspace/cotd_src/picks.json`. Use the format in the `scripts/cotd_build.py` docstring. Each tab's `gid` comes from Sheets `get_spreadsheet`, as the tab's `sheetId`.
6. **Build:** `python3 scripts/cotd_build.py /workspace/cotd_src/picks.json`
   - It checks that each image isn't blank and fits it to 1600x900 on white.
   - It writes `static/cotd/<date>/01..10.png` and `static/cotd/today.json`, and keeps the last 7 editions.
   - It stops with an error on any blank image or missing field.
7. **Check locally** (optional): run `VIEWS_STORE=browser python3 server.py 18799`, then open http://localhost:18799/ and go to the "Charts of the day" tab.
8. **Commit and deploy:** run `git add static/cotd && git commit -m "Charts of the day <date>"`. Push to main through the usual route (format-patch, then a CloudAgent pushes it). Render redeploys in a few minutes.
