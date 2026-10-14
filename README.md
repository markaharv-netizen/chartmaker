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
Update overwrites the selected view; Rename/Delete; Export/Import as JSON (import merges by name). Stored server-side in `views.json` next to server.py (GET/POST `/api/views`).
views.json is never shipped in the zip, so updates don't overwrite saved views. Re-run theme samples: `python3 shoot_themes.py`.

## Hosting / password (v4)
Env vars: `APP_PASSWORD` (if set, every route incl. /api requires login; login page at /login, logout at /logout),
`SECRET_KEY` (HMAC key for the session cookie; random per start if unset), `VIEWS_STORE=browser` (saved views live in browser localStorage
instead of views.json; use Export/Import as backup), `PORT` (overrides CLI port; binds 0.0.0.0).
Session cookie: `cm_session`, signed HMAC-SHA256, HttpOnly, Secure, SameSite=Lax, 30 days. Login throttling: after 5 failures per IP, lockout 30s doubling up to 15 min.
With no APP_PASSWORD (local PC) behavior is unchanged. Render: `render.yaml` Blueprint (free plan, `python server.py`).
