"""Tool registry for Brutus chat — lookup-first, hallucination-resistant.

Tools return structured data. The chat model only decides which tool to use and
summarizes the result. It never invents facts itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent_sessions import (
    active_counts,
    filter_cockpit,
    merge_overlays,
    scan_agent_sessions,
)
from .client import AtlasClient
from .config import BrutusCfg
from .cursor_runner import run_cursor_chat
from .focus import spoken_next_decision
from .linear_surface import (
    create_linear_ticket,
    find_linear_ticket_candidates,
    linear_work_surface,
)
from .memory import MemoryStore
from .model_gateway import judge_with_profile, run_profile
from .nucleus import build_nucleus_snapshot, invalidate_nucleus_cache, nucleus_view
from .supervisor_runtime import SupervisorRuntime
from .todos import STATUSES, Todo, TodoStore
from .unfog_compiler import UnfogContract, compile_work

ToolFn = Callable[..., dict[str, Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: ToolFn

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def _schema_arg_error(tool: Tool, args: dict[str, Any]) -> str | None:
    """Return a human-readable error if `args` don't match the tool's schema."""
    schema = tool.parameters or {}
    props: dict[str, Any] = schema.get("properties", {}) or {}
    allowed = set(props)
    required = set(schema.get("required", []) or [])
    unknown = sorted(set(args) - allowed)
    missing = sorted(required - set(args))
    if not unknown and not missing:
        return None
    parts = []
    if unknown:
        parts.append(f"does not accept {', '.join(unknown)}")
    if missing:
        parts.append(f"requires {', '.join(missing)}")
    return (
        f"{tool.name} {' and '.join(parts)}. "
        f"Valid arguments: {', '.join(sorted(allowed)) or 'none'}. "
        "The tool did not run — nothing happened."
    )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def discard(self, name: str) -> None:
        self._tools.pop(name, None)

    def list_schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "broken": True, "error": f"unknown tool {name}"}
        # A bad-arguments call is NOT a result — it means the tool never ran.
        # Returning it as a plain {"ok": False} made it indistinguishable from a
        # legitimate negative result, so the model narrated around it and told
        # Justin the work was done.
        #
        # Validate against the declared `parameters` schema rather than the
        # Python signature: most tools are registered as `lambda **kwargs`, so
        # inspect.signature().bind() accepts anything and the TypeError only
        # surfaces later, from inside the wrapped call. The schema is also what
        # the model is shown, so it is the honest contract to hold it to.
        bad = _schema_arg_error(tool, args)
        if bad:
            return {"ok": False, "broken": True, "error": bad}
        try:
            return {"ok": True, "result": tool.fn(**args)}
        except TypeError as exc:
            # Schema said yes but the function disagreed — the schema and the
            # implementation have drifted. test_tool_schemas_match_signatures
            # exists to stop this reaching production; if it fires anyway, it is
            # still a call that did not happen, so it is still `broken`.
            return {
                "ok": False,
                "broken": True,
                "error": (
                    f"{name} rejected its arguments ({exc}). The tool did not run — "
                    "nothing happened. Its schema and its implementation disagree."
                ),
            }
        except Exception as exc:  # noqa: BLE001 — tool failures must return gracefully to the model
            return {"ok": False, "error": str(exc)}


def _work_surface(
    client: AtlasClient,
    cfg: BrutusCfg | None = None,
    *,
    include_probes: bool = False,
) -> dict[str, Any]:
    """Probe-filtered board by default — same view as the Work tab.

    Attaches focus actions so chat and voice see the same batched decisions
    the screen leads with, not seventeen raw needs-you rows.
    """
    _ = (client, include_probes)
    surface = linear_work_surface(timeout_s=(cfg.timeout_s if cfg else 8.0))
    surface["next_decision"] = spoken_next_decision(surface)
    surface["atlas_ignored"] = True
    return surface


def _get_digest(
    client: AtlasClient,
    cfg: BrutusCfg | None = None,
    *,
    include_probes: bool = False,
) -> dict[str, Any]:
    """WIP digest for chat — probe-filtered board first; raw markdown capped."""
    try:
        surface = _work_surface(client, cfg, include_probes=include_probes)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "atlas6_unreachable": True}
    return {
        "ok": True,
        "source": "linear_direct",
        **surface,
        "digest_excerpt": "",
        "note": "Direct Linear work surface; Atlas is intentionally ignored.",
    }


def _dispatch_tick(
    client: AtlasClient,
    *,
    dry_run: bool = True,
    ingest_linear: bool = False,
) -> dict[str, Any]:
    """Ask Studio for one dispatcher tick. Chat defaults to dry_run=true (safe)."""
    try:
        raw = client.dispatch_tick(dry_run=bool(dry_run), ingest_linear=bool(ingest_linear))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "atlas6_unreachable": True}
    if not isinstance(raw, dict):
        return {"ok": True, "dry_run": bool(dry_run), "result": str(raw)[:1500]}
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "summary": str(raw.get("summary") or "")[:500],
        "dispatched": raw.get("dispatched") or raw.get("count") or raw.get("n"),
        "result": {k: raw.get(k) for k in ("ok", "summary", "actions", "skipped", "errors") if k in raw},
    }


def _approve_gate(client: AtlasClient, ticket: str, decision: str = "approve") -> dict[str, Any]:
    """Approve or reject a gate that is waiting on Justin.

    This exists because the system prompt has always told Justin to say
    "approve <ticket>" while no such tool was registered anywhere. The phrase
    fell through to `get_thread` — a read — and the model reported the approval
    as done. A prompt that prescribes a phrase is a contract; this is the other
    half of it.
    """
    decision = (decision or "approve").strip().lower()
    if decision not in {"approve", "reject"}:
        return {"ok": False, "error": f"decision must be approve or reject, got {decision!r}"}
    ticket = (ticket or "").strip()
    if not ticket:
        return {"ok": False, "error": "ticket is required"}
    try:
        raw = client.approve(ticket, decision=decision)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "atlas6_unreachable": True}
    # Report what the ledger says came back, not what we asked for.
    state = (raw or {}).get("status") or (raw or {}).get("state") or ""
    return {
        "ok": True,
        "ticket": ticket,
        "decision": decision,
        "status_after": state,
        "raw": raw,
    }


def _reconcile(client: AtlasClient) -> dict[str, Any]:
    """Reconcile in_flight threads against Atlas5 verified-handback receipts."""
    try:
        raw = client.reconcile()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "atlas6_unreachable": True}
    if not isinstance(raw, dict):
        return {"ok": True, "result": str(raw)[:1500]}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    closed = data.get("closed") if isinstance(data, dict) else None
    if closed is None and isinstance(data, dict):
        closed = data.get("reconciled") or data.get("count")
    return {
        "ok": True,
        "closed": closed,
        "summary": str(raw.get("summary") or data.get("summary") or "")[:500]
        if isinstance(data, dict)
        else "",
        "result": {k: raw.get(k) for k in ("ok", "closed", "reconciled", "errors") if k in raw},
    }


def _answer_steering(
    client: AtlasClient,
    ticket_id: str,
    body: str,
    scope: str = "next_turn",
) -> dict[str, Any]:
    """Answer an Atlas5 awaiting_input question (steering) for a ticket."""
    tid = (ticket_id or "").strip().upper()
    text = (body or "").strip()
    if not tid or not text:
        return {"ok": False, "error": "ticket_id and body are required"}
    try:
        raw = client.answer_steering(tid, text, scope=scope or "next_turn")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    if not isinstance(raw, dict):
        return {"ok": True, "ticket_id": tid, "result": str(raw)[:1000]}
    return {
        "ok": raw.get("ok", True) is not False,
        "ticket_id": tid,
        "resumed": raw.get("resumed"),
        "error": raw.get("error") or "",
        "result": {k: raw.get(k) for k in ("ok", "resumed", "error", "state") if k in raw},
    }


def _list_threads(client: AtlasClient) -> dict[str, Any]:
    return client.list_threads()


def _get_thread(client: AtlasClient, external_id: str = "", thread_id: str = "") -> dict[str, Any]:
    external_id = (external_id or "").strip().upper()
    thread_id = (thread_id or "").strip()
    if not external_id and not thread_id:
        return {"ok": False, "error": "external_id or thread_id required"}
    body = client.list_threads()
    threads = body.get("threads") or []
    for t in threads:
        if external_id and (t.get("external_id") or "").upper() == external_id:
            return t
        if thread_id and str(t.get("id") or "") == thread_id:
            return t
    return {"ok": False, "error": f"no open thread for {external_id or thread_id}"}


def _register_thread(
    client: AtlasClient,
    title: str,
    goal: str = "",
    external_id: str = "",
) -> dict[str, Any]:
    if not title:
        return {"ok": False, "error": "title is required"}
    return client.register(title, external_id=external_id or None, goal=goal)


def _check_email(client: AtlasClient, limit: int = 10) -> dict[str, Any]:
    """Capped Gmail peek via Atlas6 — never dumps a full inbox into chat."""
    try:
        return client.peek_gmail(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "hint": "Needs Studio GOOGLE_OAUTH_ACCESS_TOKEN; ingest stays separate (brutus ingest-gmail).",
        }


def _check_slack(client: AtlasClient, limit: int = 10) -> dict[str, Any]:
    """Capped Slack peek via Atlas6 — filtered work signals only."""
    try:
        return client.peek_slack(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "hint": "Needs Studio SLACK_BOT_TOKEN + channel_ids; ingest stays separate (brutus ingest-slack).",
        }


def _slim_atlas6_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep actionable fields; drop digest dumps that drown the chat model."""
    reply = str(raw.get("reply") or raw.get("message") or raw.get("text") or "").strip()
    if len(reply) > 2500:
        reply = reply[:2499] + "…"
    out: dict[str, Any] = {"ok": True, "reply": reply}
    for key in ("path", "source", "conversation_id"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    skill = raw.get("skill")
    if isinstance(skill, dict):
        slim_skill: dict[str, Any] = {}
        for key in ("route", "ticket_id", "external_id", "next_action", "status", "title"):
            if skill.get(key) not in (None, ""):
                slim_skill[key] = skill[key]
        # Never forward the unfiltered WIP digest into chat synthesis.
        if slim_skill:
            out["skill"] = slim_skill
    if raw.get("atlas5_busy") is not None:
        out["atlas5_busy"] = bool(raw.get("atlas5_busy"))
    return out


def _ask_atlas6(client: AtlasClient, message: str, mode: str = "manager", ticket_id: str = "") -> dict[str, Any]:
    """Route a question or command to the Atlas6 orchestrator."""
    try:
        raw = client.chat(message, mode=mode, persona="brutus", ticket_id=ticket_id or None)
    except Exception as exc:  # noqa: BLE001 — chat must survive Studio outages
        return {
            "ok": False,
            "atlas6_unreachable": True,
            "error": str(exc),
            "hint": (
                "Studio Atlas6 is unreachable. For coding use ask_cursor (allowlisted repos). "
                "For drafting/research use ask_claude. Ledger status needs Studio back."
            ),
        }
    if not isinstance(raw, dict):
        return {"ok": True, "reply": str(raw)[:2500]}
    if raw.get("ok") is False or raw.get("atlas6_unreachable"):
        return {
            "ok": False,
            "atlas6_unreachable": True,
            "error": str(raw.get("error") or "Atlas6 unreachable"),
            "hint": (
                "Studio Atlas6 is unreachable. For coding use ask_cursor; "
                "for drafting/research use ask_claude."
            ),
            "raw": _slim_atlas6_result(raw),
        }
    return _slim_atlas6_result(raw)


def _ask_cursor(cfg: BrutusCfg, message: str, repo_hint: str = "") -> dict[str, Any]:
    """Route complex coding to a one-shot Cursor SDK run on an allowlisted cwd."""
    return run_cursor_chat(cfg, message, repo_hint=repo_hint)


def _ask_claude(cfg: BrutusCfg, message: str) -> dict[str, Any]:
    """Compatibility stub: Cursor is Brutus's only reasoning backend."""
    _ = (cfg, message)
    return {"ok": False, "error": "Claude is disabled; Brutus reasons through Cursor"}


def _save_working_note(memory: MemoryStore, topic: str, body: str = "", ticket_ids: list[str] | None = None) -> dict[str, Any]:
    return memory.add_working_note(topic, body, ticket_ids=ticket_ids or []).to_dict()


def _list_working_notes(memory: MemoryStore, q: str = "", limit: int = 20) -> dict[str, Any]:
    notes = (
        memory.search_working_notes(q, limit=limit)
        if (q or "").strip()
        else memory.list_working_notes(limit=limit)
    )
    return {"ok": True, "notes": [n.to_dict() for n in notes], "count": len(notes)}


def _list_notes(todos: TodoStore, include_done: bool = False, q: str = "") -> dict[str, Any]:
    items = todos.find(q, include_done=include_done) if (q or "").strip() else todos.list(include_done=include_done)
    return {"ok": True, "notes": [t.to_dict() for t in items], "count": len(items)}


_LANE_ALIASES = {
    "inbox": "Inbox",
    "todo": "Inbox",
    "doing": "In Progress",
    "in progress": "In Progress",
    "progress": "In Progress",
    "blocked": "Blocked",
    "done": "Done",
    "finished": "Done",
    "complete": "Done",
}


def _normalise_lane(raw: str) -> str | None:
    key = (raw or "").strip().lower()
    return _LANE_ALIASES.get(key)


def _find_note(
    todos: TodoStore, note_id: str = "", q: str = ""
) -> tuple[Todo | None, dict[str, Any] | None]:
    """Resolve one note by id or phrase. Second value is an error payload."""
    hit: Todo | None = None
    nid = (note_id or "").strip()
    if nid:
        hit = todos.get(nid)
    if hit is None and (q or "").strip():
        found = todos.find(q, include_done=True)
        if len(found) == 1:
            hit = found[0]
        elif len(found) > 1:
            return None, {
                "ok": False,
                "error": "multiple notes match — pass note_id",
                "matches": [t.to_dict() for t in found[:8]],
            }
    if hit is None and nid:
        found = todos.find(nid, include_done=True)
        hit = found[0] if len(found) == 1 else None
    if hit is None:
        return None, {"ok": False, "error": "no matching note — use list_notes or pass note_id"}
    return hit, None


def _capture_note(todos: TodoStore, text: str, tags: str = "", lane: str = "") -> dict[str, Any]:
    try:
        t = todos.add(text, tags=tags or "", lane=lane or "")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "note": t.to_dict(),
        "action": "upsert",
        "hint": "On Ideas. Say 'promote <id>' to send to the ledger.",
    }


