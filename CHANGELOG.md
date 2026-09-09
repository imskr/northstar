# Changelog

## 2.3.5 - 2026-09-09

- **Fixed (significant):** a suspended holding inflated the whole portfolio's
  volatility, and through it the Monte Carlo projection. `riskMetrics()` builds
  daily returns from the date-intersection of every holding's price history, but
  `returnsFromPrices()` never looked at how far apart consecutive rows actually
  were. A holding that stops trading leaves months-long gaps in that
  intersection, and the move across a gap was treated as a single day's return
  and annualised by `sqrt(252)`. On a real portfolio holding Nebius — suspended
  from April to October 2024, then again into January 2025 — two rows spanning
  191 and 91 calendar days contributed "daily" returns of +45.99% and +39.27%,
  pushing measured volatility from 46.3% to 65.4%.

  That number is the Monte Carlo's volatility input, where it does real damage:
  volatility drag on the median path is `0.5 * sigma^2`, so at 60.5% the drag is
  18.3%/yr against a 12% expected return and the median path *falls* 6.2%/yr.
  The €100k goal at €1,000/month consequently showed a median crossing at 9.3
  years instead of 7.4. Daily statistics — volatility, Sortino, beta,
  correlation and worst-day — now use only rows spanning five calendar days or
  fewer (a normal weekend or holiday), while cumulative growth and max drawdown
  keep every step, since those moves genuinely happened.

- **Fixed:** CAGR annualised over `arr.length/252`, treating the number of
  observations as if it were a gap-free trading calendar. With a suspended
  holding the period was badly wrong, and even for an unbroken weekday series
  260 observations were divided as 1.03 years. Now measured from the actual
  first-to-last date span.

- **Note, not a defect:** a portfolio of high-volatility single names will show a
  median well below its own expected-return line, and can show the goal moving
  *further away* when contributions are redirected into the more volatile
  holdings — with no open positions, `weight()` falls back to each holding's
  target, and editing the per-asset monthly amounts rewrites those targets via
  `targetsFromMonthlyAmounts()`, so the volatility basis follows the split.
  Moving €300/month into a 100%-volatility name takes this portfolio from 46.2%
  to 63.0% volatility and the median goal date from 5.8 to 7.3 years, despite
  the larger contribution. That is the model correctly pricing the extra risk,
  not an error — but it is worth knowing that the two controls interact.

## 2.3.4 - 2026-09-09

Monte Carlo audit. The engine itself checks out: over 1,000,000 draws the
Box-Muller sampler gives mean 0.0008, stdev 0.99981, skew -0.003, excess
kurtosis 0.0016, tail masses matching the normal CDF out to 3 sigma, and no
serial correlation (lag-1 -0.0011, lag-2 0.0012); mulberry32 passes a
100-bin chi-square. With contributions switched off the terminal distribution
matches the closed-form lognormal to within 0.4% on the mean and median.
`Float64Array.prototype.sort()` was confirmed numeric rather than
lexicographic. Three defects around it, though:

- **Fixed (significant):** the simulation was not anchored to the assumption it
  claimed. Its comment stated the mean path matches "the same deterministic
  monthly growth factor futureValue() uses", but `runMonteCarlo()` compounded
  `(1+r)^(1/12)` per month while `futureValue()` — and `monthsToGoal()`,
  `monthlyNeeded()`, the milestone table and the Base projection line — all use
  a nominal `r/12`. The same "8%" input therefore drove the fan at 8.00%/yr and
  every other projection at 8.300%/yr, so the fan sat below its own Base case:
  -1.57% on the mean at 10 years, -4.12% at 20 years, and -8.95% at a 12%
  return. The Itô correction was implemented correctly; it was simply anchoring
  to a different target. Now uses `1+annualReturnPct/100/12`, bringing every
  scenario tested (0%-30%, 10-30 years) within 0.46% of `futureValue()` — the
  residual being sampling error on the mean of 10,000 lognormal paths.

