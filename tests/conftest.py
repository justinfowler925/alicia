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

# Default owner token for the suite after spend/mutate routes gained require_owner_action.
# Tests that probe unauthenticated denial mark ``no_auto_owner`` and manage headers themselves.
DEFAULT_TEST_OWNER_TOKEN = "alicia-test-owner-token"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "no_auto_owner: do not inject X-Alicia-Owner-Token into TestClient writes",
    )


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


@pytest.fixture(scope="session", autouse=True)
def _default_owner_token() -> None:
    os.environ.setdefault("ALICIA_OWNER_TOKEN", DEFAULT_TEST_OWNER_TOKEN)
    yield


@pytest.fixture(autouse=True)
def _auto_owner_on_testclient(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject owner token on TestClient mutating calls so existing suite stays green.

    Security / Canon probes that must see a naked 401 mark ``no_auto_owner``.
    """
    if request.node.get_closest_marker("no_auto_owner"):
        yield
        return
    if not os.environ.get("ALICIA_OWNER_TOKEN", "").strip():
        monkeypatch.setenv("ALICIA_OWNER_TOKEN", DEFAULT_TEST_OWNER_TOKEN)
    token = os.environ["ALICIA_OWNER_TOKEN"]

    from starlette.testclient import TestClient

    original = TestClient.request

    def request_with_owner(self, method, url, **kwargs):  # noqa: ANN001
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return original(self, method, url, **kwargs)
        headers = kwargs.get("headers")
        if headers is None:
            headers = {}
        elif not isinstance(headers, dict):
            headers = dict(headers)
        else:
            headers = dict(headers)
        lower = {str(k).lower(): v for k, v in headers.items()}
        if (
            "x-alicia-owner-token" in lower
            or "authorization" in lower
            or "x-alicia-csrf" in lower
            or lower.get("x-alicia-test-unauth") == "1"
        ):
            return original(self, method, url, **kwargs)
        headers["X-Alicia-Owner-Token"] = token
        kwargs["headers"] = headers
        return original(self, method, url, **kwargs)

    monkeypatch.setattr(TestClient, "request", request_with_owner)

    # Some transport tests use httpx ASGI clients instead of TestClient.
    import httpx

    original_async = httpx.AsyncClient.request

    async def async_request_with_owner(self, method, url, **kwargs):  # noqa: ANN001
        if str(method).upper() in {"GET", "HEAD", "OPTIONS"}:
            return await original_async(self, method, url, **kwargs)
        headers = kwargs.get("headers")
        if headers is None:
            headers = {}
        elif not isinstance(headers, dict):
            headers = dict(headers)
        else:
            headers = dict(headers)
        lower = {str(k).lower(): v for k, v in headers.items()}
        if (
            "x-alicia-owner-token" in lower
            or "authorization" in lower
            or "x-alicia-csrf" in lower
            or lower.get("x-alicia-test-unauth") == "1"
        ):
            return await original_async(self, method, url, **kwargs)
        headers["X-Alicia-Owner-Token"] = token
        kwargs["headers"] = headers
        return await original_async(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", async_request_with_owner)
    yield
