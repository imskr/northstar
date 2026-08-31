# Changelog

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
