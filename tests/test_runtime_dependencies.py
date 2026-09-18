import pathlib
"""Contracts for dependencies required by the deployed Brutus actor."""

import tomllib
from pathlib import Path


def test_cursor_sdk_is_not_a_runtime_dependency():
    """The Cursor SDK runner was deleted; the dependency must not come back."""
    import tomllib

    dependencies = tomllib.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )["project"]["dependencies"]
    assert not any(d.startswith("cursor-sdk") for d in dependencies)
