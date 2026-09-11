"""Regressions for market-data normalisation and portfolio-metric bugs.

The Python tests cover the provider layer directly. The browser-side metrics are
covered by asserting that the specific corrections are still in place, in the
same style as test_overview_market_regressions.py.
"""

from __future__ import annotations

import pathlib

import pytest

from northstar import market_provider as mp

APP_JS = pathlib.Path("static/js/app.js")


@pytest.fixture(autouse=True)
def _clear_fx_cache():
    mp._CACHE.clear()
    yield
    mp._CACHE.clear()


def test_yf_history_accepts_symbols_without_a_european_suffix(monkeypatch):
    """FX pairs such as GBPEUR=X have no exchange suffix.

    _yf_history() used to derive the currency via _symbol_parts(), which raises
    for anything that isn't a European listing — so every FX lookup died with
    "Unsupported European exchange symbol" and no non-EUR position could price.
    """

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **_kwargs):
            import pandas as pd

            index = pd.to_datetime(["2026-09-08", "2026-09-09"])
            return pd.DataFrame({"Close": [1.15, 1.16]}, index=index)

        @property
        def fast_info(self):
            raise RuntimeError("unavailable")

    monkeypatch.setitem(__import__("sys").modules, "yfinance", type("M", (), {"Ticker": FakeTicker}))
    payload = mp._yf_history("GBPEUR=X", "5d")
    assert payload["price"] == pytest.approx(1.16)
    assert payload["currency"] == "EUR"


def test_fx_to_eur_uses_yahoo_and_caches_the_rate(monkeypatch):
    calls = []

    def fake_yahoo(pair, _range):
        calls.append(pair)
        return {"price": 1.17, "provider": "Yahoo Finance"}

    monkeypatch.setattr(mp, "_yahoo_history", fake_yahoo)
    rate, _status = mp._fx_to_eur("GBP")
    assert rate == pytest.approx(1.17)
    assert calls == ["GBPEUR=X"]
    # second lookup is served from cache, not a second network call
    assert mp._fx_to_eur("GBP")[0] == pytest.approx(1.17)
    assert calls == ["GBPEUR=X"]


def test_fx_to_eur_is_a_no_op_for_eur(monkeypatch):
    monkeypatch.setattr(mp, "_yahoo_history", lambda *_a, **_k: pytest.fail("EUR must not hit the network"))
    assert mp._fx_to_eur("EUR") == (1.0, "native")


def test_non_eur_listing_normalises_to_eur(monkeypatch):
    monkeypatch.setattr(mp, "_fx_to_eur", lambda _currency: (1.16, "refreshed"))
    payload = {
        "provider": "Yahoo Finance",
        "currency": "GBP",
        "price": 100.0,
        "previous": 99.0,
        "timestamp": 1789000000,
        "history": [{"date": "2026-09-09", "close": 100.0}],
    }
    result = mp._normalize_payload("VUSA.L", payload, "refreshed")
    assert result["nativeCurrency"] == "GBP"
    assert result["price"] == pytest.approx(116.0)
    assert result["currency"] == "EUR"


def test_cash_flow_builders_agree_with_the_valuation_on_archived_holdings():
    """portfolioValueAsOfDate() values archived holdings, so the cash-flow
    builders must record what they cost. When they didn't, archiving a holding
    turned a losing year's Calendar XIRR into a triple-digit gain."""
    app_js = APP_JS.read_text()
    assert "function allAssetIds()" in app_js
    assert "function allAssets()" in app_js
    # every cash-flow builder now iterates the union, never state.assets alone
    assert "for(const a of Object.values(state.assets))" not in app_js
    assert app_js.count("for(const a of allAssets())") == 5


def test_open_shares_count_towards_value_even_when_archived():
    app_js = APP_JS.read_text()
    assert "function positions(){return Object.fromEntries(allAssetIds()" in app_js
    assert "function activeValue()" in app_js
    assert "function weight(id){const tv=activeValue();" in app_js


def test_monthly_return_is_not_extrapolated_to_a_full_month():
    app_js = APP_JS.read_text()
    assert "Math.pow(1+annualised,1/12)-1" not in app_js
    assert "Math.pow(1+annualised,spanDays/365)-1" in app_js


