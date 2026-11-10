# Charts of the day: daily refresh (for Flash)

The Chartmaker tab **Charts of the day** reads `static/cotd/today.json` (fetched with no-cache) and any previews in `static/cotd/YYYY-MM-DD/`. Updating the tab means committing a new edition to `main`. Render redeploys on push.

Each edition is a uniform random sample of charts and chartless table tabs from `static/sheets/catalog.json`. Nothing ranks or hand-picks the items. Cards do not say why an item was chosen.

The edition dated 2026-11-10 was left as published. The next run of the command below replaces it. The page no longer shows that edition's news theme, or the old why-picked, why-it-matters, and what-to-know sections.

## Daily steps (on the box)

1. **Sync:** `cd /workspace/chartmaker && timeout 30 git fetch origin && git reset --hard origin/main`
2. **Build.** This is the whole selection. Do not write a picks file, and do not choose items from the news or from X.

```
cd /workspace/chartmaker && python3 scripts/cotd_build.py
```

   - The edition date is today in America/New_York, which is the date of the 8 AM ET run. The draw is seeded with that date, so running the command again the same day, with the same catalog and the same three previous editions, writes the same items.
   - The pool is every chart on a catalog tab, plus every chartless table tab (one item, `chart_n` 1, the same favorite key as the tab). A tab with several charts is one pool entry per chart, each with its own `chart_n`.
   - Tabs in `sheets_catalog.EXCLUDED_TABS` (bot1 `333185169`, `1547332232`, `643299192`, `1791345316`, `1370617918`, `1024131985`, `292208014`, `2119398138`, `1584345646`, `1186940001`, `501919598`, `579853621`, and bot2 `418894352`) are not eligible. `cotd_build.COTD_EXCLUDED` (bot2 `645072508`, tab "69 Mark MSTR red dot tracker", BX2-069) is not eligible either. The red-dot tracker stays in the sheet catalog, the full PNG, and the thumbnail. Add a newly retired tab by putting `("book", gid)` in `EXCLUDED_TABS`.
   - An item that appears in `static/cotd/<date>/cards.json` for D-1, D-2, or D-3 is left out. A match is book + gid + `chart_n` when both the new item and the older card record `chart_n`, and book + gid when either side does not. A missing or unreadable `cards.json` excludes nothing for that day.
   - The script draws 20 items, or fewer when fewer than 20 are eligible. It does not pad and it does not drop extras from a longer list, because it never builds a longer list.
   - A preview is copied from `static/sheets/full/<book>/<gid>.png` when that file exists. Chart 2 and later use `<gid>_2.png` and do not borrow chart 1's file. Chart 1 and a table fall back to `static/sheets/thumbs/<book>/<gid>.jpg`. A blank or unreadable library image is skipped. A table with no preview is still a card, with a link to the tab.
   - It writes `static/cotd/<date>/NN.png` for cards that have a preview, `static/cotd/<date>/cards.json`, and `static/cotd/today.json`. It keeps the last 7 edition folders. Leave that default so the three previous days stay on disk.
3. **Check locally** (optional): `VIEWS_STORE=browser python3 server.py 18799`, then open http://localhost:18799/ and go to Charts of the day.
4. **Commit and deploy:** `git add static/cotd && git commit -m "Charts of the day <date>"`. Push to main through the usual route (format-patch, then a CloudAgent pushes it). Render redeploys in a few minutes.
