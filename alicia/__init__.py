"""Alicia package (formerly Brutus) — laptop talking head for Studio Atlas."""

import os as _os
import sys as _sys

__version__ = "0.1.0"


def _adopt_legacy_env() -> None:
    """Honor BRUTUS_* env vars for one release after the Alicia rename.

    Any BRUTUS_X that is set while ALICIA_X is not is copied across, so every
    ``os.environ.get("ALICIA_X")`` in the package sees it. ALICIA_* always wins.
    Remove after the release following the rename.
    """
    adopted = []
    for key, value in list(_os.environ.items()):
        if key.startswith("BRUTUS_"):
            new = "ALICIA_" + key[len("BRUTUS_"):]
            if new not in _os.environ:
                _os.environ[new] = value
                adopted.append(key)
    if adopted and not _os.environ.get("ALICIA_QUIET_LEGACY_ENV"):
        print(
            "alicia: using legacy env var(s) " + ", ".join(sorted(adopted))
            + "; rename them to ALICIA_* (BRUTUS_* support will be removed).",
            file=_sys.stderr,
        )


_adopt_legacy_env()
