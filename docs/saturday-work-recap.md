# Saturday work recap

Justin requested a private summary of projects, PRs, features, fixes, creations,
new projects, deployments and changes, available Saturdays at 09:00 America/Chicago.

The existing Studio-owned `com.jfstudio.weekly-workflow-efficiency` job starts at
08:50 Central to prepare the recap before the 09:00 Codex notification. This replaces
its previous 08:00 schedule. Studio's system timezone is America/Chicago; launchd
calendar intervals follow that system timezone (the process TZ alone does not set
launchd's clock). Preserve the Studio timezone when maintaining this job.

`weekly-workflow-efficiency.py` retains its original efficiency JSON and calls
`weekly_work_recap.py`. The recap reads both configured GitHub accounts using their
existing gh credentials without changing the globally selected account. Sources:

- authored PRs updated in the seven complete Central calendar days before Saturday;
- default-branch commits filtered to the two configured authors;
- new repositories, published releases and successful GitHub deployment records;
- metadata for reports and media modified in Studio's reports, mflux-out and Sites;
- optional existing laptop activity/Canon coverage, explicitly unavailable when offline.

PRs, commits, releases and deployments overlap: their totals are never summed as
unique outcomes. Title-based categories are labels, not independent feature acceptance.
Provider deployment success does not prove current live behavior, and a missing
GitHub deployment record does not mean no deployment occurred. Local-only work,
unlinked Git authors and other artifact folders remain coverage limitations.

Private outputs live outside the repository under
`~/.local/share/workflow-efficiency/reports/work/` on Studio. Dated JSON and HTML,
`index.html`, and `archive.html` are written atomically. The private page launch agent
binds only to Studio's tailnet address on port 8377. No report is published to the
public portfolio, sent to Slack, or emailed. The Codex heartbeat presents the new
report in the originating task; laptop availability affects notification timing,
not Studio generation. Stale reports must never be presented as current.

Install with `scripts/install-weekly-workflow-efficiency.sh`. Verify both launchd
jobs, the Central calendar schedule, the dated report, private HTTP response, and
actual browser search/archive/source navigation. The first run is execution proof;
a future unattended Saturday is still a future observation.

Test with `python -m pytest -q tests/test_weekly_efficiency.py tests/test_weekly_work_recap.py`.