def test_invested_series_and_what_if_row_account_for_money_taken_out():
    app_js = APP_JS.read_text()
    assert "function netCapitalLedger()" in app_js
    assert "return{contributed,cash}" in app_js
    assert "const tv=live?totalValue()+withdrawn:null;" in app_js
    # the gap chart's invested line must fall on a sell
    assert "else sortedBuys.push({date:t.date,amount:-(shares*price-fee)});" in app_js


def test_xirr_card_caption_is_not_overwritten():
    app_js = APP_JS.read_text()
    assert "$('#costSub').textContent=`Gross invested" not in app_js
    assert "Money-weighted, since first cash flow" in app_js


def test_allocation_editor_has_no_duplicate_listeners():
    app_js = APP_JS.read_text()
    # a duplicate click handler asked for confirmation twice on Remove
    assert app_js.count("data-remove-etf") == 2  # one template, one handler
    assert app_js.count("$('#normalizeTargetsBtn').addEventListener") == 1


def test_no_twelve_data_surface_remains():
    provider = pathlib.Path("northstar/market_provider.py").read_text()
    api = pathlib.Path("northstar/market_api.py").read_text()
    app_js = APP_JS.read_text()
    for blob in (provider, api, app_js):
        assert "twelvedata" not in blob.lower()
        assert "twelveKey" not in blob
    # no dangling reference to the batch helper that was never defined
    assert "fetchTwelveBatch" not in app_js
    assert "prefer_realtime" not in provider


def test_browser_never_calls_a_market_provider_directly():
    """Quotes go through the authenticated same-origin proxy, so connect-src
    does not need to allow any external host."""
    init = pathlib.Path("northstar/__init__.py").read_text()
    assert "connect-src 'self';" in init
    assert "finance.yahoo.com" not in init


def test_monte_carlo_is_anchored_to_the_same_growth_rate_as_every_projection():
    """runMonteCarlo() used (1+r)^(1/12) while futureValue(), monthsToGoal(),
    monthlyNeeded() and the Base projection line all use r/12. Same "8%" input,
    8.00%/yr against 8.30%/yr — the fan sat 4% below its own Base case at 20
    years, and 9% below at a 12% return."""
    app_js = APP_JS.read_text()
    assert "const targetMonthlyGrowth=1+annualReturnPct/100/12;" in app_js
    assert "Math.pow(1+annualReturnPct/100,1/12)" not in app_js
    # the Ito correction that anchors the mean must survive
    assert "const muLogM=Math.log(Math.max(1e-6,targetMonthlyGrowth))-0.5*sigmaM*sigmaM;" in app_js


def test_goal_probability_measures_reaching_the_goal_by_the_date():
    """The card reads "chance of hitting goal by target date" but the code only
    tested the value standing on the deadline itself, missing paths that reached
    the goal earlier and dipped — understating it by 4-9 percentage points."""
    app_js = APP_JS.read_text()
    assert "const reached=new Uint8Array(paths);" in app_js
    assert "if(!reached[p]&&col[p]>=goal){reached[p]=1;hits++}" in app_js


def test_goal_deadline_is_simulated_even_beyond_the_chart_horizon():
    """The deadline slider runs to 20 years but the horizon slider starts at 10,
    so monteCarloGoalProbability() clamped to the last simulated month and
    reported the 10-year answer under a 20-year label."""
    app_js = APP_JS.read_text()
    assert "months:Math.max(h,deadline)*12" in app_js
    assert "const chartSeries=series.slice(0,h*12+1);" in app_js


def test_daily_volatility_ignores_rows_that_span_a_trading_halt():
    """The aligned series jumps months when a holding stops trading. Treating a
    191-day, +46% move as a one-day return and annualising it by sqrt(252) took a
    real portfolio from 46% to 65% volatility, which drove the Monte Carlo median
    path to -6.2%/yr and pushed the goal date out by five years."""
    app_js = APP_JS.read_text()
    # returnsFromPrices must report the calendar distance between rows
    assert "days:Math.round((new Date(rows[i].date+'T00:00:00Z')" in app_js
    # and the daily statistics must filter on it
    assert "const dailyRets=rets.filter(x=>x.days<=5)" in app_js
    assert "const vol=stdev(arr)*Math.sqrt(252)*100;" in app_js
    # beta and correlation are matched against daily benchmark returns, so same rule
    assert "for(const x of dailyRets){" in app_js