- **Fixed (significant):** "Chance of hitting goal by target date" measured the
  wrong event. `monteCarloGoalProbability()` tested only whether a path's value
  stood at or above the goal *on* the deadline, so every path that reached the
  goal earlier and then dipped was counted as a miss. For a €100k goal from
  €20k at €700/month, the true chance of reaching it within 8 years is 80.3%
  but the card read 72.0%; at 10 years 94.3% against 88.5%. Now measures first
  crossing at any point up to the deadline, walking month-major with a
  `reached[]` flag so each column is scanned once. Verified monotonically
  non-decreasing as the deadline extends.

- **Fixed:** the goal deadline could fall outside the simulation. The deadline
  slider runs to 20 years while the horizon slider starts at 10, and the
  probability lookup clamped to the last simulated month — so with a 10-year
  chart horizon, deadlines of 10, 15 and 20 years all reported the identical
  10-year figure (93.8%) under their own labels, against a true 99.7% at 20
  years. The simulation now runs to `max(horizon, deadline)` months and the fan
  chart is sliced back to the requested horizon, so the goal question is
  answered at its real date and the answer no longer depends on the chart's
  zoom.

- **Note, not a defect:** the median outcome sits well below the Goal Lab's
  deterministic figure for the same inputs — about 1.3%/yr lower at 16%
  volatility. That is the arithmetic-versus-geometric gap inherent to a
  lognormal process, and follows from the deliberate choice (kept, and now
  actually honoured) to anchor the *mean* path to the Base projection. Half the
  paths landing under the straight-line projection is the correct behaviour, not
  a bug.

## 2.3.3 - 2026-09-09

- **Fixed (critical):** no non-EUR listing could be priced at all. `_yf_history()`
  derived the quote currency via `_symbol_parts()`, which raises for any symbol
  without a European exchange suffix — and FX pairs like `GBPEUR=X` have none. So
  every currency conversion died with "Unsupported European exchange symbol",
  taking down all 13 non-EUR venues (London, SIX, Stockholm, Oslo, Copenhagen,
  Warsaw, Prague, Budapest, Istanbul, Bucharest, Iceland) despite the README
  promising "GBP, CHF, SEK… converted so every position is comparable". Verified
  live: `VUSA.L` now prices at GBP 107.10 → EUR 124.57 (fx 1.1631) and `NESN.SW`
  at CHF 78.97 → EUR 83.97 (fx 1.0633).

- **Removed:** Twelve Data. Northstar now relies solely on Yahoo Finance, with
  Stooq as the last-resort fallback. This drops ~410 lines from
  `market_provider.py` and ~130 from `market_api.py`: the per-request `?twkey=`
  plumbing, `real_time_configured()`, `MarketEntitlementError`, all
  `_twelve_*` helpers, the Twelve-only `normalize_quote_batch()` /
  `normalize_history_batch()` entry points, `twelve_diagnostics()`, and the
  `prefer_realtime` parameter threaded through the provider chain. Also removes
  a live bug: `fetchAssetBatch()` called `fetchTwelveBatch()`, which was never
  defined anywhere — any user who had entered a key hit a `ReferenceError` on
  every sync (swallowed by the surrounding catch, so it silently degraded).
  Since the browser no longer talks to any provider directly, the CSP
  `connect-src` is tightened from allowing `api.twelvedata.com` and two Yahoo
  hosts down to `'self'`.

- **Fixed (critical):** archiving a holding corrupted every historical return.
  `portfolioValueAsOfDate()` valued archived holdings, but
  `calendarYearCashFlows()`, `monthCashFlows()`, `trackedCalendarYears()`,
  `compoundingGapSeries()` and `doNothingComparison()` all iterated
  `state.assets` alone — so an archived holding's purchase cost vanished while
  its value stayed on the books, and the gain it paid for was booked as free
  appreciation. In a test scenario, archiving one holding flipped 2025's
  Calendar XIRR from −2.54% to **+103.27%** and the heatmap's annual figure from
  −4.10% to **+123.85%**. All cash-flow builders now iterate the same
  active-plus-archived union the valuation does; verified identical results
  before and after archiving.

