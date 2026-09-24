#!/usr/bin/env python3
"""Poll authenticated GitHub facts into Canon Evidence.

Alicia is loopback-only, so a public webhook cannot reach it. This job uses the
repo-scoped GitHub CLI credential and feeds the same strict receiver with facts
returned by GitHub's API. Delivery ids are derived from immutable object ids.
"""

from __future__ import annotations

import json
import os
import subprocess

from alicia.canon import CanonStore
from alicia.github_evidence import GitHubEvidenceReceiver
from alicia.paths import canon_db_path


REPOSITORY = os.environ.get("ALICIA_GITHUB_REPOSITORY", "justinfowler925/alicia")


def gh_json(args: list[str]) -> list[dict]:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=45, check=False
    )
    if result.returncode:
        reason = (result.stderr or result.stdout).strip().splitlines()[:1]
        raise RuntimeError(f"gh failed rc={result.returncode}: {reason[0] if reason else 'no reason'}")
    value = json.loads(result.stdout)
    if not isinstance(value, list):
        raise RuntimeError("gh response was not a list")
    return value


def poll() -> dict[str, int]:
    store = CanonStore(canon_db_path())
    receiver = GitHubEvidenceReceiver(store)
    counts = {"captured": 0, "duplicate": 0, "ignored": 0, "rejected": 0}
    try:
        prs = gh_json([
            "pr", "list", "--repo", REPOSITORY, "--state", "merged", "--limit", "50",
            "--json", "number,url,headRefName,title,body,mergeCommit,mergedAt",
        ])
        for pr in prs:
            sha = ((pr.get("mergeCommit") or {}).get("oid") or "").strip()
            number = str(pr.get("number") or "")
            result = receiver.handle(
                "pull_request",
                {
                    "action": "closed",
                    "repository": {"full_name": REPOSITORY},
                    "pull_request": {
                        "id": number,
                        "number": number,
                        "merged": True,
                        "merge_commit_sha": sha,
                        "html_url": pr.get("url"),
                        "head": {"ref": pr.get("headRefName")},
                        "title": pr.get("title"),
                        "body": pr.get("body"),
                    },
                },
                delivery_id=f"poll-pr-{number}-{sha}",
            )
            _count(counts, result.status)

        runs = gh_json([
            "run", "list", "--repo", REPOSITORY, "--limit", "50",
            "--json", "databaseId,headBranch,headSha,url,conclusion,status,workflowName,updatedAt",
        ])
        for run in runs:
            if run.get("status") != "completed":
                continue
            run_id = str(run.get("databaseId") or "")
            sha = str(run.get("headSha") or "")
            result = receiver.handle(
                "workflow_run",
                {
                    "action": "completed",
                    "repository": {"full_name": REPOSITORY},
                    "workflow_run": {
                        "id": run_id,
                        "head_branch": run.get("headBranch"),
                        "head_sha": sha,
                        "html_url": run.get("url"),
                        "conclusion": run.get("conclusion"),
                        "name": run.get("workflowName"),
                    },
                },
                delivery_id=f"poll-run-{run_id}-{sha}",
            )
            _count(counts, result.status)
    finally:
        store.close()
    return counts


def _count(counts: dict[str, int], status: str) -> None:
    key = status if status in counts else ("rejected" if status.startswith("rejected") else "ignored")
    counts[key] += 1


if __name__ == "__main__":
    print(json.dumps(poll(), sort_keys=True))
