# Deployment guide

## Dependency profiles

| File | Use case |
|---|---|
| `requirements.txt` | Portable local profile — built-in SQLite driver, works on Python 3.11–3.14 |
| `requirements-turso.txt` | Adds `sqlalchemy-libsql` for Turso; the Render and Docker configs pin Python 3.13, for which prebuilt libSQL wheels exist |
| `requirements-dev.txt` | Everything above plus pytest and Ruff |

Do not install the Turso profile on Python 3.14 unless its native driver has
published a compatible wheel. Local development never needs it.

## Recommended: Render + Turso

Render runs the Flask app as a normal web service; Turso provides durable
SQLite-compatible storage.

### 1. Create the Turso database

Install and authenticate the [Turso CLI](https://docs.turso.tech), then:

```bash
turso auth login
turso db create northstar
turso db show --url northstar
turso db tokens create northstar
```

### 2. Deploy the blueprint

1. Push the repository to GitHub.
2. In Render, choose **New → Blueprint** and select the repository — it reads
   [`render.yaml`](../render.yaml) automatically.
3. Set the environment variables when prompted:

```env
TURSO_DATABASE_URL=libsql://your-database-your-org.turso.io
TURSO_AUTH_TOKEN=your-token
SESSION_SECRET=a-long-random-value
COOKIE_SECURE=true
ALLOW_REGISTRATION=true
```

Generate a session secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3. Lock it down

After creating your own account on the public deployment, set
`ALLOW_REGISTRATION=false` and redeploy. The instance becomes a private
single-user app while preserving login.

## Docker / VPS

The included [`Dockerfile`](../Dockerfile) runs gunicorn against `wsgi.py`. For
a home server or VPS without Turso, mount `/app/data` as a persistent volume
and omit the Turso variables — the app falls back to local SQLite.

## Reverse proxy requirements

Northstar honours forwarded host and protocol headers through Werkzeug's
`ProxyFix`. The platform should terminate HTTPS and forward the original
scheme, and `COOKIE_SECURE=true` must be set behind HTTPS.

## Where the data lives

Each trade is stored twice for resilience:

1. **Canonical record** in the normalised `trades` table, scoped to the
   logged-in user, alongside portfolio settings, baselines, reviews, and
   snapshots.
2. **Browser cache** in `localStorage` — a working copy for fast rendering and
   short-outage recovery, never a substitute for backups.

Use **Settings → Export backup** periodically for a portable JSON snapshot.
