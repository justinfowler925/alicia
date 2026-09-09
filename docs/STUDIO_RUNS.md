# Studio runs

Open **Studio** in the Brutus navigation, or `http://127.0.0.1:8768/#/studio`.
On mobile, use **More → Studio runs**. Search by job/feed name, filter by state,
then open **Details** for run evidence and the bounded latest/error log or receipt.
The summary counts the complete discovered population, before filters.

## Authority and discovery

The `com.jfstudio.brutus-studio-runs` LaunchAgent runs a read-only collector on
Studio every minute. It writes a mode-0600 atomic snapshot under
`~/.local/share/brutus-studio-runs/`. It does not execute or modify monitored
jobs. Brutus reads that snapshot over SSH, with a 20-second request cache and a
persistent offline cache. Collection continues while the laptop is asleep.

Sources:

* User LaunchAgents and system LaunchDaemons whose labels start with
  `com.clearspeed.`, `com.jfstudio.`, `com.fowlerbrain.`, or `com.justinfowler.`.
  This intentionally includes supporting continuous services, not only daily feeds.
* Every `~/atlas-direct/state/cron/*.jsonl` receipt journal, including dormant
  workflows. Cadence is explicitly unknown when the journal does not expose it.
* Nevada `~/Projects/reports/nv-sled-brief/studio-state.json`. The daily
  06:00 America/New_York report is distinct from its launchd polling/retry tick.
  Failure never borrows yesterday's completion time as this run's end time.
* SLED sweep timestamped stdout publication acknowledgements. Its start stamp
  identifies the successful run; completion time/duration are not invented.
* Optional registered receipt descriptors described below.

The September 9 initial inventory checked 48 launchd definitions and returned
64 jobs/services, including 16 Atlas journals. One malformed launchd plist
(`com.jfstudio.atlas-dispatch-gc`) is represented with an inspection error.
User crontab was empty. Brutus Canon inspection covered 211 evidence rows;
seven mentioned these workflows, but none carried structured feed job IDs.
Those records are not treated as run receipts. “Red Ops” is represented by the
existing revenue intelligence / RevOps jobs; no separate service with that exact
label was found. This is a known-source inventory, not a claim to discover every
arbitrary shell process, third-party scheduler or disabled backup plist.

## Status semantics

* **Healthy** requires a timestamped success receipt/publication acknowledgement
  within its freshness window. A launchd zero exit or log modification time alone
  is insufficient. Success is the recorded job outcome, not a new audit of its output.
* **Failed** includes explicit receipt failures and nonzero retained launchd exits.
* **Running** means a current PID or a receipt stating running. It does not mean
  publication succeeded. Long-running services can legitimately remain running.
* **Stale** means a missed calendar run after one hour of grace, an interval
  receipt older than twice its cadence (minimum one hour), a configured freshness
  threshold, or a timed run exceeding its maximum duration (default two hours).
  Unknown-cadence Atlas journals use an explicit 24-hour freshness threshold.
* **Never ran** is used only when receipt absence or zero runs is supported by
  evidence; otherwise missing timestamps are **Unknown**. A launchd run counter
  covers the current scheduler session, not the lifetime of the job.
* Installed-but-unloaded or disappeared definitions remain visible. They have no
  claimed next scheduled execution. A missing collector snapshot older than three
  minutes, or an SSH error, turns the whole cached population stale and counts
  zero healthy/running jobs while preserving the last observed results in details.

Calendar schedules use Studio's IANA timezone and account for DST gaps/folds.
Next calendar time is a calculated schedule, not a promise that a sleeping host
will execute then. Launchd interval phase is not exposed, so its next run is
shown as unavailable rather than fabricated. Last success survives later failure.
Historical durations and last-run times that were never recorded remain unknown.

## Register a new feed

A launchd job using an existing prefix automatically appears within a minute.
No Brutus release is needed. Descriptive names, source, daily cadence overriding
a polling tick, precise receipts, or non-launchd feeds can be configured on Studio
in `~/.local/share/brutus-studio-runs/jobs.json`:

```json
{
  "com.jfstudio.example-feed": {
    "name": "Example daily feed",
    "source": "Source API → published dataset",
    "schedule": {
      "kind": "calendar",
      "calendar": {"Hour": 6, "Minute": 0},
      "timezone": "America/Chicago",
      "label": "Daily 06:00"
    },
    "timezone": "America/Chicago",
    "max_runtime_seconds": 3600,
    "receipt_path": "/Users/jfstudio/feeds/example/latest-run.json"
  }
}
```

Write receipts atomically from the actual runner, first as `running`, then with
its real terminal result. Emit a failure receipt even when a step fails; preserve
the runner's nonzero exit. Do not write a success before publication completes.

```json
{
  "status": "success",
  "started_at": "2026-09-09T11:00:00Z",
  "finished_at": "2026-09-09T11:02:00Z",
  "duration_seconds": 120,
  "error": null
}
```

Supported terminal values are `success`/`ok`/`complete`/`completed`,
`failure`/`failed`/`error`, plus `running` and `unknown`. Timestamps must include
an offset. Atlas journals also accept `ts` and `duration_ms`. Descriptors are
read every collection; missing fields remain unknown. Preserve the latest
receipt and let the observer retain recent evidence and last success.

## Installation and verification

From the Brutus checkout run `./scripts/install-studio-runs.sh`, then the standard
Brutus `./scripts/deploy.sh` after landing the change. The installer replaces only
the observer; feed schedules, commands and payloads are unchanged. Override SSH
host with `BRUTUS_STUDIO_SSH` in the installer and Brutus service if necessary.
The current deployment uses the existing Studio account/path conventions.

Read-only endpoints: `GET /api/studio-runs` and
`GET /api/studio-runs/{job_id}/log?kind=stdout|stderr|receipt`.
Logs are limited to 32 KB and scrub common token/authorization patterns. Clients
cannot provide paths or shell commands; the Studio collector resolves job IDs
against its own inventory. Plist environment values are never exported.

Tests: `.venv/bin/python -m pytest -q tests/test_studio_runs.py` covers timezone,
DST, receipt precedence, stale/offline policy, discovery failure isolation,
missing definitions, Nevada timing, log allowlisting and JavaScript syntax.
`tests/studio-runs-browser.cjs` exercises the rendered tab using Playwright;
set `STUDIO_RUNS_URL` for a preview and `PLAYWRIGHT_MODULE` if Playwright is not
in Node's normal module search path.

## UI verification notes

Product precedent: Brutus Nucleus native grid (same token stylesheet and grid
classes, shared native row ordering). Catalog reference: `shadcn-queue`.
The rendered tab was tested at 1440px and 390px with no page JavaScript errors
or viewport overflow. Independent browser checks exercised search/clear,
health filter, ascending/descending ordering, pagination, Nevada details,
loading, empty, error/retry, and stale/offline data. Shine's accessibility pass
reported zero axe violations and measured text contrast at least 6.93:1.
Its full aggregate certification is not claimed: the existing Brutus SSE page
prevents the tool's network-idle reference capture, and its strict shared-table
source verifier does not establish the Python-composed native UI's provenance.