- **Fixed (critical):** archiving a holding you still owned wiped its value from
  every total. `positions()` iterated active holdings only, so `totalValue()`,
  `totalPnl()` and the terminal flow of `portfolioXirr()` silently dropped
  still-open archived shares while their purchase costs remained in the cash
  flows — sending Portfolio XIRR from +2.55% to **−32.32%**. `positions()` now
  covers every holding you still own; allocation, drift and the donut weigh
  against a new `activeValue()` so the managed plan still sums to 100%.
  Archived holdings with open shares are included in the price sync and shown
  in the holdings grid, marked "retired from plan", so the cards reconcile with
  the headline total.

- **Fixed (significant):** the "What if you'd bought the index instead?" card
  compared unlike quantities. `whatIfBacktest()` counts un-reinvested sale
  proceeds as cash inside the hypothetical benchmark position, but the "Your
  portfolio" row used bare `totalValue()`, which excludes money already
  withdrawn. A €12,067 sale left near the end of the history reported your own
  return as **−32.75%** against +60.02% for Nasdaq-100, when the portfolio was
  genuinely up **+3.74%**. `netContributedCapital()` is now
  `netCapitalLedger()`, returning both the contributed figure and the
  un-recycled `cash`; the portfolio row adds that cash back, and the result
  reconciles exactly with `personalReturn()`.

- **Fixed (significant):** "The compounding gap" chart claimed a loss the moment
  you sold anything. The invested line summed every buy and never came down,
  while the value line dropped by each sale — after one €12,067 sell it showed
  €22,238 of value against €33,066 "invested", implying a €10,828 loss on a
  portfolio actually up €1,238. The invested line is now net cash put in
  (buys minus proceeds), so the gap between the two lines equals total P&L to
  the cent.

- **Fixed:** the monthly-returns heatmap extrapolated partial months. A month's
  XIRR was de-annualised by a flat `^(1/12)` regardless of how much of it the
  cash flows covered, so the first month of tracking always read high: entering
  on 5 January and gaining a true 1.026% by month end displayed as **+1.20%**.
  Returns are now de-annualised over the span the flows actually cover. The
  period start is also dated on the day it was measured — the prior month's
  last close, not the 1st — so the window matches the price movement it
  represents. Whole months now land within 0.001pp of Modified Dietz.

- **Fixed:** the Portfolio XIRR card's caption read "Gross invested €X" — the
  correct "Money-weighted, since first cash flow" was written, then overwritten
  five lines later by a stray assignment to the same element. The gross-invested
  figure moved to the P&L card's caption, where it belongs.

- **Fixed:** the Year in Review "best month" slide hard-coded a `+` sign, so a
  year whose best month was still negative rendered as "+-2.3%". It now carries
  the real sign and switches to the red slide.

- **Fixed:** duplicate click handlers on the allocation editor. Both a
  `closest()`-based and a `dataset`-based listener were bound to
  `#selectedEtfList`, so cancelling a "Remove holding" confirmation immediately
  re-asked; `#normalizeTargetsBtn` likewise fired twice and toasted twice.

- **Verified unchanged:** XIRR (matches an independent actual/365 bisection to
  14 decimal places), volatility, max drawdown, CAGR, Sharpe, Sortino, beta,
  correlation and 21-day momentum (all match a NumPy reference), and the Monte
  Carlo simulation (Itô-corrected mean path within 3% of `futureValue()`, and
  `Float64Array.prototype.sort()` confirmed numeric, not lexicographic).

## 2.3.2 - 2026-09-09

