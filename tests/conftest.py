"""Shared test fixtures for Commit Surgeon.

Every fixture that creates a Git repository does so inside ``tmp_path``
(pytest-provided, automatically cleaned up) so that the user's real
repository is never touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command inside *cwd* and assert success."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """A temporary Git repository **with one initial commit**.

    Contents after setup::

        README.md   "# Test Repository\\n"
        (1 commit on the default branch)
    """
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@commit-surgeon.dev")
    _git(tmp_path, "config", "user.name", "Test User")

    readme = tmp_path / "README.md"
    readme.write_text("# Test Repository\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Initial commit")

    return tmp_path


@pytest.fixture()
def tmp_empty_git_repo(tmp_path: Path) -> Path:
    """A temporary Git repository **with zero commits**."""
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@commit-surgeon.dev")
    _git(tmp_path, "config", "user.name", "Test User")

    return tmp_path


@pytest.fixture()
def tmp_non_git_dir(tmp_path: Path) -> Path:
    """A plain temporary directory that is **not** a Git repository."""
    (tmp_path / "some_file.txt").write_text("not a git repo\n")
    return tmp_path


@pytest.fixture()
def tmp_messy_repo(tmp_path: Path) -> Path:
    """A repository with a realistic multi-change scenario.

    Initial committed structure::

        src/auth.py
        src/user.py
        src/user_model.py
        tests/test_auth.py
        docs/guide.md
        README.md
        pyproject.toml

    Changes left in the working tree (NOT staged unless noted)::

        Modified:   src/auth.py          (implementation)
        Modified:   tests/test_auth.py   (matching test)
        Modified:   src/user.py          (standalone source)
        Modified:   docs/guide.md        (documentation)
        Modified:   README.md            (documentation)
        Untracked:  src/new_feature.py   (new source file)
    """
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@commit-surgeon.dev")
    _git(tmp_path, "config", "user.name", "Test User")

    # ── initial committed structure ─────────────────────────────────────
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()

    (tmp_path / "src" / "auth.py").write_text(
        "def login():\n    pass\n\ndef logout():\n    pass\n"
    )
    (tmp_path / "tests" / "test_auth.py").write_text(
        "def test_login():\n    pass\n\ndef test_logout():\n    pass\n"
    )
    (tmp_path / "src" / "user.py").write_text("class User:\n    pass\n")
    (tmp_path / "src" / "user_model.py").write_text("class UserModel:\n    pass\n")
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n")
    (tmp_path / "README.md").write_text("# Project\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Initial structure")

    # ── make messy changes ──────────────────────────────────────────────
    (tmp_path / "src" / "auth.py").write_text(
        "def login():\n    return True\n\ndef logout():\n    return False\n"
    )
    (tmp_path / "tests" / "test_auth.py").write_text(
        "from src.auth import login\n\ndef test_login():\n    assert login()\n"
    )
    (tmp_path / "src" / "user.py").write_text(
        "class User:\n    name: str\n    email: str\n"
    )
    (tmp_path / "docs" / "guide.md").write_text(
        "# Updated Guide\n\nNew content here.\n"
    )
    (tmp_path / "README.md").write_text("# Updated Project\n\nWith description.\n")

    # New untracked file
    (tmp_path / "src" / "new_feature.py").write_text(
        "def awesome():\n    return 42\n"
    )

    return tmp_path


@pytest.fixture()
def tmp_staged_repo(tmp_path: Path) -> Path:
    """A repository with ALL changes staged (in the Git index).

    Changes::

        Staged modified:   src/auth.py
        Staged modified:   tests/test_auth.py
        Staged modified:   docs/guide.md
    """
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@commit-surgeon.dev")
    _git(tmp_path, "config", "user.name", "Test User")

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()

    (tmp_path / "src" / "auth.py").write_text("def login():\n    pass\n")
    (tmp_path / "tests" / "test_auth.py").write_text("def test_login():\n    pass\n")
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n")
    (tmp_path / "README.md").write_text("# Project\n")

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Initial")

    # Stage modifications
    (tmp_path / "src" / "auth.py").write_text(
        "def login():\n    return True\n"
    )
    (tmp_path / "tests" / "test_auth.py").write_text(
        "def test_login():\n    assert True\n"
    )
    (tmp_path / "docs" / "guide.md").write_text("# Updated Guide\n")
    _git(tmp_path, "add", ".")

    return tmp_path


@pytest.fixture()
def tmp_mixed_staged_unstaged(tmp_path: Path) -> Path:
    """A repository with mixed staged, unstaged, and untracked changes.

    Changes::

        Staged modified + further unstaged:  src/auth.py
        Unstaged-only modified:              src/user.py
        Untracked:                           src/new_feature.py
    """
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@commit-surgeon.dev")
    _git(tmp_path, "config", "user.name", "Test User")

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    (tmp_path / "src" / "auth.py").write_text("def login():\n    pass\n")
    (tmp_path / "tests" / "test_auth.py").write_text("def test_login():\n    pass\n")
    (tmp_path / "src" / "user.py").write_text("class User:\n    pass\n")

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Initial")

    # Stage auth change
    (tmp_path / "src" / "auth.py").write_text("def login():\n    return True\n")
    _git(tmp_path, "add", "src/auth.py")

    # Further unstaged edit to auth.py (both staged and unstaged)
    (tmp_path / "src" / "auth.py").write_text(
        "def login():\n    return True\ndef verify():\n    pass\n"
    )

    # Unstaged-only change
    (tmp_path / "src" / "user.py").write_text("class User:\n    name: str\n")

    # Untracked file
    (tmp_path / "src" / "new_feature.py").write_text(
        "def awesome():\n    return 42\n"
    )

    return tmp_path


@pytest.fixture()
def tmp_binary_repo(tmp_path: Path) -> Path:
    """A repository with a binary file change."""
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@commit-surgeon.dev")
    _git(tmp_path, "config", "user.name", "Test User")

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    (tmp_path / "README.md").write_text("# Project\n")

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Initial")

    # Modify binary
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 80)
    _git(tmp_path, "add", "assets/logo.png")

    return tmp_path
