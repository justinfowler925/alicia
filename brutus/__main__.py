"""Brutus CLI — laptop remote control for Studio Atlas6."""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from .canon.models import EvidenceType, InboxStatus, WorkItemState, WorkItemType
from .chat_resolve import resolve_chat_reply
from .client import AtlasClient
from .config import load_config
from .local_llm import list_models, probe_generation
from .memory import MemoryStore
from .paths import canon_db_path


def _die(msg: str, code: int = 1) -> None:
    print(f"brutus: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _resolve_thread_id(client: AtlasClient, target: str) -> str:
    if not target.upper().startswith("REV-"):
        return target
    threads = client.list_threads().get("threads") or []
    match = next(
        (t for t in threads if (t.get("external_id") or "").upper() == target.upper()),
        None,
    )
    if not match:
        _die(f"no open thread for {target.upper()} — try brutus register … --id {target.upper()}")
    return str(match["id"])


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="brutus",
        description="Brutus — MacBook client for Studio Atlas (ledger stays on Studio)",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("health", help="Ping Studio Atlas6")
    sub.add_parser("llm-health", help="Retired local-LLM probe (prints retired unless re-enabled)")
    sub.add_parser("digest", help="Print WIP digest from Studio ledger")
    sub.add_parser("threads", help="List open portfolio threads")
    sub.add_parser("reconcile", help="Ask Studio to reconcile VH handbacks")
    sub.add_parser("brief", help="Morning brief / Justin gates from Studio")

    reg = sub.add_parser("register", help="Register a work thread on Studio")
    reg.add_argument("title")
    reg.add_argument("--id", dest="external_id", help="e.g. REV-61")
    reg.add_argument("--source", default="manual")
    reg.add_argument("--goal", default="")

    chat = sub.add_parser("chat", help="Send a chat turn to Atlas6")
    chat.add_argument("message")
    chat.add_argument("--mode", default="manager", choices=["manager", "direct"])

    disp = sub.add_parser("dispatch", help="Ask Studio to run one dispatcher tick")
    disp.add_argument("--dry-run", action="store_true")
    disp.add_argument("--ingest-linear", action="store_true")

    sub.add_parser("ingest-linear", help="Pull open Linear issues into Studio ledger")
    peek_slack = sub.add_parser("peek-slack", help="Read-only Slack work-signal peek (no ledger writes)")
    peek_slack.add_argument("--limit", type=int, default=10)
    peek_mail = sub.add_parser("peek-email", help="Read-only Gmail work-signal peek (no ledger writes)")
    peek_mail.add_argument("--limit", type=int, default=10)
    sub.add_parser("ingest-slack", help="Scan Slack channels into Studio ledger (Studio-side)")
    sub.add_parser("ingest-gmail", help="Scan Gmail into Studio ledger (Studio-side)")
    sub.add_parser("frontier", help="List pending frontier consults")
    sub.add_parser("cursor", help="List pending Cursor agent jobs")
    sub.add_parser("status", help="Full Studio status JSON")

    fapply = sub.add_parser("frontier-apply", help="Apply frontier result onto a thread")
    fapply.add_argument("--thread-id", default="")
    fapply.add_argument("--path", default="")
    fapply.add_argument("--next-action", default="investigate")
    fapply.add_argument("--notes", default="")

    capply = sub.add_parser("cursor-apply", help="Apply Cursor job result onto a thread")
    capply.add_argument("--thread-id", default="")
    capply.add_argument("--path", default="")
    capply.add_argument("--next-action", default="dispatch_atlas5")
    capply.add_argument("--notes", default="")
    capply.add_argument("--evidence", default="")
    capply.add_argument("--mark-done", action="store_true")

    sub.add_parser("mcp", help="Run Brutus MCP server (stdio) for Cursor")
    sub.add_parser("serve", help="Start laptop Brutus UI/API (default :8768)")
    sub.add_parser("owner-token", help="Print the local owner token for browser pairing")

    canon = sub.add_parser("canon", help="Review and accept canonical Work Items")
    canon.add_argument(
        "--db",
        dest="canon_db",
        default=os.environ.get("BRUTUS_CANON_DB_PATH") or str(canon_db_path()),
        help="Canon SQLite path (default: ~/.brutus/state/canon.sqlite; BRUTUS_CANON_DB_PATH overrides)",
    )
    canon_sub = canon.add_subparsers(dest="canon_command")
    canon_list = canon_sub.add_parser("list", help="List Work Items, optionally by state")
    canon_list.add_argument("--state", choices=[state.value for state in WorkItemState])
    canon_show = canon_sub.add_parser("show", help="Show a Work Item and its linked review objects")
    canon_show.add_argument("work_item_id")
    canon_accept = canon_sub.add_parser("accept", help="Accept a reviewed Work Item as owner")
    canon_accept.add_argument("work_item_id")
    canon_reject = canon_sub.add_parser("reject", help="Reject a reviewed Work Item and cancel it")
    canon_reject.add_argument("work_item_id")
    canon_reject.add_argument("--reason", required=True)
    canon_changes = canon_sub.add_parser(
        "request-changes",
        help="Return a reviewed Work Item to execution with an owner reason",
    )
    canon_changes.add_argument("work_item_id")
    canon_changes.add_argument("--reason", required=True)
    canon_inbox = canon_sub.add_parser("inbox", help="Review captured Canon InboxItems")
    canon_inbox_sub = canon_inbox.add_subparsers(dest="inbox_command")
    canon_inbox_list = canon_inbox_sub.add_parser("list", help="List captured InboxItems")
    canon_inbox_list.add_argument("--status", choices=[status.value for status in InboxStatus])
    canon_inbox_show = canon_inbox_sub.add_parser("show", help="Show an InboxItem and its provenance")
    canon_inbox_show.add_argument("inbox_item_id")
    canon_inbox_capture = canon_inbox_sub.add_parser(
        "capture-slack",
        help="Poll Atlas6's configured Slack channels into the Canon inbox",
    )
    canon_inbox_capture.add_argument("--limit", type=int, default=50)
    canon_inbox_manual_capture = canon_inbox_sub.add_parser(
        "capture",
        help="Capture one immutable manual InboxItem without promoting it",
    )
    canon_inbox_manual_capture.add_argument("--raw-capture", required=True)
    canon_inbox_manual_capture.add_argument(
        "--source",
        required=True,
        help="Recorded provenance (for example manual:meeting-2026-08-23)",
    )
    canon_inbox_promote = canon_inbox_sub.add_parser(
        "promote",
        help="Owner-review and promote one InboxItem into a triage Work Item",
    )
    canon_inbox_promote.add_argument("inbox_item_id")
    canon_inbox_promote.add_argument("--title", required=True)
    canon_inbox_promote.add_argument("--description", default="")
    canon_inbox_promote.add_argument("--type", choices=[kind.value for kind in WorkItemType], default="task")
    canon_inbox_promote.add_argument("--priority", type=int, default=0)
    canon_work = canon_sub.add_parser("work", help="Move a Work Item through non-owner Canon lifecycle states")
    canon_work_sub = canon_work.add_subparsers(dest="work_command")
    canon_transition = canon_work_sub.add_parser(
        "transition",
        help="Apply a validated lifecycle transition (use accept/close for owner-gated states)",
    )
    canon_transition.add_argument("work_item_id")
    canon_transition.add_argument(
        "--to",
        required=True,
        choices=[state.value for state in WorkItemState],
        help="Target state",
    )
    canon_transition.add_argument("--actor", default="atlas6-worker", help="Recorded actor for this transition")
    canon_transition.add_argument("--reason", default="", help="Required for blocked/canceled and review rework")
    canon_transition.add_argument("--decision-id", default="", help="Resolved Decision required to exit decision")
    canon_transition.add_argument("--approval-id", default="", help="Approval supporting communication work")
    canon_transition.add_argument(
        "--evidence-id",
        action="append",
        default=[],
        help="Evidence ID supporting validation/review (repeatable)",
    )
    canon_transition.add_argument(
        "--owner-review-comment",
        action="store_true",
        help="Record that the owner reviewed the policy document",
    )
    canon_transition.add_argument("--superseded-by", default="", help="Required target Work Item for superseded")
    canon_transition.add_argument(
        "--decision-not-required",
        default="",
        help="Required audit reason for lightweight task execution",
    )
    canon_transition.add_argument(
        "--lightweight-scope",
        default="",
        help="Required bounded scope for lightweight task execution",
    )
    canon_transition.add_argument(
        "--low-risk",
        action="store_true",
        help="Explicit required guard for lightweight task execution",
    )
    canon_decision = canon_sub.add_parser("decision", help="Create and link resolved Canon Decisions")
    canon_decision_sub = canon_decision.add_subparsers(dest="decision_command")
    canon_decision_create = canon_decision_sub.add_parser("create", help="Create a resolved Decision")
    canon_decision_create.add_argument("--question", required=True)
    canon_decision_create.add_argument("--chosen-option", required=True)
    canon_decision_create.add_argument("--rationale", required=True)
    canon_decision_create.add_argument("--decided-by", default=os.environ.get("BRUTUS_OWNER_IDENTITY", "justin.fowler@clearspeed.com"))
    canon_decision_create.add_argument("--option", action="append", default=[], help="Option considered (repeatable)")
    canon_decision_link = canon_decision_sub.add_parser("link", help="Link a Decision to a Work Item")
    canon_decision_link.add_argument("decision_id")
    canon_decision_link.add_argument("work_item_id")
    canon_run = canon_sub.add_parser("run", help="Create, dispatch, and advance Canon Runs")
    canon_run_sub = canon_run.add_subparsers(dest="run_command")
    canon_run_start = canon_run_sub.add_parser("start", help="Persist a started worker Run")
    canon_run_start.add_argument("work_item_id")
    canon_run_start.add_argument("--actor", required=True)
    canon_run_start.add_argument("--target", default="")
    canon_run_start.add_argument("--scope", default="")
    canon_run_dispatch = canon_run_sub.add_parser(
        "dispatch",
        help="Dispatch a local Hands adapter, persist artifacts, and record Prove",
    )
    canon_run_dispatch.add_argument("work_item_id")
    canon_run_dispatch.add_argument("--actor", required=True)
    canon_run_dispatch.add_argument("--target", default="")
    canon_run_dispatch.add_argument("--scope", default="")
    canon_run_dispatch.add_argument("--summary", default="")
    canon_run_dispatch.add_argument("--claim", action="append", default=[], help="Worker claim (repeatable)")
    canon_run_dispatch.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Worker receipt as key=value (repeatable; e.g. sha=<commit>)",
    )
    canon_run_review = canon_run_sub.add_parser(
        "review",
        help="Move a validation Work Item to review through its PASS Run",
    )
    canon_run_review.add_argument("run_id")
    canon_run_review.add_argument("--actor", default="atlas6-worker")
    canon_run_review.add_argument("--decision-id", default="")
    canon_run_review.add_argument("--approval-id", default="")
    canon_run_review.add_argument("--owner-review-comment", action="store_true")
    canon_evidence = canon_sub.add_parser("evidence", help="Attach and authenticate Canon Evidence")
    canon_evidence_sub = canon_evidence.add_subparsers(dest="evidence_command")
    canon_evidence_attach = canon_evidence_sub.add_parser("attach", help="Attach Evidence to a Run")
    canon_evidence_attach.add_argument("run_id")
    canon_evidence_attach.add_argument("--type", required=True, choices=[
        "log", "screenshot", "diff", "run_output", "doc_link", "external_url"
    ])
    canon_evidence_attach.add_argument("--content-ref", required=True)
    canon_evidence_attach.add_argument("--captured-by", default="atlas6-worker")
    canon_evidence_attach.add_argument("--captured-by-kind", choices=["human", "worker"], default="worker")
    canon_evidence_verify = canon_evidence_sub.add_parser(
        "verify",
        help="Mark Evidence verified with an authenticated configured verifier",
    )
    canon_evidence_verify.add_argument("evidence_id")
    canon_evidence_verify.add_argument(
        "--verifier",
        default=os.environ.get("BRUTUS_OWNER_IDENTITY", "justin.fowler@clearspeed.com"),
    )
    canon_close = canon_sub.add_parser(
        "close",
        help="Authenticated owner closure after acceptance or monitoring",
    )
    canon_close.add_argument("work_item_id")
    canon_close.add_argument("--reason", default="", help="Recorded closure reason")
    canon_watch = canon_sub.add_parser("watch", help="Inspect and test Canon Watches")
    canon_watch_sub = canon_watch.add_subparsers(dest="watch_command")
    canon_watch_sub.add_parser("list", help="List Watches")
    canon_watch_show = canon_watch_sub.add_parser("show", help="Show one Watch")
    canon_watch_show.add_argument("watch_id")
    canon_watch_test = canon_watch_sub.add_parser(
        "test",
        help="Test-deliver one Watch against its target's current state",
    )
    canon_watch_test.add_argument("watch_id")
    canon_report = canon_sub.add_parser("report", help="View Canon portfolio rollups and aging")
    canon_report_sub = canon_report.add_subparsers(dest="report_command")
    canon_report_portfolio = canon_report_sub.add_parser(
        "portfolio",
        help="Show Project state rollups, stuck review/validation items, and failed Runs",
    )
    canon_report_portfolio.add_argument(
        "--stuck-after-hours",
        type=float,
        default=48,
        help="Flag review/validation items at or beyond this age (default: 48)",
    )
    canon_report_portfolio.add_argument(
        "--failed-lookback-days",
        type=float,
        default=7,
        help="Include failed Runs from this many days ago (default: 7)",
    )
    canon_dogfood = canon_sub.add_parser(
        "dogfood", help="Run the live Canon proof pipeline"
    )
    canon_dogfood.add_argument("--marker", default="live-proof")

    workflow = sub.add_parser(
        "workflow",
        help="Route one work item, enforce delivery, batch feedback, and measure efficiency",
    )
    workflow.add_argument(
        "--db",
        dest="canon_db",
        default=os.environ.get("BRUTUS_CANON_DB_PATH") or str(canon_db_path()),
    )
    workflow_sub = workflow.add_subparsers(dest="workflow_command")
    workflow_route = workflow_sub.add_parser("route", help="Preview or create one routed Canon item")
    workflow_route.add_argument("request")
    workflow_route.add_argument("--repo", dest="repo_hint", default="")
    workflow_route.add_argument("--cwd", default="")
    workflow_route.add_argument("--create", action="store_true")
    workflow_status = workflow_sub.add_parser("status", help="Read Canon state and delivery proof")
    workflow_status.add_argument("work_item_id")
    workflow_policy = workflow_sub.add_parser("policy", help="Validate and optionally bind a repo policy")
    workflow_policy.add_argument("repository")
    workflow_policy.add_argument("--profile", default=None, help="Explicit schema2 delivery profile")
    workflow_policy.add_argument("--bind", metavar="WORK_ITEM_ID", default="")
    workflow_delivery = workflow_sub.add_parser("delivery", help="Verify required delivery receipts")
    workflow_delivery.add_argument("work_item_id")
    workflow_receipt = workflow_sub.add_parser("receipt", help="Attach an owner-verified delivery receipt")
    workflow_receipt.add_argument("work_item_id")
    workflow_receipt.add_argument("--requirement-id", required=True)
    workflow_receipt.add_argument("--type", required=True, choices=[item.value for item in EvidenceType])
    workflow_receipt.add_argument("--content-ref", required=True)
    workflow_receipt.add_argument("--result", required=True, choices=["pass", "fail"])
    workflow_receipt.add_argument("--target", default="")
    workflow_receipt.add_argument("--artifact-digest", default="")
    workflow_receipt.add_argument("--captured-by", default="owner-local-verifier")
    workflow_receipt.add_argument("--captured-by-kind", choices=["human", "worker"], default="human")
    workflow_event = workflow_sub.add_parser("event", help="Post one idempotent bounded work event")
    workflow_event.add_argument("work_item_id")
    workflow_event.add_argument("--event-id", required=True)
    workflow_event.add_argument("--event-type", required=True)
    workflow_event.add_argument("--surface", required=True)
    workflow_event.add_argument("--source-locator", required=True)
    workflow_event.add_argument("--result", default="")
    workflow_event.add_argument("--artifact-digest", default="")
    workflow_feedback = workflow_sub.add_parser("feedback", help="Batch a JSON feedback population")
    workflow_feedback.add_argument("--input", required=True)
    workflow_feedback.add_argument("--create", action="store_true")
    workflow_scorecard = workflow_sub.add_parser("scorecard", help="Generate the local efficiency scorecard")
    workflow_scorecard.add_argument("--days", type=int, default=7)

    appr = sub.add_parser("approve", help="Approve a Justin gate (thread id or REV-XX)")
    appr.add_argument("target")
    appr.add_argument("--reject", action="store_true")

    args = parser.parse_args()
    if args.cmd == "owner-token":
        from .security import configured_owner_token

        print(configured_owner_token())
        return
    if args.cmd == "canon":
        if args.canon_command is None:
            canon.print_help()
            raise SystemExit(1)
    if args.cmd == "workflow":
        if args.workflow_command is None:
            workflow.print_help()
            raise SystemExit(1)
        from .workflow_cli import run as run_workflow

        run_workflow(args)
        return

    cfg = load_config()
    client = AtlasClient(cfg)

    try:
        if args.cmd == "canon":
            from .canon.cli import run as run_canon

            run_canon(args)
            return
        elif args.cmd == "serve":
            from .server import serve as serve_main

            serve_main(cfg)
            return
        if args.cmd == "health":
            print(json.dumps(client.health(), indent=2))
        elif args.cmd == "llm-health":
            llm = cfg.local_llm
            if llm is None or not llm.enabled:
                print(
                    json.dumps(
                        {
                            "ok": None,
                            "retired": True,
                            "error": "local LLM retired — conversation is Sonnet 5, Cursor alternate",
                        },
                        indent=2,
                    )
                )
                return
            health = list_models(cfg)
            # A green /v1/models is not a working router. Exit non-zero unless a
            # token actually came back, or this command lies the way it lied
            # through the whole 2026-08-11 outage.
            health["generation"] = probe_generation(cfg)
            print(json.dumps(health, indent=2))
            if not health.get("ok") or not health["generation"].get("ok"):
                raise SystemExit(1)
        elif args.cmd == "digest":
            body = client.digest()
            print(body.get("markdown") or json.dumps(body, indent=2))
        elif args.cmd == "threads":
            print(json.dumps(client.list_threads(), indent=2))
        elif args.cmd == "reconcile":
            print(json.dumps(client.reconcile(), indent=2))
        elif args.cmd == "brief":
            body = client.brief()
            print(body.get("data", {}).get("markdown") or body.get("summary") or json.dumps(body, indent=2))
        elif args.cmd == "ingest-linear":
            print(json.dumps(client.ingest_linear(), indent=2))
        elif args.cmd == "peek-slack":
            print(json.dumps(client.peek_slack(limit=args.limit), indent=2))
        elif args.cmd == "peek-email":
            print(json.dumps(client.peek_gmail(limit=args.limit), indent=2))
        elif args.cmd == "ingest-slack":
            print(json.dumps(client.ingest_slack(), indent=2))
        elif args.cmd == "ingest-gmail":
            print(json.dumps(client.ingest_gmail(), indent=2))
        elif args.cmd == "frontier":
            print(json.dumps(client.frontier(), indent=2))
        elif args.cmd == "cursor":
            print(json.dumps(client.cursor(), indent=2))
        elif args.cmd == "status":
            print(json.dumps(client.status(), indent=2))
        elif args.cmd == "frontier-apply":
            print(
                json.dumps(
                    client.frontier_apply(
                        path=args.path or None,
                        thread_id=args.thread_id or None,
                        next_action=args.next_action,
                        notes=args.notes,
                    ),
                    indent=2,
                )
            )
        elif args.cmd == "cursor-apply":
            print(
                json.dumps(
                    client.cursor_apply(
                        path=args.path or None,
                        thread_id=args.thread_id or None,
                        next_action=args.next_action,
                        notes=args.notes,
                        evidence=args.evidence,
                        mark_done=args.mark_done,
                    ),
                    indent=2,
                )
            )
        elif args.cmd == "mcp":
            from .mcp_server import main as mcp_main

            mcp_main()
        elif args.cmd == "register":
            source = args.source
            ext = args.external_id
            if ext and str(ext).upper().startswith("REV-"):
                source = "linear"
                ext = str(ext).upper()
            out = client.register(args.title, external_id=ext, source=source, goal=args.goal)
            print(json.dumps(out, indent=2))
        elif args.cmd == "chat":
            memory = MemoryStore()
            reply, out = resolve_chat_reply(
                client, cfg, args.message, mode=args.mode, memory=memory
            )
            memory.save_conversation(
                args.message,
                reply,
                title=(args.message or "Brutus chat")[:80],
            )
            print(reply)
            if out.get("atlas6_unreachable"):
                print("\n[brutus] Studio unreachable.", file=sys.stderr)
        elif args.cmd == "dispatch":
            print(
                json.dumps(
                    client.dispatch_tick(dry_run=args.dry_run, ingest_linear=args.ingest_linear),
                    indent=2,
                )
            )
        elif args.cmd == "approve":
            tid = _resolve_thread_id(client, args.target)
            decision = "reject" if args.reject else "approve"
            print(json.dumps(client.approve(tid, decision=decision), indent=2))
        else:
            parser.print_help()
            raise SystemExit(1)
    except httpx.ConnectError as exc:
        _die(
            f"cannot reach Atlas6 at {cfg.atlas6_url} ({exc}). "
            "Start atlas6 on Studio or fix tunnel / config.yaml atlas6_url."
        )
    except httpx.HTTPStatusError as exc:
        _die(f"Atlas6 HTTP {exc.response.status_code}: {exc.response.text[:300]}")
    except SystemExit:
        raise
    except Exception as exc:
        _die(str(exc))


if __name__ == "__main__":
    main()
