"""Authoritative local work surface from Linear when the retired Atlas ledger is offline."""

from __future__ import annotations

import os
from typing import Any

import httpx

TEAM_ID = "83fae885-2c96-4ea3-986b-c23d6ca71ea6"
OWNER_EMAIL = "justin.fowler@clearspeed.com"
QUERY = """
query AliciaWorkSurface($team: String!, $owner: String!) {
  team(id: $team) {
    issues(first: 50, orderBy: updatedAt, filter: {
      assignee: { email: { eq: $owner } }
      state: { type: { nin: ["completed", "canceled"] } }
    }) {
      nodes { identifier title priority updatedAt url state { name type } }
    }
  }
}
"""
CREATE_ISSUE = """
mutation AliciaCreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { identifier title url }
  }
}
"""
FIND_ISSUES = """
query AliciaFindIssues($team: String!, $title: String!) {
  team(id: $team) {
    issues(first: 20, orderBy: updatedAt, filter: { title: { containsIgnoreCase: $title } }) {
      nodes { identifier title url state { name type } }
    }
  }
}
"""


def _row(issue: dict[str, Any]) -> dict[str, Any]:
    state = issue.get("state") or {}
    return {
        "ticket": str(issue.get("identifier") or ""),
        "title": str(issue.get("title") or ""),
        "signal": str(state.get("name") or ""),
        "reason": str(state.get("name") or "Needs review"),
        "priority": int(issue.get("priority") or 0),
        "updated_at": str(issue.get("updatedAt") or ""),
        "link": str(issue.get("url") or ""),
        "source": "linear",
    }


def linear_work_surface(*, timeout_s: float = 8.0) -> dict[str, Any]:
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        raise RuntimeError("work surface unavailable")
    with httpx.Client(timeout=timeout_s) as client:
        response = client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": key, "Content-Type": "application/json"},
            json={"query": QUERY, "variables": {"team": TEAM_ID, "owner": OWNER_EMAIL}},
        )
        response.raise_for_status()
        payload = response.json()
    if payload.get("errors"):
        raise RuntimeError("work surface unavailable")
    issues = (((payload.get("data") or {}).get("team") or {}).get("issues") or {}).get("nodes")
    if not isinstance(issues, list):
        raise RuntimeError("work surface unavailable")

    rows = [_row(issue) for issue in issues if isinstance(issue, dict)]
    review = [row for row in rows if row["signal"].casefold() == "in review"]
    working = [row for row in rows if (row["signal"].casefold() == "in progress")]
    backlog = [row for row in rows if row not in review and row not in working]
    # Linear returned newest-first. Python's stable sort keeps that order inside
    # each priority bucket, so equally urgent work remains freshness-ranked.
    review.sort(key=lambda row: row["priority"] or 99)
    working.sort(key=lambda row: row["priority"] or 99)
    backlog.sort(key=lambda row: row["priority"] or 99)
    return {
        "headline": f"{len(review)} in review, {len(working)} in progress.",
        "needs_you": review,
        "working": working,
        "stuck": [],
        "queued": backlog[:8],
        "stuck_total": 0,
        "hidden": max(0, len(backlog) - 8),
        "alarm": {},
        "counts": {"needs_you": len(review), "working": len(working), "queued": len(backlog)},
        "actions": [],
        "justin_touchable_count": len(review),
        "include_probes": False,
        "source": "linear_direct",
    }


def create_linear_ticket(title: str, description: str, *, timeout_s: float = 12.0) -> dict[str, Any]:
    """Create exactly one Linear issue. Callers must enforce approval before this boundary."""
    clean_title = (title or "").strip()
    clean_description = (description or "").strip()
    if not clean_title or not clean_description:
        raise ValueError("title and description are required")
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Linear ticket creation unavailable")
    with httpx.Client(timeout=timeout_s) as client:
        response = client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": key, "Content-Type": "application/json"},
            json={
                "query": CREATE_ISSUE,
                "variables": {"input": {"teamId": TEAM_ID, "title": clean_title, "description": clean_description}},
            },
        )
        response.raise_for_status()
        payload = response.json()
    created = ((payload.get("data") or {}).get("issueCreate") or {})
    issue = created.get("issue") or {}
    if payload.get("errors") or not created.get("success") or not issue.get("identifier"):
        raise RuntimeError("Linear ticket creation failed")
    return {
        "ok": True,
        "created": True,
        "ticket": str(issue.get("identifier")),
        "title": str(issue.get("title") or clean_title),
        "url": str(issue.get("url") or ""),
        "source": "linear_direct",
    }


def find_linear_ticket_candidates(title: str, *, timeout_s: float = 8.0) -> list[dict[str, Any]]:
    """Read same-team issues near a proposed title before allowing creation."""
    query = (title or "").strip()
    if not query:
        raise ValueError("title is required")  # noqa: TRY004 — empty value, not wrong type
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Linear ticket lookup unavailable")
    with httpx.Client(timeout=timeout_s) as client:
        response = client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": key, "Content-Type": "application/json"},
            json={"query": FIND_ISSUES, "variables": {"team": TEAM_ID, "title": query}},
        )
        response.raise_for_status()
        payload = response.json()
    nodes = (((payload.get("data") or {}).get("team") or {}).get("issues") or {}).get("nodes")
    if payload.get("errors") or not isinstance(nodes, list):
        raise RuntimeError("Linear ticket lookup failed")
    exact = query.casefold()
    return [
        {
            "ticket_id": str(issue.get("identifier") or ""),
            "title": str(issue.get("title") or ""),
            "relationship": "exact" if str(issue.get("title") or "").strip().casefold() == exact else "related",
            "status": str((issue.get("state") or {}).get("type") or "open"),
            "evidence": "direct Linear title lookup",
        }
        for issue in nodes
        if isinstance(issue, dict) and issue.get("identifier")
    ]
