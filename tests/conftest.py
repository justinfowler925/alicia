"""The suite never touches the databases Alicia is serving.

`state_path()` resolves to ~/.alicia/state unless ALICIA_STATE_DIR says otherwise,
and plenty of code constructs a store with no explicit path — `TodoStore()`,
`build_default_registry(...)`, anything reached through a default `AliciaCfg`. Run
the suite and those defaults land on the live notepad. That is not hypothetical:
firing sample phrases at the running daemon once wrote 64 junk todos into Justin's
real pad, and separating his rows from the noise afterwards took cross-referencing
timestamps against his own voice turns.

One session-scoped directory, set before any test imports a store.
"""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_machine_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests opt into live-shaped credentials instead of inheriting the host."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)


@pytest.fixture(scope="session", autouse=True)
def _isolated_state_dir() -> "os.PathLike[str]":
    if os.environ.get("ALICIA_STATE_DIR", "").strip():
        yield Path(os.environ["ALICIA_STATE_DIR"])  # an explicit choice wins
        return
    with tempfile.TemporaryDirectory(prefix="alicia-tests-") as tmp:
        os.environ["ALICIA_STATE_DIR"] = tmp
        try:
            yield Path(tmp)
        finally:
            os.environ.pop("ALICIA_STATE_DIR", None)