def _update_note(
    todos: TodoStore,
    note_id: str = "",
    q: str = "",
    text: str = "",
    lane: str = "",
    status: str = "",
) -> dict[str, Any]:
    """Rename or move an Ideas-pad note. Free write — notepad, not ledger."""
    hit, err = _find_note(todos, note_id=note_id, q=q)
    if err:
        return err
    assert hit is not None
    lane_n = _normalise_lane(lane) if lane else ""
    status_n = (status or "").strip().lower()
    if status_n and status_n not in STATUSES:
        # Spoken "done" often arrives as status=done rather than lane=Done.
        lane_from_status = _normalise_lane(status_n)
        if lane_from_status:
            lane_n = lane_from_status
            status_n = ""
        else:
            return {"ok": False, "error": f"status must be one of {STATUSES}"}
    if not (text or "").strip() and not lane_n and not status_n:
        return {"ok": False, "error": "nothing to update — pass text, lane, or status"}
    try:
        updated = todos.update(
            hit.id,
            text=(text.strip() if (text or "").strip() else None),
            lane=(lane_n or None),
            status=(status_n or None),
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if updated is None:
        return {"ok": False, "error": "no matching note — use list_notes or pass note_id"}
    return {"ok": True, "note": updated.to_dict(), "action": "upsert"}


def _delete_note(todos: TodoStore, note_id: str = "", q: str = "") -> dict[str, Any]:
    """Remove an Ideas-pad note. Gated — destructive."""
    hit, err = _find_note(todos, note_id=note_id, q=q)
    if err:
        return err
    assert hit is not None
    snapshot = hit.to_dict()
    if not todos.delete(hit.id):
        return {"ok": False, "error": "could not delete note"}
    return {"ok": True, "note_id": hit.id, "note": snapshot, "action": "delete"}


def _promote_note(
    client: AtlasClient,
    todos: TodoStore,
    note_id: str = "",
    q: str = "",
) -> dict[str, Any]:
    """Graduate a Notes capture into the Atlas ledger (same as UI promote)."""
    hit, err = _find_note(todos, note_id=note_id, q=q)
    if err:
        return err
    assert hit is not None
    if hit.promoted_ticket:
        return {"ok": True, "already": True, "ticket": hit.promoted_ticket, "note": hit.to_dict()}
    try:
        reg = client.register(hit.text, source="justin", goal=hit.text)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not register: {exc}"}
    ticket = str(
        (reg.get("thread") or {}).get("external_id")
        or (reg.get("thread") or {}).get("id")
        or "registered"
    )
    todos.update(hit.id, promoted_ticket=ticket)
    note = todos.get(hit.id)
    return {
        "ok": True,
        "ticket": ticket,
        "note_id": hit.id,
        "note": note.to_dict() if note else hit.to_dict(),
        "action": "upsert",
    }


def _draft_lesson(
    memory: MemoryStore,
    title: str = "",
    body: str = "",
    tags: str = "",
) -> dict[str, Any]:
    """Save a local lesson draft — never auto-publishes or emails."""
    try:
        les = memory.add_lesson(title, body, tags=tags, source="chat")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "lesson": les.to_dict(),
        "hint": "Saved on this laptop only. Not sent anywhere.",
    }


