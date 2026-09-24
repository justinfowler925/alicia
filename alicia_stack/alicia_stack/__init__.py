"""Canon Hands/Prove boundary retained from REV-490.

Conversation routing belongs exclusively to ``alicia.brain``. This package
only persists worker handoffs and verifies their evidence before review.
"""

from .hands import CanonHandsDispatcher, HandsDispatcher, transition_run_to_review
from .types import ClaimVerdict, HandsResult, ProveReport

__all__ = [
    "CanonHandsDispatcher",
    "ClaimVerdict",
    "HandsDispatcher",
    "HandsResult",
    "ProveReport",
    "transition_run_to_review",
]

__version__ = "0.1.0"
