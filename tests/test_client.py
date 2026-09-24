"""Atlas compatibility client remains inert in standalone mode."""

from unittest.mock import patch

import pytest

from alicia.client import AtlasClient, AtlasDisabled
from alicia.config import AliciaCfg


def test_url_join():
    c = AtlasClient(AliciaCfg(atlas_enabled=True, atlas6_url="http://127.0.0.1:8767/"))
    assert c._url("/api/digest") == "http://127.0.0.1:8767/api/digest"


def test_disabled_client_raises_before_any_http_request():
    client = AtlasClient(AliciaCfg(atlas_enabled=False))
    with patch("alicia.client.httpx.Client") as http, pytest.raises(
        AtlasDisabled, match="intentionally ignored"
    ):
        client.health()
    http.assert_called_once()
    http.return_value.__enter__.return_value.get.assert_not_called()