def _list_lessons(memory: MemoryStore, q: str = "", limit: int = 20) -> dict[str, Any]:
    lessons = (
        memory.search_lessons(q, limit=limit)
        if (q or "").strip()
        else memory.list_lessons(limit=limit)
    )
    return {"ok": True, "lessons": [les.to_dict() for les in lessons], "count": len(lessons)}


def _list_conversations(memory: MemoryStore, limit: int = 10) -> dict[str, Any]:
    return {"conversations": [c.to_dict() for c in memory.list_conversations(limit=limit)]}


def _list_agent_threads(
    memory: MemoryStore,
    surface: str = "",
    q: str = "",
    include_hidden: bool = False,
    limit: int = 40,
) -> dict[str, Any]:
    rows = merge_overlays(scan_agent_sessions(), memory.list_agent_overlays())
    visible = filter_cockpit(rows, include_hidden=include_hidden, surface=surface, q=q)
    slim = [
        {
            "id": r.get("id"),
            "surface": r.get("surface"),
            "title": r.get("title"),
            "cwd": r.get("cwd"),
            "age": r.get("age"),
            "state": r.get("state"),
            "host_id": r.get("host_id"),
            "project_ref": r.get("project_ref"),
            "linked_rev": r.get("linked_rev"),
            "live": r.get("live"),
            "kept": r.get("kept"),
        }
        for r in visible[: max(1, min(int(limit or 40), 100))]
    ]
    return {"threads": slim, "counts": active_counts(rows)}


