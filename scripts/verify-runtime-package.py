#!/usr/bin/env python3
"""Verify wheel payload against the release source, not just its import path."""

import hashlib
import sys
from pathlib import Path


def verify(source, installed):
    files = [
        p
        for p in source.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".js", ".css", ".html", ".sql", ".json"}
        and "__pycache__" not in p.parts
    ]
    if not files:
        raise ValueError("No source files found for runtime verification")
    mismatches = []
    for path in files:
        relative = path.relative_to(source)
        target = installed / relative
        if (
            not target.is_file()
            or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest()
        ):
            mismatches.append(str(relative))
    if mismatches:
        raise ValueError("Installed Brutus package differs from release: " + ", ".join(mismatches))
    return len(files)


if __name__ == "__main__":
    try:
        count = verify(Path(sys.argv[1]), Path(sys.argv[2]))
        print(f"    installed Brutus payload matches {count} release files")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