def test_cagr_annualises_over_the_calendar_not_the_observation_count():
    """years = arr.length/252 silently assumed a gap-free 252-day year. With a
    suspended holding, or simply a weekday-only series, the period was wrong."""
    app_js = APP_JS.read_text()
    assert "const years=arr.length/252" not in app_js
    assert "spanDays/365.2425" in app_js


def test_a_holding_can_price_off_a_different_listing_than_its_history():
    """Frankfurt carries years of daily closes for some instruments but quotes them
    badly: 6RJ0.F sat 7.48% above what Trade Republic showed and reported a losing
    Rocket Lab position as a +2.76% gain, while 6RJ0.DE matched to 0.36% but has
    only 15 rows of Yahoo history."""
    app_js = APP_JS.read_text()
    assert "function quoteSymbolOf(id)" in app_js
    assert "function historySymbolOf(id)" in app_js
    # the quote listing is requested for every sync, the history listing only on a full one
    assert "const symbols=[...new Set([...quoteSymbols," in app_js
    assert "...(full?[...historySymbols,...backfillSymbols]:[])" in app_js
    # a symbol equal to the holding's own listing is stored as "no override"
    assert "if(a.priceSymbol===a.symbol)a.priceSymbol='';" in app_js


def test_historical_levels_are_rebased_onto_the_pricing_venue():
    """Reading a historical level from one venue while valuing today at another
    puts a step change at the join, biasing every period return that straddles it.
    Returns-based metrics are unaffected either way, since a constant multiplier
    cancels out of a ratio."""
    app_js = APP_JS.read_text()
    assert "function priceBasisRatio(a)" in app_js
    # applied to both the daily history and the locked month-end snapshots
    assert "if(snapshot!=null)return Number(snapshot)*ratio;" in app_js
    assert "return best?Number(best.close)*ratio:null;" in app_js
    # measured on a day both listings traded, not a live quote against a stale close
    assert "function venueRatio(quoteRows,historyRows,anchor='last')" in app_js
    # and only refreshed on the daily history sync
    assert "else if(full&&historyPayload){const ratio=venueRatio(" in app_js


def test_pricing_listing_is_validated_before_it_is_saved():
    """An unsupported listing would silently fail on every sync and leave the
    holding unpriced."""
    app_js = APP_JS.read_text()
    assert "if(value&&!Object.keys(EUROPEAN_EXCHANGES).some(suffix=>value.endsWith(suffix)))" in app_js
    assert "Not a supported European listing" in app_js


def test_a_short_series_can_be_extended_from_a_sibling_listing():
    """alignSeries() intersects dates across every holding, so a 15-row Xetra
    listing truncated a 759-row XAIX series and left the performance chart three
    weeks long, riskMetrics null and volatility on its 16% default."""
    app_js = APP_JS.read_text()
    assert "function spliceHistory(primaryRows,backfillRows)" in app_js
    assert "function backfillSymbolOf(id)" in app_js
    # sibling listings are suggested from the catalog by shared ISIN
    assert "function siblingListings(id)" in app_js
    assert "CATALOG_BY_ISIN" in app_js
    # the backfill listing is only requested on a full history sync
    assert "...(full?[...historySymbols,...backfillSymbols]:[])" in app_js


def test_splice_anchors_at_the_join_not_the_latest_day():
    """The venue relationship is noisy — across the Frankfurt/Xetra overlap the
    ratio ranges 0.934-1.018 for 6RJ0 and 0.938-1.102 for YDX. Anchoring the
    splice on the latest shared day picked up one outlier (0.9338) and injected a
    fabricated -6.6% move at the join. Anchoring on the earliest shared day is
    equivalent to carrying the backfill's own returns backwards from the primary's
    first close, so the join shows the instrument's real move."""
    app_js = APP_JS.read_text()
    assert "function venueRatio(quoteRows,historyRows,anchor='last')" in app_js
    assert "const ratio=venueRatio(primary,backfill,'first');" in app_js
    # priceBasisRatio needs the opposite end: today's history must meet today's price
    assert "const ratio=venueRatio(payload.history,historyPayload.history);" in app_js


