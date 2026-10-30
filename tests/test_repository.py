"""Tests for repository detection and working-tree status inspection.

All tests operate on isolated temporary Git repositories — the user's
real repository is never read or modified.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from commit_surgeon.core.repository import (
    RepositoryInfo,
    RepositoryNotFoundError,
    detect_repository,
)
from commit_surgeon.git.status import (
    ConflictType,
    FileStatus,
    WorkingTreeStatus,
    get_working_tree_status,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Shorthand for running git inside a test repo."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


# =========================================================================
# Repository detection
# =========================================================================


class TestDetectRepository:
    """Tests for ``detect_repository()``."""

    def test_valid_repository(self, tmp_git_repo: Path) -> None:
        """A repo with at least one commit is detected correctly."""
        info = detect_repository(tmp_git_repo)

        assert info.root == tmp_git_repo
        assert info.branch == "main"
        assert info.commit_hash is not None
        assert len(info.commit_hash) == 40  # full SHA-1
        assert not info.is_empty
        assert not info.is_detached

    def test_non_git_directory(self, tmp_non_git_dir: Path) -> None:
        """A directory without ``.git`` raises ``RepositoryNotFoundError``."""
        with pytest.raises(RepositoryNotFoundError):
            detect_repository(tmp_non_git_dir)

    def test_empty_repository(self, tmp_empty_git_repo: Path) -> None:
        """An empty repo (``git init``, no commits) is detected as empty."""
        info = detect_repository(tmp_empty_git_repo)

        assert info.root == tmp_empty_git_repo
        assert info.is_empty is True
        assert info.commit_hash is None
        # The unborn branch name should still be discoverable.
        assert info.branch == "main"

    def test_detached_head(self, tmp_git_repo: Path) -> None:
        """Detached HEAD is detected and branch is None."""
        expected_hash = _git(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
        _git(tmp_git_repo, "checkout", "--detach")

        info = detect_repository(tmp_git_repo)

        assert info.is_detached is True
        assert info.branch is None
        assert info.commit_hash == expected_hash

    def test_subdirectory_detection(self, tmp_git_repo: Path) -> None:
        """Repository root is found when invoked from a nested subdir."""
        subdir = tmp_git_repo / "src" / "deep" / "nested"
        subdir.mkdir(parents=True)

        info = detect_repository(subdir)

        assert info.root == tmp_git_repo

    def test_branch_name(self, tmp_git_repo: Path) -> None:
        """Current branch name is reported accurately after a switch."""
        _git(tmp_git_repo, "checkout", "-b", "feature/test-branch")

        info = detect_repository(tmp_git_repo)

        assert info.branch == "feature/test-branch"


# =========================================================================
# Working-tree status
# =========================================================================


class TestWorkingTreeStatus:
    """Tests for ``get_working_tree_status()``."""

    def test_clean_tree(self, tmp_git_repo: Path) -> None:
        """A freshly-committed repo should be clean."""
        status = get_working_tree_status(tmp_git_repo)

        assert status.is_clean
        assert status.total_changed == 0

    def test_modified_file(self, tmp_git_repo: Path) -> None:
        """An unstaged modification to a tracked file is detected."""
        (tmp_git_repo / "README.md").write_text("# Changed\n")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.unstaged) == 1
        assert status.unstaged[0].path == "README.md"
        assert status.unstaged[0].status == FileStatus.MODIFIED

    def test_staged_new_file(self, tmp_git_repo: Path) -> None:
        """A newly added and staged file is detected."""
        (tmp_git_repo / "new.py").write_text("x = 1\n")
        _git(tmp_git_repo, "add", "new.py")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.staged) == 1
        assert status.staged[0].path == "new.py"
        assert status.staged[0].status == FileStatus.ADDED

    def test_staged_modification(self, tmp_git_repo: Path) -> None:
        """A staged modification to an existing file is detected."""
        (tmp_git_repo / "README.md").write_text("# Staged change\n")
        _git(tmp_git_repo, "add", "README.md")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.staged) == 1
        assert status.staged[0].status == FileStatus.MODIFIED

    def test_untracked_file(self, tmp_git_repo: Path) -> None:
        """Untracked files are detected."""
        (tmp_git_repo / "untracked.txt").write_text("hello\n")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.untracked) == 1
        assert "untracked.txt" in status.untracked

    def test_deleted_file(self, tmp_git_repo: Path) -> None:
        """Deleting a tracked file without staging is detected."""
        (tmp_git_repo / "README.md").unlink()

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.unstaged) == 1
        assert status.unstaged[0].path == "README.md"
        assert status.unstaged[0].status == FileStatus.DELETED

    def test_staged_deletion(self, tmp_git_repo: Path) -> None:
        """A staged deletion is detected."""
        _git(tmp_git_repo, "rm", "README.md")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.staged) == 1
        assert status.staged[0].status == FileStatus.DELETED

    def test_staged_and_further_modified(self, tmp_git_repo: Path) -> None:
        """A file staged then further modified appears in both lists."""
        readme = tmp_git_repo / "README.md"
        readme.write_text("# Staged version\n")
        _git(tmp_git_repo, "add", "README.md")
        readme.write_text("# Further edit after staging\n")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.staged) == 1
        assert len(status.unstaged) == 1
        assert status.staged[0].path == "README.md"
        assert status.unstaged[0].path == "README.md"

    def test_renamed_file(self, tmp_git_repo: Path) -> None:
        """A ``git mv`` rename is detected with old and new paths."""
        # Write enough content for rename detection to kick in.
        original = tmp_git_repo / "original.py"
        original.write_text("def hello():\n    return 'world'\n" * 5)
        _git(tmp_git_repo, "add", "original.py")
        _git(tmp_git_repo, "commit", "-m", "add original")

        _git(tmp_git_repo, "mv", "original.py", "renamed.py")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.staged) == 1
        change = status.staged[0]
        assert change.status == FileStatus.RENAMED
        assert change.path == "renamed.py"
        assert change.old_path == "original.py"

    def test_multiple_change_types(self, tmp_git_repo: Path) -> None:
        """Multiple kinds of changes are detected simultaneously."""
        # Unstaged modification
        (tmp_git_repo / "README.md").write_text("# Modified\n")
        # Staged addition
        (tmp_git_repo / "staged.py").write_text("y = 2\n")
        _git(tmp_git_repo, "add", "staged.py")
        # Untracked file
        (tmp_git_repo / "untracked.log").write_text("log\n")

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.staged) == 1       # staged.py
        assert len(status.unstaged) == 1     # README.md
        assert len(status.untracked) == 1    # untracked.log
        assert status.total_changed == 3

    def test_empty_repo_status(self, tmp_empty_git_repo: Path) -> None:
        """An empty repo with no files reports a clean tree."""
        status = get_working_tree_status(tmp_empty_git_repo)
        assert status.is_clean

    def test_empty_repo_with_untracked(self, tmp_empty_git_repo: Path) -> None:
        """An empty repo with an untracked file still detects it."""
        (tmp_empty_git_repo / "hello.txt").write_text("hi\n")

        status = get_working_tree_status(tmp_empty_git_repo)

        assert len(status.untracked) == 1
        assert "hello.txt" in status.untracked

    def test_merge_conflict(self, tmp_git_repo: Path) -> None:
        """Merge conflicts are detected with correct conflict type."""
        readme = tmp_git_repo / "README.md"

        # branch-a: modify README one way
        _git(tmp_git_repo, "checkout", "-b", "branch-a")
        readme.write_text("# Branch A content\n")
        _git(tmp_git_repo, "add", ".")
        _git(tmp_git_repo, "commit", "-m", "branch-a change")

        # branch-b from main: modify README differently
        _git(tmp_git_repo, "checkout", "main")
        _git(tmp_git_repo, "checkout", "-b", "branch-b")
        readme.write_text("# Branch B content\n")
        _git(tmp_git_repo, "add", ".")
        _git(tmp_git_repo, "commit", "-m", "branch-b change")

        # Merge branch-a into branch-b → conflict
        merge_result = subprocess.run(
            ["git", "merge", "branch-a", "--no-edit"],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
        )
        assert merge_result.returncode != 0, "Expected merge conflict"

        status = get_working_tree_status(tmp_git_repo)

        assert len(status.conflicts) >= 1
        conflict = status.conflicts[0]
        assert conflict.path == "README.md"
        assert conflict.conflict_type == ConflictType.BOTH_MODIFIED
