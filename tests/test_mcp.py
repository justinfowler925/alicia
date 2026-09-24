"""MCP app builds with expected tools."""

from alicia.mcp_server import build_mcp


def test_build_mcp_tools():
    mcp = build_mcp()
    tools = mcp._tool_manager.list_tools()  # type: ignore[attr-defined]
    names = {getattr(t, "name", None) or str(t) for t in tools}
    assert "alicia_digest" in names
    assert "alicia_register" in names
    assert "alicia_dispatch" in names
    assert "alicia_approve" in names
    assert "alicia_query" in names
    assert "alicia_explain" in names
    assert "alicia_listen" in names
    assert "alicia_speak" in names
    assert "alicia_peek_slack" in names
    assert "alicia_peek_email" in names
    assert "alicia_ingest_slack" in names
    assert "alicia_ingest_gmail" in names
    assert "alicia_work_route" in names
    assert "alicia_work_status" in names
    assert "alicia_work_event" in names
    assert "alicia_feedback_batch" in names
    assert "alicia_workflow_scorecard" in names


def test_alicia_query_description_says_read_only():
    mcp = build_mcp()
    tools = mcp._tool_manager.list_tools()  # type: ignore[attr-defined]
    query = next((t for t in tools if getattr(t, "name", None) == "alicia_query"), None)
    assert query is not None
    desc = getattr(query, "description", "")
    assert "read-only" in desc.lower() or "CANNOT" in desc
    assert "mutate" in desc.lower() or "modify" in desc.lower() or "approve" in desc.lower()
