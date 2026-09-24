"""A launchd job must run from the deployed artifact, never a shared checkout.

The daemon was moved to ~/.alicia/app because ~/Projects/alicia is a checkout
other sessions switch branches in, and twice in one day the service ended up
running someone else's unmerged branch. Three sibling agents — the tunnel, the
local LLM, and the Zoom notes feeder — were left pointing at the shared repo and
inherited the whole problem quietly.

On 2026-08-09 that checkout sat on a branch 8,625 lines behind main, so every one
of those three had been executing five-month-old scripts on every launch. Nothing
reported an error: the jobs ran, they just ran the wrong code.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from alicia import paths

LAUNCHD = Path(__file__).resolve().parent.parent / "launchd"
SHARED_CHECKOUT = Path.home() / "Projects" / "alicia"

PLISTS = sorted(LAUNCHD.glob("*.plist"))


def test_there_are_plists_to_check():
    """Guard the guard: a glob that matches nothing passes every test below."""
    assert PLISTS, f"no plists found under {LAUNCHD} — this suite would be vacuous"


@pytest.mark.parametrize("plist", PLISTS, ids=lambda p: p.stem)
def test_the_job_runs_from_the_deployed_artifact(plist: Path):
    spec = plistlib.loads(plist.read_bytes())

    scripts = [
        arg
        for arg in spec.get("ProgramArguments", [])
        if isinstance(arg, str) and arg.endswith((".sh", ".py"))
    ]
    assert scripts, f"{plist.name} runs no script we can locate"

    for script in scripts:
        assert not Path(script).is_relative_to(SHARED_CHECKOUT), (
            f"{plist.name} runs {script} out of the shared checkout. "
            f"Point it at {paths.DEPLOYED_APP}, which deploy.sh keeps on origin/main."
        )

    cwd = spec.get("WorkingDirectory")
    if cwd:
        assert not Path(cwd).is_relative_to(SHARED_CHECKOUT), (
            f"{plist.name} sets WorkingDirectory to the shared checkout ({cwd})"
        )


@pytest.mark.parametrize("plist", PLISTS, ids=lambda p: p.stem)
def test_the_script_it_names_exists_in_this_repo(plist: Path):
    """A path under ~/.alicia/app is a path in this tree — so it is checkable here.

    Repointing a plist at the artifact is only safe if the artifact actually
    carries the script. This catches the typo before launchd reports it as a job
    that silently never runs.
    """
    spec = plistlib.loads(plist.read_bytes())
    repo = LAUNCHD.parent

    for arg in spec.get("ProgramArguments", []):
        if not (isinstance(arg, str) and arg.endswith((".sh", ".py"))):
            continue
        try:
            rel = Path(arg).relative_to(paths.DEPLOYED_APP)
        except ValueError:
            continue
        assert (repo / rel).is_file(), f"{plist.name} names {rel}, absent from this repo"
