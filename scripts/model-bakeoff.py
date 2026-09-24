"""Bake-off: score a model on the work Alicia actually asks of it.

Uses Alicia's OWN prompt assembly (`_build_messages`) and its OWN tool-call
parser (`_parse_tool_call`), so a pass here means the real code path would have
worked. A hand-written prompt or a hand-written regex would be measuring
something adjacent and calling it the product.

Scenarios only cover what REACHES the model: `_lookup_intent` force-routes
approve/reject, reconcile, dispatch, digest, steering and capture by regex
before the LLM is consulted, so those prove nothing about a model. Every
scenario below is asserted to fall through that regex first.

SCOPE — read this before filing a bug off a red line here. This harness calls
the model directly. It measures the MODEL, not Alicia. A failure below is a
hypothesis about user impact, not evidence of one: `resolve_chat_reply` sits in
front of all of this and answers gate questions deterministically
(`spoken_next_decision`), retries tool calls, and since #68 strips ticket ids
from Alicia's own prior turns and refuses to emit an id this turn cannot
justify. `gate.stale_history` in particular still fails on Qwen3-8B-4bit and is
expected to — the model really does resurrect the id, and the guard upstream of
this connection point means it never reaches Justin. Confirm any finding by
driving `resolve_chat_reply` before calling it a bug.

    python bakeoff.py <base_url> <model> <label>
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alicia.chat_resolve import (  # noqa: E402
    _build_messages,
    _lookup_intent,
    _parse_tool_call,
)
from alicia.config import load_config  # noqa: E402
from alicia.tools import build_default_registry  # noqa: E402

BASE, MODEL, LABEL = sys.argv[1], sys.argv[2], sys.argv[3]

cfg = load_config()
REGISTRY = build_default_registry(cfg)

# A realistic board. Four items need him — enough that "list them all" and "ask
# one" are visibly different answers.
BOARD = {
    "needs_you": [
        {"external_id": "REV-451", "title": "Weekly Tracker export perms", "note": "waiting on approve/reject"},
        {"external_id": "REV-462", "title": "VP approval checkbox → radio", "note": "waiting on approve/reject"},
        {"external_id": "REV-470", "title": "Pricing intake junk gate", "note": "waiting on approve/reject"},
        {"external_id": "REV-474", "title": "Contract sync drift audit", "note": "waiting on approve/reject"},
    ],
    "working": [{"external_id": "REV-455", "title": "CRO cockpit coverage"}],
    "stuck": [],
    "queued": [],
}
EMPTY_BOARD = {"needs_you": [], "working": [], "stuck": [], "queued": []}

FILLER = ("certainly", "happy to help", "great question", "i'd be glad", "i would be glad",
          "of course!", "absolutely!", "as an ai")


def no_markdown_furniture(t):
    """The chat panel renders **bold** and nothing else — headers/tables/rules
    show up as raw symbols. The prompt says this in capitals."""
    bad = []
    if re.search(r"^\s*#{1,6}\s", t, re.M):
        bad.append("markdown header")
    if re.search(r"^\s*\|.*\|", t, re.M):
        bad.append("table")
    if re.search(r"^\s*---+\s*$", t, re.M):
        bad.append("horizontal rule")
    return (not bad), ",".join(bad)


def no_filler(t):
    hit = [f for f in FILLER if f in t.lower()]
    return (not hit), ",".join(hit)


def routes_to(*names):
    def check(t):
        call = _parse_tool_call(t)
        if not call:
            return False, "no parseable TOOL:/ARGS: block"
        return (call[0] in names), f"chose {call[0]}, wanted {'|'.join(names)}"
    return check


def exact_refusal(t):
    return (t.strip() == "Sorry, I can't find that shit."), f"said {t.strip()[:70]!r}"


def invents_no_ticket(t):
    found = set(re.findall(r"REV-\d+", t))
    return (not found), f"invented {sorted(found)}"


def one_question_only(t):
    body = _parse_tool_call(t)
    if body:
        return False, "emitted a tool call instead of asking"
    qs = t.count("?")
    ids = set(re.findall(r"REV-\d+", t))
    if qs > 1:
        return False, f"{qs} questions"
    if len(ids) > 1:
        return False, f"named {len(ids)} tickets: {sorted(ids)}"
    return True, ""


def all_of(*checks):
    def check(t):
        notes = []
        ok = True
        for c in checks:
            good, note = c(t)
            ok = ok and good
            if note:
                notes.append(note)
        return ok, "; ".join(notes)
    return check


def grounded_or_routed(t):
    """Board is in context, so BOTH are correct: call the tool, or answer from
    it. v1 demanded the tool call and scored a correct answer as a failure —
    the prompt explicitly says "if you can answer from the context without a
    tool, just reply normally". What must never happen is inventing."""
    call = _parse_tool_call(t)
    if call:
        return (call[0] in ("get_work_surface", "get_digest")), f"chose {call[0]}"
    return one_question_only(t)


def not_a_refusal(t):
    """The mirror of exact_refusal. Without this, a model that answers every
    question with "Sorry, I can't find that shit." scores 100% on refusals —
    the check would be unfalsifiable in the direction that matters."""
    if t.strip().lower().startswith("sorry, i can't find"):
        return False, "refused an answerable question"
    return True, ""


def says_alarm_first(t):
    first = re.split(r"(?<=[.!?])\s", t.strip(), 1)[0].lower()
    return ("alarm" in first or "nothing has ever finished" in first
            or "no ticket" in first), f"first sentence was {first[:70]!r}"


def mentions_only(*allowed):
    """Stale tickets quoted in earlier turns must not be resurrected."""
    def check(t):
        found = set(re.findall(r"REV-\d+", t))
        bad = found - set(allowed)
        return (not bad), f"resurrected {sorted(bad)}" if bad else ""
    return check