def _get_nucleus(
    client: AtlasClient,
    memory: MemoryStore,
    q: str = "",
    status: str = "",
    source: str = "",
    limit: int = 60,
    force: bool = False,
) -> dict[str, Any]:
    """Read the same operating graph rendered by the Nucleus screen."""
    snapshot = build_nucleus_snapshot(client, memory, force=force)
    return nucleus_view(snapshot, q=q, status=status, surface=source, limit=limit)


def _organize_agent_thread(memory: MemoryStore, agent_id: str, **changes: Any) -> dict[str, Any]:
    allowed = {"pinned", "snooze_until", "archived", "labels", "linked_rev", "notes"}
    selected = {key: value for key, value in changes.items() if key in allowed}
    if not selected:
        return {"ok": False, "error": "at least one thread organization field is required"}
    row = memory.upsert_agent_overlay(agent_id, **selected)
    invalidate_nucleus_cache()
    return {"ok": True, "agent_id": agent_id, "overlay": row, "native_thread_changed": False}


def _organize_project(memory: MemoryStore, project_id: str, **changes: Any) -> dict[str, Any]:
    allowed = {"pinned", "archived", "objective", "notes"}
    selected = {key: value for key, value in changes.items() if key in allowed}
    if not selected:
        return {"ok": False, "error": "at least one project organization field is required"}
    row = memory.upsert_project_overlay(project_id, **selected)
    invalidate_nucleus_cache()
    return {"ok": True, "project_id": project_id, "overlay": row, "source_records_changed": False}


def _assess_agent_thread(
    supervisor: SupervisorRuntime,
    agent_id: str = "",
    q: str = "",
) -> dict[str, Any]:
    snapshot = supervisor.snapshot(force=True)
    rows = snapshot.get("sessions") or []
    aid = (agent_id or "").strip().casefold()
    qn = (q or "").strip().lower()
    hit = next(
        (
            row for row in rows
            if (aid and aid in str(row.get("id") or "").casefold())
            or (qn and qn in f"{row.get('title')} {row.get('id')}".casefold())
        ),
        None,
    )
    if not hit:
        return {"ok": False, "error": "no matching agent thread"}
    return {"ok": True, "thread": hit, "assessment": hit.get("assessment")}


