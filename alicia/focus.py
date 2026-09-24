"""Focus analyzer — ranked actions for Justin, not a ticket dump."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from .client import question_needs_retriage
from .speechify import speakable_name

KIND_GATE = "gate"
KIND_UNSTICK = "gone_quiet"
KIND_CURSOR = "needs_code"
KIND_FRONTIER = "needs_judgement"
KIND_WAIT = "bots_working"

_PRIORITY = {
    KIND_GATE: 0,
    KIND_UNSTICK: 1,
    KIND_CURSOR: 1,
    KIND_FRONTIER: 2,
    KIND_WAIT: 3,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return None


def age_minutes(iso: str | None, *, now: datetime | None = None) -> float | None:
    dt = _parse_ts(iso)
    if not dt:
        return None
    now = now or _now()
    return max(0.0, (now - dt).total_seconds() / 60.0)


def evidence_badge(evidence: str) -> dict[str, str]:
    ev = (evidence or "").strip()
    if not ev:
        return {"type": "none", "label": "(none)"}
    if ev.startswith("inbox:"):
        return {"type": "inbox", "label": "Studio inbox job"}
    if ev.startswith("job_ledger:"):
        return {"type": "job_ledger", "label": ev.replace("job_ledger:", "", 1)[:80]}
    if ev.startswith("cursor:"):
        return {"type": "cursor", "label": ev.replace("cursor:", "", 1)[:80]}
    if ev.startswith("http://") or ev.startswith("https://"):
        return {"type": "url", "label": ev[:100]}
    return {"type": "other", "label": ev[:100]}


def linear_url(external_id: str | None, workspace: str) -> str | None:
    if not external_id:
        return None
    m = re.match(r"^(REV-\d+)$", str(external_id).strip(), re.I)
    if not m:
        return None
    ws = (workspace or "clearspeed").strip().strip("/")
    return f"https://linear.app/{quote(ws)}/issue/{m.group(1).upper()}"


# Atlas5 burn-in probes are synthetic self-test tickets. They are real rows in
# the ledger but they are never Justin's work, so they must never consume a slot
# on the work surface. They are still counted, so they can't vanish silently.
_PROBE_RE = re.compile(r"\[atlas5 proof(?: burn-in)?\]|atlasproof", re.I)


def is_probe(thread: dict[str, Any]) -> bool:
    """True for Atlas5 self-test/burn-in threads that are not human work."""
    blob = f"{thread.get('title') or ''} {thread.get('goal') or ''} {thread.get('question') or ''}"
    return bool(_PROBE_RE.search(blob))


def probe_ticket_ids(status: dict[str, Any]) -> set[str]:
    """External ids of probe threads, from every thread list the status carries.

    Atlas5's ``awaiting_input`` rows arrive with the *question* as their title, so
    they cannot be identified by text. The ledger knows they are probes, so match
    on ticket id instead.
    """
    ids: set[str] = set()
    for key in ("blocked_justin", "in_flight", "ready", "threads", "blocked_frontier", "done"):
        for t in status.get(key) or []:
            if isinstance(t, dict) and is_probe(t):
                ext = str(t.get("external_id") or "").upper().strip()
                if ext:
                    ids.add(ext)
    return ids


def _norm_blocker(blocker: str) -> str:
    """Collapse key for grouping gates that share one underlying cause."""
    b = (blocker or "").strip().lower()
    if not b:
        return "(no blocker given)"
    b = re.sub(r"\d+", "N", b)
    b = re.sub(r"\s+", " ", b)
    return b[:90]


def _is_stale(thread: dict[str, Any], *, stale_minutes: int, now: datetime) -> bool:
    blob = (
        f"{thread.get('blocker') or ''} {thread.get('evidence') or ''} {thread.get('last_error') or ''}"
    ).lower()
    if re.search(r"reaped|failed|stale", blob):
        return True
    mins = age_minutes(thread.get("last_dispatched_at") or thread.get("updated_at"), now=now)
    return mins is not None and mins >= stale_minutes


def _item_from_thread(t: dict[str, Any], workspace: str, *, now: datetime) -> dict[str, Any]:
    ext = t.get("external_id") or ""
    mins = age_minutes(t.get("last_dispatched_at") or t.get("updated_at"), now=now)
    links = []
    lu = linear_url(ext, workspace)
    if lu:
        links.append({"label": "Linear", "href": lu})
    return {
        "thread_id": t.get("id"),
        "external_id": ext,
        "title": t.get("title") or "(untitled)",
        "executor": t.get("executor") or "",
        "blocker": t.get("blocker") or "",
        "next_action": t.get("next_action") or "",
        "age_minutes": round(mins, 1) if mins is not None else None,
        "evidence": evidence_badge(str(t.get("evidence") or "")),
        "links": links,
        "environment": t.get("environment") or "",
        "autonomy_class": t.get("autonomy_class") or "",
        "goal_excerpt": _goal_excerpt(str(t.get("goal") or "")),
    }


def _goal_excerpt(goal: str, n: int = 220) -> str:
    g = goal.replace("\r", "")
    g = re.sub(r"^>.*\n+", "", g)
    g = g.replace("**", "").replace("`", "")
    g = re.sub(r"^#+\s*", "", g, flags=re.M)
    m = re.search(r"##?\s*Goal\s*\n+([\s\S]*?)(?:\n##|$)", g, re.I)
    if m:
        g = m.group(1)
    g = re.sub(r"\n{3,}", "\n\n", g).strip()
    if len(g) > n:
        return g[: n - 1] + "…"
    return g


def _item_from_job(j: dict[str, Any], workspace: str, *, kind: str) -> dict[str, Any]:
    ext = j.get("external_id") or ""
    links = []
    lu = linear_url(ext, workspace)
    if lu:
        links.append({"label": "Linear", "href": lu})
    return {
        "thread_id": j.get("thread_id"),
        "external_id": ext,
        "title": j.get("title") or "(untitled)",
        "path": j.get("_path") or "",
        "reason": j.get("reason") or "",
        "repo_hint": j.get("repo_hint") or "",
        "prompt_excerpt": (j.get("prompt") or "")[:240],
        "evidence": evidence_badge(f"cursor:{j.get('_path')}" if kind == "cursor" else ""),
        "links": links,
    }


def build_focus(
    status: dict[str, Any],
    *,
    stale_inflight_minutes: int = 45,
    linear_workspace: str = "clearspeed",
    max_cursor_actions: int = 8,
    awaiting_input: list[dict[str, Any]] | None = None,
    max_actions: int = 7,
    alarm: dict[str, Any] | None = None,
    include_probes: bool = False,
) -> dict[str, Any]:
    """Deterministic focus queue + chart rollups from Studio status JSON."""
    now = _now()
    actions: list[dict[str, Any]] = []

    gates_all = list(status.get("blocked_justin") or [])
    inflight_all = list(status.get("in_flight") or [])
    frontier = list(status.get("frontier_pending") or [])
    cursor = list(status.get("cursor_pending") or [])
    awaiting = list(awaiting_input or status.get("awaiting_input") or [])

    # Drop Atlas5 self-test probes from the surface, but keep the count. They are
    # the right vehicle for a deliberate end-to-end test, so include_probes brings
    # them back on demand without putting them in the daily view.
    if include_probes:
        gates, inflight = gates_all, inflight_all
    else:
        gates = [t for t in gates_all if not is_probe(t)]
        inflight = [t for t in inflight_all if not is_probe(t)]
    probes_hidden = (len(gates_all) - len(gates)) + (len(inflight_all) - len(inflight))

    # Probe questions come from Atlas5 titled with the question text, so filter
    # them by ticket id against the ledger's own probe threads — and by the
    # probe marker in the row's own text, because the status thread lists
    # rotate and the id set alone goes stale (that's how aged-out parks leak).
    if not include_probes:
        probe_ids = probe_ticket_ids(status)
        before = len(awaiting)
        awaiting = [
            q for q in awaiting
            if str(q.get("ticket_id") or "").upper().strip() not in probe_ids
            and not is_probe(q)
        ]
        probes_hidden += before - len(awaiting)

    # Split scoping failures out of the question stream. "I had no grounding data"
    # is a retriage, not a decision, and it dominated the live queue.
    retriage: list[dict[str, Any]] = []
    real_questions: list[dict[str, Any]] = []
    for q in awaiting:
        if question_needs_retriage(str(q.get("question") or "")):
            retriage.append(q)
        else:
            real_questions.append(q)

    if retriage:
        actions.append(
            {
                "id": "retriage:no_grounding",
                "kind": KIND_UNSTICK,
                "priority": _PRIORITY[KIND_UNSTICK],
                "title": f"{len(retriage)} tickets the bot couldn't figure out",
                "what": "The bot didn't have enough information about your Salesforce setup to start, so it asked you instead of investigating.",
                "do": "Start these over — it can look the data up itself now.",
                "why": (
                    "The worker reported no schema/field/flow grounding, so it asked a "
                    "human instead of investigating. These need grounding + a re-run, "
                    "not an answer."
                ),
                "recommended_verb": "requeue_stale",
                "batchable": True,
                "items": [
                    {
                        "external_id": str(q.get("ticket_id") or ""),
                        "title": str(q.get("title") or "")[:100],
                        "blocker": str(q.get("question") or "")[:200],
                        "links": [],
                        "evidence": evidence_badge("atlas5:no_grounding"),
                    }
                    for q in retriage
                ],
                "links": [],
                "ticket_ids": [str(q.get("ticket_id") or "") for q in retriage],
            }
        )

    # Real Atlas5 questions first — these are the only true "need you" decisions.
    awaiting = real_questions
    seen_tickets: set[str] = set()
    for q in awaiting:
        tid = str(q.get("ticket_id") or "").upper()
        if not tid or tid in seen_tickets:
            continue
        seen_tickets.add(tid)
        question = str(q.get("question") or "").strip()
        links = []
        if q.get("linear_url"):
            links.append({"label": "Linear", "href": q["linear_url"]})
        elif tid:
            links.append(
                {
                    "label": "Linear",
                    "href": f"https://linear.app/{linear_workspace}/issue/{quote(tid)}",
                }
            )
        actions.append(
            {
                "id": f"answer:{tid}",
                "kind": KIND_GATE,
                "priority": _PRIORITY[KIND_GATE],
                "title": f"{tid} is waiting on an answer from you",
                "what": "The bot stopped and asked a question. It can't continue until you reply.",
                "do": "Type your answer and it picks up where it left off.",
                "why": question,
                "recommended_verb": "answer_input",
                "batchable": False,
                "ticket_id": tid,
                "action": q.get("action") or "investigate",
                "question": question,
                "items": [
                    {
                        "external_id": tid,
                        "title": q.get("title") or question[:80],
                        "blocker": question,
                        "links": links,
                        "evidence": evidence_badge("atlas5:awaiting_input"),
                    }
                ],
                "links": links,
            }
        )

    # Group remaining gates by their underlying cause. Twelve threads blocked by
    # one dependency outage is ONE decision, not twelve — fanning a single fault
    # out into N identical cards is what made the old surface unreadable.
    gate_groups: dict[str, list[dict[str, Any]]] = {}
    for t in gates:
        # A thread with no external_id is keyed in Atlas5 by its FULL thread id,
        # not the first 8 chars. Truncating here never matched, so such a thread
        # rendered TWICE — once as a gate card ("Approve/Reject") and once as an
        # answer card ("type your answer") — two contradictory verbs for one
        # thread, only one of which does anything. Compare every form Atlas5
        # might have keyed it under.
        ext = str(t.get("external_id") or "").upper()
        tid_full = str(t.get("id") or "").upper()
        candidates = {c for c in (ext, tid_full, tid_full[:8]) if c}
        if candidates & seen_tickets:
            continue  # already surfaced via Atlas5 awaiting_input
        gate_groups.setdefault(_norm_blocker(str(t.get("blocker") or "")), []).append(t)

    for cause, threads in sorted(gate_groups.items(), key=lambda kv: -len(kv[1])):
        items = [_item_from_thread(t, linear_workspace, now=now) for t in threads]
        blocker = str(threads[0].get("blocker") or "").strip() or "Blocked on Justin gate"
        label, explanation = human_reason(blocker)
        if len(threads) == 1:
            ext = (threads[0].get("external_id") or (threads[0].get("id") or "")[:8] or "").upper()
            title = f"{ext} — {label}"
        else:
            title = f"{len(threads)} tickets — {label}"
        actions.append(
            {
                "id": f"gate:{cause[:40]}",
                "kind": KIND_GATE,
                "priority": _PRIORITY[KIND_GATE],
                "title": title,
                "why": explanation,
                "reason_label": label,
                "blocker_raw": blocker,
                "what": explanation,
                "do": (
                    f"Approve clears the Justin gate for all {len(threads)}. "
                    "Reject parks them. Start over sends them back to the bots."
                    if len(threads) > 1
                    else "Approve clears the Justin gate. Reject parks it. "
                    "Start over sends it back to the bots."
                ),
                "recommended_verb": "decide_gate",
                "batchable": True,
                "thread_id": threads[0].get("id") if len(threads) == 1 else None,
                "thread_ids": [t.get("id") for t in threads if t.get("id")],
                "items": items,
                "links": items[0].get("links") or [] if items else [],
            }
        )

    stale = [t for t in inflight if _is_stale(t, stale_minutes=stale_inflight_minutes, now=now)]
    fresh = [t for t in inflight if t not in stale]
    if stale:
        items = [_item_from_thread(t, linear_workspace, now=now) for t in stale]
        actions.append(
            {
                "id": "unstick:stale",
                "kind": KIND_UNSTICK,
                "priority": _PRIORITY[KIND_UNSTICK],
                "title": f"{len(stale)} tickets have gone quiet",
                "what": "These were handed to a bot but nothing has happened since. Usually the bot died or never picked them up.",
                "do": "Start them over from the beginning.",
                "why": "Past age threshold or failed/reaped — requeue to ready for retriage",
                "recommended_verb": "requeue_stale",
                "batchable": True,
                "items": items,
                "links": [],
                "thread_ids": [t.get("id") for t in stale if t.get("id")],
            }
        )

    # One card per ticket, not per queued file. Multiple queued jobs for the same
    # ticket are one piece of work to drain, and rendering them separately put
    # duplicate "Drain Cursor job REV-271" rows on the surface.
    cursor_groups: dict[str, list[dict[str, Any]]] = {}
    for j in cursor:
        if str(j.get("external_id") or "").upper() in probe_ids:
            probes_hidden += 1
            continue
        key = str(j.get("external_id") or j.get("thread_id") or j.get("_path") or "?")
        cursor_groups.setdefault(key.upper(), []).append(j)

    for ext, jobs in list(cursor_groups.items())[:max_cursor_actions]:
        items = [_item_from_job(j, linear_workspace, kind="cursor") for j in jobs]
        suffix = f" ({len(jobs)} queued)" if len(jobs) > 1 else ""
        actions.append(
            {
                "id": f"cursor:{ext}",
                "kind": KIND_CURSOR,
                "priority": _PRIORITY[KIND_CURSOR],
                "title": f"{ext} needs code written{suffix}",
                "what": "This one needs an actual code change, which happens in Cursor rather than automatically.",
                "do": "Copy the brief, do it in Cursor, then mark it applied.",
                "why": items[0].get("reason") or "Pending Cursor coding work",
                "recommended_verb": "open_cursor",
                "batchable": True,
                "items": items,
                "links": items[0].get("links") or [],
                "path": jobs[0].get("_path") or "",
                "paths": [j.get("_path") for j in jobs if j.get("_path")],
                "thread_id": jobs[0].get("thread_id"),
            }
        )

    reason_groups: dict[str, list[dict[str, Any]]] = {}
    for j in frontier:
        reason = (j.get("reason") or "unknown").strip() or "unknown"
        reason_groups.setdefault(reason, []).append(j)
    for reason, jobs in sorted(reason_groups.items(), key=lambda kv: -len(kv[1])):
        items = [_item_from_job(j, linear_workspace, kind="frontier") for j in jobs]
        actions.append(
            {
                "id": f"frontier:{hash(reason) & 0xffffffff:x}",
                "kind": KIND_FRONTIER,
                "priority": _PRIORITY[KIND_FRONTIER],
                "title": f"{len(jobs)} tickets need a judgement call",
                "what": f"All {len(jobs)} stopped for the same reason, so it's one decision, not {len(jobs)}.",
                "do": "Decide once and it applies to all of them.",
                "why": "Same failure pattern — fix once / batch requeue, do not treat as 25 tickets",
                "recommended_verb": "batch_frontier",
                "batchable": True,
                "items": items,
                "links": [],
                "reason": reason,
                "paths": [j.get("_path") for j in jobs if j.get("_path")],
                "thread_ids": [j.get("thread_id") for j in jobs if j.get("thread_id")],
            }
        )

    atlas5_fresh = [t for t in fresh if (t.get("executor") or "") == "atlas5"]
    if atlas5_fresh:
        actions.append(
            {
                "id": "wait:atlas5",
                "kind": KIND_WAIT,
                "priority": _PRIORITY[KIND_WAIT],
                "title": f"Bots are working on {len(atlas5_fresh)} tickets",
                "what": "Nothing for you here. This is just so you can see it's actually running.",
                "do": "",
                "why": "Healthy in-flight builds; watchdog will reconcile when done",
                "recommended_verb": "none",
                "batchable": False,
                "items": [_item_from_thread(t, linear_workspace, now=now) for t in atlas5_fresh[:8]],
                "links": [{"label": "Atlas6 verify", "href": "http://127.0.0.1:8767/"}],
                "informational": True,
            }
        )

    actions.sort(key=lambda a: (a["priority"], a["title"]))

    # Hard attention cap. Everything below the cut is summarised in one card so
    # nothing disappears silently, but the screen stays answerable in a minute.
    deferred_count = 0
    if max_actions and len(actions) > max_actions:
        keep = [a for a in actions[:max_actions]]
        dropped = actions[max_actions:]
        deferred_count = sum(len(a.get("items") or []) or 1 for a in dropped)
        keep.append(
            {
                "id": "deferred:overflow",
                "kind": KIND_WAIT,
                "priority": 9,
                "title": f"{deferred_count} more things, hidden to keep this short",
                "what": "Clear the items above and these move up.",
                "do": "",
                "why": "Held back to keep this screen answerable. Raise max_actions or clear the top items first.",
                "recommended_verb": "none",
                "batchable": False,
                "items": [],
                "links": [],
                "informational": True,
                "deferred_titles": [str(a.get("title"))[:80] for a in dropped[:12]],
            }
        )
        actions = keep

    by_kind = Counter(a["kind"] for a in actions if not a.get("informational"))
    # Count actionable items not just action cards for chart weight
    gate_count = len(awaiting) + sum(
        1
        for t in gates
        if (t.get("external_id") or "").upper() not in seen_tickets
    )
    by_kind_weighted = {
        KIND_GATE: gate_count,
        KIND_UNSTICK: len(stale),
        KIND_CURSOR: len(cursor),
        KIND_FRONTIER: len(frontier),
        KIND_WAIT: len(atlas5_fresh),
    }
    by_executor = Counter((t.get("executor") or "unknown") for t in inflight)
    frontier_reasons = Counter((j.get("reason") or "unknown") for j in frontier)

    justin_touchable = [a for a in actions if a["kind"] in {KIND_GATE, KIND_UNSTICK, KIND_CURSOR, KIND_FRONTIER}]

    # One plain sentence a human can read in two seconds. Replaces the charts.
    need_you = len([a for a in actions if a["kind"] == KIND_GATE])
    bots_on = len(atlas5_fresh)
    stuck_n = len(stale)
    last_done = (alarm or {}).get("hours_since_last_terminal")
    done_total = (alarm or {}).get("done_total")
    bits: list[str] = []
    if need_you == 0:
        bits.append("Nothing needs you right now.")
    elif need_you == 1:
        bits.append("1 thing needs you.")
    else:
        bits.append(f"{need_you} things need you.")
    if bots_on:
        bits.append(f"Bots are working on {bots_on}.")
    if stuck_n == 1:
        bits.append("1 has gone quiet.")
    elif stuck_n:
        bits.append(f"{stuck_n} have gone quiet.")
    if done_total == 0:
        bits.append("Nothing has ever finished — something is wrong.")
    elif last_done is not None and last_done > 24:
        bits.append(f"Nothing has finished in {int(last_done // 24)}d.")
    elif last_done is not None:
        bits.append(f"Last finished {int(last_done)}h ago.")
    headline = " ".join(bits)

    return {
        "headline": headline,
        "generated_at": now.isoformat(),
        "stale_inflight_minutes": stale_inflight_minutes,
        "linear_workspace": linear_workspace,
        "actions": actions,
        "justin_touchable_count": len(justin_touchable),
        # The completion alarm (§1.10) previously only reached a log file nobody
        # reads. Carrying it here puts it on the surface where it means something.
        "alarm": alarm or {},
        "probes_hidden": probes_hidden,
        "include_probes": include_probes,
        "deferred_count": deferred_count,
        "charts": {
            "by_kind": by_kind_weighted,
            "by_kind_cards": dict(by_kind),
            "by_executor": dict(by_executor),
            "stale_vs_fresh": {"stale": len(stale), "fresh": len(fresh)},
            "frontier_reason_buckets": [
                {"reason": r, "count": n} for r, n in frontier_reasons.most_common(8)
            ],
        },
        "summary": {
            "gates": gate_count,
            "gate_causes": len(gate_groups),
            "probes_hidden": probes_hidden,
            "awaiting_input": len(awaiting),
            "stale": len(stale),
            "cursor": len(cursor),
            "frontier": len(frontier),
            "atlas5_working": len(atlas5_fresh),
            "ready": int((status.get("counts") or {}).get("ready") or 0),
        },
    }


# ---------------------------------------------------------------------------
# The board: a dense, Linear-style list. Sections by what is needed, one row
# per ticket showing the REAL ticket title, and stuck work grouped by the
# reason it stopped so N tickets with one cause are one decision.
#
# The earlier card design failed because it showed categories instead of work:
# "REV-302 needs code written" says nothing about what REV-302 is.
# ---------------------------------------------------------------------------

# blocker text -> (short label a human can group by, what it actually means)
_REASONS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"job_state=unknown", re.I),
     "The bot never picked it up",
     "It was handed over but the worker never started it. Usually the queue was blocked."),
    (re.compile(r"job_state=completed", re.I),
     "Finished but never proved it",
     "The bot says it completed, but there is no verification receipt — so we don't trust it. "
     "That check is what catches a bot claiming a deploy it never made."),
    (re.compile(r"job_state=running", re.I),
     "Stuck part-way through",
     "The bot started and never came back. It is probably wedged on something."),
    (re.compile(r"no VH receipt|verified.handback", re.I),
     "Nothing came back",
     "Handed over, the queue emptied, but no result was ever reported."),
    (re.compile(r"no_artifact", re.I),
     "No handback artifact",
     "The worker stopped without leaving a receipt the ledger trusts. "
     "Approve clears this Justin gate so triage can continue; Reject parks the ticket; "
     "Start over sends it back to the bots from scratch."),
    (re.compile(r"no deterministic route|escalate frontier", re.I),
     "Needs a judgement call",
     "Nothing on the automation path can finish this — it needs a human judgement "
     "(frontier), not another Salesforce-gate Approve."),
    (re.compile(r"wip_collapse", re.I),
     "Held for WIP limit",
     "Atlas5 already has something in flight, so this was demoted rather than started. "
     "Approve clears the gate; the conductor will pick it up when a slot opens."),
    (re.compile(r"no grounding", re.I),
     "The bot lacked Salesforce details",
     "It could not read your objects and fields, so it asked you instead of investigating. "
     "It can look them up itself now."),
    (re.compile(r"autonomy=", re.I),
     "Needs your approval to run",
     "This touches something outside a test sandbox, so it will not run on its own."),
    (re.compile(r"unhealthy", re.I),
     "The Salesforce bot was down",
     "It was parked because the worker was unreachable at the time."),
    (re.compile(r"supervised_build_failed|exit=1|build failed", re.I),
     "The build failed",
     "A real failure with an error to read — not a timeout or a queue problem."),
]


def human_reason(blocker: str) -> tuple[str, str]:
    """Plain label + full explanation for why a ticket stopped.

    Returns the reason *untruncated*. Callers clip for display with `clip`,
    which marks the cut — a gate decision must never be made on a sentence
    that stops mid-word with no way to read the rest.
    """
    b = (blocker or "").strip()
    if not b:
        return "Stopped without a reason", "Nothing recorded why. It needs a fresh start."
    for pat, label, why in _REASONS:
        if pat.search(b):
            return label, why
    # A question directed at a human reads as its own reason.
    if "?" in b:
        return "Waiting on an answer", b
    return "Stopped", b


def clip(text: str, limit: int = 400) -> str:
    """Shorten for display, cutting on a word boundary and marking the cut."""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    head = t[:limit]
    space = head.rfind(" ")
    if space > limit * 0.6:
        head = head[:space]
    return head.rstrip(" ,.;:") + "…"


def _age_phrase(mins: float | None) -> str:
    if mins is None:
        return ""
    if mins < 90:
        return f"{int(mins)}m"
    if mins < 48 * 60:
        return f"{int(round(mins / 60))}h"
    return f"{int(round(mins / 1440))}d"


def _advance_signal(t: dict[str, Any]) -> str:
    """One line: what Justin needs to do (or wait on) to move this ticket."""
    nxt = " ".join(str(t.get("next_action") or "").split())
    if nxt:
        return nxt[:140]
    goal = _goal_excerpt(str(t.get("goal") or ""), n=120)
    if goal:
        return goal
    blocker = " ".join(str(t.get("blocker") or "").split())
    if blocker:
        return blocker[:140]
    return ""


def _row(t: dict[str, Any], workspace: str, *, now: datetime) -> dict[str, Any]:
    ext = str(t.get("external_id") or "")
    mins = age_minutes(t.get("last_dispatched_at") or t.get("updated_at"), now=now)
    signal = _advance_signal(t)
    out = {
        "ticket": ext,
        "title": str(t.get("title") or "(untitled)")[:110],
        "age": _age_phrase(mins),
        "age_minutes": round(mins, 1) if mins is not None else None,
        "thread_id": t.get("id"),
        "link": linear_url(ext, workspace),
    }
    if signal:
        out["signal"] = signal
    return out


def build_board(
    status: dict[str, Any],
    *,
    awaiting_input: list[dict[str, Any]] | None = None,
    alarm: dict[str, Any] | None = None,
    linear_workspace: str = "clearspeed",
    include_probes: bool = False,
    stale_inflight_minutes: int = 120,
) -> dict[str, Any]:
    now = _now()
    probe_ids = set() if include_probes else probe_ticket_ids(status)

    def keep(t: dict[str, Any]) -> bool:
        if include_probes:
            return True
        return not is_probe(t) and str(t.get("external_id") or "").upper() not in probe_ids

    gates = [t for t in (status.get("blocked_justin") or []) if keep(t)]
    frontier_threads = [t for t in (status.get("blocked_frontier") or []) if keep(t)]
    inflight = [t for t in (status.get("in_flight") or []) if keep(t)]
    ready = [t for t in (status.get("ready") or []) if keep(t)]
    # Filter awaiting by id AND by the row's own text: the status thread lists
    # rotate, so probe_ids goes stale — that's how aged-out parked probes leak
    # back onto the board as "questions for Justin".
    awaiting_all = list(awaiting_input or [])
    awaiting = [
        q for q in awaiting_all
        if include_probes
        or (str(q.get("ticket_id") or "").upper() not in probe_ids and not is_probe(q))
    ]
    hidden = len(awaiting_all) - len(awaiting)
    for key in ("blocked_justin", "blocked_frontier", "in_flight", "ready"):
        hidden += len(status.get(key) or []) - len([t for t in (status.get(key) or []) if keep(t)])

    # Prefer the ledger's ticket title over Atlas5's question-as-title so the
    # card names the work, not the bot's confusion.
    title_by_ext: dict[str, str] = {}
    for key in ("blocked_justin", "blocked_frontier", "in_flight", "ready", "done"):
        for t in status.get(key) or []:
            if not isinstance(t, dict):
                continue
            ext = str(t.get("external_id") or "").upper().strip()
            title = str(t.get("title") or "").strip()
            if ext and title and not is_probe(t):
                title_by_ext.setdefault(ext, title)

    # --- Needs you: a real question, or a decision only a human can make ------
    needs_you: list[dict[str, Any]] = []
    seen: set[str] = set()
    retriage_rows: list[dict[str, Any]] = []
    for q in awaiting:
        tid = str(q.get("ticket_id") or "").upper()
        question = str(q.get("question") or "").strip()
        if not tid or tid in seen:
            continue
        age_mins = q.get("age_s", 0) / 60 if q.get("age_s") else None
        await_title = str(q.get("title") or "").strip()
        # Never put the question text in the title slot — that is what made
        # REV-367/408 unreadable on the live board.
        if await_title and question and (
            await_title == question[: len(await_title)] or await_title.startswith(question[:40])
        ):
            await_title = ""
        display_title = (title_by_ext.get(tid) or await_title or tid)[:110]
        row_common = {
            "ticket": tid,
            "title": display_title,
            "age": _age_phrase(age_mins),
            # Numeric age so the UI can sort chronologically; "17h" vs "5d"
            # string-compares wrong.
            "age_minutes": round(age_mins, 1) if age_mins is not None else None,
            "link": linear_url(tid, linear_workspace),
        }
        # "I had no grounding" is a scoping failure, not a question for Justin —
        # but it must stay visible (bot-side, restartable), or it rots for weeks.
        if question_needs_retriage(question):
            retriage_rows.append(row_common)
            continue
        seen.add(tid)
        signal = question or "Needs your answer"
        needs_you.append(
            {**row_common, "question": question, "verb": "answer", "signal": signal[:140]}
        )
    for t in gates:
        ext = str(t.get("external_id") or "").upper()
        if ext in seen:
            continue
        label, why = human_reason(str(t.get("blocker") or ""))
        # Mechanical stalls are not decisions — they belong in Stuck.
        if label in ("The bot never picked it up", "Finished but never proved it",
                     "Stuck part-way through", "Nothing came back",
                     "The bot lacked Salesforce details", "The Salesforce bot was down"):
            frontier_threads.append(t)
            continue
        seen.add(ext)
        r = _row(t, linear_workspace, now=now)
        # `why` is what Justin reads before Approve/Reject. Send the clipped
        # form for the card and the full text for the disclosure, so the
        # decision is never made on a half-sentence.
        shown = clip(why)
        r.update({"verb": "decide", "reason": label, "why": shown})
        if shown != why:
            r["why_full"] = why
        r["signal"] = (f"Decide: {label}" + (f" — {shown}" if shown else ""))[:140]
        needs_you.append(r)

    # --- Stuck: grouped by reason, so N tickets with one cause is one decision -
    buckets: dict[str, dict[str, Any]] = {}
    for t in frontier_threads:
        label, why = human_reason(str(t.get("blocker") or ""))
        g = buckets.setdefault(
            label, {"reason": label, "why": clip(why), "rows": [], "unstick": "requeue"}
        )
        row = _row(t, linear_workspace, now=now)
        row["signal"] = (label if not why else f"{label} — {clip(why, 80)}")[:140]
        g["rows"].append(row)
    groups = list(buckets.values())
    if retriage_rows:
        # Atlas5-paused rows: no atlas6 thread_id, so requeue can't reach them —
        # the remedy is steering ("look it up yourself"), which auto-resumes.
        for row in retriage_rows:
            row["signal"] = "Restart: tell it to look up Salesforce details itself"
        groups.append({
            "reason": "Asked for Salesforce details it can now look up",
            "why": "It paused to ask for schema facts instead of investigating. "
                   "Grounding works now — restarting tells it to look them up itself.",
            "rows": retriage_rows,
            "unstick": "steer",
        })
    stuck = sorted(groups, key=lambda g: -len(g["rows"]))
    for g in stuck:
        g["count"] = len(g["rows"])
        g["thread_ids"] = [r["thread_id"] for r in g["rows"] if r.get("thread_id")]
        g["tickets"] = [r["ticket"] for r in g["rows"] if r.get("ticket")]

    working = [_row(t, linear_workspace, now=now) for t in inflight]
    queued = [_row(t, linear_workspace, now=now) for t in ready]

    stuck_total = sum(g["count"] for g in stuck)
    a = alarm or {}
    # Headline is overwritten by attach_focus() with the focus analyzer's
    # sentence (batched gates count as one). Keep a board-local fallback for
    # callers that skip attach.
    if needs_you:
        n = len(needs_you)
        head = f"{n} thing needs you." if n == 1 else f"{n} things need you."
    else:
        head = "Nothing needs you."
    if working:
        head += f" {len(working)} with the bots."
    if stuck_total:
        head += f" {stuck_total} stuck."
    if a.get("done_total") == 0:
        head += " Nothing has ever finished."

    return {
        "headline": head,
        "generated_at": now.isoformat(),
        "needs_you": needs_you,
        "working": working,
        "queued": queued,
        "stuck": stuck,
        "stuck_total": stuck_total,
        "hidden": hidden,
        "alarm": a,
        "counts": {
            "needs_you": len(needs_you),
            "working": len(working),
            "queued": len(queued),
            "stuck": stuck_total,
        },
    }


def attach_focus(board: dict[str, Any], focus: dict[str, Any]) -> dict[str, Any]:
    """Merge focus actions onto the board payload — one fetch, one needs-you number.

    The board keeps per-ticket rows for expand/detail. The surface leads with
    ``actions`` (batched). Headline and ``counts.needs_you`` come from focus so
    "/api/board" and "/api/focus" never disagree about how many things need Justin.
    """
    actions = list(focus.get("actions") or [])
    gate_cards = [a for a in actions if a.get("kind") == KIND_GATE]
    touchable = int(focus.get("justin_touchable_count") or 0)
    out = dict(board)
    out["headline"] = focus.get("headline") or board.get("headline") or ""
    out["actions"] = actions
    out["focus_summary"] = focus.get("summary") or {}
    out["justin_touchable_count"] = touchable
    out["deferred_count"] = int(focus.get("deferred_count") or 0)
    counts = dict(out.get("counts") or {})
    # Badge = decisions (batched), not raw ticket rows.
    counts["needs_you"] = len(gate_cards) if gate_cards else touchable
    counts["needs_you_items"] = len(board.get("needs_you") or [])
    counts["touchable"] = touchable
    out["counts"] = counts
    # Carry focus alarm if board lacked one.
    if focus.get("alarm") and not (out.get("alarm") or {}).get("alarm"):
        out["alarm"] = focus["alarm"]
    return out


_TOUCHABLE_KINDS = {KIND_GATE, KIND_UNSTICK, KIND_CURSOR, KIND_FRONTIER}


def spoken_next_decision(board: dict[str, Any] | None) -> str:
    """One short spoken question Justin can approve or answer. Never a briefing.

    The screen can show eight cards. Out loud, only the top item is a question —
    dumping eighty ready tickets is what made him say "shit" while it kept talking.
    """
    board = board or {}
    actions = [
        a
        for a in (board.get("actions") or [])
        if isinstance(a, dict)
        and not a.get("informational")
        and a.get("kind") in _TOUCHABLE_KINDS
    ]
    gates = [a for a in actions if a.get("kind") == KIND_GATE]
    pick = gates or actions
    if pick:
        top = pick[0]
        n = len(pick)
        more = f" Then {n - 1} more." if n > 1 and top.get("kind") == KIND_GATE else ""
        verb = str(top.get("recommended_verb") or "")
        title = str(top.get("title") or "").strip() or "This one"
        why = clip(str(top.get("why") or top.get("what") or ""), 100)
        kind = top.get("kind")
        if kind == KIND_GATE:
            if verb == "answer_input":
                tid_m = re.search(r"\b(REV-\d+)\b", title, re.I)
                tid = tid_m.group(1).upper() if tid_m else title
                body = why or "Needs your answer."
                if not body.endswith(("?", ".", "!")):
                    body += "."
                return f"{tid}: {body} What's your answer?{more}"
            return f"{title}. Approve, reject, or start over?{more}"
        if kind == KIND_FRONTIER:
            return f"{title}. Needs a judgement call — sit with it?"
        if kind == KIND_CURSOR:
            return f"{title}. Needs code — want Cursor on it?"
        return f"{title}. It's gone quiet — requeue?"

    rows = [r for r in (board.get("needs_you") or []) if isinstance(r, dict)]
    if rows:
        top = rows[0]
        n = len(rows)
        more = f" Then {n - 1} more." if n > 1 else ""
        who = speakable_name(
            str(top.get("ticket") or ""),
            str(top.get("title") or ""),
            fallback="This",
        )
        if top.get("verb") == "answer":
            q = clip(str(top.get("question") or top.get("signal") or "Needs your answer"), 120)
            if not q.endswith(("?", ".", "!")):
                q += "."
            return f"{who}: {q} What's your answer?{more}"
        reason = clip(str(top.get("reason") or top.get("signal") or "Needs a decision"), 100)
        return f"{who}: {reason}. Approve or reject?{more}"

    queued = [r for r in (board.get("queued") or []) if isinstance(r, dict)]
    if queued:
        top = queued[0]
        who = speakable_name(
            str(top.get("ticket") or ""),
            str(top.get("title") or top.get("signal") or "ready work"),
            fallback="This",
        )
        title = clip(str(top.get("title") or top.get("signal") or "ready work"), 80)
        return f"{who}: {title}. Ready — want me to dispatch it?"

    return "Nothing needs you right now."
