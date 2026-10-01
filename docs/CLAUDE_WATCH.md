# Claude session watch

The Alicia service on Mac Studio owns the watch loop, SQLite decision records,
Cursor Pro assessments, and Slack delivery. Claude hooks on each work host report
bounded, redacted owner/assistant context and deliver instructions back to the
same Claude process with `asyncRewake`. No competing resume process is started.

Run `python3 scripts/install-claude-watch.py --host <stable-host-name>` on a work
host after deploying Studio. The installer preserves unrelated Claude settings
and hooks, saves a settings backup, and installs no polling LaunchAgent. Existing
idle sessions attach on their next hook event. A live listener, not a historical
transcript or installed configuration, is required for the connected indicator.
If a work host sleeps, its sessions cannot execute; Studio and Slack remain up.

Pending decisions appear above the Alicia conversation. Everything else is in
the collapsed session history. Reply in the bot's Slack DM **thread**, the page,
or Alicia's conversation. The first saved answer wins. An actual owner prompt
entered in Claude is mirrored without being sent back to Claude. System hook
notifications do not count as owner authorization or reset continuation limits.

The watch can continue concrete work already authorized in the owner's supplied
context, at most twice per owner turn. If that is insufficient it asks for a new
direction. It cannot approve Claude's native permission dialogs. Reported
completion is distinct from independent verification. Queued, dispatched, and
subsequent observed activity are separate delivery states. A disconnect between
claim and delivery is not automatically replayed, to avoid duplicate execution.

Slack uses the existing bot credential from the Studio 1Password contract. It
opens only the configured owner's DM and accepts that owner's text replies in
known decision threads. It does not take over other bot events or require a
public webhook. Slack polling and assessments run independently. Provider errors
are visible; failed assessments back off and do not starve other sessions.

Validation on 2026-10-01 UTC: UI reply persisted and updated its original Slack
message; a live PM Tracker session received the hook reminder and resumed in the
same transcript; model classification exercised real Cursor Pro calls. Two of
ten existing laptop Claude sessions attached during setup. Eight idle sessions
had not fired their next hook. The owner's one-time Slack reply check remained
pending. No WhatsApp integration is enabled.
