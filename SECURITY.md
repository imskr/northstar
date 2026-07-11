# Security policy

Please do not report vulnerabilities through a public GitHub issue.

Contact the repository owner privately with:

- affected version;
- reproduction steps;
- expected impact;
- suggested mitigation, when available.

## Supported version

Only the latest release is supported.

## Deployment checklist

- Set a long random `SESSION_SECRET`.
- Set `COOKIE_SECURE=true` behind HTTPS.
- Keep `TURSO_AUTH_TOKEN` server-side only.
- Set `ALLOW_REGISTRATION=false` after creating a private account.
- Rotate credentials after accidental exposure.
- Back up the Turso database and export Northstar JSON periodically.