def _compile_unfog_work(**kwargs: Any) -> dict[str, Any]:
    contract = UnfogContract(
        outcome=kwargs.pop("outcome", ""),
        target=kwargs.pop("target", ""),
        premise=kwargs.pop("premise", ""),
        scope=kwargs.pop("scope", ""),
        preservation=kwargs.pop("preservation", ""),
        acceptance=tuple(kwargs.pop("acceptance", ()) or ()),
        delivery=kwargs.pop("delivery", ""),
    )
    # A ticket recommendation is only useful if "new" has been checked against
    # the live ledger and local in-flight sessions.  Callers may supply their
    # own evidence (for replay/tests), otherwise this read-only compiler owns
    # the preflight before it ever returns draft_new_ticket.
    title = str(kwargs.get("draft_title") or contract.outcome).strip()
    if "existing_tickets" not in kwargs and title:
        kwargs["existing_tickets"] = find_linear_ticket_candidates(title)
    if "active_work" not in kwargs and title:
        normalized = {title.casefold(), contract.outcome.casefold()}
        active = next(
            (
                row for row in scan_agent_sessions()
                if row.get("live")
                and str(row.get("title") or "").strip().casefold() in normalized
            ),
            None,
        )
        if active:
            kwargs["active_work"] = {
                "work_id": str(active.get("id") or ""),
                "matches_contract": True,
                "status": "running",
                "evidence": "live provider session with exact title",
            }
    decision = compile_work(contract, **kwargs)
    return {"ok": True, "decision": asdict(decision)}


def _ask_frontier(cfg: BrutusCfg, **contract: Any) -> dict[str, Any]:
    import json

    prompt = (
        "Resolve this material work question with Unfog. Return an evidence-backed, "
        "execution-ready recommendation; do not create tickets or change files.\n"
        + json.dumps(contract, indent=2, sort_keys=True)
    )
    return run_profile(cfg, "frontier", prompt, cwd=Path(__file__).resolve().parents[1])


