# Charts of the day: daily refresh (for Flash)

The Chartmaker tab **Charts of the day** reads `static/cotd/today.json` (fetched with no-cache) and any previews in `static/cotd/YYYY-MM-DD/`. Updating the tab means committing a new edition to `main`. Render redeploys on push.

Each edition has two sections:

1. **Today's picks.** Five charts or table tabs you choose. Each card shows a short description of what the chart shows and the reason it was picked.
2. **Random from the library.** Twenty charts or chartless table tabs drawn uniformly from `static/sheets/catalog.json`. They do not repeat the five picks.

If you do not pass a picks file, the edition is the random section only and the picks section stays hidden. An older edition that has only an `items` list still renders as that single list.

## Picks file

Write `picks.json` anywhere on the box (it is an input, not a file to commit). It is a JSON array of exactly 5 objects:

```json
[
  {
    "book": "bot1",
    "gid": 1642110491,
    "chart": 1,
    "description": "Calendar-year returns of bitcoin against the S&P 500, with 2026 year to date.",
    "reason": "The spread widened this week and this is the chart that shows it."
  }
]
```

The snippet is one object so the fields are visible. The file needs five objects in that shape.

- `book` is the catalog book id (`bot1`, `bot2`, `bot3`), not the spreadsheet id.
- `gid` is the sheet tab id.
- `chart` is optional. It is the chart's `n` in the catalog `chartList` (1 for the only chart on a tab, or for a table). Omit it when the tab has one chart or is a table. Set it when the tab has several charts.
- `description` is one or two sentences on what the chart shows. `reason` is why it was picked today. Both are required non-empty strings.
- An object shaped `{"picks": [ ... ]}` is accepted too. The array above is the form to write.

The build rejects a pick that is excluded, blank, untitled, unknown, already in the previous 3 editions, or duplicated inside the file. The message names the pick number and the reason.

## Daily steps (on the box)

1. **Sync:** `cd /workspace/chartmaker && timeout 30 git fetch origin && git reset --hard origin/main`
2. **Write the 5 picks** to `picks.json` using the format above, then build. This is the command to run:

```
cd /workspace/chartmaker && python3 scripts/cotd_build.py --picks picks.json
```

   With no picks that day, omit the flag. That publishes the random section only:

```
cd /workspace/chartmaker && python3 scripts/cotd_build.py
```

   - The edition date is today in America/New_York, which is the date of the 8 AM ET run. The draw is seeded with that date, so running the command again the same day, with the same catalog, the same picks file, and the same three previous editions, writes the same random items.
   - The pool is every chart on a catalog tab, plus every chartless table tab (one item, `chart_n` 1, the same favorite key as the tab). A tab with several charts is one pool entry per chart, each with its own `chart_n`.
   - Tabs in `sheets_catalog.EXCLUDED_TABS` (bot1 `333185169`, `1547332232`, `643299192`, `1791345316`, `1370617918`, `1024131985`, `292208014`, `2119398138`, `1584345646`, `1186940001`, `501919598`, `579853621`, `1308503061`, `748431671`, `254001772`, and bot2 `418894352`) are not eligible. `cotd_build.COTD_EXCLUDED` (bot2 `645072508`, tab "69 Mark MSTR red dot tracker", BX2-069) is not eligible either. The red-dot tracker stays in the sheet catalog, the full PNG, and the thumbnail. Add a newly retired tab by putting `("book", gid)` in `EXCLUDED_TABS`.
   - A chart with no title, or titled `Chart N` (Google's untitled default, such as `Chart 2`), is not eligible. A chart whose own PNG is blank or near-uniform, including the empty Google chart, is not eligible. A card that would be titled `Chart N` because two charts share a title is left out when that chart has no image of its own. Image import and thumbnail refresh skip the same blank images, and import also skips an untitled chart and a chart number that is no longer in the catalog `chartList`.
   - An item that appears in `static/cotd/<date>/cards.json` for D-1, D-2, or D-3 is left out, including that day's picks. A match is book + gid + `chart_n` when both the new item and the older card record `chart_n`, and book + gid when either side does not. A missing or unreadable `cards.json` excludes nothing for that day. `cards.json` keeps an `items` list of every card (picks first when there are picks). Each pick has `section` `"picks"`, `description`, and `reason`. The file also has `picks` and `random` arrays. An older file with only `items` still counts. When `items` is present it wins, so the arrays are not counted twice.
   - The script draws 20 random items, or fewer when fewer than 20 are eligible. It does not pad and it does not drop extras from a longer list, because it never builds a longer list. The 20 do not include the 5 picks. The picks themselves are not a random draw.
   - A preview is copied from `static/sheets/full/<book>/<gid>.png` when that file exists. Chart 2 and later use `<gid>_2.png` and do not borrow chart 1's file. Chart 1 and a table fall back to `static/sheets/thumbs/<book>/<gid>.jpg`. A blank or unreadable library image is skipped. A table with no preview is still a card, with a link to the tab.
   - It writes `static/cotd/<date>/NN.png` for cards that have a preview, `static/cotd/<date>/cards.json`, and `static/cotd/today.json`. It keeps the last 7 edition folders. Leave that default so the three previous days stay on disk.
3. **Check locally** (optional): `VIEWS_STORE=browser python3 server.py 18799`, then open http://localhost:18799/ and go to Charts of the day.
4. **Commit and deploy:** `git add static/cotd && git commit -m "Charts of the day <date>"`. Push to main through the usual route (format-patch, then a CloudAgent pushes it). Render redeploys in a few minutes.
