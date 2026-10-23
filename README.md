# Chartmaker
Browser app for X-ready stock/crypto charts (not for trading). Data: Yahoo Finance chart API via a tiny stdlib Python proxy (no pip installs).

Start: `cd /workspace/chartmaker && nohup python3 server.py 18790 > server.log 2>&1 &` then open http://localhost:18790
(Port 8790 is held by the box's egress tunnel, so the app runs on 18790.)

Use: tickers (comma-separated, e.g. `NBIS,CRWV,BTC-USD`), optional display names, range (1D/5D intraday, YTD, 1M, 6M, 1Y, 5Y, Max, Custom),
type (price line, % change indexed to start, bar ranking of % return, ratio A/B with exactly two tickers), title, color, watermark toggle.
"Download PNG" exports 1600x900 at 2x (3200x1800). Status bar shows "data as of" (ET); fetch failures show an error and render nothing.

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
With a view selected, Save view overwrites it right away (no prompt; status shows Saved “name”); with "— pick a view —" selected it asks for a name and saves a new view. Views are per tab. Rename/Delete; Export/Import as JSON (import merges by name). Stored server-side in `views.json` next to server.py (GET/POST `/api/views`).
views.json is never shipped in the zip, so updates don't overwrite saved views. Re-run theme samples: `python3 shoot_themes.py`.

## Hosting / password (v4)
Env vars: `APP_PASSWORD` (if set, every route incl. /api requires login; login page at /login, logout at /logout),
`SECRET_KEY` (HMAC key for the session cookie; random per start if unset), `VIEWS_STORE=browser` (saved views live in browser localStorage
instead of views.json; use Export/Import as backup), `PORT` (overrides CLI port; binds 0.0.0.0).
Session cookie: `cm_session`, signed HMAC-SHA256, HttpOnly, Secure, SameSite=Lax, 30 days. Login throttling: after 5 failures per IP, lockout 30s doubling up to 15 min.
With no APP_PASSWORD (local PC) behavior is unchanged. Render: `render.yaml` Blueprint (free plan, `python server.py`).

## Sentiment tab

Ranked bull-vs-bear board (like the "analyst sentiment" boards on X). The app only reads the static
`sentiment.json` (served at `/api/sentiment`); nothing is scraped on Render.

**Board tickers (24):** AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AVGO, INTC, **MU**, MSTR, COIN, BTC-USD, **ETH-USD**, CRWV, NBIS, IREN, APLD, CIFR, WULF, **ASST**, **SPCX**, **RKLB**, **ASTS**. Added 2026-10-21: RKLB (Rocket Lab; news "Rocket Lab RKLB stock"), ASTS (AST SpaceMobile; news "AST SpaceMobile ASTS stock") — both tickers are unambiguous on X, so plain `RKLB`/`ASTS` queries; ETH-USD (Ethereum; crypto row like BTC-USD: `display` "ETH", `logoKey` "c:ETH", `kind` "crypto"; X queries use `Ethereum ...`; news "Ethereum price"), ASST (Strive; news "Strive ASST stock"), MU (Micron; X queries use `MU Micron ...` because bare "MU" is ambiguous; news "Micron stock"), SPCX (SpaceX, so every DCF-model stock — TSLA, SPCX, MSTR — is on the board; news "SpaceX SPCX stock"; plain query `SPCX SpaceX -pump -memecoin ...`). The daily input file must list all 24; they first appear on the board at the next daily refresh (no board snapshot is fabricated for them before that).

**Data pipeline (daily refresh)**
1. An agent with the X connector collects the last 7 days per ticker (≈3 calls/ticker):
   `search_posts_all` "`<T> -is:retweet -is:reply lang:en`" (relevancy, 25), a second call adding
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
- Rows are sorted by σ, descending. The ticker box filters the dataset; it can't add tickers that aren't in the json.
- Crypto rows: give the input ticker `display`, `logoKey` (e.g. `"c:BTC"`, same crypto logo path as the heatmap) and `kind: "crypto"`;
  they pass through to `sentiment.json`. With more than 12 rows the board switches to a compact layout (no company-name line, short no-bear text) so 18+ rows fit the 1600×900 PNG.

### Over time (history) view

Sentiment tab → **View: Over time**. Weekly bull share (0–100%) per ticker with its 95% band shaded, a dashed 50% line,
and an optional dashed price line on a right axis (weekly closes from the normal `/api/chart` path with its usual fallbacks:
`$` for one ticker, % from start when several are picked). The picker lists only tickers that have history: weekly = **MSTR, TSLA, SPCX, SATA, STRC, BTC-USD**; daily (Points: Daily) = all 26 names. Hover shows window dates, bull %, band, bull/bear counts, posts and the close.
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
| SPCX | `SPCX SpaceX -pump -memecoin -is:retweet -is:reply lang:en` | `SPCX (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued) -pump -memecoin -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en` |

(The very first SATA window, ending Apr 19, used `SATA Strategy -is:retweet -is:reply lang:en` for the plain query.)

### Daily refresh routine (7:30 AM ET) — exact steps

Do both parts every morning. Budget ≈ 12 X calls for the history + ≈ 3 per board ticker; stay under 30 calls/minute.

**A. History points for the 6 history tickers** (MSTR, TSLA, SPCX, SATA, STRC, BTC-USD):
1. Let `END` = now rounded down to the hour in UTC (e.g. `2026-10-19T11:00:00Z`) and `START` = `END` − 7 days.
2. For each ticker run the 2 searches from the table above with `start_time=START`, `end_time=END`, saving each result to a file
   (connector `write_to_file`). No news.
3. For each ticker:
   `python3 tools/sentiment_history.py ingest <TICKER> <START> <file_plain> <file_opinion> --end <END> --src x-rolling7 --q "<plain query>" --q "<opinion query>"`
   This writes `tools/sentiment_input/history/<TICKER>/<END date>.json` (rules-labeled posts; re-running the same day overwrites that day's file).
   Never hand-edit labels in history files: the series must stay one consistent labeler.
4. `python3 tools/sentiment_history.py build` → rewrites `series` in `sentiment_history.json` from every saved file.

**B. Board snapshot** (the 24 board tickers listed above, incl. ETH-USD, ASST, MU, SPCX, RKLB, ASTS): steps 1–3 of "Data pipeline" above, then
`python3 tools/build_sentiment.py tools/sentiment_input/<YYYY-MM-DD>.json` **after** A.4 (so MSTR/TSLA/BTC σ uses today's history point).
It writes `sentiment.json` and adds `daily[<date>]` to `sentiment_history.json` without touching `series`.

**A2. Daily points (all 26 names: the 24 board tickers + SATA, STRC)** — `series_daily`, shown with Over time → Points: Daily.
For yesterday's UTC day `D` (00:00–24:00Z), per ticker run ONE `search_posts_all` with the opinion query
`<SYM> (bullish OR bearish OR bull OR bear OR upside OR downside OR overvalued OR undervalued[ OR mNAV for MSTR | OR par OR yield for STRC/SATA]) [-pump -memecoin for SPCX] -whatsapp -telegram -airdrop -is:retweet -is:reply lang:en`
(BTC-USD uses `Bitcoin`, ETH-USD uses `Ethereum`, MU uses `MU Micron`), `start_time=D`, `end_time=D+1`, max 25, relevancy, same fields as above; then
`python3 tools/sentiment_history.py ingest-daily <SYM> <D> <file> --q "<query>"` and re-run `build`. ≈ 26 calls/day.
**Backfill still open:** target start 2026-07-21 (`DAILY_START`); as of 2026-10-22 (backfill run 6) Sep 1 – Oct 21 are collected, so each ticker has
43 gap days (Jul 21 – Aug 31, plus Oct 22 until the morning routine; RKLB also Oct 3, whose search returned 0 posts; `daily_gaps` in `sentiment_history.json`). Continue backwards with the same single search per
ticker-day (≈ 1,092 calls, i.e. ~1–2 more nightly runs of the 1,000/day X budget, ≤ 30/min). Never interpolate gaps. Daily points are thin
(1 search, often < 8 bull+bear posts → hollow dots); read them for turns, not levels.

**C.** Commit `sentiment.json`, `sentiment_history.json`, `tools/sentiment_input/<date>.json` and `tools/sentiment_input/history/` together.

**How daily points mix with the weekly backfill:** both are 7-day windows with the same 2 queries, labeler and math, so they are one
series: the backfill gives one point per week (windows ending Sundays, `src: "x-weekly"`), and from Oct 19 the routine adds one
overlapping rolling-7-day point per day (`src: "x-rolling7"`, plotted at its end date). Consecutive daily points share 6 of 7 days,
so they move smoothly; don't read day-to-day wiggles as new information. The weekly points are left as they are (never
recomputed from daily files). σ "own" compares the newest point with all earlier points (weekly + daily).
If a search errors or returns nothing, skip that ticker for the day (no point is better than an invented one) and note it in the commit message.

## BTC Top 100

Tab **BTC Top 100** (the Treasury tab is unchanged: it still reads hand-edited `treasury.json` for mNAV / amplification / BTC per share on MSTR, ASST, Metaplanet and Smarter Web). This tab is the ranked corporate-holdings board: top ~100 public companies, columns Rank, Company, Ticker, Country, Change, BTC Holdings, As-of Date, Source, Source Link, Notes. The chart is the top 10/15/20/25 as horizontal bars. Download PNG is the 1600×900 chart with the as-of range and the primary-source footnote. Download CSV is the full table.

The page only reads the committed snapshot `btctop100.json` (`GET /api/btctop100`). It does not hit a filings site on page load. Once a day is enough.

**Daily refresh**

```
python3 tools/refresh_btctop100.py            # rewrite btctop100.json
python3 tools/refresh_btctop100.py --dry-run  # print, don't write
python3 tools/refresh_btctop100.py --fresh    # ignore the 12h document cache
```

Commit `btctop100.json` when the file changes. No API keys. SEC requests use the same contact User-Agent as the financials tab. `bitcointreasuries.net` is not contacted.

**What the job rewrites** from a primary document, and only when the parsed as-of is newer than the snapshot and the new total is within 0.5×–2× of the current figure (otherwise it flags and leaves the row):

- SEC 8-K / 6-K / 10-Q / 10-K text: Strategy (MSTR), Strive (ASST), MARA, Tesla, Riot, CleanSpark, Twenty One (XXI), Trump Media (DJT), Block (XYZ), SpaceX (SPCX), American Bitcoin (ABTC), Coinbase (investment BTC only).
- Metaplanet (3350.T): the latest English TSE timely-disclosure PDF linked from metaplanet.jp (TDnet / xj-storage).

**What stays curated** (seeded 2026-10-23 from the "BTCTC Top 100" tab of the My Bot's Sheets spreadsheet):

- The other ~87 names. Their citations are the sheet's filing / RNS / DART / SEDAR+ / HKEX / press-release notes.
- Hut 8 is review-only. Its 10-Q consolidates American Bitcoin (17,316). The snapshot keeps Hut ex-ABTC (9,314) so the two rows are not double-counted.
- Bullish is review-only. The snapshot is the treasury-only figure (18,200), not total BTC including inventory, receivables and ETFs (23,800). The earnings slides are not an SEC total the parser will overwrite.
- CleanSpark's September operational update (13,530 as of 2026-09-30, company IR) is newer than the 10-Q (12,205 as of 2026-06-30), so the job keeps the press-release figure.

A same-day disagreement, an ambiguous filing (two different totals), or a jump outside 0.5×–2× is recorded on the row as `check` and is not applied. Change stays blank until a refresh actually moves the BTC count.
