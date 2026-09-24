"""Provider-neutral model selection for Alicia work profiles.

This module intentionally knows nothing about provider SDKs.  It selects an
explicitly configured candidate by capability and leaves execution to the
caller.  In particular, it never turns an unavailable Claude, Cursor, or Codex
candidate into an implicit fallback to one of the others.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

PROFILE_REQUIREMENTS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "conversation": frozenset({"conversation", "low_latency"}),
        "supervisor": frozenset({"structured_output", "session_reasoning"}),
        "frontier": frozenset({"frontier_reasoning", "unfog"}),
        "builder": frozenset({"workspace_tools", "code_editing"}),
    }
)


class ModelProfileUnavailable(LookupError):
    """No configured candidate can honestly serve a requested profile."""

    def __init__(
        self,
        profile: str,
        required_capabilities: frozenset[str],
        diagnostics: tuple[str, ...],
    ) -> None:
        self.profile = profile
        self.required_capabilities = required_capabilities
        self.diagnostics = diagnostics
        required = ", ".join(sorted(required_capabilities)) or "none"
        inspected = "; ".join(diagnostics) or "no candidates configured"
        super().__init__(
            f"model profile '{profile}' is unavailable; required capabilities: "
            f"{required}; candidates: {inspected}"
        )


@dataclass(frozen=True)
class ModelCandidate:
    """One configured provider/model endpoint and its verified capabilities."""

    provider: str
    model: str
    capabilities: frozenset[str]
    enabled: bool = True
    priority: int = 100
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        model = self.model.strip()
        if not provider:
            raise ValueError("model candidate provider is required")
        if not model:
            raise ValueError("model candidate model is required")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(
            self,
            "capabilities",
            frozenset(str(value).strip() for value in self.capabilities if str(value).strip()),
        )
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


@dataclass(frozen=True)
class ModelProfile:
    """A named workload profile with an ordered, explicit candidate set."""

    name: str
    candidates: tuple[ModelCandidate, ...]
    required_capabilities: frozenset[str] | None = None

    def requirements(self) -> frozenset[str]:
        if self.required_capabilities is not None:
            return frozenset(self.required_capabilities)
        try:
            return PROFILE_REQUIREMENTS[self.name]
        except KeyError as exc:
            raise ValueError(
                f"unknown model profile '{self.name}'; configure required_capabilities explicitly"
            ) from exc


def select_model_profile(
    profile: ModelProfile | str,
    candidates: Iterable[ModelCandidate] | None = None,
    *,
    required_capabilities: Iterable[str] | None = None,
) -> ModelCandidate:
    """Select the highest-priority configured candidate that meets the profile.

    Ties preserve configuration order.  The function never manufactures a
    provider or model fallback: an empty or ineligible set raises
    :class:`ModelProfileUnavailable` with per-candidate diagnostics.
    """

    if isinstance(profile, ModelProfile):
        if candidates is not None or required_capabilities is not None:
            raise TypeError("a ModelProfile cannot be combined with candidate overrides")
        name = profile.name
        configured = profile.candidates
        required = profile.requirements()
    else:
        name = str(profile).strip()
        configured = tuple(candidates or ())
        if required_capabilities is None:
            try:
                required = PROFILE_REQUIREMENTS[name]
            except KeyError as exc:
                raise ValueError(
                    f"unknown model profile '{name}'; provide required_capabilities"
                ) from exc
        else:
            required = frozenset(str(value).strip() for value in required_capabilities if str(value).strip())

    eligible: list[tuple[int, int, ModelCandidate]] = []
    diagnostics: list[str] = []
    for index, candidate in enumerate(configured):
        label = f"{candidate.provider}/{candidate.model}"
        if not candidate.enabled:
            diagnostics.append(f"{label} disabled")
            continue
        missing = required - candidate.capabilities
        if missing:
            diagnostics.append(f"{label} missing {','.join(sorted(missing))}")
            continue
        diagnostics.append(f"{label} eligible")
        eligible.append((candidate.priority, index, candidate))

    if not eligible:
        raise ModelProfileUnavailable(name, required, tuple(diagnostics))
    return min(eligible, key=lambda item: (item[0], item[1]))[2]
