# Upgrading from the first open-source build

The first package installed the optional Turso/libSQL driver during every local setup. On Python 3.14 that driver could attempt a Rust source build and fail while parsing `Cargo.lock`.

This release separates the dependency profiles:

- `requirements.txt`: portable local SQLite setup; use this on macOS and Python 3.14.
- `requirements-turso.txt`: optional Turso driver; used automatically by the included Python 3.13 Render and Docker deployments.

## Upgrade steps

1. Replace the old repository files with this release, or unzip this release into a new folder.
2. Double-click `start_northstar.command`.
3. The launcher detects an incomplete or stale `.venv` and repairs it.

For a completely clean reinstall:

```bash
rm -rf .venv
./start_northstar.command
```

Your portfolio database is stored separately at `data/northstar.db`. Do not delete that file if it already contains your account and transactions.
