# Changelog

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
