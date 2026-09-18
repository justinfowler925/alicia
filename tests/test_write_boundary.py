"""The write boundary has to hold at the HTTP layer, not just in the registry.

tests/test_chat_resolve.py already asserted "read_only cannot call atlas6" — but
it did so by calling resolve_chat_reply directly with a fake chat_completion. It
said nothing about /api/chat, which never passed read_only at all and therefore
defaulted to False. The browser's voice path ran the full mutating registry.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from brutus.config import BrutusCfg
from brutus.tools import build_default_registry

MUTATING = [
    "approve_gate",
    "ask_atlas6",
    "ask_model",
    "ask_claude",
    "register_thread",
    "dispatch_tick",
    "reconcile",
    "answer_steering",
    "promote_note",
    "capture_canon_inbox",
    "promote_canon_inbox",
    "review_canon_work",
]


def test_read_only_registry_has_no_mutating_tool():
    reg = build_default_registry(MagicMock(), BrutusCfg(), read_only=True)
    present = [n for n in MUTATING if reg.get(n) is not None]
    assert present == [], f"read_only registry exposed: {present}"


def test_writable_registry_has_them():
    reg = build_default_registry(MagicMock(), BrutusCfg(), read_only=False)
    available = {n for n in MUTATING if reg.get(n) is not None}
    assert available == {
        "ask_model",
        "capture_canon_inbox",
        "promote_canon_inbox",
        "review_canon_work",
    }


def test_chat_endpoint_forwards_read_only():
    """The one-line omission that made the parameter decorative."""
    from brutus.server import ChatRequest

    assert "read_only" in ChatRequest.model_fields
    assert ChatRequest(message="hi").read_only is False
    assert ChatRequest(message="hi", read_only=True).read_only is True

    import inspect

    from brutus import server

    src = inspect.getsource(server)
    chat_src = src[src.index('@app.post("/api/chat")') :][:1500]
    assert "read_only=req.read_only" in chat_src, "/api/chat still drops read_only"


def test_read_only_turn_never_reaches_a_mutating_client_method():
    """End to end through resolve_chat_reply: the client must stay untouched."""
    from brutus.chat_resolve import resolve_chat_reply
    from brutus.config import LocalLLMCfg
    from brutus.memory import MemoryStore

    client = MagicMock()
    cfg = BrutusCfg(
        local_llm=LocalLLMCfg(enabled=True, model="m", router_url="http://127.0.0.1:7901")
    )
    with patch("brutus.chat_resolve.chat_completion", return_value="ok"):
        for phrase in ("approve REV-412", "dispatch a tick for real", "reconcile now"):
            resolve_chat_reply(
                client, cfg, phrase, memory=MemoryStore(), read_only=True
            )
    for method in ("approve", "dispatch_tick", "reconcile", "answer_steering"):
        assert not getattr(client, method).called, f"read_only turn called client.{method}"


# --- the timeout that measured 6s when it declared 1s ---------------------


def test_a_with_block_executor_defeats_its_own_timeout():
    """Characterises the bug, so nobody reintroduces the `with` form."""
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(time.sleep, 1.5)
        try:
            fut.result(timeout=0.2)
        except Exception:
            pass
    # __exit__ called shutdown(wait=True) and blocked for the full sleep.
    assert time.monotonic() - started > 1.0
