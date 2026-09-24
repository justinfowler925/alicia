"""Alicia must never report work it did not do.

Every test here corresponds to a measured failure against the live 14B, not a
hypothetical. The headline one: given "approve REV-412" the model answered
"Approved REV-412." three times out of three, while no approve tool existed
anywhere in the registry and the phrase fell through to get_thread — a read.
"""

from unittest.mock import MagicMock

import pytest

from alicia.chat_resolve import _lookup_intent, _summarize_tool_result
from alicia.config import AliciaCfg
from alicia.tools import Tool, ToolRegistry, build_default_registry, format_tool_catalog

# --- the approve contract -------------------------------------------------


def test_approve_phrase_cannot_route_to_atlas_gate():
    """Standalone mode may read the ticket, but cannot mutate Atlas."""
    assert _lookup_intent("approve REV-412")[0] == "get_thread"
    assert _lookup_intent("reject REV-9")[0] == "get_thread"


def test_bare_ticket_still_reads():
    """Regression guard: only approve/reject is special, not every ticket id."""
    tool, _args = _lookup_intent("what's REV-418 about")
    assert tool == "get_thread"


def test_approve_gate_is_absent_even_when_local_writes_are_allowed():
    reg = build_default_registry(MagicMock(), AliciaCfg(), read_only=False)
    assert reg.get("approve_gate") is None


def test_approve_gate_is_absent_in_read_only():
    reg = build_default_registry(MagicMock(), AliciaCfg(), read_only=True)
    assert reg.get("approve_gate") is None


def test_approve_gate_cannot_reach_the_ledger():
    client = MagicMock()
    client.approve.return_value = {"status": "approved", "id": "REV-412"}
    reg = build_default_registry(client, AliciaCfg(), read_only=False)
    out = reg.call("approve_gate", {"ticket": "REV-412"})
    assert out["ok"] is False
    client.approve.assert_not_called()


def test_approve_gate_is_unavailable_without_calling_atlas():
    client = MagicMock()
    client.approve.side_effect = RuntimeError("atlas6 unreachable")
    reg = build_default_registry(client, AliciaCfg(), read_only=False)
    out = reg.call("approve_gate", {"ticket": "REV-412"})
    assert out["ok"] is False
    client.approve.assert_not_called()


# --- bad arguments are not results ---------------------------------------


def _registry_with(tool: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


def _probe_tool() -> Tool:
    return Tool(
        name="get_thread",
        description="Fetch one thread.",
        parameters={
            "type": "object",
            "properties": {"external_id": {"type": "string"}, "thread_id": {"type": "string"}},
        },
        fn=lambda **kw: {"found": True, **kw},
    )


def test_unknown_argument_is_broken_not_a_false_result():
    """`ticket_id` instead of `external_id` — 5 of 6 spoken phrasings did this."""
    reg = _registry_with(_probe_tool())
    out = reg.call("get_thread", {"ticket_id": "REV-418"})
    assert out["ok"] is False
    assert out["broken"] is True
    assert "external_id" in out["error"]
    assert "did not run" in out["error"]


def test_missing_required_argument_is_broken():
    reg = _registry_with(
        Tool(
            name="capture_note",
            description="Capture a note.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            fn=lambda **kw: kw,
        )
    )
    out = reg.call("capture_note", {})
    assert out["broken"] is True
    assert "requires text" in out["error"]


def test_good_arguments_still_run():
    reg = _registry_with(_probe_tool())
    out = reg.call("get_thread", {"external_id": "REV-418"})
    assert out["ok"] is True
    assert out["result"]["found"] is True


def test_a_genuine_false_result_is_not_marked_broken():
    """The distinction the old code destroyed: ran-and-said-no vs never-ran."""
    reg = _registry_with(
        Tool(
            name="check",
            description="Check something.",
            parameters={"type": "object", "properties": {}},
            fn=lambda: {"ok": False, "error": "nothing found"},
        )
    )
    out = reg.call("check", {})
    assert out["ok"] is True  # the CALL succeeded
    assert not out.get("broken")
    assert out["result"]["ok"] is False  # the ANSWER was negative


def test_unknown_tool_is_broken():
    assert ToolRegistry().call("nope", {})["broken"] is True


# --- the model never gets to narrate a broken call ------------------------


def test_summarizer_refuses_to_dress_up_a_broken_call():
    """No model is invoked — a broken call short-circuits to a plain sentence.

    _summarize_tool_result would otherwise hit the LLM; if this test ever needs
    a network mock, the short-circuit has regressed.
    """
    out = _summarize_tool_result(
        AliciaCfg(),
        "approve_gate",
        {"ok": False, "broken": True, "error": "approve_gate requires ticket."},
        "approve REV-412",
        None,
        None,
    )
    assert "didn't run" in out
    assert "nothing changed" in out.lower()
    assert "approved" not in out.lower()


# --- the catalog tells the model the argument names -----------------------


def test_catalog_lists_argument_names():
    reg = _registry_with(_probe_tool())
    line = format_tool_catalog(reg)
    assert "external_id" in line
    assert "thread_id" in line


def test_catalog_marks_optional_arguments():
    reg = _registry_with(_probe_tool())
    assert "external_id?" in format_tool_catalog(reg)


def test_catalog_marks_required_arguments_without_a_question_mark():
    reg = _registry_with(
        Tool(
            name="capture_note",
            description="d",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            fn=lambda **kw: kw,
        )
    )
    assert "capture_note(text)" in format_tool_catalog(reg)


@pytest.mark.parametrize("read_only", [True, False])
def test_every_real_tool_declares_its_arguments(read_only):
    """A tool with args but an empty schema is one the model must guess at."""
    reg = build_default_registry(MagicMock(), AliciaCfg(), read_only=read_only)
    catalog = format_tool_catalog(reg)
    for name in reg._tools:
        assert f"{name}(" in catalog, f"{name} missing from the catalog"


@pytest.mark.parametrize("read_only", [True, False])
def test_declared_arguments_are_actually_accepted(read_only):
    """Schema and implementation must agree, or the catalog teaches a lie.

    Calls each tool with every declared argument set to a placeholder and
    asserts it is never rejected as `broken`. The underlying call is expected
    to fail against a MagicMock client — that is fine; only an argument-shape
    rejection is a defect.
    """
    reg = build_default_registry(MagicMock(), AliciaCfg(), read_only=read_only)
    for name, tool in reg._tools.items():
        props = (tool.parameters or {}).get("properties", {}) or {}
        args = {}
        for arg, spec in props.items():
            kind = spec.get("type")
            args[arg] = (
                False if kind == "boolean" else 1 if kind == "integer" else "x"
            )
        out = reg.call(name, args)
        assert not out.get("broken"), f"{name} rejected its own declared arguments: {out['error']}"
