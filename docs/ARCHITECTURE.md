# Architecture

## Frontend

The UI is a dependency-free single-page application served from `static/`:

| File | Responsibility |
|---|---|
| `index.html` | Markup only — pages, forms, modals |
| `css/app.css` | The complete neo-brutalist design system (tokens, components, responsive rules) |
| `js/app.js` | Portfolio engine: state, XIRR/Monte Carlo math, canvas charts, API sync |
| `password_reset.js` | Self-contained reset-flow widget |

The browser keeps a `localStorage` working copy for fast rendering, but every
material change synchronises to the authenticated server API — after login the
database is the canonical source.

## Authentication

The server issues an opaque random session token delivered via an HttpOnly
cookie. The database stores only its SHA-256 hash and expiry, so a database
leak does not expose usable sessions.

## Persistence

- Portfolio state is stored as JSON **without** the transaction array.
- Transactions are validated and stored in a normalised `trades` table.
- A load request merges both back into the frontend state format.

This keeps the existing portfolio engine intact while making trades
independently queryable and auditable.

## Market data

`northstar/market_provider.py` resolves quotes and history in strict priority
order:

1. **Yahoo Finance** (via yfinance) — delayed, free, no API key.
2. **Stooq** — delayed, best-effort last resort.

All prices are normalised to EUR, including non-EUR listings: the quote currency
is converted at a `<CCY>EUR=X` rate fetched from the same provider and cached.
`/api/market` is authenticated and same-origin, keeping provider details out of
the browser and preventing public-endpoint abuse.

## Local versus production database

- Without `TURSO_DATABASE_URL`: SQLAlchemy uses `sqlite:///data/northstar.db`.
- With it: SQLAlchemy uses the libSQL dialect plus the Turso auth token.

The schema (`schema.sql`) is identical in both modes.
