# Fowler Demo Maker (Voicemaker Studio)

Single-user web app for building multi-voice TTS audio and finished Clearspeed **demo videos**, plus the **demo library** that feeds [www.clearspeeddemos.com](https://www.clearspeeddemos.com).

Three things in one app:

1. **TTS studio** — projects are ordered lists of segments; each segment is one TTS call (Voicemaker.in or ElevenLabs), stitched with ffmpeg into a single mp3/wav with per-segment pauses.
2. **Demo video renderer** — a project tagged with question roles renders to a 1080p/30fps MP4 of the Clearspeed assessment player UI (question list, participant panel, live voice-analysis workflow, results grid), timed automatically from the real segment durations.
3. **Demo library** — published renders are tagged (vertical / use case / outcome / language), served from a gallery, and auto-synced to the public `clearspeed-demos` repo → Vercel → clearspeeddemos.com.

---

## Hosting & topology

| Where | What |
|---|---|
| **Live app** | `https://justins-mac-studio.tailbaa084.ts.net:8790` — **tailnet-only**, deliberately not public |
| **Host** | Mac Studio; code ships with Alicia (`demo_maker/` in the alicia repo, deployed to `~/.alicia/app/demo_maker`), state in `~/.alicia/state/demo-maker` (`DEMO_MAKER_STATE`) |
| **Process** | launchd `com.jfstudio.alicia-demo-maker` via `scripts/deploy-studio.sh`; logs `~/.alicia/logs/demo-maker*.log`; `tailscale serve --https=8790 → 127.0.0.1:4173`, embedded as the Alicia `#demo-maker` tab |
| **Deploy** | `scripts/deploy-studio.sh` from the alicia repo (first time only: `scripts/demo-maker-cutover.sh`) |
| **Public site** | publishing pushes mp4 + `library.json` to `ClearspeedRevOps/clearspeed-demos` via the GitHub Contents API → Vercel auto-deploys clearspeeddemos.com (~1–3 min lag) |

Source of truth: this directory in `justinfowler925/alicia`. The old `voicemaker-studio` repos are retired.

## Stack

- **Server:** Express + `node:sqlite` (NOT better-sqlite3 — its native build fails on Node 26). DB file `server/db/studio.sqlite3` (gitignored; schema in `schema.sql`, additive migrations in `server/db/index.js`).
- **Frontend:** vanilla JS/CSS, no build step (`public/`).
- **Audio:** ffmpeg/ffprobe (`/opt/homebrew/bin`) — normalize clips to 48kHz stereo, generate silence gaps, concat.
- **Video:** Playwright headless Chromium screenshots the player frame-by-frame → ffmpeg muxes with the audio.
- **TTS providers:** Voicemaker.in + ElevenLabs, routed per segment by `voices_cache.provider` (`server/lib/tts.js`).
- **Translation:** `@anthropic-ai/sdk` structured outputs (`server/lib/translate.js`) — segment text *and* the player's own UI chrome (`server/lib/uiLocale.js`).

## Repo layout

```
server/
  index.js            Express app + all API routes + render job registry
  db/                 schema.sql + node:sqlite bootstrap & migrations
  lib/
    voicemaker.js     Voicemaker.in API (voice list nests under data.voices_list)
    elevenlabs.js     ElevenLabs API (clips → renders/el-clips/, 24h sweep)
    tts.js            provider router: segment → convert() by voice provider
    audio.js          download / silence / concat helpers (TMP_DIR)
    templates.js      demo FORMAT templates (sales / production) — stamps out full segment lists
    demoVideo.js      TTS per segment → measured timeline → deriveConfig() → renderVideo()
    video.js          Playwright parallel JPEG frame capture → ffmpeg encode
    translate.js      Claude project translation (clone project into target language) + UI chrome
    uiLocale.js       the player's own language: locale/flag resolution + the English chrome pack
    publicSync.js     GitHub Contents API push to the public clearspeed-demos repo
public/
  index.html, app.js, style.css    the studio UI (projects, segments, templates, render history, publish)
  demo/player.html    self-contained 4-quadrant assessment player (drives ALL video frames)
  demo/data/          per-project player config JSON   ← host-generated, NEVER rsync-delete
  demo/audio/         per-project stitched demo mp3     ← host-generated, NEVER rsync-delete
  library/index.html  Atlas-hosted gallery (email-gated downloads)
  library/videos/     published mp4s                    ← host-generated, NEVER rsync-delete
scripts/
  smoke.mjs           headless player sanity: render 5 frames of the hmpps demo, report page errors
  render-video.mjs    re-encode an existing demo config to MP4 without regenerating TTS
```

## Local dev

```bash
npm install
npx playwright install chromium   # headless shell for video rendering
cp .env.example .env              # fill in keys
npm start                         # http://localhost:4173
```

ffmpeg + ffprobe must be on PATH. The DB self-creates on first boot.

## Environment variables

| Var | Purpose |
|---|---|
| `VOICEMAKER_API_KEY` | Voicemaker.in TTS |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS (voices grouped under language "ElevenLabs" in pickers) |
| `ANTHROPIC_API_KEY` | translation feature (falls back to `ANTHROPIC_PCM3_API_KEY`, the Doppler name) |
| `TRANSLATE_MODEL` | optional model override for translation |
| `GITHUB_TOKEN` | public-site sync — Contents API pushes to the public repo |
| `PUBLIC_REPO` / `PUBLIC_REPO_BRANCH` | default `ClearspeedRevOps/clearspeed-demos` / `main` |
| `PUBLIC_SITE_URL` | shown in publish UI links (default clearspeeddemos.com) |
| `FORWARD_WEBHOOK_URL` | optional: forwards library leads + watch events as a signal |
| `PORT` | default 4173 |
| `VIDYARD_API_TOKEN` | optional legacy Dashboard API path; the current Vidyard package does not include API access |

Keys live in `.env` on **both** machines (laptop + Atlas5); rsync excludes `.env`, so set new vars on the host by hand.

## Data model

`projects → segments` (ordered), plus `renders` (history), `library` / `library_leads` / `library_events` (publishing + gallery analytics), `voices_cache`.

The segment fields that drive demo video:

| Field | Meaning |
|---|---|
| `role` | `intro` / `question` / `answer` / `closing` / `transition` / `other` — cues come from questions+answers; intro/closing/transition just occupy time |
| `result` | `G` (clear) or `R` (high risk) — the badge a **scored** question posts |
| `qtype` | `PQ` (pertinent) or `NQ` (neutral) |
| `q_key` | shared key across iterations — both asks of the same question carry one `q_key` (e.g. `PQ1`) and share one row in the on-screen question list |
| `iteration` | 1 or 2 (production format asks the same set twice) |
| `scored` | 0/1 — **only scored asks create a results column** |

Projects carry `demo_format` (`sales` / `production`), `demo_irn`, `demo_region`, `demo_language`. The **project name** is the org label shown atop the player's Questions/Participants panels.

## Demo formats

`server/lib/templates.js` stamps out the entire segment list for either format — pick format + question count, then replace the placeholder pertinent-question text.

### sales (counts 3/4/5)

Intro → N × (pertinent question, answer) → Closing. **Every question scored.** This is the shape of every pre-template library demo.

### production (counts 3/4)

Mirrors the real assessment: the same question set asked **twice**, transition segment between iterations, and **only the second iteration's pertinent questions are scored**. Neutral questions are never scored — no column, no result, in either iteration; same for *all* iteration-1 asks. The results grid therefore has exactly P columns for P pertinent questions, populated only during the second pass.

- Order for P pertinent questions (P+2 neutrals): two neutrals up front, then each PQ followed by an NQ — `P=4 → NQ1 NQ2 PQ1 NQ3 PQ2 NQ4 PQ3 NQ5 PQ4 NQ6`.
- **Standard neutral set (use verbatim):** NQ1 "Is this the year 1995?" → **No**; NQ2 television, NQ3 car, NQ4 truck, NQ5 bus, NQ6 bicycle → all **Yes**. Every neutral answers Yes except the first, which is always No.
- **Outcome default:** every scored question is clear except the **last** pertinent one, which is high risk (GGR / GGGR) — the video builds to the risk.
- No "Baseline" terminology anywhere — unscored asks are labeled by their pass: "Iteration 1" / "Iteration 2".

In the editor, the segment Type dropdown offers **NQ**/**PQ** directly (stored as `role='question'` + `qtype`); tagging a question auto-assigns the next `q_key` and auto-inserts its answer segment. Each question shows its live mapping — "→ Q1" vs "not scored" — so the results-grid shape is visible while authoring.

## Video render pipeline

`POST /api/projects/:id/render-video` returns `{jobId}` immediately and runs in the background; poll `GET /api/render-jobs/:jobId` for `{phase, percent, etaSeconds, …}`. Phases: **speech** (per-segment TTS) → **audio** (stitch) → **frames** → **encoding** → done.

1. Each spoken segment is TTS'd, normalized (48kHz stereo), and **ffprobe-measured** — all timing derives from real durations. Never hand-author cues; the final mix has a music bed, so silence-detection can't recover timing.
2. `deriveConfig()` (pure function — testable without spending TTS) maps measured timings to the player model:
   - `questions[]` — one row per **distinct** question (segments sharing `q_key` share a row)
   - `columns[]` — one results column per **scored ask**, in ask order
   - `cues[]` — one per ask: `qi` (row to highlight), `col` (column to post, `null` = unscored), `ask`/`answer`/`answerEnd`/`post` times, `iter`
   - **Cue index ≠ row index ≠ column index.** Keeping them distinct is what lets a question highlight twice but post once.
   - Legacy fallback: if **no** segment is marked scored, every question is treated as scored (the pre-template sales shape) — this keeps all old library demos rendering identically.
3. Config JSON → `public/demo/data/<slug>.json`, audio → `public/demo/audio/<slug>.mp3`, then `video.js` renders: N parallel headless pages (N = min(cores−2, 8)) pull frame indices from a shared counter, screenshot JPEG (quality 92), ffmpeg muxes at ~34 fps render speed on Atlas5.

Timing constants (`demoVideo.js`): `POST_DELAY` 4.0s (transcribe + analyze beat before a scored badge posts), `UNSCORED_DELAY` 1.6s (unscored asks settle into "captured" — nothing is computed).

## The player (`public/demo/player.html`)

Self-contained vanilla JS/canvas recreation of the Clearspeed UI. Everything renders from a single `time` value; `?render=1` exposes `window.__renderAt(t)` for deterministic headless capture. Brand tokens: navy `#112578`, orange `#ed5925`, signal `#2bb8e8`.

- Workflow phases per ask: **Record** → (scored: **Transcribe** ~0.9s → **Analyzing** ~3s → **posted** CLEAR/HIGH RISK) or (unscored: **captured** → settled, muted, no verdict color; neutral questions don't display their answer).
- The workflow tracks its **own** cue (earliest started-but-unposted), decoupled from the question-list highlight, so analysis finishes even when the next question starts reading.
- `VERDICT_DWELL` (1.8s hold on a posted verdict) is **production-format only, deliberately** — sales demos must stay byte-identical to their published renders, and the established sales norm is that intermediate verdicts get cut off by the next ask (only the final one displays). Don't "fix" that.
- `normalize()` upgrades old config JSON (no `columns`/`qi`/`col`, oldest have only `{ask, result}`) so **every published demo keeps playing** — verify against the live configs on Atlas5 after any player change.

### The player's own language (`server/lib/uiLocale.js`)

A translated demo has a translated **interface**, not just translated questions — and the language selector in the top-right of the Questions panel shows that language's flag. Arabic questions under "🇺🇸 English ▾" is the detail that gives a demo away.

- **Which language:** `projects.demo_language` (a BCP47 tag, set by the **UI language** picker in Demo video settings) when set, otherwise the language of the voices actually speaking — the dominant segment `language_code`. `/translate` stamps the tag on the project it creates, so a translated project needs nothing set.
- **The voices are only believed when the text agrees with them.** A project's voices can be swapped long after its text was written: `Embassy Clearance` (id 8 on Atlas5) is a **published English** demo cast with ar-SA voices, and voice-only inference would flip it to an Arabic interface on its next re-render. A positive script mismatch (Latin text, Arabic-script language) falls back to English; an indecisive one (short text, digits) trusts the voices. Languages sharing the Latin script can't be told apart this way and don't need to be. Two cases therefore need the picker: a non-English **ElevenLabs** demo (those voices all report `en-US` whatever they are saying), and a non-English demo whose voices are tagged as something else.
- **Flag + language name** are derived, not tabled: the flag emoji from the tag's region subtag (`ar-AE` → 🇦🇪, `en-GB` → 🇬🇧), the label from ICU (`Intl.DisplayNames`, e.g. `Deutsch`, `العربية`). The **Region** chip's flag comes from the project's own Region field (`REGION_FLAG`), falling back to the language's flag — it used to be a hardcoded 🇺🇸 next to whatever the region said.
- **The Region NAME is derived too, and that is the part that was wrong.** `projects.demo_region` carries a column DEFAULT of `'United States'`, so every project held it whether anyone chose it or not — and nine live demos rendered an Arabic or British interface beside "Region 🇺🇸 United States". `resolveRegionName()` therefore treats that exact value as **unset** and derives from the demo's locale (`en-GB` → United Kingdom, `ar-AE` → United Arab Emirates); any other stored value is a deliberate override and wins. The resolved name travels in the `ui` block as `regionName` so `deriveConfig` prints the same name the flag was computed from — a name and a flag resolved independently is how "United States 🇦🇪" happens. A config with **no `ui` block** keeps the old literal fallback, which is what keeps pre-locale renders reproducible.
- Derivation follows the **locale, never the project name**: `Pet Insurance UK` is voiced by an `en-US` voice, so it derives United States. If a demo's name and its voices disagree, either fix the voice or hand-set Region — the code will not guess from a title.
- **Chrome strings:** `UI_EN` in `uiLocale.js` is the source of truth for which strings are translatable; `translateUiStrings()` localizes them. Generated once — at `/translate` time, or on the first render of a non-English demo — and stored in `projects.demo_ui_strings` as `{code, t}`, so a render never depends on a live translation call and re-renders are byte-stable. A pack whose `code` doesn't match the resolved locale is **ignored** rather than painted. A translation failure degrades to English labels with the correct flag; it never fails a render.
- **The player defaults to English per key** (`DEFAULT_UI` in `player.html`), so a config with no `ui` block — every demo rendered before this existed — renders **byte-identically**. That is the invariant to re-check after touching this: `git show HEAD:public/demo/player.html` into a second route, screenshot both at the same frame times, compare hashes.
- **RTL** (`ar`, `he`, `fa`, …): the stage gets `dir="rtl"` and mirrors — panels, table columns and the Record→Analyze→Score pipeline all flip. Mixed-direction runs inside RTL text (the `18-Feb-2026` dates, `SUM…`) need `class="bidi" dir="auto"`, or bidi reorders them to `Feb-2026-18` and throws a trailing `…` to the wrong end. Org names, region names, IRNs and question text are **data** and are never translated.

## Standard audio components

`server/lib/standardAudio.js` holds the fixed Clearspeed system audio — the two Intro 1 variants (claims / underwriting), Intro 2, the "single yes or no" waiting prompt, both transitions, both closings, the seven quality prompts (too soft/loud/fast/slow, missing answer, background noise, unknown audio) and a 3-second silence.

- **They are not segments.** They never enter a project's segment list, never appear in the editor, and never affect a rendered demo video. Every **segment export** (`POST /:id/render-segments`) renders the whole set and drops it in the zip beside the project's own files, named `std-01-…` through `std-16-…` so they sort after the numbered segments.
- **Voiced by the demo's own narrator**, resolved by `narratorVoiceFor()`: a narration segment's voice wins over `projects.narrator_voice_id`, because a translated project's segments are what actually got re-voiced. Answer segments are skipped — that voice is the participant, not the system.
- **The wording is fixed English.** Five live demos have an Arabic narrator (including `Emirates Airlines`, whose name does not say so), and those exports come out as English text in an Arabic voice. Voicemaker accepts it rather than erroring, so the export response sets `standard.englishTextNonEnglishVoice` and the status line says which voice was used — the audio is never dropped silently.
- **`<break time="500ms"/>` is cut locally**, not passed through as SSML: the text is split, each run is synthesized, and an ffmpeg silence goes between. Voicemaker does honour the tag (it turns 500ms into ~1.0s), but segments route per voice to either Voicemaker or ElevenLabs and the two interpret it differently. A stitched component assembles in mp3 and transcodes at the end, because `concatenate()` always encodes mp3 — writing a concat straight to a `.wav` name puts mp3 bytes behind a wav extension.
- **Cached** under `renders/_standard/<voice>-<lang>-<fmt>-<wordingHash>/`. Same text, same voice, same audio — without the cache every export spends ~15 fresh TTS calls. Measured: 7s cold, 0s warm, byte-identical. Changing any component's wording changes the hash and re-renders.

## Library & publishing

- **Publish** (per mp4 in Render history) tags Vertical + Use case + Outcome + Language/Accent. Taxonomy is set on the project under **Library publishing** (`demo_vertical` / `demo_use_case` / `demo_summary` / `demo_sensitive` / `demo_org`) and pre-fills the publish modal. Same lists live in `LIB_TAXONOMY` (app.js) + `TAXONOMY` (library/index.html).
- Publish is an **upsert on `source_project_id`**: republishing replaces the row + file (locally and in the public repo). One library entry per project is the product model.
- **Vidyard:** Publish runs a **headless Playwright upload** through the account's uploader widget (`server/lib/vidyardWidgetUpload.js`) on Atlas5 — no popup, no file picker, no Dashboard API token. The player UUID is scraped from the widget share link (webhook is a fallback). Advanced → paste an existing player skips the upload.
- **Auto-upload is first-publish only.** The widget cannot swap the video inside an existing player, so an upload always mints a *new* one — uploading on every republish just filled the Vidyard library with copies of the same demo. A republish of an entry that already has a player therefore keeps it (`vidyard_action = kept-existing`) and Vidyard goes on playing the previous cut; tick **Upload a new Vidyard video** in the publish modal to mint a replacement, which appends the old uuid to `vidyard_superseded` and stales every share link already in circulation. Delete the superseded player in Vidyard by hand — there is no API to do it.
- Uploads are staged under the demo's **title** before being handed to the widget: Vidyard names the asset after the filename it receives, and the raw `lib-32-ffe066d8b01c.mp4` names made the library unreadable.
- `scripts/smoke-vidyard-headless.cjs` uploads for real and needs `--for-real`; anything it creates has to be deleted in Vidyard by hand.
- **Sensitive videos:** publish checkbox (auto-suggested for use case "Sensitive") → unguessable filename, redacted public `library.json` (no src), full mapping in the public repo's `api/_private-library.json`. The @clearspeed.com email-code verify flow lives in the **clearspeed-demos** repo, not here.
- **Public sync** (`publicSync.js`): every publish/delete pushes mp4 + regenerated `library.json` to the public repo via the Contents API; Vercel redeploys. Publishing IS automatic — the only lag is the Vercel build (~1–3 min); wait and refresh before debugging. `POST /api/library/sync` = manual full resync. To unpublish, `DELETE /api/library/:id` on the studio (it cleans the repo too) — **never hand-edit the repo copy**.
- **Leads:** the public site's gate/event endpoints create Salesforce leads via Web-to-Lead (that code is in clearspeed-demos); this app's own `/api/library/gate` + `/api/library/event` capture to SQLite and optionally forward to `FORWARD_WEBHOOK_URL`.

## API surface

Voices: `GET /api/voices`, `POST /api/voices/refresh` (upsert-only — a blind DELETE+INSERT once emptied the cache on one dup VoiceId).
Projects: CRUD + `POST /:id/clone`, `POST /:id/translate`, `PUT /:id/segments`.
Templates: `GET /api/templates`, `POST /api/projects/:id/apply-template` `{template, count}`.
Audio: `POST /api/segments/preview`, `POST /api/projects/:id/render` (full mix), `POST /:id/render-segments` (per-segment wav/mp3 zip + the standard components — see below).
Video: `POST /api/projects/:id/render-video` → `GET /api/render-jobs/:jobId`; render history `GET /:id/renders`, `DELETE /api/renders/:id`.
Library: `GET /api/library`, `POST /api/library/publish`, `DELETE /api/library/:id`, `POST /api/library/sync`, `POST /api/library/gate`, `POST /api/library/event`.

## Deploying to Atlas5

Edit on the laptop, rsync over, restart the launchd job **only for server-code changes** (`public/*`-only changes need no restart).

```bash
rsync -avn --delete \
  --exclude node_modules --exclude 'server/db/studio.sqlite3*' \
  --exclude renders/ --exclude tmp/ --exclude logs/ --exclude .env --exclude .git/ \
  --exclude public/library/videos/ --exclude public/demo/audio/ --exclude public/demo/data/ \
  ./ jfstudio@100.93.125.5:voicemaker-studio/
```

**⚠ ALWAYS dry-run first (`-n`) and read every `deleting …` line before the real run.** `public/demo/{data,audio}/` and `public/library/videos/` are **host-generated per render/publish** — Atlas5 holds one config+mp3 per published demo while the laptop only has whatever it last rendered locally. A `--delete` sync without those excludes would destroy the live demo configs (nearly happened 2026-07-28).

```bash
ssh jfstudio@100.93.125.5 'launchctl kickstart -k gui/$(id -u)/com.jfstudio.voicemaker-studio'
```

New dependencies need `npm install` on the box (rsync excludes node_modules); Playwright needs a one-time `npx playwright install chromium` there (~80MB, `~/Library/Caches/ms-playwright`).

## Testing & verification

- `node scripts/smoke.mjs` — boots a throwaway static server, drives the player headless through 5 known frames of the hmpps demo, reports page errors.
- `node scripts/render-video.mjs <slug> [fps]` — re-encode an existing config to MP4 without regenerating TTS.
- `deriveConfig` is pure: feed it `buildSegments()` output with fake timings to assert the questions/columns/cues mapping (e.g. production P=4 → 10 rows, 4 columns, 20 cues, columns only on iteration-2 PQs) — no TTS spend.
- The gold standard for "sales demos unchanged" after player edits: render the same config through old and new player in two iframes and diff per-frame state (workflow status, chip, feed, pipeline glyphs, badges, summary, active row). The 3 legacy demos were verified frame-for-frame identical when the production format shipped.

## Gotchas

- **Autosave exists for a reason:** editor state autosaves (debounced 1.2s) + localStorage snapshot ring (last 10 per project) + flush-before-project-switch + `beforeunload` guard. A whole demo was lost before this existed.
- The question list scrolls via `scrollTop`, so offsets MUST come from `offsetTop`/`offsetHeight`, never `getBoundingClientRect` — the stage carries a CSS scale transform; client rects are post-transform. Mixing them is silently correct at exactly 1:1 (the render size) and broken in every live browser window.
- Don't pass a function with optional params straight to `addEventListener` — the Event object lands in the first param (this silently broke the template count picker once).
- Voicemaker's voice-list response nests under `data.voices_list`.
- ElevenLabs community voices must be added to the workspace (POST /v1/voices/add) before they appear in the voice list; refresh voices after adding in the EL UI.
- `library.json` on the public site is read with `cache: no-store` from static hosting; the KV/Blob comment in the site's `libraryData.ts` is stale.
- Schema changes go in **both** `schema.sql` and the crash-safe migration loop in `server/db/index.js` (a DB holding "bad" data must still boot — the dup-library migration relies on this).
