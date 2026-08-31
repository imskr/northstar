# Contributing

Thanks for improving Northstar!

## Development setup

```bash
make install   # .venv + dev dependencies
make init-db   # local SQLite from schema.sql
make dev       # http://127.0.0.1:8000
```

## Before opening a pull request

1. Create a focused branch — one concern per PR.
2. Keep financial calculations covered by tests.
3. Never commit API keys, Turso tokens, `.env`, or `data/*.db`.
4. Make sure the same checks CI runs are green locally:

```bash
ruff check .
ruff format --check .
pytest
```

`make lint` and `make test` are shortcuts for the first and last.

## Code style

- Python is formatted with **Ruff** (`ruff format`) and linted with the rule
  set in [`pyproject.toml`](pyproject.toml) — no exceptions, no excluded files.
- Frontend: markup in `static/index.html`, styles in `static/css/app.css`,
  logic in `static/js/app.js`. Bump the `?v=` cache-buster when you change
  either asset.

## Design principles

- Calm long-term investing experience — bold, but never a trading terminal.
- No silent market-data fallbacks or misleading price labels.
- Contribution-based rebalancing before any sell recommendation.
- User data stays private and account-scoped.
- Every projection must clearly state it is a model, not a guarantee.

## Pull request description

Please cover: the problem, the approach, test coverage, screenshots for UI
changes, and any storage or migration impact.
