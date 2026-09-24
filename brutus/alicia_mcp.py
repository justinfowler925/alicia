"""MCP transport for the same Alicia brain used by the web and voice clients.

Set ALICIA_BRAIN_URL and ALICIA_SURFACE_TOKEN, then run
python -m brutus.alicia_mcp. No model or memory is hosted in this adapter.
"""
import os
import uuid

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Alicia")


async def post(path, payload):
    url = os.environ["ALICIA_BRAIN_URL"].rstrip("/")
    async with httpx.AsyncClient(base_url=url, timeout=150,
                                headers={"Authorization": "Bearer " + os.environ["ALICIA_SURFACE_TOKEN"]}) as client:
        response = await client.post(path, json=payload)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def alicia_talk(session_id: str, message: str, request_id: str = "") -> dict:
    """Talk to Alicia; reuse session_id to continue from another surface.

Supply the same request_id only to retry an identical request without creating
another turn. The central service owns the transcript and personal memory.
"""
    return await post("/v1/turns", {"session_id": session_id, "message": message,
                                  "request_id": request_id or str(uuid.uuid4())})


@mcp.tool()
async def alicia_remember(key: str, value: str) -> dict:
    """Save a preference explicitly supplied by the user to shared memory."""
    return await post("/v1/memory", {"key": key, "value": value})


@mcp.tool()
async def alicia_feedback(turn_id: str, dimension: str, rating: str, correction: str = "") -> dict:
    """Review a completed turn: conversation/organization/presentation and helpful/needs_work."""
    return await post("/v1/feedback", {"turn_id": turn_id, "dimension": dimension,
                                      "rating": rating, "correction": correction})


if __name__ == "__main__":
    mcp.run()
