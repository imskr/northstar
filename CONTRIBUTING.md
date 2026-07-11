# Contributing

Thank you for improving Northstar.

## Before opening a pull request

1. Create a focused branch.
2. Keep financial calculations covered by tests.
3. Do not commit API keys, Turso tokens, `.env`, or database files.
4. Run:

```bash
ruff check northstar tests scripts
pytest
```

## Design principles

- Calm long-term investing experience, not a trading-terminal aesthetic.
- No silent market-data fallback or misleading price labels.
- Contribution-based rebalancing before sell recommendations.
- User data remains private and account-scoped.
- Any investment projection must clearly state that it is a model, not a guarantee.

## Pull requests

Describe:

- the problem;
- the approach;
- test coverage;
- screenshots for UI changes;
- storage or migration impact.
