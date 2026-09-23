# Local Forge tools

Forge chat uses the resident Gemma model on Mac Studio. Its tool adapters do not
call a hosted model, import the hosted persona launcher, or attach to a user's
browser. Optional packs stay out of the model context until Forge calls
`enable_capability`; cold turns only expose the always-on core.

## Always-on

- `workspace_command`: sandboxed shell in the conversation workspace (no network).
- `web_search`, `web_fetch`: public internet research (outside the shell sandbox).
- `file_list`, `file_read`, `file_write`: workspace text-file operations.
  Reads return at most 300 lines/64 KB; writes accept at most 200 KB and require
  `overwrite=true` for existing files. These file tools reject paths outside the
  conversation workspace and preserve original attachments.
- `enable_capability`: load one on-demand pack for the remainder of the turn.
  Omit `name` or pass `list` to read the catalog without loading anything.

## On-demand (call `enable_capability` first)

- `github`: compact read-only Studio `gh` tools — `github_search`, `github_issue`,
  `github_pr`, `github_repo`, `github_api`. `github_api` is GET-only; method
  overrides (`-X`, `--method`) are rejected. No create/comment/merge in this
  surface.
- `knowledge`: `search_project_knowledge`, `read_project_source`, `project_status`
  via the private owner knowledge MCP. Search results retain provenance; indexed
  snapshots are not live deployment or Salesforce evidence.
- `browser`: Playwright MCP launched headless with an isolated profile, no saved
  cookies, and no extension or CDP attachment — `browser_navigate`,
  `browser_snapshot`, `browser_click`, `browser_type`, `browser_select_option`,
  `browser_press_key`, `browser_wait_for`, `browser_take_screenshot`,
  `browser_close`. Screenshots are saved on Studio; Gemma does not visually
  inspect pixels. Browser actions only authorize work within the current user
  request (no purchases, outbound messages, or private uploads without explicit
  instruction).
- `shine`: loads `~/.agents/skills/shine/SKILL.md` as guidance text (bounded);
  does not add tool schemas.
- `hollywood`: loads `~/.agents/skills/studio-media/SKILL.md` and, when the Studio
  media MCP is present, enables its allowlisted media tools.
- `strike-package`: loads `~/.codex/skills/strike-package/SKILL.md` as guidance
  text (bounded); does not add tool schemas.

Skill bodies are returned in the tool result for that turn. They are not pasted
into the system prompt and do not stay in the tools array.

The existing shell network restriction remains in place. Knowledge, browser,
GitHub, and Hollywood services run as explicit adapters outside that shell
sandbox. This is a private owner service, not a multi-user authorization system.

Each run keeps these files under `~/.local/share/studio-agents/runs/<run-id>/`:

- `tool-capabilities.json`: always-on tools, available catalog, enabled packs,
  missing services, and execution scope (`loading: on_demand`).
- `tool-receipts.jsonl`: append-only dispatch and completion records, arguments,
  observed result, success/failure, timestamps, and result SHA-256.
- `browser/`: browser snapshots and screenshots (after browser is enabled).
- `*-tools.log`: adapter diagnostics for enabled MCP packs.

A dispatch receipt alone is not completion proof. A nonzero command exit,
MCP error, or adapter error is a failed tool result. If every attempted tool
fails, the turn cannot be marked successful. Mixed-success turns retain every
individual failure for review. Browser cleanup runs on completion, failure and
user cancellation; MCP child processes are reaped. A tool timeout disables that
service for the remainder of the turn rather than silently retrying an action
whose outcome may be uncertain.

Both Python modules are shipped together in a content-addressed Studio runtime.
Changing either module selects a new runtime directory; active turns keep their
original source. Brutus deployment and `/version` readback use the normal
repository delivery policy.
