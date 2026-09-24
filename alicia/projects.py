"""Live view of Justin's actual project streams — read straight from git.

The work surface must cover more than Linear tickets. The inventory that
grounds this: 5 of the 10 most active projects have NO plan doc at all — their
state lives only in git log. So the honest source for "what am I working on"
is the repos themselves: last commit, current branch, uncommitted files,
unpushed commits. Alicia runs on the laptop, where ~/Projects lives, so this
is the one surface that can see it directly.

Read-only. Never touches a working tree.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECTS_ROOT = Path.home() / "Projects"
_CACHE: dict[str, Any] = {"at": 0.0, "data": []}
_CACHE_TTL_S = 90.0

# Directories that are app data / artifacts, not projects. Non-git dirs are
# skipped anyway; this also hides git repos that are not really "work".
_SKIP = {"sfdc-wip-stash", "sfdc-local-artifacts", "_runners"}


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _age_phrase(epoch: float) -> str:
    if not epoch:
        return ""
    d = max(0.0, time.time() - epoch)
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 172800:
        return f"{int(round(d / 3600))}h ago"
    return f"{int(round(d / 86400))}d ago"


def canonical_remote(value: str) -> str:
    """Stable project identity for HTTPS, SSH, and local Git remotes."""
    raw = (value or "").strip().removesuffix(".git")
    if not raw:
        return ""
    if raw.startswith("git@") and ":" in raw:
        host, path = raw[4:].split(":", 1)
        return f"{host}/{path}".strip("/").lower()
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        host = parsed.hostname or parsed.netloc
        return f"{host}/{parsed.path.strip('/')}".lower()
    return raw.lower()


def _scan_one(repo: Path) -> dict[str, Any] | None:
    if not (repo / ".git").exists():
        return None
    last = _git(repo, "log", "-1", "--format=%ct|%s")
    if not last or "|" not in last:
        return None
    ts_s, subject = last.split("|", 1)
    try:
        ts = float(ts_s)
    except ValueError:
        ts = 0.0
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    remote = canonical_remote(_git(repo, "remote", "get-url", "origin"))
    dirty = len([ln for ln in _git(repo, "status", "--porcelain").splitlines() if ln.strip()])
    ahead = 0
    never_pushed = False
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        counts = _git(repo, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
        parts = counts.split()
        if len(parts) == 2:
            try:
                ahead = int(parts[1])
            except ValueError:
                ahead = 0
    elif branch not in ("main", "master", "?"):
        # A feature branch with NO upstream is the riskiest state of all: its
        # commits exist only on this laptop, and "unpushed=0" hid exactly the
        # four sfdc worktrees the inventory flagged. Count commits not on any
        # remote branch instead.
        not_on_remote = _git(repo, "rev-list", "--count", "HEAD", "--not", "--remotes")
        try:
            ahead = int(not_on_remote) if not_on_remote else 0
        except ValueError:
            ahead = 0
        never_pushed = ahead > 0
    days = (time.time() - ts) / 86400 if ts else 999
    return {
        "name": repo.name,
        "path": str(repo),
        "project_id": remote or f"local/{repo.name.lower()}",
        "remote": remote,
        "workspace": repo.name,
        "is_worktree": (repo / ".git").is_file(),
        "branch": branch,
        "last_commit": subject[:100],
        "last_commit_epoch": ts,
        "age": _age_phrase(ts),
        "dirty": dirty,
        "unpushed": ahead,
        "never_pushed": never_pushed,
        # attention flags a human cares about, precomputed so the UI stays dumb
        "at_risk": dirty > 0 or ahead > 0,
        "activity": "hot" if days <= 7 else ("warm" if days <= 30 else "cold"),
    }


def scan_projects(root: Path | None = None, *, force: bool = False) -> list[dict[str, Any]]:
    """All git repos under ~/Projects, newest activity first. Cached ~90s."""
    now = time.time()
    if not force and _CACHE["data"] and now - _CACHE["at"] < _CACHE_TTL_S:
        return _CACHE["data"]
    base = Path(root) if root else PROJECTS_ROOT
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(repo: Path, *, display_name: str | None = None) -> None:
        key = str(repo.resolve())
        if key in seen:
            return
        seen.add(key)
        info = _scan_one(repo)
        if not info:
            return
        if display_name:
            info["name"] = display_name
        out.append(info)

    if base.is_dir():
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP:
                continue
            add(child)
            # Worktree collections are real workspaces, but not projects of
            # their own. Discover them generically and let `project_id` group
            # every checkout that points at the same remote.
            if child.name.endswith("-wt") or child.name.endswith("-worktrees"):
                for workspace in sorted(child.iterdir()):
                    if workspace.is_dir() and not workspace.name.startswith("."):
                        add(workspace, display_name=f"{child.name}/{workspace.name}")
    out.sort(key=lambda p: -p["last_commit_epoch"])
    _CACHE["at"], _CACHE["data"] = now, out
    return out
