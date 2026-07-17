from __future__ import annotations

from pathlib import Path

from northstar import market_provider


ROOT = Path(__file__).resolve().parents[1]


def test_xetra_fallback_prefers_stooq(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(market_provider, "real_time_configured", lambda: False)
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)

    def stooq(symbol: str):
        calls.append("stooq")
        return {
            "provider": "Stooq",
            "realtime": False,
            "delayed": True,
            "name": symbol,
            "currency": "EUR",
            "price": 30.0,
            "previous": None,
            "timestamp": 1_700_000_000,
            "market_state": None,
            "history": [],
        }

    monkeypatch.setattr(market_provider, "_stooq_quote", stooq)
    monkeypatch.setattr(market_provider, "_yahoo_history", lambda *_: (_ for _ in ()).throw(AssertionError("Yahoo should not run first")))

    result = market_provider._load_quote("BCFP.DE", prefer_realtime=False)
    assert result["provider"] == "Stooq"
    assert calls == ["stooq"]


def test_xetra_history_prefers_stooq(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(market_provider, "real_time_configured", lambda: False)
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)

    def stooq(symbol: str, range_: str):
        calls.append("stooq")
        return {
            "provider": "Stooq",
            "realtime": False,
            "delayed": True,
            "name": symbol,
            "currency": "EUR",
            "price": 30.0,
            "previous": 29.0,
            "timestamp": 1_700_000_000,
            "market_state": None,
            "history": [{"date": "2026-07-15", "close": 29.0}, {"date": "2026-07-16", "close": 30.0}],
        }

    monkeypatch.setattr(market_provider, "_stooq_history", stooq)
    monkeypatch.setattr(market_provider, "_yahoo_history", lambda *_: (_ for _ in ()).throw(AssertionError("Yahoo should not run first")))

    result = market_provider._load_history("EMSM.DE", "1y", prefer_realtime=False)
    assert result["provider"] == "Stooq"
    assert len(result["history"]) == 2
    assert calls == ["stooq"]


def test_overview_uses_cost_basis_and_two_point_fallback():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert "valuationPrice=marketPrice||(avg>0?avg:null)" in html
    assert "function portfolioValuation()" in html
    assert "function portfolioFallbackSeries()" in html
    assert "result.estimated=true" in html
    assert "dateAfterMonths(mtg,true)" in html
    assert "years.toFixed(2)" in html
