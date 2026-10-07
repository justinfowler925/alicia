# Rehosting Fowler Demo Maker off the Mac Studio

## Why

The app runs on a personal Mac Studio reached over Tailscale, and Nucleus frames
it into `/revops`. That made a developer machine part of a production surface:
it put the tailnet hostname in a CSP header readable without a session (fixed in
nucleus#594), and the tool only works for someone on the tailnet — the panel is
blank for everyone else. Tailnet was the only authentication; there was none in
the app.

## What is in this directory now

Everything except the Clearspeed-owned hosting account.

- `Dockerfile` — Playwright v1.52.0 base + `ffmpeg`, non-root `pwuser`.
- `.dockerignore` — keeps SQLite, renders and library videos out of the image.
- `fly.toml` — one machine, one volume at `/data`, health check on `/healthz`,
  suspend when idle. Single instance is required: DB + renders are local files.
- `server/lib/paths.js` — `DATA_DIR` (container) or `DEMO_MAKER_STATE` (Studio).
- `server/lib/access-gate.js` — when `DEMO_MAKER_ACCESS_SECRET` is set, requires
  a Nucleus-minted HMAC embed session; `/healthz` stays open for probes.
- `/healthz`, `HOST=0.0.0.0` bind.

Vercel `internal-ops` is the wrong shape for this app (long Playwright/ffmpeg
renders + local SQLite). Fly / Render / ECS with a persistent volume is the
equivalent Clearspeed-managed host.

## What it needs from IT / Justin

1. A container host account **Clearspeed owns** (Fly org preferred; Render API
   key in Atlas is currently unauthorized; no Fly token in Atlas yet — blocked
   on Rick’s corporate vault / billing owner in the same way as other secrets).
2. A ~10GB volume mounted at `/data`.
3. Secrets from 1Password `Atlas` (same values Studio reads today):

| Secret | Notes |
|---|---|
| `VOICEMAKER_API_KEY` | TTS |
| `ELEVENLABS_API_KEY` | TTS |
| `ANTHROPIC_API_KEY` | translate |
| `GITHUB_TOKEN` | publicSync → `ClearspeedRevOps/clearspeed-demos` |
| `DEMO_MAKER_ACCESS_SECRET` | **new** — shared with Nucleus; gates the public origin |
| `VIDYARD_API_TOKEN` | optional legacy |

4. **Do not publish a public origin without `DEMO_MAKER_ACCESS_SECRET` set** on
   both Demo Maker and Nucleus. The gate refuses ungated public access by
   design; unset secret is only for Studio/tailnet.

## Migrating state

Stop the Studio Demo Maker job so the SQLite WAL is quiet, then copy state:

```bash
ssh jfstudio@100.102.92.119 'launchctl bootout gui/$(id -u)/com.jfstudio.alicia-demo-maker'
rsync -avz jfstudio@100.102.92.119:~/.alicia/state/demo-maker/ ./state/
```

Copy `state/` into the volume at `/data` (same relative paths: `studio.sqlite3`,
`renders/`, `library/`, `.env` → prefer platform secrets over copying `.env`).

## Then, in Nucleus

1. Set `DEMO_MAKER_URL` to the new HTTPS origin (no path).
2. Set the same `DEMO_MAKER_ACCESS_SECRET`.
3. Merge → `scripts/deploy.sh` → verify:
   - Unauthenticated `GET /revops` does **not** put the new host in CSP (status≥300).
   - Authenticated RevOps → Demo Maker frames the new origin; `/auth/embed` sets cookie.
   - Direct `GET https://<host>/` without cookie → 401 HTML (not the studio UI).
   - Direct `GET /api/projects` without cookie → 401 JSON.
   - `/healthz` → 200 without cookie.

Nucleus mints `/auth/embed?exp=&email=&sig=` from the RevOps session; CSP
`frame-src` still follows `demoMakerOrigin()` only.
