"""MCP app builds with expected tools."""

from brutus.mcp_server import build_mcp


def test_build_mcp_tools():
    mcp = build_mcp()
    tools = mcp._tool_manager.list_tools()  # type: ignore[attr-defined]
    names = {getattr(t, "name", None) or str(t) for t in tools}
    assert "brutus_digest" in names
    assert "brutus_register" in names
    assert "brutus_dispatch" in names
    assert "brutus_approve" in names
    assert "brutus_query" in names
    assert "brutus_explain" in names
    assert "brutus_listen" in names
    assert "brutus_speak" in names
    assert "brutus_peek_slack" in names
    assert "brutus_peek_email" in names
    assert "brutus_ingest_slack" in names
    assert "brutus_ingest_gmail" in names
    assert "brutus_work_route" in names
    assert "brutus_work_status" in names
    assert "brutus_work_event" in names
    assert "brutus_feedback_batch" in names
    assert "brutus_workflow_scorecard" in names


def test_brutus_query_description_says_read_only():
    mcp = build_mcp()
    tools = mcp._tool_manager.list_tools()  # type: ignore[attr-defined]
    query = next((t for t in tools if getattr(t, "name", None) == "brutus_query"), None)
    assert query is not None
    desc = getattr(query, "description", "")
    assert "read-only" in desc.lower() or "CANNOT" in desc
    assert "mutate" in desc.lower() or "modify" in desc.lower() or "approve" in desc.lower()
