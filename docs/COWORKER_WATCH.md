# Coworker follow-ups

A Studio-owned service checks Justin's work Gmail every five minutes. It reads recent conversations involving Clearspeed senders and revisits tracked open conversations beyond the seven-day discovery window (up to 40 oldest-checked open threads per cycle). Attachments are not read. Initial backfill advances one 40-message page per cycle.

Cursor reviews one changed conversation at a time. An obligation requires high confidence and an exact source quote. Dates require their own source quote. Related requests are grouped by conversation; this is not a guarantee that every separate request in a long thread will be captured. Later evidence can suggest completion but cannot silently mark a task done. Owner dispositions survive rescans. The interface keeps follow-ups collapsed by default.

Updates from Alicia's tools, the page, and the owner's replies to Alicia's notification threads share SQLite state and an audit trail. Stale page saves return a conflict. New Slack alerts are limited to due/overdue obligations within one day, at most one new alert per hour. Undated tasks remain available in the page and conversation. No coworker replies are sent by this watcher.

## Connection status

Gmail uses Studio's selected, verified work-account OAuth credentials with read-only access. The existing notification bot can DM Justin but cannot read his own private Slack conversations. Slack message discovery is not operational until a dedicated user connection is authorized; invalid stored access and refresh tokens were verified during setup. The UI reports that gap explicitly. The preliminary Slack search adapter is not a qualified full-history/thread integration and must be completed and verified with the restored connection before Slack coverage can be claimed.

## Verification

Unit tests cover source evidence, low-confidence exclusion, thread deduplication, persistent owner dispositions, completion review, stale writes, changing-source races, and continued polling of old open work. Live email ingestion and model processing were observed. No successful full Slack message ingestion or owner Slack-reply round trip is claimed by this delivery.
