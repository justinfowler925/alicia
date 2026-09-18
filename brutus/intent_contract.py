"""Compile gated Brutus proposals into concrete, reviewable contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class IntentNotReady(ValueError):
    """A proposal is missing a detail that changes what will execute."""


@dataclass(frozen=True)
class IntentContract:
    outcome: str
    target: str
    scope: str
    preserve: str
    acceptance: tuple[str, ...]
    evidence: str
    assumptions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _text(args: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(args.get(key) or "").strip()
        if value:
            return value
    return ""


def _require(tool: str, args: dict[str, Any], *groups: tuple[str, ...]) -> None:
    missing = [" or ".join(group) for group in groups if not _text(args, *group)]
    if missing:
        raise IntentNotReady(
            f"{tool} needs material intent detail: {', '.join(missing)}. "
            "Resolve it from conversation history or ask one focused question."
        )


def compile_proposal(tool: str, args: dict[str, Any]) -> IntentContract:
    """Return the contract for the exact args that approval will execute."""
    a = dict(args or {})
    evidence = "pending approval and tool receipt"

    if tool == "approve_gate":
        _require(tool, a, ("ticket",))
        decision = _text(a, "decision") or "approve"
        if decision not in {"approve", "reject"}:
            raise IntentNotReady("approve_gate decision must be approve or reject")
        ticket = _text(a, "ticket")
        return IntentContract(
            outcome=f"{decision} the waiting decision for {ticket}",
            target=f"Atlas gate {ticket}",
            scope="one named gate",
            preserve="every other gate and the ticket body",
            acceptance=(f"tool receipt confirms {decision}", "the same ticket id appears in the receipt"),
            evidence=evidence,
        )

    if tool == "dispatch_tick":
        live = not bool(a.get("dry_run", True))
        return IntentContract(
            outcome="run one live dispatcher tick" if live else "preview one dispatcher tick",
            target="Atlas portfolio dispatcher",
            scope=(
                "all eligible work at execution time; receipt must state the denominator"
                if live
                else "read-only preview of all eligible work"
            ),
            preserve="ineligible, gated, and already in-flight work",
            acceptance=("one dispatcher receipt", "eligible and affected counts are stated"),
            evidence=evidence,
        )

    if tool == "answer_steering":
        _require(tool, a, ("ticket_id",), ("body",))
        ticket = _text(a, "ticket_id")
        return IntentContract(
            outcome=f"answer the worker's open question with: {_text(a, 'body')[:180]}",
            target=f"steering prompt for {ticket}",
            scope="one answer on one named ticket",
            preserve="ticket scope, prior evidence, and other awaiting-input threads",
            acceptance=("steering receipt names the ticket", "the answer body is preserved verbatim"),
            evidence=evidence,
        )

    if tool == "register_thread":
        _require(tool, a, ("title",))
        title = _text(a, "title")
        goal = _text(a, "goal")
        assumptions = () if goal else ("Title is intake only; Atlas must scope it before execution.",)
        return IntentContract(
            outcome=goal or title,
            target="Atlas work ledger",
            scope="one new tracked thread",
            preserve="existing threads and human-authored source records",
            acceptance=("registration receipt returns a work key", "no execution starts from registration alone"),
            evidence=evidence,
            assumptions=assumptions,
        )

    if tool in {"promote_note", "delete_note"}:
        _require(tool, a, ("note_id", "id", "q"))
        selector = _text(a, "note_id", "id", "q")
        verb = "promote" if tool == "promote_note" else "delete"
        return IntentContract(
            outcome=f"{verb} the selected idea",
            target=f"Ideas note {selector}",
            scope="one exactly resolved note",
            preserve="all non-matching notes",
            acceptance=(f"tool receipt confirms {verb}", "receipt names the resolved note"),
            evidence=evidence,
        )

    if tool in {"ask_atlas6", "ask_claude", "ask_model", "ask_frontier"}:
        _require(tool, a, ("message", "question"))
        backend = tool.removeprefix("ask_")
        return IntentContract(
            outcome=_text(a, "message", "question")[:240],
            target=f"{backend} backend" + (f" · {_text(a, 'ticket_id', 'repo_hint')}" if _text(a, "ticket_id", "repo_hint") else ""),
            scope="one backend task; no authority beyond the drafted message",
            preserve="unmentioned systems, records, and repositories",
            acceptance=("backend receipt is attached", "claims are backed by artifact or record evidence"),
            evidence=evidence,
        )

    if tool == "create_linear_ticket":
        _require(
            tool,
            a,
            ("title",),
            ("outcome",),
            ("target",),
            ("premise",),
            ("scope",),
            ("preservation",),
            ("delivery",),
        )
        if not a.get("acceptance"):
            raise IntentNotReady("create_linear_ticket needs material intent detail: acceptance")
        return IntentContract(
            outcome=f"create one Linear ticket: {_text(a, 'title')}",
            target="Clearspeed Linear workspace",
            scope="one new issue whose reviewed description executes verbatim",
            preserve="existing issues, active sessions, and all unmentioned work",
            acceptance=("receipt returns one Linear identifier", "receipt title matches the reviewed title"),
            evidence=evidence,
        )

    if tool == "organize_agent_thread":
        _require(tool, a, ("agent_id",))
        changed = tuple(key for key in ("pinned", "archived", "snooze_until", "labels", "linked_rev", "notes") if key in a)
        if not changed:
            raise IntentNotReady("organize_agent_thread needs at least one organization field")
        agent_id = _text(a, "agent_id")
        return IntentContract(
            outcome=f"update Brutus organization fields for {agent_id}: {', '.join(changed)}",
            target=f"local agent-thread overlay {agent_id}",
            scope="one exact provider-stable thread id",
            preserve="the native Codex, Cursor, or Claude thread and every other overlay",
            acceptance=("receipt names the exact agent id", "receipt states native_thread_changed=false"),
            evidence=evidence,
        )

    if tool == "organize_project":
        _require(tool, a, ("project_id",))
        changed = tuple(key for key in ("pinned", "archived", "objective", "notes") if key in a)
        if not changed:
            raise IntentNotReady("organize_project needs at least one organization field")
        project_id = _text(a, "project_id")
        return IntentContract(
            outcome=f"update Brutus organization fields for {project_id}: {', '.join(changed)}",
            target=f"local Nucleus project overlay {project_id}",
            scope="one exact canonical project id",
            preserve="GitHub and Linear source records and every other project",
            acceptance=("receipt names the exact project id", "receipt states source_records_changed=false"),
            evidence=evidence,
        )

    raise IntentNotReady(f"{tool or 'proposal'} has no intent-contract definition")
