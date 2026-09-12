#!/usr/bin/env python3
"""Cross-agent prompt hook that rejects broad-root repository mutations."""

from __future__ import annotations

import json
import sys

from brutus.workflow_control import evaluate_route_guard


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        decision = evaluate_route_guard(payload if isinstance(payload, dict) else {})
    except Exception as exc:  # noqa: BLE001 - a broken efficiency hook must fail open
        print(json.dumps({"continue": True, "route_guard_error": str(exc)[:240]}))
        return 0
    if decision.allow:
        print(json.dumps({"continue": True}))
        return 0
    print(json.dumps({"continue": False, "stopReason": decision.message}))
    print(decision.message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
