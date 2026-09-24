"""Regression coverage for distributable package configuration."""

import tomllib
from pathlib import Path


def test_wheel_target_includes_alicia_stack():
    """The documented editable dev install must expose Canon Hands."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text())

    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "alicia_stack/alicia_stack" in packages
