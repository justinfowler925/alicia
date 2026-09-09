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

    A ref named on the command line is the opposite of that, and it is needed:
    a machine whose gh credential cannot push has no other way to deploy a
    tested fix. So it is allowed, and it shouts.
    """
    from pathlib import Path

    deploy = (Path(__file__).parents[1] / "scripts/deploy.sh").read_text()

    assert 'TARGET_REF="${BRUTUS_DEPLOY_REF:-origin/main}"' in deploy
    assert "--ref)" in deploy
    assert 'git -C "$APP" checkout -q --detach "$TARGET_REF"' in deploy
    # It announces itself, records itself, and reports itself afterwards.
    assert 'DEPLOYING $TARGET_REF — NOT origin/main' in deploy
    assert '"ref":"%s"' in deploy
    assert "NOT origin/main — a plain deploy will replace it" in deploy
    # And the default is unchanged.
    assert deploy.count('BRUTUS_DEPLOY_REF:-origin/main') == 1
