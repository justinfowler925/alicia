"""Manager assign → join/poll → Canon handback → one status surface.

Alicia chooses a platform lane, runs it (join when possible), writes an honest
receipt + Canon Run, and updates the work item. Local lane failure never
promotes to Cursor Cloud / OpenAI / Claude.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .canon import CanonStore, Evidence, Run, WorkItem
from .canon.models import EvidenceType, RunStatus
from .canon_binding import intake_work, project_rollups
from .config import AliciaCfg
from .earned_autonomy import record_success
from .manager_status import merged_work_surface
from .paths import canon_db_path
from .platform_registry import (
    assert_honest_attribution,
    choose_lane,
    executor_for,
    lane_meta,
    normalize_lane,
    registry_snapshot,
)
from .specialist_router import _RECEIPTS, _write_receipt

_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted", "blocked", "ready"})


def _persist_receipt(receipt: dict[str, Any]) -> Path:
    _RECEIPTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    lane = str(receipt.get("lane") or receipt.get("specialist") or "unknown")
    path = _RECEIPTS / f"{int(time.time())}-{lane}-manager.json"
    return _write_receipt(receipt, path=path)


def _handback_canon(
    store: CanonStore,
    *,
    work_item_id: str,
    executor: str,
    lane: str,
    result: dict[str, Any],
    receipt_path: str,
) -> dict[str, Any]:
    work = store.get(WorkItem, work_item_id)
    if work is None:
        raise ValueError(f"work item {work_item_id!r} not found")

    ok = bool(result.get("ok"))
    run = Run(
        actor=executor,
        work_item_id=work_item_id,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=RunStatus.READY_FOR_REVIEW if ok else RunStatus.FAILED,
        target=lane,
        scope=f"manager_loop:{lane}",
    )
    store.save(run)

    evidence = Evidence(
        type=EvidenceType.RUN_OUTPUT,
        captured_by=executor,
        captured_by_kind="worker",
        linked_object_id=run.id,
        content_ref=receipt_path,
        verified=False,
        metadata={
            "lane": lane,
            "executor": executor,
            "ok": ok,
            "reply_excerpt": str(result.get("reply") or result.get("answer_excerpt") or "")[:500],
            "model": str(result.get("model") or ""),
        },
    )
    store.save(evidence)
    if evidence.id not in run.evidence_refs:
        run.evidence_refs.append(evidence.id)
        store.save(run)
    if evidence.id not in work.evidence_refs:
        work.evidence_refs.append(evidence.id)

    work.assignee = executor
    work.bindings = {
        **(work.bindings or {}),
        "executor": executor,
        "lane": lane,
        "last_run_id": run.id,
        "last_receipt": receipt_path,
        "owned_by": "alicia",
    }
    store.save(work)
    return {
        "run_id": run.id,
        "evidence_id": evidence.id,
        "work_item_id": work.id,
        "project_id": work.project_id,
        "executor": executor,
        "lane": lane,
    }


def _run_forge(cfg: AliciaCfg, message: str, cwd: str = "") -> dict[str, Any]:
    from .tools import _ask_forge

    return _ask_forge(cfg, message, cwd=cwd)


def _run_cursor_local(cfg: AliciaCfg, message: str, repo_hint: str) -> dict[str, Any]:
    from .tools import _ask_cursor

    return _ask_cursor(cfg, message, repo_hint=repo_hint)


def _run_cursor_cloud(cfg: AliciaCfg, message: str) -> dict[str, Any]:
    from .tools import _ask_cursor_cloud

    return _ask_cursor_cloud(cfg, message)


def _run_openai(cfg: AliciaCfg, message: str) -> dict[str, Any]:
    from . import openai_lane

    return openai_lane.run_openai(cfg, message)


def _run_claude(cfg: AliciaCfg, message: str) -> dict[str, Any]:
    from .claude import ask_claude

    claude_cfg = cfg.claude
    if claude_cfg is None or not getattr(claude_cfg, "enabled", False):
        return {
            "ok": False,
            "executor": "claude",
            "error": "Claude platform lane disabled (claude.enabled=false). Opt-in only.",
        }
    if not getattr(claude_cfg, "platform_lane", False):
        return {
            "ok": False,
            "executor": "claude",
            "error": (
                "Claude platform lane requires claude.platform_lane=true "
                "(hosted opt-in; not Alicia's reasoning brain)."
            ),
        }
    out = ask_claude(cfg, message)
    if isinstance(out, dict):
        out.setdefault("executor", "claude")
        out.setdefault("attribution", "Claude")
        if out.get("ok"):
            assert_honest_attribution(out, lane="claude")
    return out


def _run_studio(
    specialist: str,
    message: str,
    *,
    cwd: str = "",
    work_item: str = "",
    read_only: bool = True,
    join: bool = True,
    wait_s: float = 120.0,
) -> dict[str, Any]:
    from .specialist_router import route_specialist

    launched = route_specialist(
        message,
        specialist=specialist,
        dry_run=False,
        cwd=cwd,
        work_item=work_item,
        read_only=read_only,
    )
    result = launched.get("result") if isinstance(launched.get("result"), dict) else {}
    run_id = str(result.get("run_id") or "")
    payload: dict[str, Any] = {
        "ok": bool(launched.get("ok")),
        "executor": specialist,
        "attribution": lane_meta(specialist)["label"],
        "run_id": run_id,
        "status": result.get("status") or "queued",
        "launcher": result.get("launcher"),
        "read_only": read_only,
        "reply": "",
    }
    if not join or not run_id:
        return payload

    # Poll studio-agent status until terminal or timeout.
    studio = Path.home() / ".local/bin/studio-agent"
    deadline = time.time() + max(5.0, wait_s)
    while time.time() < deadline:
        try:
            import subprocess

            proc = subprocess.run(
                [str(studio), "status", run_id],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                try:
                    st = json.loads(proc.stdout or "{}")
                except json.JSONDecodeError:
                    st = {}
                status = str(st.get("status") or "").lower()
                payload["status"] = status or payload["status"]
                if status in _TERMINAL:
                    answer = Path.home() / ".local/share/studio-agents/runs" / run_id / "answer.md"
                    if answer.is_file():
                        payload["reply"] = answer.read_text()[:4000]
                    payload["ok"] = status in {"succeeded", "ready"}
                    break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2.0)
    return payload


def _run_scout_import(cfg: AliciaCfg) -> dict[str, Any]:
    """Prefer scout_import.import_once for a safe Scout proof (no prod mutation)."""
    from . import scout_import

    # Dry structural probe: call import_once only when explicitly allowed via env.
    # Default path records a read-only capabilities receipt without mutating.
    if not getattr(cfg, "_allow_scout_import_live", False):
        return {
            "ok": True,
            "executor": "scout",
            "attribution": "Scout",
            "status": "succeeded",
            "reply": "scout_import path available; live import_once gated",
            "path": "scout_import.import_once",
            "read_only": True,
        }
    # Live path left for verify script with explicit flag.
    try:
        out = scout_import.import_once(cfg=cfg, todos=None, zoom_store=None)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "executor": "scout",
            "error": str(exc),
            "attribution": "Scout",
        }
    return {
        "ok": True,
        "executor": "scout",
        "attribution": "Scout",
        "status": "succeeded",
        "reply": json.dumps(out, default=str)[:2000],
        "read_only": True,
    }


def assign_and_run(
    cfg: AliciaCfg,
    *,
    task: str,
    lane: str | None = None,
    work_item_id: str = "",
    project_hint: str = "",
    repo_hint: str = "",
    cwd: str = "",
    read_only: bool = True,
    join: bool = True,
    wait_s: float = 120.0,
    store: CanonStore | None = None,
) -> dict[str, Any]:
    """Full manager loop for one task: ensure Canon work, run lane, handback, status."""
    task = (task or "").strip()
    if not task:
        raise ValueError("task is required")

    chosen, reason = choose_lane(task, lane=lane, repo_hint=repo_hint)
    meta = lane_meta(chosen)
    executor = executor_for(chosen)

    # Hosted lanes require explicit lane argument (never auto-chosen by policy).
    if meta["hosted"] and normalize_lane(lane) != chosen:
        raise ValueError(
            f"hosted lane {chosen!r} requires explicit Justin opt-in (pass lane=)"
        )

    own = store is None
    store = store or CanonStore(canon_db_path())
    try:
        if work_item_id:
            work = store.get(WorkItem, work_item_id)
            if work is None:
                raise ValueError(f"work item {work_item_id!r} not found")
            wid = work.id
            project_id = work.project_id
        else:
            intake = intake_work(
                task, source="alicia:manager_loop", project_hint=project_hint, store=store
            )
            wid = str(intake["work_item_id"])
            project_id = intake.get("project_id")

        # Execute lane — local failure must NOT promote to hosted.
        try:
            if chosen == "forge_local":
                result = _run_forge(cfg, task, cwd=cwd)
            elif chosen == "cursor_local":
                result = _run_cursor_local(cfg, task, repo_hint or "alicia")
            elif chosen == "cursor_cloud":
                result = _run_cursor_cloud(cfg, task)
            elif chosen == "openai":
                result = _run_openai(cfg, task)
            elif chosen == "claude":
                result = _run_claude(cfg, task)
            elif chosen == "scout" and read_only:
                result = _run_scout_import(cfg)
            elif chosen in {"atlas", "scout", "hollywood"}:
                result = _run_studio(
                    chosen,
                    task,
                    cwd=cwd,
                    work_item=wid,
                    read_only=read_only,
                    join=join,
                    wait_s=wait_s,
                )
            else:
                result = {"ok": False, "executor": executor, "error": f"unhandled lane {chosen}"}
        except Exception as exc:  # noqa: BLE001 — fail closed, no hosted promotion
            result = {
                "ok": False,
                "executor": executor,
                "error": str(exc),
                "hint": "Local/Studio lane failed; Alicia will not promote to hosted platforms.",
            }

        if not isinstance(result, dict):
            result = {"ok": False, "executor": executor, "error": "invalid lane result"}
        result.setdefault("executor", executor)
        assert_honest_attribution(result, lane=chosen)

        receipt = {
            "ok": bool(result.get("ok")),
            "dry_run": False,
            "lane": chosen,
            "specialist": chosen if chosen in {"atlas", "scout", "hollywood", "forge"} else chosen,
            "reason": reason,
            "task": task[:4000],
            "work_item_id": wid,
            "project_id": project_id,
            "executor": executor,
            "attribution": meta["label"],
            "launched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "result": result,
            "run_status": result.get("status") or ("succeeded" if result.get("ok") else "failed"),
            "accepted": False,
            "note": "Manager join/handback — process success is not delivery acceptance",
        }
        path = _persist_receipt(receipt)
        handback = _handback_canon(
            store,
            work_item_id=wid,
            executor=executor,
            lane=chosen,
            result=result,
            receipt_path=str(path),
        )

        if result.get("ok") and meta.get("earn_eligible"):
            # Map lane to a safe earn class for successful read-only / trivial runs.
            earn_class = {
                "forge_local": "forge_trivial",
                "atlas": "atlas_read_only",
                "scout": "scout_import" if read_only else "scout_read_only",
                "hollywood": "hollywood_inspect",
            }.get(chosen)
            if earn_class:
                record_success(chosen, earn_class)

        status = merged_work_surface(timeout_s=4.0)
        rollups = project_rollups(store)
        return {
            "ok": bool(result.get("ok")),
            "lane": chosen,
            "reason": reason,
            "executor": executor,
            "attribution": meta["label"],
            "work_item_id": wid,
            "project_id": project_id,
            "receipt_path": str(path),
            "handback": handback,
            "result": {
                k: result.get(k)
                for k in (
                    "ok",
                    "reply",
                    "error",
                    "model",
                    "status",
                    "run_id",
                    "bc_id",
                    "executor",
                    "attribution",
                    "hint",
                )
                if k in result
            },
            "status_headline": status.get("headline"),
            "status_source": status.get("source"),
            "project_rollups": [
                r for r in rollups if r.get("id") == project_id or r.get("work_item_count", 0) > 0
            ][:8],
            "platforms": registry_snapshot()["platforms"],
            "accepted": False,
            "note": "Alicia owns this work until done or escalated; process success ≠ acceptance",
        }
    finally:
        if own:
            store.close()
