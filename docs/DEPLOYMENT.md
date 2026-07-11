# Deployment guide

## Dependency profiles

- `requirements.txt` is the portable local profile. It uses Python's built-in SQLite driver and works with Python 3.14.
- `requirements-turso.txt` adds `sqlalchemy-libsql` for Turso. The included Render and Docker configurations pin Python 3.13, for which prebuilt libSQL wheels are available.

Do not install the Turso profile on Python 3.14 unless its native driver publishes a compatible wheel. Local development does not need it.


## Recommended: Render + Turso

Render runs the Flask application as a normal web service, while Turso provides durable SQLite-compatible storage.

Required production variables:

- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`
- `SESSION_SECRET`
- `COOKIE_SECURE=true`
- `ALLOW_REGISTRATION=true` for the first deploy

After signup, change `ALLOW_REGISTRATION=false`.

## Reverse proxy requirements

Northstar honours forwarded host and protocol headers through Werkzeug `ProxyFix`. The platform should terminate HTTPS and forward the original scheme.

## Persistent local deployment

For a home server or VPS without Turso, mount `/app/data` as a persistent volume and omit the Turso variables.
