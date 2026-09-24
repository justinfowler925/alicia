from __future__ import annotations

import subprocess
from pathlib import Path


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def test_old_checkout_accepts_only_dirty_paths_that_match_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "-q", cwd=repo)
    _run("git", "config", "user.email", "eval@example.invalid", cwd=repo)
    _run("git", "config", "user.name", "Eval", cwd=repo)
    (repo / "landed.txt").write_text("old\n", encoding="utf-8")
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-qm", "base", cwd=repo)
    base = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()

    (repo / "landed.txt").write_text("new\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("new main file\n", encoding="utf-8")
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-qm", "target", cwd=repo)
    target = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()

    _run("git", "checkout", "-q", "--detach", base, cwd=repo)
    (repo / "landed.txt").write_text("new\n", encoding="utf-8")
    gate = Path(__file__).parents[1] / "scripts" / "check-deploy-drift.sh"
    accepted = subprocess.run([str(gate), str(repo), target, "--prepare"])
    assert accepted.returncode == 0
    _run("git", "checkout", "-q", "--detach", target, cwd=repo)
    assert _run("git", "status", "--porcelain", cwd=repo).stdout == ""

    _run("git", "checkout", "-q", "--detach", base, cwd=repo)
    (repo / "landed.txt").write_text("different\n", encoding="utf-8")
    rejected = subprocess.run([str(gate), str(repo), target])
    assert rejected.returncode != 0


def test_deploy_can_target_a_named_ref_but_never_silently():
    """The failure this file guards was a SILENT unmerged branch.

    A ref named on the command line is the opposite of that: it is how you try
    a fix on the real daemon before landing it. So it is allowed, and it shouts.
    """
    from pathlib import Path

    deploy = (Path(__file__).parents[1] / "scripts/deploy.sh").read_text()

    assert 'TARGET_REF="${ALICIA_DEPLOY_REF:-origin/main}"' in deploy
    assert "--ref)" in deploy
    assert 'git -C "$APP" checkout -q --detach "$TARGET_REF"' in deploy
    # It announces itself, records itself, and reports itself afterwards.
    assert 'DEPLOYING $TARGET_REF — NOT origin/main' in deploy
    assert '"ref":"%s"' in deploy
    assert "NOT origin/main — a plain deploy will replace it" in deploy
    # And the default is unchanged.
    assert deploy.count('ALICIA_DEPLOY_REF:-origin/main') == 1


def test_landing_never_mutates_the_active_gh_account():
    """`gh auth switch` is global state every session on this machine shares.

    deploy.sh already names the hazard for fetch: a parallel session switching
    to the personal account makes a work repo read as missing rather than
    forbidden. A push that switches accounts and switches back has a window
    where exactly that is true.
    """
    from pathlib import Path

    land = (Path(__file__).parents[1] / "scripts/land.sh").read_text()
    # The comments explain why `gh auth switch` is wrong, so check the code.
    body = "\n".join(
        line for line in land.splitlines() if not line.lstrip().startswith("#")
    )

    assert "gh auth switch" not in body
    assert "gh auth switch" in land, "and it should still say why"
    assert "credential-run" in land
    assert "github-personal-mcp" in land and "atlas-core" in land
    # The token reaches git through the environment, never argv, never a file.
    assert r"password=\${$VAR}" in land
    # `credential.helper` is multi-valued, so -c appends. Without the reset git
    # asks the gh helper first and the 1Password token is never consulted.
    assert "-c credential.helper= \\" in land
    assert land.index("-c credential.helper= ") < land.index("password=")
    assert "mktemp" not in land and "> /tmp" not in land
    # And it proves the remote actually moved.
    assert 'origin/$BRANCH is $AFTER' in land


def test_landing_picks_the_profile_from_the_repo_owner():
    from pathlib import Path

    land = (Path(__file__).parents[1] / "scripts/land.sh").read_text()
    owners = land[land.index("case \"$OWNER\""):land.index("esac", land.index("case \"$OWNER\""))]

    assert "justinfowler925) PROFILE=\"github-personal-mcp\"" in owners
    assert "clearspeedrevops" in owners
    # An owner with no declared credential fails loudly rather than guessing.
    assert "no credential profile declared" in land
