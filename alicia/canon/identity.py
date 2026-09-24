"""Authenticated principal boundary for Canon owner-gated operations.

This module intentionally does not authenticate a browser session itself.
Instead, it makes Canon consume an explicit principal issued by a trusted
registry, rather than treating a caller-provided name as proof of identity.
The first session/OAuth caller can replace the registry issuance boundary
without changing Canon's authorization checks.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import Enum

from alicia.config import (
    CANON_AUTOMATED_VERIFIER_IDENTITIES,
    CANON_WORKER_IDENTITIES,
    OWNER_IDENTITY,
)


class CanonError(ValueError):
    """Raised when a Canon action lacks a valid authenticated identity."""


class PrincipalKind(str, Enum):
    HUMAN_OWNER = "human_owner"
    WORKER = "worker"
    AUTOMATED_VERIFIER = "automated_verifier"


@dataclass(frozen=True, init=False)
class AuthenticatedPrincipal:
    """An identity issued by :class:`IdentityRegistry`, not a free-text claim."""

    identity: str
    kind: PrincipalKind
    _credential: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError(
            "AuthenticatedPrincipal instances must be issued by an IdentityRegistry"
        )

    @classmethod
    def _issue(
        cls, identity: str, kind: PrincipalKind, credential: str
    ) -> AuthenticatedPrincipal:
        principal = object.__new__(cls)
        object.__setattr__(principal, "identity", identity)
        object.__setattr__(principal, "kind", kind)
        object.__setattr__(principal, "_credential", credential)
        return principal


class IdentityRegistry:
    """Allowlisted principal issuer and verifier for one Canon deployment.

    Issuance is the seam where session/OAuth authentication will plug in. The
    current standalone implementation only issues the config-defined owner,
    configured worker identities, and configured automated verifiers.
    """

    def __init__(
        self,
        *,
        owner_identity: str,
        worker_identities: frozenset[str] = frozenset(),
        automated_verifier_identities: frozenset[str] = frozenset(),
    ) -> None:
        if not owner_identity.strip():
            raise ValueError("owner_identity must be configured")
        self.owner_identity = owner_identity.strip()
        self.worker_identities = frozenset(worker_identities)
        self.automated_verifier_identities = frozenset(automated_verifier_identities)
        self._issued: dict[str, tuple[str, PrincipalKind]] = {}

    def owner_principal(self) -> AuthenticatedPrincipal:
        """Issue the config-defined human owner principal."""
        return self._issue(self.owner_identity, PrincipalKind.HUMAN_OWNER)

    def worker_principal(self, identity: str) -> AuthenticatedPrincipal:
        """Issue a principal only for a configured worker/agent identity."""
        if identity not in self.worker_identities:
            raise CanonError(f"worker identity '{identity}' is not allowlisted")
        return self._issue(identity, PrincipalKind.WORKER)

    def verifier_principal(self, identity: str) -> AuthenticatedPrincipal:
        """Issue the owner or a configured automated verifier principal."""
        if identity == self.owner_identity:
            return self.owner_principal()
        if identity not in self.automated_verifier_identities:
            raise CanonError(f"verifier identity '{identity}' is not allowlisted")
        return self._issue(identity, PrincipalKind.AUTOMATED_VERIFIER)

    def _issue(self, identity: str, kind: PrincipalKind) -> AuthenticatedPrincipal:
        credential = secrets.token_urlsafe(32)
        self._issued[credential] = (identity, kind)
        return AuthenticatedPrincipal._issue(identity, kind, credential)

    def resolve(self, principal: AuthenticatedPrincipal | None) -> AuthenticatedPrincipal:
        """Return a registry-issued principal or reject a forged/stale one."""
        if not isinstance(principal, AuthenticatedPrincipal):
            raise CanonError("an authenticated principal is required")
        expected = self._issued.get(principal._credential)
        if expected != (principal.identity, principal.kind):
            raise CanonError("authenticated principal was not issued by this registry")
        return principal


DEFAULT_IDENTITY_REGISTRY = IdentityRegistry(
    owner_identity=OWNER_IDENTITY,
    worker_identities=CANON_WORKER_IDENTITIES,
    automated_verifier_identities=CANON_AUTOMATED_VERIFIER_IDENTITIES,
)


def verify_actor(
    claimed_actor: str,
    authenticated_principal: AuthenticatedPrincipal | None,
    *,
    registry: IdentityRegistry = DEFAULT_IDENTITY_REGISTRY,
) -> AuthenticatedPrincipal:
    """Verify that an authenticated principal, not a name, owns ``claimed_actor``."""
    principal = registry.resolve(authenticated_principal)
    if principal.identity != claimed_actor:
        raise CanonError(
            f"claimed actor '{claimed_actor}' does not match authenticated principal "
            f"'{principal.identity}'"
        )
    return principal


def require_owner(
    claimed_actor: str,
    authenticated_principal: AuthenticatedPrincipal | None,
    *,
    registry: IdentityRegistry = DEFAULT_IDENTITY_REGISTRY,
) -> AuthenticatedPrincipal:
    """Require that the claimed actor is the config-defined authenticated owner."""
    principal = verify_actor(
        claimed_actor, authenticated_principal, registry=registry
    )
    if (
        principal.kind != PrincipalKind.HUMAN_OWNER
        or principal.identity != registry.owner_identity
    ):
        raise CanonError(f"actor '{claimed_actor}' is not the authenticated owner")
    return principal


def require_verifier(
    claimed_actor: str,
    authenticated_principal: AuthenticatedPrincipal | None,
    *,
    registry: IdentityRegistry = DEFAULT_IDENTITY_REGISTRY,
) -> AuthenticatedPrincipal:
    """Require an authenticated owner or allowlisted automated verifier."""
    principal = verify_actor(
        claimed_actor, authenticated_principal, registry=registry
    )
    if principal.kind not in {
        PrincipalKind.HUMAN_OWNER,
        PrincipalKind.AUTOMATED_VERIFIER,
    }:
        raise CanonError(f"actor '{claimed_actor}' is not an authenticated verifier")
    return principal
