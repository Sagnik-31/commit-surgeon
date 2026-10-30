"""Working-tree status inspection.

Parses ``git status --porcelain=v2`` to produce a structured view of every
kind of change in the repository — staged, unstaged, untracked, renamed,
and conflicted — without modifying any file or index state.

Porcelain v2 reference (requires Git ≥ 2.11):

    Header lines
        # branch.oid <commit> | (initial)
        # branch.head <branch> | (detached)

    Ordinary changed entry
        1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>

    Renamed / copied entry
        2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\\t<origPath>

    Unmerged entry
        u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>

    Untracked
        ? <path>

    Ignored
        ! <path>

XY semantics (X = index status, Y = work-tree status):
    . not modified   M modified   A added   D deleted
    R renamed        C copied     T type-changed
    U unmerged (only in ``u`` lines)
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from commit_surgeon.git import run_git


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FileStatus(str, Enum):
    """Category of a single-file change."""

    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"


class ConflictType(str, Enum):
    """How two branches disagree about a path."""

    BOTH_MODIFIED = "both_modified"
    BOTH_ADDED = "both_added"
    BOTH_DELETED = "both_deleted"
    ADDED_BY_US = "added_by_us"
    ADDED_BY_THEM = "added_by_them"
    DELETED_BY_US = "deleted_by_us"
    DELETED_BY_THEM = "deleted_by_them"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class FileChange(BaseModel):
    """A single change — either staged (index vs HEAD) or unstaged
    (work-tree vs index)."""

    path: str
    """Relative path inside the repository."""

    status: FileStatus
    """What happened to the file."""

    old_path: str | None = None
    """Previous path for renames / copies.  *None* for non-rename changes."""


class ConflictEntry(BaseModel):
    """A path that has an unresolved merge conflict."""

    path: str
    conflict_type: ConflictType


class WorkingTreeStatus(BaseModel):
    """Structured snapshot of the entire working-tree status.

    The categories are mutually exclusive per entry: a file that is both
    staged and has further working-tree modifications will appear once in
    ``staged`` and once in ``unstaged``.  Conflicts are listed separately
    and are *not* duplicated in ``staged`` / ``unstaged``.
    """

    staged: list[FileChange] = []
    """Changes recorded in the index (ready to commit)."""

    unstaged: list[FileChange] = []
    """Changes in the work-tree that are *not* staged."""

    untracked: list[str] = []
    """Paths not tracked by Git."""

    conflicts: list[ConflictEntry] = []
    """Paths with unresolved merge conflicts."""

    @property
    def total_changed(self) -> int:
        """Total number of entries across all categories."""
        return (
            len(self.staged)
            + len(self.unstaged)
            + len(self.untracked)
            + len(self.conflicts)
        )

    @property
    def is_clean(self) -> bool:
        """True when there are absolutely no changes."""
        return self.total_changed == 0


# ---------------------------------------------------------------------------
# Internal lookup tables
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, FileStatus] = {
    "M": FileStatus.MODIFIED,
    "A": FileStatus.ADDED,
    "D": FileStatus.DELETED,
    "R": FileStatus.RENAMED,
    "C": FileStatus.COPIED,
    "T": FileStatus.TYPE_CHANGED,
}

_CONFLICT_MAP: dict[str, ConflictType] = {
    "DD": ConflictType.BOTH_DELETED,
    "AU": ConflictType.ADDED_BY_US,
    "UD": ConflictType.DELETED_BY_THEM,
    "UA": ConflictType.ADDED_BY_THEM,
    "DU": ConflictType.DELETED_BY_US,
    "AA": ConflictType.BOTH_ADDED,
    "UU": ConflictType.BOTH_MODIFIED,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_working_tree_status(repo_path: Path | None = None) -> WorkingTreeStatus:
    """Read the working-tree status without modifying anything.

    Internally invokes::

        git status --porcelain=v2 --untracked-files=all --renames

    Args:
        repo_path: Repository root (or any path inside it).  Defaults to
            the current working directory.

    Returns:
        A fully populated :class:`WorkingTreeStatus`.
    """
    cwd = repo_path or Path.cwd()

    result = run_git(
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--renames",
        cwd=cwd,
    )

    if not result.success:
        # Bare repos, broken repos, etc. — return an empty status rather
        # than crashing so that the caller can still render *something*.
        return WorkingTreeStatus()

    return _parse_porcelain_v2(result.stdout)


# ---------------------------------------------------------------------------
# Parser internals
# ---------------------------------------------------------------------------


def _parse_porcelain_v2(output: str) -> WorkingTreeStatus:
    """Parse the full output of ``git status --porcelain=v2``."""
    staged: list[FileChange] = []
    unstaged: list[FileChange] = []
    untracked: list[str] = []
    conflicts: list[ConflictEntry] = []

    for line in output.splitlines():
        if not line:
            continue

        prefix = line[0]

        if prefix == "#":
            continue  # header line

        if prefix == "?":
            # Untracked: ``? <path>``
            untracked.append(line[2:])
            continue

        if prefix == "!":
            continue  # ignored

        if line.startswith("u "):
            _parse_unmerged(line, conflicts)
            continue

        if line.startswith("1 "):
            _parse_ordinary(line, staged, unstaged)
            continue

        if line.startswith("2 "):
            _parse_renamed(line, staged, unstaged)
            continue

    return WorkingTreeStatus(
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicts=conflicts,
    )


def _parse_ordinary(
    line: str,
    staged: list[FileChange],
    unstaged: list[FileChange],
) -> None:
    """Parse an ordinary changed entry (prefix ``1``).

    Format::

        1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
          ^^^                                    ^^^^
          index/work-tree status codes           file path
    """
    parts = line.split(" ", 8)
    if len(parts) < 9:
        return  # malformed — skip silently

    xy = parts[1]
    path = parts[8]
    x_code = xy[0]  # index (staged)
    y_code = xy[1]  # work-tree (unstaged)

    if x_code != "." and x_code in _STATUS_MAP:
        staged.append(FileChange(path=path, status=_STATUS_MAP[x_code]))

    if y_code != "." and y_code in _STATUS_MAP:
        unstaged.append(FileChange(path=path, status=_STATUS_MAP[y_code]))


def _parse_renamed(
    line: str,
    staged: list[FileChange],
    unstaged: list[FileChange],
) -> None:
    """Parse a renamed / copied entry (prefix ``2``).

    Format::

        2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\\t<origPath>
    """
    parts = line.split(" ", 9)
    if len(parts) < 10:
        return

    xy = parts[1]
    x_code = xy[0]
    y_code = xy[1]

    path_field = parts[9]
    tab_parts = path_field.split("\t", 1)
    if len(tab_parts) != 2:
        return  # malformed

    new_path, old_path = tab_parts

    if x_code != "." and x_code in _STATUS_MAP:
        staged.append(
            FileChange(path=new_path, status=_STATUS_MAP[x_code], old_path=old_path)
        )

    # The renamed file might *also* be modified in the work-tree.
    if y_code != "." and y_code in _STATUS_MAP:
        unstaged.append(FileChange(path=new_path, status=_STATUS_MAP[y_code]))


def _parse_unmerged(line: str, conflicts: list[ConflictEntry]) -> None:
    """Parse an unmerged entry (prefix ``u``).

    Format::

        u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
    """
    parts = line.split(" ", 10)
    if len(parts) < 11:
        return

    xy = parts[1]
    path = parts[10]

    conflict_type = _CONFLICT_MAP.get(xy, ConflictType.BOTH_MODIFIED)
    conflicts.append(ConflictEntry(path=path, conflict_type=conflict_type))
