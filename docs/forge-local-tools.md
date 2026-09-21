# Local Forge tools

Forge chat uses the resident Gemma model on Mac Studio. Its tool adapters do not
call a hosted model, import the hosted persona launcher, or attach to a user's
browser. New turns discover the installed tool services and record unavailable
services explicitly; workspace file tools remain available if discovery fails.

Available tools:

- Existing workspace shell, public web search and public page fetching.
- `file_list`, `file_read`, `file_write`: explicit workspace text-file operations.
  Reads return at most 300 lines/64 KB; writes accept at most 200 KB and require
  `overwrite=true` for existing files. These file tools reject paths outside the
  conversation workspace and preserve original attachments.
- `search_project_knowledge`, `read_project_source`, `project_status`: the private
  owner knowledge MCP on Studio. Search results retain their source provenance;
  indexed snapshots are not live deployment or Salesforce evidence.
- `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`,
  `browser_select_option`, `browser_press_key`, `browser_wait_for`,
  `browser_take_screenshot`, `browser_close`: the installed Playwright MCP,
  launched headless with an isolated profile, no saved cookies, and no extension
  or CDP attachment. Screenshots are saved on Studio. The text adapter does not
  give Gemma visual inspection of screenshot pixels. Use `browser_snapshot`
  after navigation for page contents and interaction references.

Browser tools only authorize actions within the user's current request. They do
not grant permission to send messages, make purchases, or upload private data.
The existing shell network restriction remains in place. Knowledge and browser
services run as explicit adapters outside that shell sandbox. This is a private
owner service, not a multi-user authorization system.

Each run keeps these files under `~/.local/share/studio-agents/runs/<run-id>/`:

- `tool-capabilities.json`: discovered tools, missing services, and execution scope.
- `tool-receipts.jsonl`: append-only dispatch and completion records, arguments,
  observed result, success/failure, timestamps, and result SHA-256.
- `browser/`: browser snapshots and screenshots.
- `knowledge-tools.log` and `browser-tools.log`: adapter diagnostics.

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
