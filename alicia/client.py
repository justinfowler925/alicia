"""Compatibility client for the temporarily disabled Atlas services."""

from __future__ import annotations

import re
from typing import Any

import httpx

from .config import AliciaCfg, load_config

_PLACEHOLDER_RE = re.compile(r"^<[^>]*>$")
_MISSING_INPUT_RE = re.compile(r"missing\s+input", re.IGNORECASE)


class AtlasDisabled(RuntimeError):
    """Raised before I/O when Alicia is running in standalone mode."""


# A scoping failure is not a question Justin can usefully answer. When the model
# reports it had no schema/field/flow grounding, the right move is to re-triage
# the ticket with real grounding, not to hand a human a wall of prose. These
# dominated the live queue (~15 of 25), which is why the surface felt useless.
_NO_GROUNDING_RE = re.compile(
    r"no grounding (?:data|information)"
    r"|contains no grounding"
    r"|no (?:salesforce )?(?:object|field|flow)[^.]{0,40}descriptions?"
    r"|\(no grounding",
    re.IGNORECASE,
)


def question_is_real(question: str | None) -> bool:
    """True when a human could actually answer this in one line."""
    text = (question or "").strip()
    if len(text) < 15:
        return False
    if _PLACEHOLDER_RE.match(text):
        return False
    if text.lower() in {"none", "null"}:
        return False
    if _MISSING_INPUT_RE.search(text) and len(text) < 40:
        return False
    return True


def question_needs_retriage(question: str | None) -> bool:
    """True when the 'question' is really the model reporting it lacked grounding."""
    return bool(_NO_GROUNDING_RE.search((question or "").strip()))


