# Forge chat in Brutus

Open `http://127.0.0.1:8768/#forge`, or choose **Forge chat** from Brutus.
Send a message, continue with a follow-up, use **New chat**, or select a saved
conversation. **Stop** requests cancellation of that conversation's active run.
Status distinguishes queued, working, replied, failed, blocked and cancelled.

Forge uses the local Gemma runtime described below. It sends
the current user message and saved prior turns to Forge. Each conversation has
its own Studio workspace. The full conversational answer is the local model's
reply; a successful process is not an independent claim
that code was delivered or a task was accepted.

History lives in `~/.local/share/brutus-forge-chat/chat.sqlite3` on Studio.
Runs remain in `~/.local/share/studio-agents`, with their existing queue,
single-worker limit, receipts and cancellation mechanism. Closing Brutus does
not stop a Studio run. A request ID prevents replay after a lost response;
**Reconnect** checks the same request. There is no timer or agent started just
by opening the page. Browser polling observes an active request only.

The deployed `forge_bridge.py` is sent over existing private SSH for each
request. This avoids an independently installed bridge drifting from Brutus.
User text travels in JSON on stdin, never shell command text. No additional
network listener, credential, provider or public access is introduced.

The transport accepts only loopback peers and loopback Host values, checks
Origin when supplied, and requires the non-simple `X-Brutus-Chat` header with
no CORS grant. It follows Brutus's local typed-input trust boundary. Existing
owner-token-protected actions retain their own authentication requirements.

Verification: `tests/test_forge_chat.py` covers retry deduplication, recovery
after an interrupted save, follow-up context, isolated cancellation and
cross-origin/remote rejection. The browser workflow and layout contracts live
under `docs/forge-chat/`; the connectivity example is deliberately a no-tools,
no-files conversation with two turns.


## File attachments

Choose **Attach files** or drop files anywhere in the Forge panel. Up to 10 files
per message, with no application file-size cap. Review the file rows, remove anything unwanted,
then send a message (files alone send “Please review the attached files.”).
Uploads show Ready only after Studio acknowledges the bytes; a failed upload has
Retry and Remove controls. Send is disabled until all selected files are ready.
Ready attachment metadata survives reload. Incomplete uploads after reload must
be removed and reselected. Attachment names remain visible in saved turns.

Bytes travel through the loopback, same-origin/header-protected Brutus endpoint
and private SSH to the conversation workspace on Studio. No local permanent copy
is created. File names are display data; storage uses UUID directories and safe
basenames. The bridge streams bounded chunks without a file-size cap, scopes file IDs to the conversation, persists
the turn/file association and checks it on retried sends. Atomic writes recover
an interrupted upload. Forge receives actual paths and can read files in later
turns. Supported interpretation depends on Forge's available file-reading tools.
Removing a staged file detaches it from the outgoing message; uploaded bytes remain
in the Studio conversation workspace (no automatic deletion policy).

Verification: `tests/test_forge_chat.py` covers bytes, retry, history, stream interruption and
cross-conversation rejection. `scripts/verify-forge-attachments.cjs` runs a headless
browser against `FORGE_URL` (defaults to local production); supply
`PLAYWRIGHT_MODULE` if Playwright is installed outside the project. It creates a
benign test conversation and checks picker/drop, failed-upload retry, a 32 MiB upload, removal, reload, narrow layout and a real Forge read of file-only text.

Uploads stream browser → Brutus → SSH → Studio with backpressure and no whole-file buffering. An explicit end marker prevents interrupted uploads from being committed. Storage capacity remains the practical limit.


## Turn activity

The activity strip shows the conversation turn number and active turn count (queued
turns count as active), elapsed time, event update count and tool-call count. These
come from the Studio run timestamps and actual Codex event log, with no reasoning,
command text or tool output exposed in the activity API. The shine runs during
recent running/starting activity and respects reduced motion. Queued, stopping,
terminal and disconnected states do not animate. After 90 seconds without output,
the strip says “No recent activity”; this signals uncertainty, not a claim that the
agent has crashed. After 15 seconds without a fresh active-run status it says
“Status stale”; a failed poll says “Connection lost” and labels counts last known.
Active runs automatically retry polling every three seconds after a failed poll.
Elapsed time survives reload and stops at the recorded finish timestamp.

`tests/test_forge_chat.py` checks real event metadata and excludes private payloads.
`scripts/verify-forge-activity.cjs` uses a private browser with mocked API snapshots
on the real page to deterministically verify running, quiet, offline, recovery,
queued, stopped and complete states, ticking/frozen elapsed time, reduced motion
and narrow layout. Live run verification and Shine proof supplement those states.


## Local inference (2026-09-18)

New Forge chat turns use the resident Gemma 4 31B IT 4-bit weights at
`/Users/jfstudio/.local/share/atlas-models/gemma4-31b-it-4bit` through
Studio's loopback `http://127.0.0.1:8081/v1/chat/completions`. The transport
ships the deployed local worker over private SSH; no Codex process, hosted
provider credentials, or hosted fallback is used. Responses naming a different
model are rejected. Proxy environment settings and HTTP redirects are disabled.

Historical hosted turns are preserved and labeled with their original model.
New local run state is in `studio-agents/forge-local.sqlite3`; historical run
records are read only. Files and chat history remain in their original Studio
locations. Cancellation stops the local worker; an in-flight server inference
may need to finish internally before the resident accepts its next request.

Gemma can use a bounded workspace command tool to inspect attachments and edit
copies. Commands run under macOS sandbox-exec with no network access and writes
restricted to the conversation workspace. No credentials are inherited. Missing
local tools, blocked network dependencies, model errors, and step limits are
reported as failures, never silently delegated to another agent or model.


## Public web tools

Local Gemma has `web_search` (Bing web search results) and `web_fetch`
(public page text), alongside the workspace file tool. These are ordinary
web requests from Studio, not hosted model calls. Search snippets are leads;
Gemma must read and verify relevant sources before asserting specifications,
price or availability. Captchas, login requirements and JavaScript-only pages
are reported as source-specific limitations. No challenge bypass is attempted.
Source HTTP/HTTPS links in replies are clickable and rendered without HTML.
Shell commands remain network-isolated; dedicated web tools inherit no browser
sessions or credentials and do not permit private/local network URLs.
