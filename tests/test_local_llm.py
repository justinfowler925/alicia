"""Local LLM HTTP client (mocked)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from alicia.config import AliciaCfg, LocalLLMCfg
from alicia.local_llm import LocalLLMError, chat_completion, list_models


def _cfg(enabled: bool = True) -> AliciaCfg:
    return AliciaCfg(
        local_llm=LocalLLMCfg(
            enabled=enabled,
            router_url="http://127.0.0.1:7901",
            model="test-model",
            timeout_s=5.0,
        )
    )


def test_list_models_ok():
    cfg = _cfg()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"id": "test-model"}]}

    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value = mock_resp
        out = list_models(cfg)
    assert out["ok"] is True
    client.get.assert_called_once_with("http://127.0.0.1:7901/v1/models")


def test_list_models_disabled():
    out = list_models(_cfg(enabled=False))
    assert out["ok"] is False


def test_list_models_connect_error():
    cfg = _cfg()
    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = httpx.ConnectError("refused")
        out = list_models(cfg)
    assert out["ok"] is False
    assert "cannot connect" in out["error"]


def test_chat_completion_success():
    cfg = _cfg()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Hello from local"}}],
    }

    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = mock_resp
        text = chat_completion(cfg, [{"role": "user", "content": "hi"}])
    assert text == "Hello from local"
    client.post.assert_called_once()
    payload = client.post.call_args.kwargs["json"]
    assert payload["model"] == "test-model"


def test_chat_completion_connect_error():
    cfg = _cfg()
    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.side_effect = httpx.ConnectError("down")
        with pytest.raises(LocalLLMError, match="not reachable"):
            chat_completion(cfg, [{"role": "user", "content": "x"}])


def test_chat_completion_timeout_override_and_fail_fast():
    cfg = _cfg()
    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.side_effect = httpx.ReadTimeout("slow")
        with pytest.raises(LocalLLMError, match="timed out after 35"):
            chat_completion(cfg, [{"role": "user", "content": "x"}], timeout_s=35.0)
    assert client_cls.call_args.kwargs["timeout"] == 35.0


def test_strip_thinking_paired_block():
    from alicia.local_llm import _strip_thinking

    assert _strip_thinking("<think>musing…</think>\nThe answer.") == "The answer."


def test_strip_thinking_unopened_leading_thought():
    """Qwen3 templates can start generation inside a think block — the reply
    arrives as raw thoughts ending in </think> with no opening tag."""
    from alicia.local_llm import _strip_thinking

    raw = "Okay, let's see. The user asked…\n</think>\n\nNothing needs you."
    assert _strip_thinking(raw) == "Nothing needs you."


def test_chat_completion_thinking_only_reply_errors():
    cfg = _cfg()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "<think>ran out of tokens"}}],
    }

    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = mock_resp
        with pytest.raises(LocalLLMError, match="thinking-only"):
            chat_completion(cfg, [{"role": "user", "content": "x"}])
