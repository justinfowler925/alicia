import pytest

from alicia.model_profiles import (
    ModelCandidate,
    ModelProfileUnavailable,
    select_model_profile,
)


def candidate(provider, model, capabilities, priority=100, enabled=True):
    return ModelCandidate(
        provider=provider,
        model=model,
        capabilities=frozenset(capabilities),
        priority=priority,
        enabled=enabled,
    )


def test_all_three_providers_can_be_selected_by_configured_capability_profiles():
    configured = (
        candidate("claude", "sonnet", {"conversation", "low_latency"}, 10),
        candidate("cursor", "composer", {"workspace_tools", "code_editing"}, 10),
        candidate("codex", "frontier", {"frontier_reasoning", "unfog"}, 10),
    )

    assert select_model_profile("conversation", configured).provider == "claude"
    assert select_model_profile("builder", configured).provider == "cursor"
    assert select_model_profile("frontier", configured).provider == "codex"


def test_priority_is_configuration_driven_not_provider_hardwired():
    configured = (
        candidate("claude", "a", {"conversation", "low_latency"}, 30),
        candidate("codex", "b", {"conversation", "low_latency"}, 5),
        candidate("cursor", "c", {"conversation", "low_latency"}, 20),
    )

    assert select_model_profile("conversation", configured).provider == "codex"


def test_unavailable_profile_fails_with_candidate_diagnostics_and_no_fallback():
    configured = (
        candidate("claude", "sonnet", {"conversation"}),
        candidate("cursor", "composer", {"workspace_tools", "code_editing"}, enabled=False),
    )

    with pytest.raises(ModelProfileUnavailable) as exc_info:
        select_model_profile("conversation", configured)

    message = str(exc_info.value)
    assert "low_latency" in message
    assert "claude/sonnet" in message
    assert "cursor/composer disabled" in message


def test_unknown_profile_requires_explicit_capabilities():
    custom = candidate("claude", "special", {"vision"})

    assert select_model_profile(
        "visual_review", (custom,), required_capabilities={"vision"}
    ) == custom
    with pytest.raises(ValueError, match="unknown model profile"):
        select_model_profile("visual_review", (custom,))