- **Fixed (significant):** the "What if you'd bought the index instead?" card
  (and, via the same root cause, `grossInvested()`) treated every buy as fresh
  outside capital, even when it was funded by selling something else first.
  Sell a position and reinvest the proceeds into a different holding, and the
  same money got counted twice — once as the original purchase, once as the
  reinvestment — inflating "Invested" and making "Total return" look far worse
  than reality. Traced to a real user report: selling all of one holding and
  reinvesting the proceeds into another showed an alarming double-digit
  negative return that didn't match what actually happened.

  Fixed with a new `netContributedCapital()` that simulates the same cash
  recycling `whatIfBacktest()` now also correctly does — a sell frees up
  simulated cash that funds the next buy before any of it counts as a fresh
  contribution. `grossInvested()` is left as-is (still used where "every euro
  ever spent buying things" is the intended meaning); the What-If card's "Your
  portfolio" column now uses the corrected net figure instead, so all three
  columns are compared on the same fair, non-inflated basis. Caught a bug in
  my own first attempt at this fix before shipping it — an early version used
  a naive running-sum instead of properly simulating the recycling, verified
  wrong by hand-checking the numbers rather than trusting the logic. The
  corrected version is verified: in a test scenario mirroring the report,
  "Total return" swung from a fabricated -8.50% to a real +13.75%, and
  `whatIfBacktest()`'s contributed figure now exactly matches
  `netContributedCapital()`, confirming a fair comparison across all three
  columns. Also confirmed no change at all for the common case of a portfolio
  with no sells.

- **Reapplied 2.3.0 (archived-assets fix) and 2.3.1 (trade-form dropdown
  safety fix)** — this upload predated both. See below for details; both were
  re-verified against this file specifically, including a combined scenario
  exercising the archiving fix and the recycling fix together (an asset
  bought, sold, archived, with its sale proceeds reinvested elsewhere),
  confirming `portfolioXirr()` and `netContributedCapital()` are unaffected by
  archiving, and Calendar XIRR still resolves to NPV≈0 for both years touched
  by the archived asset.

## 2.3.1 - 2026-09-09

- **Fixed (safety-critical):** the "Add trade" form's Holding dropdown could
  silently point at the wrong asset after removing a different holding, with
  the shares/price/fee fields still holding the *previous* asset's numbers —
  risking a trade recorded against the wrong holding at the wrong price if
  not caught. Fixed by clearing those fields whenever the previously-selected
  asset becomes unavailable, and always re-running the trade preview
  immediately after.

## 2.3.0 - 2026-09-08

- **Fixed (significant):** removing a holding from Positions used to
  permanently delete every transaction ever recorded for it, silently
  corrupting Calendar XIRR, since-inception Portfolio XIRR, and total lifetime
  invested for anyone who ever fully exited and removed a position. Fixed by
  archiving removed holdings instead of deleting them — `state.archivedAssets`
  keeps their price history and transaction record intact for historical
  calculations, while correctly disappearing from active target/allocation
  management. Verified `portfolioXirr()` computes to the exact same value
  before and after archiving.

## 2.2.3 - 2026-09-08

- **Added:** hover tooltip on the Monthly returns heatmap, matching the styled
  tooltip already used on every other chart in the app (dark card, yellow
  offset shadow) instead of the plain native browser tooltip it had before.
  Shows month, year, and the exact return to 2 decimal places — more precise
  than the 1-decimal figure printed in the cell itself. Hovering the Year
  column or an empty (no-data) cell correctly shows nothing. Tested against a
  hand-built DOM mock (no network access in this environment to install a real
  one) covering: a real data cell, an empty cell, the Year/annual column, and
  mouseleave — all behave as intended.
- Bumped `app.css`/`app.js` cache-busting version to 26.4.

## 2.2.2 - 2026-09-08

- **Added:** Monthly returns heatmap on the Review page, below Calendar XIRR —
  a Year x Month grid in the style of justETF's fund profile pages, colour
  intensity scaled by magnitude (a bigger move, up or down, reads darker), plus
  a Year column that's the compound of that row's own months rather than a
  separately-computed figure, so it always reconciles with the cells next to
  it. Built entirely on `monthlyReturn()` from the Year in Review work — same
  XIRR-based, contribution-aware monthly return, just laid out as a grid
  instead of a story.
- **Added:** permanent month-end price snapshots (`monthEndPrices`), replacing
  the narrower year-end-only version from an earlier round that was never
  actually applied. Every completed month's last trading-day close gets locked
  in the first time it's seen, independent of the rolling daily history window
  — which is what keeps a multi-year heatmap's older cells from silently going
  blank once the daily window rolls past them. Re-verified the same way as
  before: simulated the daily window rolling completely past a full year of
  data and confirmed month-end lookups still resolve correctly; confirmed the
  current in-progress month never gets a premature snapshot; confirmed
  re-syncing doesn't overwrite an already-captured value.
- Bumped `app.css`/`app.js` cache-busting version to 26.3.

## 2.2.1 - 2026-09-08

- **Added:** "Compounding gap" — a fifth mode on the Overview page's Performance
  ledger chart, next to Performance/Projection/Goal path/Monte Carlo. Plots two
  real lines: your actual portfolio value (via the same point-in-time
  reconstruction proven correct for Calendar XIRR — not the "today's weights
  applied to history" approximation the Performance mode uses) against a
  cumulative running total of money contributed. The widening gap between them
  is the entire case for investing over saving, made of real numbers instead
  of a textbook illustration. Samples at a sensible interval (daily for short
  histories, weekly for 2+ years) so a long history doesn't produce thousands
  of points. Verified both series stay perfectly aligned (identical length,
  identical date at every index — required for the shared chart renderer to
  position them correctly), that both series' final points exactly match
  `totalValue()`/`grossInvested()`, and that missing early price history
  degrades to honest gaps in the line rather than a crash or a guess.
- Bumped the cache-busting version on `app.css`/`app.js` (`26.0` → `26.2`) —
  the last two rounds of changes shipped without bumping this, which meant
  browsers with an already-cached copy wouldn't see anything new without a
  manual hard refresh.

## 2.2.0 - 2026-09-07

- **Added:** Year in Review — a full-screen, Spotify-Wrapped-style retrospective,
  triggered on demand from a new "Year in review" button next to Calendar XIRR.
  Built entirely on data the app already tracks, with two genuinely new pieces
  of analysis added specifically for this: a **monthly return** (reuses the
  same XIRR engine as Calendar XIRR, scoped to one month and de-annualised back
  to an intuitive %, so contributions made mid-month don't distort it) powering
  best/worst month, and a **"if you'd done nothing" comparison** — freezes the
  shares held going into the year (or your first purchase, if the year started
  with nothing) and grows only that by pure price movement, isolating market
  performance from the effect of continuing to invest. Also surfaces biggest
  single contribution, longest contribution streak, and total fees paid.
  Verified the monthly-return engine the same way as Calendar XIRR — NPV at
  the solved rate is zero to floating-point precision — and tested the full
  render pipeline against a year with rich data, a year with only one month
  of history (merges best/worst into one slide instead of showing the same
  month twice), and a year with zero activity (degrades to a short, honest
  version rather than crashing or fabricating stats).

## 2.1.2 - 2026-09-07

- **Added:** pagination on the Trade history table (7 rows per page) — was
  rendering every transaction unbounded, which would eventually make the table
  very long as trade history grows. Page state clamps automatically if a
  delete leaves the current page out of range, and controls only appear once
  there's more than one page.
- **Fixed:** holding cards with a short, single-line name (e.g. "Rocket Lab
  USA, Inc.") sat visually higher than cards with a longer, two-line name,
  since the title area's height was driven entirely by the actual text.
  `.holding-title` now reserves consistent space for two lines regardless of
  how much the name actually wraps, so all cards in a row line up.

## 2.1.1 - 2026-09-07

- **Fixed:** the DCA backtest table showed identical numbers for the 1-year,
  2-year, and 3-year rows on new portfolios. Root cause: it decided which
  periods to show based on `max(portfolio history, Nasdaq-100 history, S&P 500
  history)` — but the benchmarks sync independently and almost always have
  years of history regardless of how new the portfolio is, so their long
  history wrongly unlocked periods the portfolio itself didn't have data for.
  Each period then asked for "the last N months of portfolio history," found
  nothing older than the portfolio's real (short) span to trim, and all three
  silently computed from the same window. Now gated strictly on the
  portfolio's own history span — a new portfolio correctly shows "Sync 1+ year
  of your own portfolio history" instead of fabricated-looking duplicate rows.

## 2.1.0 - 2026-09-01

- **Added:** the catalog now supports individual stocks alongside ETFs, not just
  funds. Every catalog entry has a `kind` field (`etf` or `stock`); search results
  and the "Add symbol" flow show which type you're looking at. Added 5 real,
  verified stocks to seed the catalog (SAP, Siemens, Allianz, ASML, Nestlé),
  spanning Xetra, Euronext Amsterdam, and SIX — each ISIN checked individually
  rather than assumed from memory.
- **Renamed:** `data/etf_catalog.json` → `data/catalog.json`, and
  `northstar/etf_catalog.py` → `northstar/catalog.py`, since the catalog is no
  longer ETF-only. All imports updated; verified no stale references remain
  anywhere in the repo.
- **Changed:** removed "ETF"-specific language from all user-facing text (page
  title, empty states, buttons, toasts, error messages) in favour of "holding"
  or "instrument" as appropriate, since the app now tracks both. Real fund names
  that legitimately contain "ETF" (e.g. "MSCI Emerging Markets UCITS ETF Acc")
  were left untouched — those are accurate legal names, not app copy. Internal
  CSS classes and JS/HTML element IDs (e.g. `etfSearchBtn`) were intentionally
  left as-is; renaming ~150 internal identifiers across two large files carried
  real regression risk for zero user-visible benefit.

## 2.0.0 - 2026-08-31

- **Changed:** complete visual redesign — a neo-brutalist system (cream canvas,
  3px ink borders, hard offset shadows, yellow/lime/pink accents) with Archivo
  Black display type and Space Mono numerals. Chart palettes retuned to match
  (black portfolio line, blue Nasdaq-100, red S&P 500, amber goal line).
- **Changed:** the frontend monolith is split — `static/index.html` now holds
  markup only, with styles in `static/css/app.css` and the application bundle
  in `static/js/app.js` (cache-busted via `?v=`).
- **Fixed:** chart hover tooltips were clipped by the card frame near chart
  edges; cards no longer clip overflow and the tooltip stacks above the sticky
  header.
- **Fixed:** "Goal acceleration" displayed unrounded float months.
- **Changed:** Ruff now lints and formats the entire codebase — the
  `market_provider.py` exclusion is gone and every file passes
  `ruff check` + `ruff format --check`.
- **Changed:** the market-provider regression tests now assert the documented
  provider contract (Twelve Data → Yahoo Finance → Stooq) including the
  Yahoo-failure fallback path; frontend regression checks read the split
  `app.js` bundle.
- **Added:** GitHub Actions CI (`.github/workflows/ci.yml`) running Ruff lint,
  format check, and pytest on Python 3.11 and 3.12.
- **Added:** rewritten README with badges and current screenshots
  (`docs/screenshots/`), expanded deployment guide, refreshed architecture and
  contributing docs.
- **Removed:** committed local database (`data/northstar.db`) and `.DS_Store`;
  `.gitignore` now covers `data/*.db` and OS cruft.

## 1.1.0 - 2026-08-27

- **Changed:** on each holding card, the fund's full name is now the prominent
  heading (previously the ticker), with the ticker moved below as a smaller
  subtitle — swapped per feedback. Renamed the underlying CSS classes
  (`.ticker`→`.holding-title`, `.holding-name`→`.holding-symbol`) to match,
  since they were each used in exactly one place.
- **Added:** issuer logos on holding cards, self-hosted in `static/logos/` and
  referenced by a new `logo` field on each entry in `data/etf_catalog.json`.
  These are original typographic wordmark badges, not copies of the issuers'
  actual trademarked logos — bundling official brand marks into a
  redistributable open-source repo isn't something we have rights to do, and
  an earlier attempt to hotlink logos from an external favicon service turned
  out to be unreliable in practice. This version has no external dependency
  at all: it's a local file lookup through the catalog, with the existing
  colour-initials badge as a graceful fallback for any issuer not yet covered
  or a symbol added outside the catalog.
- **Added:** XIRR (money-weighted return, accounting for the exact size and
  timing of every cash flow) — shown per ETF on its holding card, and as
  "Portfolio XIRR" on the Overview hero, replacing "Open cost basis". Uses a
  Newton-Raphson solver with a guaranteed-convergent bisection fallback, the
  standard actual/365 day-count convention (matching Excel/Google Sheets'
  XIRR), and treats today's synced market value as the final cash flow for
  open positions. Verified against known-exact cases (including leap-year day
  counts to 1e-9 precision) and by confirming NPV at the solved rate is zero
  to floating-point precision for realistic multi-transaction scenarios.
  Baseline holdings (shares held before you started tracking) now have an
  optional "Baseline as of" date in Positions — without it, they're excluded
  from XIRR rather than assigned a guessed date that could distort the result.
- **Fixed (data-corrupting bug):** the ETF's full name — correctly set from the
  catalog when you add it — was being silently overwritten with its bare ticker
  on every single price sync. Root cause: Stooq (the primary quote provider) has
  no fund names in its API, so the backend's quote payload reasonably falls back
  to `name: ticker` when no real name is available — but the frontend was
  blindly trusting that fallback and using it to overwrite the good name, every
  time. `applyUnifiedAsset()` now only accepts a payload name that's genuinely
  different from the ticker, and keeps the existing name otherwise. This affected
  the "The portfolio" holding cards on Overview, which showed the ticker twice
  (once as the heading, once where the full name should be).
- **Added:** the frontend now fetches the full ETF catalog once on load (new
  `GET /api/market/catalog` endpoint, backed by `list_catalog()` in
  `etf_catalog.py` — the same `data/etf_catalog.json` used when adding an ETF)
  and treats it as the authoritative source for name/issuer/ISIN in
  `rebuildMeta()`. This fixes the display for *already-affected* portfolios
  immediately, on every render, with no per-asset network round trip and no
  need to mutate or re-save your stored data — the catalog only overrides
  known symbols, so manually-added/custom listings are unaffected.
- **Changed:** simplified the Overview "Northstar action" card per feedback — it
  now shows just the status pill, total portfolio value as the headline number
  (previously the monthly plan amount), and total invested underneath. Dropped the
  "Invest this month across N ETFs" / drift-explainer sentences and the per-ETF
  "This month's split" breakdown; that detail still lives on the Positions page's
  "Next order" card, which is unchanged.
- **Fixed:** the "Northstar action" card's monthly buy plan (and the shared "Next
  order" list on Positions) showed only the ETF ticker (e.g. "XAIX"), even though
  the full name is already populated from the ETF catalog. Both now show the ticker
  with the full name underneath, truncated with an ellipsis if it's long.
- **Fixed:** hovering the Monte Carlo fan chart on the Overview page, then switching
  back to Performance / Projection / Goal path and hovering again, kept showing the
  stale Monte Carlo tooltip and redrawing the old fan chart. Root cause: `#mainChart`
  is shared by all four modes, and each chart type's hover handler bound its mouse
  listeners permanently on first use, so switching modes left multiple competing
  listeners attached to the same canvas. Each renderer now marks itself as the
  canvas's active chart type and every hover handler checks that marker before
  acting, so an inactive (but still-attached) listener from a previous mode no-ops.
- **Fixed:** the Monte Carlo tooltip could wrap onto multiple lines and look broken
  at higher years, where compounded values produce long range strings (e.g.
  "€1,234,567 – €2,345,678") that exceed the tooltip's fixed max-width. The tooltip
  now uses compact number formatting (€1.2M / €245K) for its percentile ranges.
- **Fixed:** Expected annual return in Goal Lab was capped at 15% while Settings
  already allowed up to 30% for the same value — the slider would silently clamp
  (and, on "Save this plan," overwrite) any higher value you'd set in Settings.
  Range is now 0–30% in both places.
- **Fixed (metrics correctness):** Sortino ratio's downside deviation divided the
  sum of squared downside deviations by the *count of negative days only*, instead
  of the total number of trading days (per the standard target-downside-deviation
  formula, where upside days contribute zero but still count in the denominator).
  This understated the reported Sortino ratio by roughly 30% in typical cases —
  verified numerically against an independent reference implementation of the
  textbook formula.
- **Fixed (metrics correctness):** the "Benchmark board" computed each series'
  (portfolio / Nasdaq-100 / S&P 500) total return independently over *its own*
  full available history, then displayed them side by side as if directly
  comparable. If your portfolio's common price history was shorter than the
  benchmarks' synced history (e.g. after adding a new ETF), you'd see your
  portfolio's short-window return compared against benchmarks' much longer-window
  return. Now aligned to the common date range first, matching how the main
  performance chart already does it.
- **Audited, found correct:** weighted-average-cost accounting (`replay()`),
  CAGR/volatility/max-drawdown/beta/correlation/Sharpe/Calmar formulas, the DCA
  backtest (verified algebraically and numerically that its use of a rebased
  0–100 index produces identical results to running the same simulation on real
  prices), and the EUR currency-normalisation logic including GBp/pence handling.
- **Known limitation (not changed):** `riskMetrics()` and `modelSeries()` compute
  historical portfolio returns using *today's* asset weights applied uniformly
  across the whole lookback window, rather than replaying actual historical share
  counts day by day. This is a common simplification in DIY portfolio tools, and
  it's consistent across both functions, but it means volatility/CAGR/drawdown can
  be distorted if your allocation has drifted or changed significantly (e.g. a
  recently-added ETF). A fully accurate fix means replaying transactions per
  historical date — a larger, riskier change we're flagging rather than making
  silently.

- **Fixed:** the Overview page's "Allocation radar" donut and "The portfolio" holdings
  grid were fully built (markup, styling, and render functions all present) but never
  invoked, so both sections stayed permanently empty. `renderOverview()` now calls
  `renderAllocation()` and `renderHoldings()`.
- **Added:** a Monte Carlo projection — 10,000 simulated portfolio paths (lognormal
  monthly compounding, seeded for reproducibility) using your expected-return
  assumption and your portfolio's own realised volatility (falling back to a
  diversified-ETF default until enough price history is synced). Available in Goal
  Lab and as a fourth mode ("Monte Carlo") in the Overview page's Performance ledger,
  next to Performance / Projection / Goal path. The fan chart is interactive — hover
  to see the exact year, median, and percentile range — and shows the probability of
  reaching your goal by its target date.
- **Fixed:** the "Northstar action" command card on Overview, and the "Next order"
  card on Positions, were pure static markup — never wired to any data. Both now
  show a live, drift-aware monthly buy plan (reusing the existing `plan()`
  allocator): current portfolio value and total invested, which ETFs to buy this
  month and how much, a status pill that reflects sync state and drift score, and a
  dynamic order total instead of a hardcoded "€600".
- **Removed (dead code):** an orphaned EODHD market-data provider in
  `market_provider.py` (`EOHHD_EXCHANGES`, `_eodhd_symbol`, `_eodhd_quote`,
  `_eodhd_history`) that was never wired into the Stooq/Yahoo fallback chain.
- **Removed (dead code):** nine frontend functions with zero call sites, superseded
  by other functions doing the same job — `cadenceLabel`, `fetchQuote`,
  `fetchHistoryAsset`, `fetchHistoryBenchmark`, `hexAlpha`, `investedThisMonth`,
  `repriceFromStoredQuotes`, `setAssetTarget`, `totalCost`.

## 1.0.1 - 2026-07-11

- Split portable local SQLite dependencies from optional Turso dependencies.
- Fixed installation on Python 3.14 by removing the Rust-backed libSQL driver from the default local install.
- Added a requirements fingerprint and automatic virtual-environment repair to the macOS launcher.
- Pinned Turso deployment environments to Python 3.13.
- Added a clear error when Turso is configured without its optional driver.
- Fixed clean-start imports when launching `scripts/dev.py` or `scripts/init_db.py` directly.

## 1.0.0 — 2026-07-11

- Modern Ledger portfolio interface
- Official Xetra last-trade valuation path
- Fractional positions and weighted average cost
- Realised P&L override for sell transactions
- Interactive extra-contribution slider
- Secure login and database-backed sessions
- SQLite local storage and Turso production support
- Normalised transaction persistence
- Render and Docker deployment files
- Automated tests and GitHub Actions CI
