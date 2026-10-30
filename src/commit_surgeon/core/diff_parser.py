"""Diff parser — enriches raw Git status with diff statistics and metadata.

Builds on the Phase 1 status parser by adding:

* Per-file addition / deletion counts (via ``git diff --numstat``).
* Derived metadata: extension, directory, basename.
* A unified :class:`ChangeSet` that separates staged, unstaged, and
  untracked changes.

All operations are **read-only** — nothing is staged, committed, or
modified.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, computed_field

from commit_surgeon.git import run_git
from commit_surgeon.git.status import (
    FileStatus,
    WorkingTreeStatus,
    get_working_tree_status,
)

if False:  # TYPE_CHECKING
    from pathlib import Path


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class FileDiff(BaseModel):
    """A single file change enriched with diff statistics and metadata."""

    path: str
    """Relative path inside the repository."""

    old_path: str | None = None
    """Previous path (renames / copies only)."""

    status: FileStatus
    """What happened: added, modified, deleted, renamed, …"""

    additions: int = 0
    """Lines added (0 for binary or untracked files)."""

    deletions: int = 0
    """Lines removed (0 for binary or untracked files)."""

    is_binary: bool = False
    """True when git reports the file as binary."""

    is_staged: bool = False
    """True when this change is in the index (staged)."""

    is_untracked: bool = False
    """True when the file is not tracked by Git."""

    # -- derived metadata (computed from *path*) ----------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def extension(self) -> str:
        """File extension including the dot, e.g. ``'.py'``."""
        return PurePosixPath(self.path).suffix

    @computed_field  # type: ignore[prop-decorator]
    @property
    def directory(self) -> str:
        """Parent directory, or ``''`` for root-level files."""
        parent = str(PurePosixPath(self.path).parent)
        return "" if parent == "." else parent

    @computed_field  # type: ignore[prop-decorator]
    @property
    def basename(self) -> str:
        """Filename stem without the extension."""
        return PurePosixPath(self.path).stem


class ChangeSet(BaseModel):
    """Complete set of changes in a repository, split by location."""

    staged: list[FileDiff] = []
    """Changes in the index (ready to commit)."""

    unstaged: list[FileDiff] = []
    """Changes in the work-tree that have **not** been staged."""

    untracked: list[FileDiff] = []
    """Files that are not tracked by Git at all."""

    @property
    def all_files(self) -> list[FileDiff]:
        """Every file change regardless of location."""
        return self.staged + self.unstaged + self.untracked

    @property
    def total_files(self) -> int:
        """Count of unique changed paths."""
        return len({f.path for f in self.all_files})

    @property
    def is_empty(self) -> bool:
        return len(self.all_files) == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_changes(repo_path: "Path") -> ChangeSet:
    """Parse every change in the repository into an enriched :class:`ChangeSet`.

    Internally combines ``git status --porcelain=v2`` (via the Phase 1
    status parser) with ``git diff --numstat`` for line-level statistics.

    Args:
        repo_path: Repository root (or any path inside it).

    Returns:
        A fully populated :class:`ChangeSet`.
    """
    tree_status: WorkingTreeStatus = get_working_tree_status(repo_path)

    # Gather per-file addition/deletion stats.
    staged_stats = _get_numstat(repo_path, cached=True)
    unstaged_stats = _get_numstat(repo_path, cached=False)

    staged = [
        FileDiff(
            path=change.path,
            old_path=change.old_path,
            status=change.status,
            additions=staged_stats.get(change.path, _ZERO_STAT)[0],
            deletions=staged_stats.get(change.path, _ZERO_STAT)[1],
            is_binary=staged_stats.get(change.path, _ZERO_STAT)[2],
            is_staged=True,
        )
        for change in tree_status.staged
    ]

    unstaged = [
        FileDiff(
            path=change.path,
            old_path=change.old_path,
            status=change.status,
            additions=unstaged_stats.get(change.path, _ZERO_STAT)[0],
            deletions=unstaged_stats.get(change.path, _ZERO_STAT)[1],
            is_binary=unstaged_stats.get(change.path, _ZERO_STAT)[2],
            is_staged=False,
        )
        for change in tree_status.unstaged
    ]

    untracked = [
        FileDiff(
            path=path,
            status=FileStatus.ADDED,
            is_untracked=True,
        )
        for path in tree_status.untracked
    ]

    return ChangeSet(staged=staged, unstaged=unstaged, untracked=untracked)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Sentinel for missing stats: (additions, deletions, is_binary).
_ZERO_STAT: tuple[int, int, bool] = (0, 0, False)


def _get_numstat(
    repo_path: "Path",
    *,
    cached: bool = False,
) -> dict[str, tuple[int, int, bool]]:
    """Run ``git diff --numstat -M`` and return a mapping of
    ``path → (additions, deletions, is_binary)``.
    """
    args = ["diff", "--numstat", "-M"]
    if cached:
        args.append("--cached")

    result = run_git(*args, cwd=repo_path)
    if not result.success:
        return {}

    stats: dict[str, tuple[int, int, bool]] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue

        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue

        add_str, del_str, path = parts

        is_binary = add_str.strip() == "-" and del_str.strip() == "-"
        additions = 0 if is_binary else int(add_str.strip())
        deletions = 0 if is_binary else int(del_str.strip())

        # git diff --numstat -M uses "old => new" for renames.
        if " => " in path:
            path = _extract_rename_target(path)

        stats[path] = (additions, deletions, is_binary)

    return stats


def _extract_rename_target(path: str) -> str:
    """Extract the *new* path from git's rename notation.

    Examples::

        "old.py => new.py"              → "new.py"
        "{old => new}/file.py"          → "new/file.py"
        "dir/{old.py => new.py}"        → "dir/new.py"
    """
    if "{" in path and "}" in path:
        brace_start = path.index("{")
        brace_end = path.index("}")
        prefix = path[:brace_start]
        suffix = path[brace_end + 1:]
        inner = path[brace_start + 1 : brace_end]

        if " => " in inner:
            _, new_part = inner.split(" => ", 1)
            return prefix + new_part + suffix

    if " => " in path:
        _, new_path = path.split(" => ", 1)
        return new_path.strip()

    return path