def _create_linear_ticket_from_unfog(
    *,
    title: str,
    outcome: str,
    target: str,
    premise: str,
    scope: str,
    preservation: str,
    acceptance: list[str],
    delivery: str,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recompile against live sources at execution time, then create at most one issue."""
    contract = UnfogContract(
        outcome=outcome,
        target=target,
        premise=premise,
        scope=scope,
        preservation=preservation,
        acceptance=tuple(acceptance or ()),
        delivery=delivery,
    )
    candidates = find_linear_ticket_candidates(title)
    normalized = {title.strip().casefold(), outcome.strip().casefold()}
    active = next(
        (
            row
            for row in scan_agent_sessions()
            if row.get("live") and str(row.get("title") or "").strip().casefold() in normalized
        ),
        None,
    )
    decision = compile_work(
        contract,
        evidence=evidence or (),
        existing_tickets=candidates,
        active_work=(
            {
                "work_id": str(active.get("id")),
                "matches_contract": True,
                "status": "running",
                "evidence": "live provider session with exact title",
            }
            if active
            else None
        ),
        draft_title=title,
    )
    if decision.action != "draft_new_ticket":
        return {
            "ok": False,
            "blocked": True,
            "error": decision.reason,
            "decision": asdict(decision),
        }
    evidence_lines = [
        f"- {item.get('claim')}: {item.get('observation')} ({item.get('source')})"
        for item in (evidence or [])
    ]
    description = "\n".join(
        [
            "## Outcome", outcome,
            "## Target", target,
            "## Premise", premise,
            "## Scope", scope,
            "## Preservation", preservation,
            "## Acceptance", *(f"- {item}" for item in acceptance),
            "## Delivery", delivery,
            "## Evidence", *(evidence_lines or ["- No external evidence supplied."]),
        ]
    )
    return create_linear_ticket(title, description)


def _canon_snapshot() -> dict[str, Any]:
    from .canon.surface import open_canon_store, snapshot

    store = open_canon_store()
    try:
        return snapshot(store)
    finally:
        store.close()


def _capture_canon_inbox(raw_capture: str, source: str = "brutus:chat") -> dict[str, Any]:
    from .canon.surface import capture_inbox, open_canon_store

    store = open_canon_store()
    try:
        item = capture_inbox(store, raw_capture=raw_capture, source=source)
        return item.model_dump(mode="json")
    finally:
        store.close()


def _promote_canon_inbox(
    inbox_item_id: str, title: str, description: str = ""
) -> dict[str, Any]:
    from .canon.surface import open_canon_store, promote

    store = open_canon_store()
    try:
        work = promote(store, inbox_item_id, title=title, description=description)
        return work.model_dump(mode="json")
    finally:
        store.close()


def _review_canon_work(
    work_item_id: str, action: str, reason: str = ""
) -> dict[str, Any]:
    from .canon.surface import open_canon_store, owner_review

    store = open_canon_store()
    try:
        work = owner_review(store, work_item_id, action, reason=reason)
        return work.model_dump(mode="json")
    finally:
        store.close()


def build_default_registry(
    client: AtlasClient,
    cfg: BrutusCfg | None = None,
    memory: MemoryStore | None = None,
    todos: TodoStore | None = None,
    *,
    read_only: bool = False,
) -> ToolRegistry:
    memory = memory or MemoryStore()
    todos = todos or TodoStore()
    runtime_cfg = cfg or BrutusCfg()
    supervisor_judge = None
    if runtime_cfg.claude.enabled:
        supervisor_judge = lambda prompt: judge_with_profile(
            runtime_cfg, "supervisor", prompt, cwd=Path(__file__).resolve().parents[1]
        )
    supervisor = SupervisorRuntime(judge=supervisor_judge, overlays=memory.list_agent_overlays)
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="get_nucleus",
            description=(
                "Read the canonical Nucleus operating graph used by the Brutus screen: "
                "projects joined to Linear issues and Codex, Cursor, and Claude threads, "
                "with attention reasons, source freshness, and mapping coverage."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "project, ticket, thread, or path search"},
                    "status": {"type": "string", "description": "attention, active, watch, or quiet"},
                    "source": {"type": "string", "description": "codex, cursor, claude, or linear"},
                    "limit": {"type": "integer"},
                    "force": {"type": "boolean", "description": "bypass the short snapshot cache"},
                },
            },
            fn=lambda **kwargs: _get_nucleus(client, memory, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="get_work_surface",
            description=(
                "Return the current work surface (probe-filtered): what needs Justin, "
                "what is working, stuck, queued, and the completion alarm."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "include_probes": {
                        "type": "boolean",
                        "description": "include Atlas5 self-test probe tickets (default false)",
                    },
                },
            },
            fn=lambda **kwargs: _work_surface(client, cfg, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="get_digest",
            description=(
                "Return a probe-filtered WIP digest: headline, needs_you, working, stuck, "
                "alarm, plus a capped raw digest excerpt. Prefer this over inventing status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "include_probes": {"type": "boolean"},
                },
            },
            fn=lambda **kwargs: _get_digest(client, cfg, **kwargs),
        )
    )
    if not read_only:
        reg.register(
            Tool(
                name="ask_atlas6",
                description="Send a complex work question, execution command, or ticket scoping request to Atlas6/Atlas5. Use this when Justin needs real bot work, deep ledger context, or ticket creation.",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "the question or command to send to Atlas6"},
                        "mode": {"type": "string", "description": "manager or worker"},
                        "ticket_id": {"type": "string", "description": "optional REV-XX ticket id"},
                    },
                    "required": ["message"],
                },
                fn=lambda **kwargs: _ask_atlas6(client, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="ask_cursor",
                description=(
                    "Run a one-shot Cursor SDK agent on an allowlisted laptop repo "
                    "(default repo_hint=brutus; atlas6 also allowed). Use for coding "
                    "investigations Atlas6 cannot do. Never invents a cwd outside the allowlist."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "the task or question to send to Cursor"},
                        "repo_hint": {
                            "type": "string",
                            "description": "allowlisted repo basename or path (brutus or atlas6)",
                        },
                    },
                    "required": ["message"],
                },
                fn=lambda **kwargs: _ask_cursor(cfg or BrutusCfg(), **kwargs),
            )
        )
        reg.register(
            Tool(
                name="ask_frontier",
                description=(
                    "Run one read-only frontier-model Unfog pass for explicitly material ambiguity, "
                    "risk, or conflicting evidence. The exact contract is preserved in the receipt."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "outcome": {"type": "string"}, "target": {"type": "string"},
                        "premise": {"type": "string"}, "scope": {"type": "string"},
                        "preservation": {"type": "string"},
                        "acceptance": {"type": "array", "items": {"type": "string"}},
                        "delivery": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "object"}},
                        "justification": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["question", "outcome", "target", "premise", "scope", "preservation", "acceptance", "delivery", "justification"],
                },
                fn=lambda **kwargs: _ask_frontier(cfg or BrutusCfg(), **kwargs),
            )
        )
        reg.register(
            Tool(
                name="create_linear_ticket",
                description=(
                    "Create exactly one Linear ticket from a reviewed Unfog contract. "
                    "This is a gated write and must not be used when matching work or a matching ticket exists."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"}, "outcome": {"type": "string"},
                        "target": {"type": "string"}, "premise": {"type": "string"},
                        "scope": {"type": "string"}, "preservation": {"type": "string"},
                        "acceptance": {"type": "array", "items": {"type": "string"}},
                        "delivery": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["title", "outcome", "target", "premise", "scope", "preservation", "acceptance", "delivery"],
                },
                fn=_create_linear_ticket_from_unfog,
            )
        )
        reg.register(
            Tool(
                name="ask_claude",
                description=(
                    "Send a long-form research, writing, or analysis task to Claude "
                    "(Anthropic API). Use when Studio is busy/down or for drafting."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "the task or question to send to Claude"},
                    },
                    "required": ["message"],
                },
                fn=lambda **kwargs: _ask_claude(cfg or BrutusCfg(), **kwargs),
            )
        )
    reg.register(
        Tool(
            name="list_threads",
            description="List all open portfolio threads from the Atlas6 ledger.",
            parameters={},
            fn=lambda: _list_threads(client),
        )
    )
    reg.register(
        Tool(
            name="get_thread",
            description="Look up one open thread by REV-XX ticket id or thread UUID.",
            parameters={
                "type": "object",
                "properties": {
                    "external_id": {"type": "string", "description": "REV-XX ticket id"},
                    "thread_id": {"type": "string", "description": "thread UUID"},
                },
            },
            fn=lambda **kwargs: _get_thread(client, **kwargs),
        )
    )
    if not read_only:
        reg.register(
            Tool(
                name="register_thread",
                description="Register a new work thread in the Atlas6 ledger. Use this when Justin wants to start tracking a new task or ticket.",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "short title"},
                        "goal": {"type": "string", "description": "description of what needs to happen"},
                        "external_id": {"type": "string", "description": "optional REV-XX ticket id"},
                    },
                    "required": ["title"],
                },
                fn=lambda **kwargs: _register_thread(client, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="dispatch_tick",
                description=(
                    "Ask Studio to run one portfolio dispatcher tick. Defaults to dry_run=true "
                    "(preview only). Set dry_run=false only when Justin explicitly wants a live tick. "
                    "Aligns with the laptop watchdog's dispatch step."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "dry_run": {
                            "type": "boolean",
                            "description": "true = preview (default); false = live dispatch",
                        },
                        "ingest_linear": {"type": "boolean"},
                    },
                },
                fn=lambda **kwargs: _dispatch_tick(client, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="approve_gate",
                description=(
                    "Approve or reject a gate that is waiting on Justin. Use this — and only this — "
                    "when he says 'approve REV-XX' or 'reject REV-XX'. Never claim a gate was "
                    "approved without calling it and seeing ok=true come back."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticket": {
                            "type": "string",
                            "description": "Ticket id, e.g. REV-412 (or the thread uuid)",
                        },
                        "decision": {
                            "type": "string",
                            "enum": ["approve", "reject"],
                            "description": "approve (default) or reject",
                        },
                    },
                    "required": ["ticket"],
                },
                fn=lambda **kwargs: _approve_gate(client, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="reconcile",
                description=(
                    "Reconcile in_flight threads against Atlas5 verified-handback receipts. "
                    "Same step the laptop watchdog runs every tick."
                ),
                parameters={},
                fn=lambda: _reconcile(client),
            )
        )
        reg.register(
            Tool(
                name="answer_steering",
                description=(
                    "Answer an Atlas5 awaiting_input question for a ticket (steering). "
                    "Use when Justin is answering a bot's question on REV-XX."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticket_id": {"type": "string", "description": "REV-XX"},
                        "body": {"type": "string", "description": "Justin's answer"},
                        "scope": {"type": "string", "description": "usually next_turn"},
                    },
                    "required": ["ticket_id", "body"],
                },
                fn=lambda **kwargs: _answer_steering(client, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="save_working_note",
                description="Save a working note to Brutus's laptop memory. Use this for reminders, half-formed thoughts, or context Justin wants to keep across sessions.",
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "short topic"},
                        "body": {"type": "string", "description": "note body"},
                        "ticket_ids": {"type": "array", "items": {"type": "string"}, "description": "related REV-XX ids"},
                    },
                    "required": ["topic"],
                },
                fn=lambda **kwargs: _save_working_note(memory, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="capture_note",
                description=(
                    "Capture a thought onto the Ideas pad (Inbox). Two-second capture — "
                    "not ledger work until promote_note."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "tags": {"type": "string"},
                        "lane": {"type": "string", "description": "Inbox / In Progress / Blocked / Done"},
                    },
                    "required": ["text"],
                },
                fn=lambda **kwargs: _capture_note(todos, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="update_note",
                description=(
                    "Rename or move an Ideas-pad note. Pass note_id or q, plus text and/or "
                    "lane (Inbox / In Progress / Blocked / Done) or status (todo/doing/done)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "note_id": {"type": "string"},
                        "q": {"type": "string"},
                        "text": {"type": "string"},
                        "lane": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
                fn=lambda **kwargs: _update_note(todos, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="delete_note",
                description=(
                    "Delete an Ideas-pad note. Destructive — gated. Pass note_id or q."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "note_id": {"type": "string"},
                        "q": {"type": "string"},
                    },
                },
                fn=lambda **kwargs: _delete_note(todos, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="promote_note",
                description=(
                    "Graduate an Ideas capture into the Atlas ledger (register + tag promoted). "
                    "Pass note_id or q to match text."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "note_id": {"type": "string"},
                        "q": {"type": "string", "description": "match note text when id unknown"},
                    },
                },
                fn=lambda **kwargs: _promote_note(client, todos, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="draft_lesson",
                description=(
                    "Save a local lesson draft on this laptop (what we learned). "
                    "Never emails, Slacks, or publishes — draft only."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "tags": {"type": "string"},
                    },
                    "required": ["body"],
                },
                fn=lambda **kwargs: _draft_lesson(memory, **kwargs),
            )
        )
    reg.register(
        Tool(
            name="list_notes",
            description="List Notes pad captures (todos/WIP/ideas). Optional q filter.",
            parameters={
                "type": "object",
                "properties": {
                    "include_done": {"type": "boolean"},
                    "q": {"type": "string"},
                },
            },
            fn=lambda **kwargs: _list_notes(todos, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="list_working_notes",
            description="List or search Brutus working notes in laptop memory.",
            parameters={
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
            fn=lambda **kwargs: _list_working_notes(memory, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="list_lessons",
            description="List or search local lesson drafts saved on this laptop.",
            parameters={
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
            fn=lambda **kwargs: _list_lessons(memory, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="list_conversations",
            description="List recent Brutus conversations so Justin can pick up where he left off.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "max conversations to return"},
                },
            },
            fn=lambda **kwargs: _list_conversations(memory, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="list_agent_threads",
            description=(
                "List recent Codex, Cursor Agent, and Claude Code threads on this laptop "
                "with provider-stable ids and observed state. Use when Justin asks what threads are open."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "surface": {"type": "string", "description": "codex, cursor, or claude"},
                    "q": {"type": "string", "description": "filter by title/cwd/project"},
                    "include_hidden": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
            },
            fn=lambda **kwargs: _list_agent_threads(memory, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="assess_agent_thread",
            description=(
                "Judge one Codex, Cursor, or Claude session from lifecycle and transcript evidence. "
                "Returns goal, verified progress, blocker or decision, and one next action; never a raw summary."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "codex:…, cursor:…, or claude:… id"},
                    "q": {"type": "string", "description": "match title/cwd when id unknown"},
                },
            },
            fn=lambda **kwargs: _assess_agent_thread(supervisor, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="get_supervised_work",
            description=(
                "Observe Codex, Cursor, and Claude sessions incrementally. Return only evidence-backed "
                "session assessments and ranked interventions; normal progress stays silent."
            ),
            parameters={
                "type": "object",
                "properties": {"force": {"type": "boolean"}, "limit": {"type": "integer"}},
            },
            fn=lambda **kwargs: {"ok": True, **supervisor.snapshot(**kwargs)},
        )
    )
    reg.register(
        Tool(
            name="compile_unfog_work",
            description=(
                "Compile a complete Unfog contract against active work and evidenced ticket candidates. "
                "Returns continue, update_existing, draft_new_ticket, frontier, or needs_input; never mutates."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "outcome": {"type": "string"}, "target": {"type": "string"},
                    "premise": {"type": "string"}, "scope": {"type": "string"},
                    "preservation": {"type": "string"},
                    "acceptance": {"type": "array", "items": {"type": "string"}},
                    "delivery": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                    "existing_tickets": {"type": "array", "items": {"type": "object"}},
                    "active_work": {"type": "object"},
                    "material_ambiguities": {"type": "array", "items": {"type": "string"}},
                    "material_risks": {"type": "array", "items": {"type": "string"}},
                    "conflicting_evidence": {"type": "array", "items": {"type": "string"}},
                    "material_fork": {"type": "string"}, "draft_title": {"type": "string"},
                },
                "required": ["outcome", "target", "premise", "scope", "preservation", "acceptance", "delivery"],
            },
            fn=_compile_unfog_work,
        )
    )
    reg.register(
        Tool(
            name="check_email",
            description=(
                "Peek recent work-like Gmail (capped, metadata/snippets only). "
                "Draft-only: never sends mail. Does not register ledger threads."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "max items (default 10)"},
                },
            },
            fn=lambda **kwargs: _check_email(client, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="check_slack",
            description=(
                "Peek recent work-like Slack messages in configured channels (capped). "
                "Draft-only: never posts to Slack. Does not register ledger threads."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "max items (default 10)"},
                },
            },
            fn=lambda **kwargs: _check_slack(client, **kwargs),
        )
    )
    reg.register(
        Tool(
            name="get_canon_snapshot",
            description=(
                "Read the Canon work surface: inbox, today, review, sealed cards, "
                "and stuck items. Use this before claiming what is waiting."
            ),
            parameters={"type": "object", "properties": {}},
            fn=lambda **_kwargs: _canon_snapshot(),
        )
    )
    if not read_only:
        reg.register(
            Tool(
                name="organize_agent_thread",
                description=(
                    "Organize one exact Codex, Cursor, or Claude thread in Brutus: pin, archive, "
                    "snooze, label, link a REV ticket, or add notes. This changes only Brutus's "
                    "local overlay; it never mutates or terminates the native agent thread."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "pinned": {"type": "boolean"},
                        "archived": {"type": "boolean"},
                        "snooze_until": {"type": "string"},
                        "labels": {"type": "string"},
                        "linked_rev": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["agent_id"],
                },
                fn=lambda **kwargs: _organize_agent_thread(memory, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="organize_project",
                description=(
                    "Organize one exact Nucleus project in Brutus: pin or archive it, set its "
                    "objective, or add notes. This changes only Brutus's local overlay and "
                    "never edits GitHub or Linear source records."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "pinned": {"type": "boolean"},
                        "archived": {"type": "boolean"},
                        "objective": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["project_id"],
                },
                fn=lambda **kwargs: _organize_project(memory, **kwargs),
            )
        )
        reg.register(
            Tool(
                name="capture_canon_inbox",
                description="Capture a thought into the Canon inbox. Does not start work.",
                parameters={
                    "type": "object",
                    "properties": {
                        "raw_capture": {
                            "type": "string",
                            "description": "verbatim capture text",
                        },
                        "source": {
                            "type": "string",
                            "description": "where this came from (default brutus:chat)",
                        },
                    },
                    "required": ["raw_capture"],
                },
                fn=lambda **kwargs: _capture_canon_inbox(**kwargs),
            )
        )
        reg.register(
            Tool(
                name="promote_canon_inbox",
                description="Turn one inbox capture into a work item after Justin reviews it.",
                parameters={
                    "type": "object",
                    "properties": {
                        "inbox_item_id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["inbox_item_id", "title"],
                },
                fn=lambda **kwargs: _promote_canon_inbox(**kwargs),
            )
        )
        reg.register(
            Tool(
                name="review_canon_work",
                description="Accept, reject, or request changes on a work item in review.",
                parameters={
                    "type": "object",
                    "properties": {
                        "work_item_id": {"type": "string"},
                        "action": {
                            "type": "string",
                            "description": "accept, reject, or request-changes",
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["work_item_id", "action"],
                },
                fn=lambda **kwargs: _review_canon_work(**kwargs),
            )
        )
    # Cursor is the only reasoning backend. Atlas compatibility code remains
    # behind a reversible flag, but standalone Brutus must not expose a tool
    # that could route into it.
    reg.discard("ask_claude")
    if not (cfg or BrutusCfg()).atlas_enabled:
        for name in (
            "ask_atlas6",
            "list_threads",
            "get_thread",
            "register_thread",
            "promote_note",
            "approve_gate",
            "dispatch_tick",
            "answer_steering",
            "reconcile",
            "check_email",
            "check_slack",
        ):
            reg.discard(name)
    return reg


def format_tool_catalog(registry: ToolRegistry) -> str:
    """Name, description, AND argument names.

    Listing only names and descriptions left the model guessing argument names,
    and it guessed wrong most of the time — `get_thread(ticket_id=...)` instead
    of `external_id`, `ask_cursor(question=...)` instead of `message`. Every one
    of those raised TypeError inside ToolRegistry.call and came back as a value
    the model then narrated around.
    """
    lines = []
    for tool in registry._tools.values():
        props: dict[str, Any] = (tool.parameters or {}).get("properties", {}) or {}
        required = set((tool.parameters or {}).get("required", []) or [])
        if props:
            args = ", ".join(
                name if name in required else f"{name}?" for name in props
            )
            lines.append(f"- {tool.name}({args}): {tool.description}")
        else:
            lines.append(f"- {tool.name}(): {tool.description}")
    return "\n".join(lines)
