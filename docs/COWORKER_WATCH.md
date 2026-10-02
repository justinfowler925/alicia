# Coworker follow-ups

A Studio-owned service checks Justin's work Gmail every five minutes. It reads recent conversations involving Clearspeed senders and revisits tracked open conversations beyond the seven-day discovery window (up to 40 oldest-checked open threads per cycle). Attachments are not read. Initial backfill advances one 40-message page per cycle.

Cursor reviews one changed conversation at a time. An obligation requires high confidence and an exact source quote. Dates require their own source quote. Related requests are grouped by conversation; this is not a guarantee that every separate request in a long thread will be captured. Later evidence can suggest completion but cannot silently mark a task done. Owner dispositions survive rescans. The interface keeps follow-ups collapsed by default.

Updates from Alicia's tools, the page, and the owner's replies to Alicia's notification threads share SQLite state and an audit trail. Stale page saves return a conflict. New Slack alerts are limited to due/overdue obligations within one day, at most one new alert per hour. Undated tasks remain available in the page and conversation. No coworker replies are sent by this watcher.

## Connection status

Gmail uses Studio's selected work-account OAuth credentials. Slack uses the existing RevOps 2030 user connection for Justin (`U03TVK7B057`, Clearspeed `TL8SFF7J8`) through `ALICIA_WATCH_SLACK_USER_TOKEN`. The notification bot remains separate. The reader uses `users.conversations`, `conversations.history`, and `conversations.replies`; it does not require Slack search access or any write scope.

Slack scans joined public/private channels and direct/group messages. It discovers messages rooted within the last seven days and reads their full threads, keeping human DMs and conversations involving Justin. Tracked open threads are revisited beyond that window (40 oldest-checked per cycle). New replies to older, untracked roots are outside this discovery window. Pagination and partial threads survive restarts; a scan uses at most 40 paced API calls per five-minute cycle. The UI distinguishes a scan in progress from a completed scan. A rate limit preserves its cursor and honors Retry-After. Slack retention and membership determine available history; this is not an all-workspace archive.

The obligation reviewer uses explicit `ALICIA_COWORKER_MODEL` (default `composer-2.5`) independently of the interactive conversation model. It never raises a spending limit. Source checks and review errors are reported separately.

## Verification

Tests cover evidence, confidence, owner dispositions, completion review, stale writes, source races, old tracked threads, membership/history/reply pagination, resumable scan budgets, wrong-account rejection, and rate-limit recovery. Model qualification uses isolated positive, FYI, and prompt-injection conversations. Live reading is qualified against the owner's actual connection; fixture testing never writes to the running service's database.
