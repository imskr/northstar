"""Regression tests for market-provider ordering and overview valuation logic.

Provider contract (see ``market_provider._load_quote`` / ``_load_history``):
2. Yahoo Finance (delayed, free).
3. Stooq (delayed, free, best-effort fallback).
"""

from __future__ import annotations

from pathlib import Path

from northstar import market_provider

ROOT = Path(__file__).resolve().parents[1]


def _stooq_quote_payload(symbol: str) -> dict:
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
        "history": [
            {"date": "2026-07-15", "close": 29.0},
            {"date": "2026-07-16", "close": 30.0},
        ],
    }


def test_quote_prefers_yahoo_before_stooq(monkeypatch):
    """Without a real-time key, Yahoo is tried first and wins when it succeeds."""
    calls: list[str] = []
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)

    def yahoo(symbol: str, *_args, **_kwargs):
        calls.append("yahoo")
        payload = _stooq_quote_payload(symbol)
        payload["provider"] = "Yahoo Finance"
        return payload

    monkeypatch.setattr(market_provider, "_yf_history", yahoo)
    monkeypatch.setattr(
        market_provider,
        "_stooq_quote",
        lambda *_: (_ for _ in ()).throw(AssertionError("Stooq must only run as fallback")),
    )

    result = market_provider._load_quote("BCFP.DE")
    assert result["provider"] == "Yahoo Finance"
    assert calls == ["yahoo"]


def test_quote_falls_back_to_stooq_when_yahoo_fails(monkeypatch):
    calls: list[str] = []
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)

    def yahoo(*_args, **_kwargs):
        calls.append("yahoo")
        raise RuntimeError("yahoo down")

    def stooq(symbol: str):
        calls.append("stooq")
        return _stooq_quote_payload(symbol)

    monkeypatch.setattr(market_provider, "_yf_history", yahoo)
    monkeypatch.setattr(market_provider, "_stooq_quote", stooq)

    result = market_provider._load_quote("BCFP.DE")
    assert result["provider"] == "Stooq"
    assert calls == ["yahoo", "stooq"]


def test_history_falls_back_to_stooq_when_yahoo_fails(monkeypatch):
    calls: list[str] = []
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)

    def yahoo(*_args, **_kwargs):
        calls.append("yahoo")
        raise RuntimeError("yahoo down")

    def stooq(symbol: str, range_: str):
        calls.append("stooq")
        return _stooq_quote_payload(symbol)

    monkeypatch.setattr(market_provider, "_yf_history", yahoo)
    monkeypatch.setattr(market_provider, "_stooq_history", stooq)

    result = market_provider._load_history("EMSM.DE", "1y")
    assert result["provider"] == "Stooq"
    assert len(result["history"]) == 2
    assert calls == ["yahoo", "stooq"]


def test_overview_uses_cost_basis_and_two_point_fallback():
    """The overview valuation logic lives in the frontend bundle."""
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "valuationPrice=marketPrice||(avg>0?avg:null)" in app_js
    assert "function portfolioValuation()" in app_js
    assert "function portfolioFallbackSeries()" in app_js
    assert "result.estimated=true" in app_js
    assert "dateAfterMonths(mtg,true)" in app_js
    assert "years.toFixed(2)" in app_js


def test_index_references_split_assets():
    """index.html must load the extracted stylesheet and app bundle."""
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/static/css/app.css' in html
    assert '<script src="/static/js/app.js' in html
