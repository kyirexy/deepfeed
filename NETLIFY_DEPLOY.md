# Tide Opinion Public — Netlify

This repository is ready for a public Netlify deployment.

## One-click import

Use Netlify's Git import flow for this public repository:

https://app.netlify.com/start/deploy?repository=https://github.com/kyirexy/deepfeed

The root `netlify.toml` publishes the repository root and maps:

- `/api/live` -> `netlify/functions/live.js`
- `/api/health` -> `netlify/functions/health.js`
- `/api/search` -> `netlify/functions/search.js`

No environment variable is required for the archive-backed public version. `LIVE_DATA_URL` is optional. `SEARXNG_URL` is optional and not required for the archive search endpoint.

## Acceptance checks

After deployment, verify:

- `/` returns the Tide dashboard
- `/api/health` returns `ok: true` and `mock: false`
- `/api/live` returns the minute archive and `mock: false`
- `/api/search?q=梦回甄嬛传&platform=all&time_range=month` returns `mock: false`

The public deployment must open in an incognito browser without Netlify/Vercel authentication.