def test_splice_refuses_without_a_shared_trading_day():
    """Without a shared date there is no honest way to align two venues' levels,
    and a wrong one would corrupt every return crossing the join."""
    app_js = APP_JS.read_text()
    assert "if(!(ratio>0))return{rows:primary,ratio:null,added:0};" in app_js
    assert "could not be aligned (no shared trading day)" in app_js


def test_symbol_fields_write_to_their_own_key():
    """priceSymbol and backfillSymbol share the 'symbol' input type, and the save
    handler wrote both into priceSymbol regardless of data-key — so setting a
    backfill listing never persisted and the feature silently did nothing."""
    app_js = APP_JS.read_text()
    assert "asset[i.dataset.key]=value===String(asset.symbol||'').toUpperCase()?'':value;" in app_js
    assert "asset.priceSymbol=value===String(asset.symbol||'').toUpperCase()?'':value;" not in app_js


def test_changing_a_listing_forces_a_history_resync():
    """A backfill or pricing listing only takes effect on a full history sync, but
    the gate only asked "is the last sync a day old" — so a freshly synced
    portfolio ignored the new setting for up to 24 hours."""
    app_js = APP_JS.read_text()
    assert "function historyConfigKey(a)" in app_js
    assert "function historyConfigStale(a)" in app_js
    assert "(asset.history||[]).length<2||historyConfigStale(asset)" in app_js
    # and a full sync records which listing combination it built the history from
    assert "if(full)a.historyShapedFor=historyConfigKey(a);" in app_js


def test_app_js_cache_buster_matches_the_bundle():
    """index.html pins app.js with a ?v= query string; leaving it stale serves
    browsers the previous bundle, so new fields never appear at all."""
    html = pathlib.Path("static/index.html").read_text()
    assert 'src="/static/js/app.js?v=27.0"' in html


def test_dca_window_is_whole_calendar_months():
    """filterSeriesMonths() cut at "today minus N months", which lands mid-month
    and therefore spans N+1 calendar months — so a "1 year" DCA charged 13
    contributions (13,000 EUR at 1,000/month) and "2 years" charged 25."""
    app_js = APP_JS.read_text()
    assert "function dcaSchedule(series,months)" in app_js
    assert "keys.length<months?[]:keys.slice(-months)" in app_js
    # the row only renders when the portfolio covers the whole window
    assert "filter(p=>dcaSchedule(ps,p.months).length===p.months)" in app_js


def test_dca_columns_share_one_schedule_and_one_end_date():
    """Each series was filtered independently, so over the same "3 years" the
    portfolio bought 30 times (30,000 EUR) while the benchmarks bought 37 times
    (37,000 EUR) — and the two returns were then differenced as though they had
    been measured the same way."""
    app_js = APP_JS.read_text()
    assert "function dcaSimOnSchedule(series,schedule,monthly,endDate)" in app_js
    assert (
        "const p=dcaSimOnSchedule(ps,schedule,monthly,endDate),q=dcaSimOnSchedule(qs,schedule,monthly,endDate)"
        in app_js
    )
    # a series that cannot cover the shared schedule yields null, not a partial answer
    assert "if(firstOfMonth.size!==schedule.length)return null;" in app_js
    # one valuation date for every column
    assert "const endDate=[ps,qs,ss].filter(x=>x&&x.length).map(x=>x.at(-1).date).sort()[0];" in app_js


def test_dca_discloses_that_it_weights_history_by_todays_allocation():
    """modelSeries() applies current market-value weights across the whole
    history, so the backtest flatters whatever has already run up: 204.7% on
    today's weights against 153.9% on the plan's target weights for the same
    portfolio and window."""
    html = pathlib.Path("static/index.html").read_text()
    assert "not a track record" in html
    app_js = APP_JS.read_text()
    # the contribution count is shown alongside the amount
    assert "· ${p.months}×${fmt(monthly,0)}" in app_js
