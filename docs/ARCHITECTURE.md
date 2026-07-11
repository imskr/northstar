# Architecture

## Frontend

`static/index.html` is a dependency-free single-page application. It keeps a browser cache for fast rendering, but synchronises every material change to the authenticated server API.

## Authentication

The server creates an opaque random session token. The browser receives the raw token through an HttpOnly cookie. The database stores only its SHA-256 hash and expiration date.

## Persistence

- Portfolio state is stored as JSON without the transaction array.
- Transactions are validated and stored in a normalised `trades` table.
- A load request combines both sources into the original frontend state format.

This arrangement preserves the existing portfolio engine while making transactions independently queryable and auditable.

## Market data

`northstar/market_provider.py` retrieves ETF quote snapshots from the Deutsche Börse path and historical benchmark data separately. `/api/market` is authenticated and same-origin, which keeps provider details outside the browser and prevents public endpoint abuse.

## Local versus production database

- Without `TURSO_DATABASE_URL`, SQLAlchemy uses `sqlite:///data/northstar.db`.
- With `TURSO_DATABASE_URL`, SQLAlchemy uses the libSQL dialect and the Turso auth token.