ALARM_BOARD = dict(BOARD, alarm={"alarm": True, "done_total": 0}, headline="Nothing has completed.")
STALE_HISTORY = [
    {"role": "user", "content": "what needs me"},
    {"role": "assistant", "content": "REV-401 is waiting on you — approve or reject? Then 2 more."},
]

# (id, user message, board, grader, needs_tool_catalog, history)
SCENARIOS = [
    # --- routing the model actually decides ---
    ("route.cursor", "have cursor take a look at the watchdog code in alicia", EMPTY_BOARD,
     all_of(routes_to("ask_cursor")), True, None),
    ("route.claude", "draft me a long writeup on our Q3 pipeline risks", EMPTY_BOARD,
     all_of(routes_to("ask_claude")), True, None),
    ("route.atlas6", "get the bot to scope out a ticket for the pricing intake rebuild", EMPTY_BOARD,
     all_of(routes_to("ask_atlas6")), True, None),
    ("route.register", "start tracking a new one: rebuild the pricing intake screen", EMPTY_BOARD,
     all_of(routes_to("register_thread")), True, None),
    ("route.notes", "what's on my ideas list", EMPTY_BOARD,
     all_of(routes_to("list_notes")), True, None),
    ("route.grounded", "so what's on my plate right now", BOARD,
     all_of(grounded_or_routed, no_filler), True, None),

    # --- gate discipline: one question, never the whole board ---
    ("gate.one_question", "anything i need to look at?", BOARD,
     all_of(one_question_only, no_markdown_furniture, no_filler), False, None),
    ("gate.empty_no_invention", "anything waiting on me?", EMPTY_BOARD,
     all_of(invents_no_ticket, no_filler), False, None),
    # Expected to FAIL on small models and that is the point: it measures raw
    # susceptibility. Production is guarded upstream (#68) — see SCOPE above.
    ("gate.stale_history", "what about now?", EMPTY_BOARD,
     all_of(mentions_only(), no_filler), False, STALE_HISTORY),
    ("gate.alarm_first", "how are we doing", ALARM_BOARD,
     all_of(says_alarm_first, no_markdown_furniture), False, None),

    # --- refusal, and the false-refusal mirror ---
    ("refuse.exact", "what did Sergii say in the pricing call last Tuesday", EMPTY_BOARD,
     exact_refusal, False, None),
    ("refuse.exact2", "pull up the ARR number for the Bahrain account", EMPTY_BOARD,
     exact_refusal, False, None),
    ("refuse.not_overeager", "how many items are waiting on me?", BOARD,
     all_of(not_a_refusal, no_filler), False, None),
    ("refuse.not_overeager2", "what's the top thing waiting on me", BOARD,
     all_of(not_a_refusal, one_question_only), False, None),

    # --- format: the panel renders **bold** and nothing else ---
    ("format.no_table", "compare atlas5 and atlas6 for me, what does each one do", EMPTY_BOARD,
     all_of(no_markdown_furniture, no_filler, not_a_refusal), False, None),
    ("format.no_headers", "walk me through how you'd design a retry queue for the zoom feeder",
     EMPTY_BOARD, all_of(no_markdown_furniture, no_filler, not_a_refusal), False, None),
    ("format.design_work", "argue with me: should the watchdog restart the router or just alert?",
     EMPTY_BOARD, all_of(no_markdown_furniture, no_filler, not_a_refusal), False, None),

    ("voice.brief", "morning", BOARD,
     all_of(no_filler, no_markdown_furniture), False, None),
]


def chat(messages, max_tokens=700):
    payload = {
        "model": MODEL, "messages": messages, "temperature": 0.3,
        "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    dt = time.monotonic() - t0
    txt = (d["choices"][0].get("message") or {}).get("content") or ""
    # mlx_lm returns Qwen think blocks inline even with thinking off.
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
    if "</think>" in txt:
        txt = txt.rsplit("</think>", 1)[1]
    usage = d.get("usage") or {}
    return txt.strip(), dt, usage.get("completion_tokens") or 0


results = []
for sid, msg, board, grader, want_tools, history in SCENARIOS:
    forced = _lookup_intent(msg)
    if forced:
        results.append({"id": sid, "ok": None, "note": f"regex-forced to {forced[0]} — never reaches the model",
                        "s": 0.0, "toks": 0, "reply": ""})
        continue
    messages = _build_messages(
        msg, {"reply": "", "atlas6_unreachable": False}, board=board,
        history=history, registry=REGISTRY if want_tools else None,
    )
    try:
        txt, dt, toks = chat(messages)
    except Exception as e:
        results.append({"id": sid, "ok": False, "note": f"{type(e).__name__}: {e}",
                        "s": 0.0, "toks": 0, "reply": ""})
        continue
    ok, note = grader(txt)
    results.append({"id": sid, "ok": ok, "note": note, "s": round(dt, 2),
                    "toks": toks, "reply": txt[:400]})

scored = [r for r in results if r["ok"] is not None]
passed = sum(1 for r in scored if r["ok"])
lat = [r["s"] for r in scored if r["s"] > 0]
tps = [r["toks"] / r["s"] for r in scored if r["s"] > 0 and r["toks"]]
out = {
    "label": LABEL, "model": MODEL,
    "passed": passed, "scored": len(scored),
    "skipped_regex_forced": len(results) - len(scored),
    "median_s": round(sorted(lat)[len(lat) // 2], 2) if lat else None,
    "max_s": round(max(lat), 2) if lat else None,
    "median_tok_s": round(sorted(tps)[len(tps) // 2], 1) if tps else None,
    "results": results,
}
print(json.dumps(out, indent=2))
