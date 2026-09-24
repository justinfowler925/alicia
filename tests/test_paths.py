"""State lives outside every checkout, because it must survive a branch switch.

It used to resolve to `<repo>/state/`, which made Alicia's memory a property of
which checkout was running it. The service runs from a shared repo that other
sessions switch branches in — twice in one day the daemon served a different
branch than intended, and the same mechanism one step further hands it an empty
notes pad with no error anywhere.
"""

import importlib
from pathlib import Path

import pytest

from alicia import paths


@pytest.fixture(autouse=True)
def _reset_migration_guard():
    paths._migrated = False
    yield
    paths._migrated = False


def test_canon_db_lives_in_the_shared_state_dir(monkeypatch):
    monkeypatch.delenv("ALICIA_CANON_DB_PATH", raising=False)
    monkeypatch.delenv("ALICIA_STATE_DIR", raising=False)
    assert paths.canon_db_path() == paths.SHARED_STATE_DIR / "canon.sqlite"
    assert paths.SHARED_STATE_DIR.parent.name in {".alicia", ".brutus"}


def test_canon_db_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICIA_CANON_DB_PATH", str(tmp_path / "custom.sqlite"))
    assert paths.canon_db_path() == tmp_path / "custom.sqlite"


def test_the_default_is_outside_any_checkout(monkeypatch):
    monkeypatch.delenv("ALICIA_STATE_DIR", raising=False)
    d = paths.default_state_dir()
    assert d == paths.alicia_home() / "state"
    assert "Projects" not in str(d), "state must not live inside a checkout"


def test_the_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "elsewhere"))
    assert paths.default_state_dir() == tmp_path / "elsewhere"


def test_state_dir_is_created(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "made"))
    assert paths.state_dir().is_dir()


def test_legacy_state_is_migrated_once(monkeypatch, tmp_path):
    legacy = tmp_path / "repo" / "state"
    legacy.mkdir(parents=True)
    (legacy / "memory.sqlite").write_bytes(b"the real memory")
    (legacy / "todos.sqlite").write_bytes(b"the real todos")
    monkeypatch.setattr(paths, "_LEGACY_DIR", legacy)
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "new"))

    target = paths.state_dir()
    assert (target / "memory.sqlite").read_bytes() == b"the real memory"
    assert (target / "todos.sqlite").read_bytes() == b"the real todos"
    assert (legacy / "memory.sqlite").exists(), "the originals stay put"


def test_migration_never_overwrites_live_state(monkeypatch, tmp_path):
    """The dangerous direction: re-copying would roll memory back to whatever
    the old checkout happened to hold."""
    legacy = tmp_path / "repo" / "state"
    legacy.mkdir(parents=True)
    (legacy / "memory.sqlite").write_bytes(b"STALE")
    target = tmp_path / "new"
    target.mkdir()
    (target / "memory.sqlite").write_bytes(b"CURRENT")

    monkeypatch.setattr(paths, "_LEGACY_DIR", legacy)
    monkeypatch.setenv("ALICIA_STATE_DIR", str(target))
    paths.state_dir()
    assert (target / "memory.sqlite").read_bytes() == b"CURRENT"


def test_migration_is_a_no_op_without_legacy_state(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_LEGACY_DIR", tmp_path / "nothing-here")
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "new"))
    assert paths.state_dir().is_dir()


def test_a_broken_migration_never_blocks_startup(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise OSError("disk on fire")

    monkeypatch.setattr(paths.shutil, "copy2", boom)
    legacy = tmp_path / "repo" / "state"
    legacy.mkdir(parents=True)
    (legacy / "memory.sqlite").write_bytes(b"x")
    monkeypatch.setattr(paths, "_LEGACY_DIR", legacy)
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "new"))
    assert paths.state_dir().is_dir()  # must not raise


@pytest.mark.parametrize(
    "module,attr", [("alicia.memory", "MemoryStore"), ("alicia.todos", "TodoStore"), ("alicia.session", "SessionStore")]
)
def test_stores_default_outside_the_checkout(monkeypatch, tmp_path, module, attr):
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "s"))
    mod = importlib.import_module(module)
    store = getattr(mod, attr)()
    assert str(store.path).startswith(str(tmp_path / "s"))
    assert "/alicia/state/" not in str(store.path)


def test_no_module_still_resolves_state_relative_to_the_package():
    """The regression guard: this is the pattern that made memory a property of
    which checkout was running."""
    import alicia

    root = Path(alicia.__file__).resolve().parent
    offenders = []
    for py in root.glob("*.py"):
        if py.name == "paths.py":
            continue
        text = py.read_text()
        if 'parent.parent / "state"' in text:
            offenders.append(py.name)
    assert offenders == [], f"still resolving state from the checkout: {offenders}"


def test_home_prefers_alicia(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICIA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".alicia").mkdir()
    (tmp_path / ".brutus").mkdir()
    assert paths.alicia_home() == tmp_path / ".alicia"


def test_home_falls_back_to_legacy_brutus_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICIA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".brutus").mkdir()
    assert paths.alicia_home() == tmp_path / ".brutus"
    assert paths.home_path("~/.alicia/app") == str(tmp_path / ".brutus" / "app")


def test_fresh_machine_gets_alicia_home(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICIA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.alicia_home() == tmp_path / ".alicia"
