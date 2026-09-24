"""Contracts for dependencies required by the deployed Alicia actor."""

import tomllib
from pathlib import Path


def test_cursor_sdk_is_a_declared_runtime_dependency():
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]

    assert any(dependency.startswith("cursor-sdk") for dependency in dependencies)

    from cursor_sdk import Agent

    assert callable(Agent.prompt)