class AtlasClient:
    def __init__(self, cfg: AliciaCfg | None = None) -> None:
        self.cfg = cfg or load_config()

    def _url(self, path: str) -> str:
        self._ensure_enabled()
        return f"{self.cfg.atlas6_url.rstrip('/')}{path}"

    def _atlas5_url(self, path: str) -> str:
        self._ensure_enabled()
        return f"{self.cfg.atlas5_url.rstrip('/')}{path}"

    def _ensure_enabled(self) -> None:
        if not self.cfg.atlas_enabled:
            raise AtlasDisabled("Atlas is intentionally ignored by Alicia")

    def health(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/healthz"))
            r.raise_for_status()
            return r.json()

    def digest(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/digest"))
            r.raise_for_status()
            return r.json()

    def register(
        self,
        title: str,
        *,
        external_id: str | None = None,
        source: str = "manual",
        goal: str = "",
    ) -> dict[str, Any]:
        payload = {
            "title": title,
            "source": source,
            "external_id": external_id,
            "goal": goal,
        }
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(self._url("/api/threads"), json=payload)
            r.raise_for_status()
            return r.json()

    def chat(
        self,
        message: str,
        *,
        mode: str = "manager",
        persona: str = "alicia",
        ticket_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message, "mode": mode, "persona": persona}
        if ticket_id:
            payload["ticket_id"] = ticket_id
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(self._url("/api/chat"), json=payload)
            r.raise_for_status()
            return r.json()

    def dispatch_tick(self, *, dry_run: bool = False, ingest_linear: bool = False) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(
                self._url("/api/dispatch/tick"),
                json={"dry_run": dry_run, "ingest_linear": ingest_linear},
            )
            r.raise_for_status()
            return r.json()

    def approve(self, thread_id: str, *, decision: str = "approve") -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(
                self._url(f"/api/threads/{thread_id}/approve"),
                json={"decision": decision},
            )
            r.raise_for_status()
            return r.json()

    def list_threads(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/threads"), params={"open_only": "true"})
            r.raise_for_status()
            return r.json()

    def reconcile(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(self._url("/api/reconcile"))
            r.raise_for_status()
            return r.json()

    def ingest_linear(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(self._url("/api/ingest/linear"))
            r.raise_for_status()
            return r.json()

    def brief(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/brief"))
            r.raise_for_status()
            return r.json()

    def peek_slack(self, *, limit: int | None = None) -> dict[str, Any]:
        """Read-only Slack work-signal peek (no ledger writes)."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/peek/slack"), params=params)
            r.raise_for_status()
            return r.json()

    def peek_gmail(self, *, limit: int | None = None) -> dict[str, Any]:
        """Read-only Gmail work-signal peek (no ledger writes)."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/peek/gmail"), params=params)
            r.raise_for_status()
            return r.json()

    def ingest_slack(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(self._url("/api/ingest/slack"))
            r.raise_for_status()
            return r.json()

    def ingest_gmail(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(self._url("/api/ingest/gmail"))
            r.raise_for_status()
            return r.json()

    def frontier(self, status: str = "pending") -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/frontier"), params={"status": status})
            r.raise_for_status()
            return r.json()

    def frontier_apply(
        self,
        *,
        path: str | None = None,
        thread_id: str | None = None,
        next_action: str = "investigate",
        notes: str = "",
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(
                self._url("/api/frontier/apply"),
                json={
                    "path": path,
                    "thread_id": thread_id,
                    "next_action": next_action,
                    "notes": notes,
                },
            )
            r.raise_for_status()
            return r.json()

    def cursor(self, status: str = "pending") -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/cursor"), params={"status": status})
            r.raise_for_status()
            return r.json()

    def cursor_apply(
        self,
        *,
        path: str | None = None,
        thread_id: str | None = None,
        next_action: str = "dispatch_atlas5",
        notes: str = "",
        evidence: str = "",
        mark_done: bool = False,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(
                self._url("/api/cursor/apply"),
                json={
                    "path": path,
                    "thread_id": thread_id,
                    "next_action": next_action,
                    "notes": notes,
                    "evidence": evidence,
                    "mark_done": mark_done,
                },
            )
            r.raise_for_status()
            return r.json()

    def cursor_dispatch(self, *, thread_id: str | None = None, external_id: str | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(
                self._url("/api/cursor/dispatch"),
                params={"thread_id": thread_id, "external_id": external_id},
            )
            r.raise_for_status()
            return r.json()

    def status(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._url("/api/status"))
            r.raise_for_status()
            return r.json()

    def requeue_threads(
        self,
        thread_ids: list[str],
        *,
        next_action: str = "triage",
        note: str = "requeued_from_alicia",
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(
                self._url("/api/threads/requeue"),
                json={
                    "thread_ids": thread_ids,
                    "next_action": next_action,
                    "note": note,
                },
            )
            r.raise_for_status()
            return r.json()

    def list_awaiting_input(self) -> list[dict[str, Any]]:
        """Real Atlas5 paused questions (phantoms filtered out).

        Retries briefly on tunnel/Studio disconnects so the board does not
        blank Needs-you on a single flaky read.
        """
        last_exc: Exception | None = None
        body: Any = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=min(self.cfg.timeout_s, 20.0)) as c:
                    r = c.get(self._atlas5_url("/api/operator/linear-queue"))
                    r.raise_for_status()
                    body = r.json()
                break
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt >= 2:
                    raise
                continue
        if body is None and last_exc is not None:
            raise last_exc
        items = body.get("items") if isinstance(body, dict) else body
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ledger = item.get("ledger") or {}
            run_state = str(ledger.get("run_state") or "").lower()
            if run_state != "paused":
                continue
            wq = item.get("work_question") or {}
            question = wq.get("question") if isinstance(wq, dict) else wq
            question = str(question or "").strip()
            if not question_is_real(question):
                continue
            out.append(
                {
                    "ticket_id": item.get("ticket_id"),
                    "action": item.get("action") or (wq.get("action") if isinstance(wq, dict) else "") or "investigate",
                    "title": item.get("title") or "",
                    "question": question,
                    "age_s": ledger.get("age_s"),
                    "linear_url": ((item.get("linear") or {}).get("url") or ""),
                }
            )
        return out

    def answer_steering(
        self,
        ticket_id: str,
        body: str,
        *,
        scope: str = "next_turn",
        replace_pending: bool = True,
    ) -> dict[str, Any]:
        """POST steering — Atlas5 auto-resumes paused jobs when applicable.

        If Studio still has the old steering path (answer saved, resume fails
        with "no inbox work order"), recover here: re-drop a work order,
        uncap attempts when needed, then resume. Atlas4 has the same fix
        in-process; this covers live Studio until that deploy lands.
        """
        tid = (ticket_id or "").strip().upper()
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.post(
                self._atlas5_url(f"/api/operator/linear-queue/{tid}/steering"),
                json={
                    "body": body,
                    "scope": scope,
                    "replace_pending": replace_pending,
                },
            )
            r.raise_for_status()
            data = r.json()
        if not isinstance(data, dict):
            data = {}
        err = str(data.get("dispatch_error") or "")
        if data.get("ok") and not data.get("resumed") and "no inbox work order" in err:
            data = self._recover_missing_work_order(tid, body, data)
        return data

    def _queue_item(self, ticket_id: str) -> dict[str, Any]:
        tid = (ticket_id or "").strip().upper()
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._atlas5_url("/api/operator/linear-queue"))
            r.raise_for_status()
            body = r.json()
        items = body.get("items") if isinstance(body, dict) else body
        if not isinstance(items, list):
            return {}
        for item in items:
            if isinstance(item, dict) and str(item.get("ticket_id") or "").upper() == tid:
                return item
        return {}

    def _recover_missing_work_order(
        self,
        ticket_id: str,
        answer_body: str,
        prior: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-drop inbox + resume after steering saved but dispatch had no file."""
        tid = (ticket_id or "").strip().upper()
        item = self._queue_item(tid)
        ledger = item.get("ledger") if isinstance(item.get("ledger"), dict) else {}
        action = (
            str(item.get("action") or ledger.get("action") or "investigate").strip()
            or "investigate"
        )
        attempts = int(ledger.get("attempts") or 0)
        work_body = (
            f"# Operator resume after answer\n\n"
            f"Ticket: {tid}\n"
            f"Action: {action}\n\n"
            f"## Operator answer (also in steering store)\n\n"
            f"{(answer_body or '').strip()[:4000] or '(no body)'}\n"
        )
        prior_err = str(prior.get("dispatch_error") or "")
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            if attempts >= 5:
                rr = c.post(
                    self._atlas5_url(f"/api/operator/linear-queue/{tid}/reset-attempts"),
                    json={"action": action, "reason": "alicia answer recovery"},
                )
                if rr.status_code >= 400:
                    prior["dispatch_error"] = (
                        f"{prior_err}; reset-attempts failed: HTTP {rr.status_code}"
                    )
                    prior["recovered"] = False
                    return prior
            drop = c.post(
                self._atlas5_url("/api/operator/inbox"),
                json={"ticket_id": tid, "action": action, "body": work_body},
            )
            if drop.status_code >= 400:
                prior["dispatch_error"] = (
                    f"{prior_err}; re-drop failed: HTTP {drop.status_code} {drop.text[:200]}"
                )
                prior["recovered"] = False
                return prior
            resume = c.post(
                self._atlas5_url(f"/api/operator/linear-queue/{tid}/resume"),
                json={"action": action, "reason": "alicia re-drop after answer"},
            )
            if resume.status_code >= 400:
                prior["dispatch_error"] = (
                    f"{prior_err}; after re-drop resume failed: "
                    f"HTTP {resume.status_code} {resume.text[:200]}"
                )
                prior["recovered"] = False
                return prior
        prior["resumed"] = True
        prior["dispatch_error"] = None
        prior["recovered"] = True
        prior["redropped"] = True
        return prior

    def list_capped_attempts(self, *, min_attempts: int = 5) -> list[dict[str, Any]]:
        """Tickets whose job ledger attempts are at/over the retry cap."""
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            r = c.get(self._atlas5_url("/api/operator/linear-queue"))
            r.raise_for_status()
            body = r.json()
        items = body.get("items") if isinstance(body, dict) else body
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ledger = item.get("ledger") if isinstance(item.get("ledger"), dict) else {}
            attempts = int(ledger.get("attempts") or 0)
            if attempts < min_attempts:
                continue
            tid = str(item.get("ticket_id") or "").upper()
            if not tid:
                continue
            out.append(
                {
                    "ticket": tid,
                    "action": str(
                        item.get("action") or ledger.get("action") or "investigate"
                    ).strip()
                    or "investigate",
                    "attempts": attempts,
                    "run_state": str(ledger.get("run_state") or ""),
                    "status": str(ledger.get("status") or ""),
                    "notes": str(ledger.get("notes") or "")[:180],
                    "title": str(item.get("title") or tid)[:110],
                }
            )
        out.sort(key=lambda r: (-int(r["attempts"]), r["ticket"]))
        return out

    def reset_attempts(
        self,
        ticket_id: str,
        *,
        action: str = "investigate",
        reason: str = "alicia operator uncap",
        resume: bool = True,
    ) -> dict[str, Any]:
        """Reset attempt counter (and optionally resume) for a capped ticket."""
        tid = (ticket_id or "").strip().upper()
        act = (action or "investigate").strip() or "investigate"
        with httpx.Client(timeout=self.cfg.timeout_s) as c:
            rr = c.post(
                self._atlas5_url(f"/api/operator/linear-queue/{tid}/reset-attempts"),
                json={"action": act, "reason": reason},
            )
            if rr.status_code >= 400:
                return {
                    "ok": False,
                    "ticket_id": tid,
                    "error": f"reset-attempts HTTP {rr.status_code}: {rr.text[:200]}",
                }
            data: dict[str, Any] = {"ok": True, "ticket_id": tid, "reset": rr.json()}
            if resume:
                resume_r = c.post(
                    self._atlas5_url(f"/api/operator/linear-queue/{tid}/resume"),
                    json={"action": act, "reason": reason},
                )
                if resume_r.status_code >= 400:
                    data["resumed"] = False
                    data["resume_error"] = (
                        f"HTTP {resume_r.status_code}: {resume_r.text[:200]}"
                    )
                    # Reset succeeded; resume may need a work order — still ok.
                    data["ok"] = True
                else:
                    data["resumed"] = True
                    data["resume"] = resume_r.json()
            return data
