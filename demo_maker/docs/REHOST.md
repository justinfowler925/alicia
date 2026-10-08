# Demo Maker hosting — keep Studio (safe host)

**Fly / Render cutover is out of scope.** Determinism gate + Rick withdrawal keep
compute on the Mac Studio. See Nucleus Project `docs/demo-maker-safe-host.md`.

## Production shape (Studio)

| Item | Value |
|------|--------|
| **Owner** | Justin Fowler (RevOps / Internal Ops) |
| **Purpose** | Internal demo render tool; published MP4 + TTS cache are SoT |
| **Listen** | `HOST=127.0.0.1` `PORT=4173` (launchd). Not `0.0.0.0` / LAN. |
| **Tailscale** | Serve `:8790` → `127.0.0.1:4173` (**tailnet only**). Off-tailnet must time out. |
| **Auth** | Nucleus SSO + `DEMO_MAKER_ACCESS_SECRET` HMAC `/auth/embed` (opaque `sub`, not email) |
| **Health** | `GET /healthz` → `{"ok":true}` (ungated) |
| **Power** | Studio awake on AC; launchd `com.jfstudio.alicia-demo-maker` KeepAlive |
| **Public** | `demo.clearspeed.com` gallery only — no Demo Maker API |

## Secrets

Store `DEMO_MAKER_ACCESS_SECRET` in `~/.alicia/state/demo-maker/.env` on Studio
and the same value on Nucleus (Vercel). Never commit it. Rotate if leaked.

## Container packaging (optional later)

`Dockerfile` / `fly.toml` remain for a future Clearspeed-owned persistent host.
Do **not** publish a public origin without `DEMO_MAKER_ACCESS_SECRET` on both
sides. Until then, Studio + Tailscale Serve + HMAC is the supported path.

## Nucleus same-origin proxy

Preferred: browser only sees `nucleus.clearspeed.com`; Nucleus server fetches
Studio. Requires Vercel→Studio egress (Tailscale or a gated Funnel path). Until
egress exists, Nucleus may frame the Tailscale Serve origin for SSO’d RevOps
users when the shared secret is set (residual: hostname visible to entitled
users, not a public WAN listen).
