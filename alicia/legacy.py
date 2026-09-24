"""Deprecated ``brutus`` / ``brutus-mcp`` console scripts.

Alicia was called Brutus. These entry points keep old muscle memory and
scripts working for one release: they print a notice to stderr and forward.
"""

from __future__ import annotations

import sys


def _notice(old: str, new: str) -> None:
    print(f"{old}: renamed to `{new}`; `{old}` will be removed.", file=sys.stderr)


def brutus_main() -> None:
    _notice("brutus", "alicia")
    from .__main__ import main

    main()


def brutus_mcp_main() -> None:
    # stdout is the MCP stdio channel, so the notice goes to stderr only.
    _notice("brutus-mcp", "alicia-mcp")
    from .mcp_server import main

    main()
