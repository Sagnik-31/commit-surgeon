"""Repository detection and metadata inspection.

This module answers the question "what repository am I in?" without
modifying any state.  It handles:

- Normal repositories with commits.
- Empty repositories (``git init`` with no commits).
- Detached HEAD (e.g. after ``git checkout <sha>``).
- Invocations from arbitrary subdirectories.
- Missing ``git`` binary.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from commit_surgeon.git import GitNotFoundError, run_git


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class RepositoryInfo(BaseModel):
    """Immutable snapshot of Git repository metadata."""

    root: Path
    """Absolute path to the repository work-tree root."""

    branch: str | None = None
    """Current branch name, or *None* for detached HEAD / unborn branch."""

    commit_hash: str | None = None
    """Full SHA-1 of HEAD, or *None* in an empty repository."""

    is_empty: bool = False
    """True when the repository has no commits yet."""

    is_detached: bool = False
    """True when HEAD points directly at a commit (no branch)."""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepositoryNotFoundError(Exception):
    """The given path is not inside a Git work-tree."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_repository(path: Path | None = None) -> RepositoryInfo:
    """Detect and inspect the Git repository that contains *path*.

    Args:
        path: Any path inside the target repository.  Defaults to the
            current working directory.

    Returns:
        :class:`RepositoryInfo` populated with branch, commit hash, and
        state flags.

    Raises:
        RepositoryNotFoundError: If *path* is not inside a Git work-tree.
        GitNotFoundError: If the ``git`` binary is missing.
    """
    cwd = Path(path) if path is not None else Path.cwd()

    # 1. Is this inside a work-tree at all?
    result = run_git("rev-parse", "--is-inside-work-tree", cwd=cwd)
    if not result.success:
        raise RepositoryNotFoundError(
            f"Not a Git repository: {cwd}\n"
            "Run 'git init' to create one, or change to a directory that "
            "is already inside a Git work-tree."
        )

    # 2. Resolve the absolute work-tree root.
    root_result = run_git("rev-parse", "--show-toplevel", cwd=cwd, check=True)
    root = Path(root_result.stdout.strip())

    # 3. Try to resolve HEAD to a commit hash.
    head_result = run_git("rev-parse", "HEAD", cwd=cwd)
    is_empty = not head_result.success  # fails when there are zero commits
    commit_hash = head_result.stdout.strip() if head_result.success else None

    # 4. Determine branch name and detached-HEAD status.
    branch: str | None = None
    is_detached = False

    if is_empty:
        # No commits yet → try to read the symbolic ref for the unborn
        # branch name (e.g. "main").
        sym_result = run_git("symbolic-ref", "--short", "HEAD", cwd=cwd)
        if sym_result.success:
            branch = sym_result.stdout.strip()
    else:
        branch_result = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        if branch_result.success:
            name = branch_result.stdout.strip()
            if name == "HEAD":
                # ``--abbrev-ref`` returns the literal string "HEAD" when
                # the head is detached.
                is_detached = True
            else:
                branch = name

    return RepositoryInfo(
        root=root,
        branch=branch,
        commit_hash=commit_hash,
        is_empty=is_empty,
        is_detached=is_detached,
    )
