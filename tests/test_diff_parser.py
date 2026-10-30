"""Tests for the diff parser."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from commit_surgeon.core.diff_parser import (
    ChangeSet,
    FileDiff,
    _extract_rename_target,
    parse_changes,
)
from commit_surgeon.git.status import FileStatus


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


# =========================================================================
# parse_changes — end-to-end
# =========================================================================


class TestParseChanges:
    """End-to-end tests that create real git repos."""

    def test_clean_repo(self, tmp_git_repo: Path) -> None:
        cs = parse_changes(tmp_git_repo)
        assert cs.is_empty
        assert cs.total_files == 0

    def test_staged_modification(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").write_text("# Changed\n")
        _git(tmp_git_repo, "add", "README.md")

        cs = parse_changes(tmp_git_repo)

        assert len(cs.staged) == 1
        f = cs.staged[0]
        assert f.path == "README.md"
        assert f.status == FileStatus.MODIFIED
        assert f.is_staged is True
        assert f.additions >= 1

    def test_unstaged_modification(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").write_text("# Edited\n")

        cs = parse_changes(tmp_git_repo)

        assert len(cs.unstaged) == 1
        f = cs.unstaged[0]
        assert f.status == FileStatus.MODIFIED
        assert f.is_staged is False

    def test_untracked_file(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "untracked.py").write_text("x = 1\n")

        cs = parse_changes(tmp_git_repo)

        assert len(cs.untracked) == 1
        f = cs.untracked[0]
        assert f.path == "untracked.py"
        assert f.is_untracked is True
        assert f.status == FileStatus.ADDED

    def test_deleted_file(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").unlink()

        cs = parse_changes(tmp_git_repo)

        assert len(cs.unstaged) == 1
        assert cs.unstaged[0].status == FileStatus.DELETED

    def test_renamed_file(self, tmp_git_repo: Path) -> None:
        orig = tmp_git_repo / "module.py"
        orig.write_text("def hello():\n    return 'world'\n" * 5)
        _git(tmp_git_repo, "add", "module.py")
        _git(tmp_git_repo, "commit", "-m", "add module")

        _git(tmp_git_repo, "mv", "module.py", "renamed.py")

        cs = parse_changes(tmp_git_repo)

        assert len(cs.staged) == 1
        f = cs.staged[0]
        assert f.status == FileStatus.RENAMED
        assert f.path == "renamed.py"
        assert f.old_path == "module.py"

    def test_mixed_staged_and_unstaged(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").write_text("# Staged\n")
        _git(tmp_git_repo, "add", "README.md")
        (tmp_git_repo / "README.md").write_text("# Further edit\n")

        cs = parse_changes(tmp_git_repo)

        assert len(cs.staged) == 1
        assert len(cs.unstaged) == 1

    def test_total_files_deduplicates(self, tmp_git_repo: Path) -> None:
        """total_files should count unique paths."""
        (tmp_git_repo / "README.md").write_text("# Staged\n")
        _git(tmp_git_repo, "add", "README.md")
        (tmp_git_repo / "README.md").write_text("# Further\n")
        (tmp_git_repo / "new.txt").write_text("hi\n")

        cs = parse_changes(tmp_git_repo)

        # README.md appears in both staged & unstaged, new.txt in untracked
        assert cs.total_files == 2

    def test_empty_repo(self, tmp_empty_git_repo: Path) -> None:
        cs = parse_changes(tmp_empty_git_repo)
        assert cs.is_empty


# =========================================================================
# FileDiff computed fields
# =========================================================================


class TestFileDiffMetadata:
    """Computed metadata on FileDiff."""

    def test_extension(self) -> None:
        f = FileDiff(path="src/main.py", status=FileStatus.MODIFIED)
        assert f.extension == ".py"

    def test_directory(self) -> None:
        f = FileDiff(path="src/pkg/main.py", status=FileStatus.MODIFIED)
        assert f.directory == "src/pkg"

    def test_directory_root(self) -> None:
        f = FileDiff(path="setup.py", status=FileStatus.MODIFIED)
        assert f.directory == ""

    def test_basename(self) -> None:
        f = FileDiff(path="src/auth_handler.py", status=FileStatus.MODIFIED)
        assert f.basename == "auth_handler"


# =========================================================================
# Rename-target extraction
# =========================================================================


class TestExtractRenameTarget:
    """Unit tests for _extract_rename_target."""

    def test_simple_rename(self) -> None:
        assert _extract_rename_target("old.py => new.py") == "new.py"

    def test_brace_prefix(self) -> None:
        assert _extract_rename_target("{old => new}/file.py") == "new/file.py"

    def test_brace_suffix(self) -> None:
        assert _extract_rename_target("dir/{old.py => new.py}") == "dir/new.py"

    def test_no_rename(self) -> None:
        assert _extract_rename_target("plain.py") == "plain.py"
