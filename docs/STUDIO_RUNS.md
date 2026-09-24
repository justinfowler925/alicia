# Studio runs

Open **Studio** in the Alicia navigation, or `http://127.0.0.1:8768/#/studio`.
On mobile, use **More → Studio runs**. Search by job/feed name, filter by state,
then open **Details** for run evidence and the bounded latest/error log or receipt.
The default view and summary show **data feeds only** (10 at the September 9 audit).
Use **Show** to inspect maintenance, supporting services, sandbox jobs, inactive jobs,
and historical journals. Each summary counts only the selected category, before health/search filters.
New unclassified jobs are visible under **Needs classification**, with a notice in the default view.

## Authority and discovery

The `com.jfstudio.brutus-studio-runs` LaunchAgent runs a read-only collector on
Studio every minute. It writes a mode-0600 atomic snapshot under
`~/.local/share/brutus-studio-runs/`. It does not execute or modify monitored
jobs. Alicia reads that snapshot over SSH, with a 20-second request cache and a
persistent offline cache. Collection continues while the laptop is asleep.

Sources:

* User LaunchAgents and system LaunchDaemons whose labels start with
  `com.clearspeed.`, `com.jfstudio.`, `com.fowlerbrain.`, or `com.justinfowler.`.
  Supporting services are retained in their own category, outside the default feed list.
* Every `~/atlas-direct/state/cron/*.jsonl` receipt journal, including historical
  workflows in the History category. Cadence is explicitly unknown when the journal does not expose it.
* Nevada `~/Projects/reports/nv-sled-brief/studio-state.json`. The daily
  06:00 America/New_York report is distinct from its launchd polling/retry tick.
  Failure never borrows yesterday's completion time as this run's end time.
* SLED sweep timestamped stdout publication acknowledgements. Its start stamp
  identifies the successful run; completion time/duration are not invented.
* Optional registered receipt descriptors described below.

The September 9 initial inventory checked 48 launchd definitions and returned
64 jobs/services, including 16 Atlas journals. One malformed launchd plist
(`com.jfstudio.atlas-dispatch-gc`) is represented with an inspection error.
User crontab was empty. Alicia Canon inspection covered 211 evidence rows;
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

A launchd job using an existing prefix is discovered within a minute. Known production
feed families appear under Data feeds; unfamiliar jobs appear under Needs classification.
Set `"category": "feed"` in its descriptor to include it in the feed summary. Other values:
`service`, `maintenance`, `sandbox`, `inactive`, `history`, `unclassified`.
Classification never depends on success/failure; broken feeds remain visible.
No Alicia release is needed. Descriptive names, source, daily cadence overriding
a polling tick, precise receipts, or non-launchd feeds can be configured on Studio
in `~/.local/share/brutus-studio-runs/jobs.json`:

```json
{
  "com.jfstudio.example-feed": {
    "name": "Example daily feed",
    "category": "feed",
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

From the Alicia checkout run `./scripts/install-studio-runs.sh`, then the standard
Alicia `./scripts/deploy.sh` after landing the change. The installer replaces only
the observer; feed schedules, commands and payloads are unchanged. Override SSH
host with `ALICIA_STUDIO_SSH` in the installer and Alicia service if necessary.
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

Product precedent: Alicia Nucleus native grid (same token stylesheet and grid
classes, shared native row ordering). Catalog reference: `shadcn-queue`.
The rendered tab was tested at 1440px and 390px with no page JavaScript errors
or viewport overflow. Independent browser checks exercised search/clear,
health filter, ascending/descending ordering, pagination, Nevada details,
loading, empty, error/retry, and stale/offline data. Shine's accessibility pass
reported zero axe violations and measured text contrast at least 6.93:1.
Its full aggregate certification is not claimed: the existing Alicia SSE page
prevents the tool's network-idle reference capture, and its strict shared-table
source verifier does not establish the Python-composed native UI's provenance.

## September 9 cleanup

The original 65 entries were an infrastructure inventory, not 65 data feeds:
10 data feeds, 20 maintenance tasks, 13 supporting services, 16 historical Atlas
journals, 3 sandbox schedules and 3 unloaded definitions. The three sandbox
schedules were subsequently paused at Justin's request and retained under Inactive.
The 16 journals stopped when the old in-process Atlas scheduler was replaced in June;
they are not treated as current stale obligations. Revived/new journals require
classification rather than being silently archived.

Fixed the launchd parser to recognize annotated exits such as `78: EX_CONFIG`.
CRO history now preserves prior successful publication even while its current
scheduler fails. The observer does not repair or conceal unhealthy services.

Eight periodic production feeds now run through `scripts/studio-run-job.py`,
installed as `~/.local/share/brutus-studio-runs/run_job.py`. It writes atomic
running/terminal receipts, duration and real exit code, forwards termination,
and prevents overlapping invocations of the same job. The original command and
schedule remain intact. Nevada already has report receipts; the grant worker is
continuous. Registration uses the normal `receipt_path` descriptor. To wrap a new
job, prefix its launchd ProgramArguments with `/usr/bin/python3`, the wrapper path,
`--job`, its label, `--`, then the original arguments; register the resulting
`receipts/<label>.json`. Validate the original runner propagates real failures.

Studio repair backups are in
`~/.local/share/brutus-studio-runs/repair-backup-20260909/`.
Paused sandbox plists are in `paused-sandbox-20260909/` beside that directory;
restore a definition to `~/Library/LaunchAgents/` and bootstrap it to resume.
Scripts, logs, receipts and historical journals were preserved.

## Publication-only verification

A receipt may include `verification_scope` to disclose omitted steps, such as notifications disabled during an operator-approved data publication check. The scope appears in Details and remains in the receipt.

For a read-only diagnostic that did not execute the feed, register `verification_receipt_path` separately from `receipt_path`. Alicia shows the diagnostic result and timestamp in Details while preserving the actual scheduled-run status and last successful publication. A passing diagnostic must not turn a failed publication green.
