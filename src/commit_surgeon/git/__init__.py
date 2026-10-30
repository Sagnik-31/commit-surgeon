"""Git operations for Commit Surgeon.

This module provides a safe subprocess-based interface to Git commands.
All functions are read-only — they never modify the user's repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(Exception):
    """Raised when a git command fails unexpectedly."""


class GitNotFoundError(GitError):
    """Raised when the git binary is not installed or not on PATH."""


@dataclass(frozen=True, slots=True)
class GitResult:
    """Result of a git command execution."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


def run_git(
    *args: str,
    cwd: Path | None = None,
    check: bool = False,
    timeout: int = 30,
) -> GitResult:
    """Run a git command safely via subprocess.

    This is the single point of entry for all git interactions. It never
    writes to the repository — callers are responsible for ensuring that
    the arguments they pass are read-only operations.

    Args:
        *args: Git sub-command and arguments (e.g., ``"status"``,
            ``"--porcelain=v2"``).
        cwd: Working directory. ``None`` means the current process cwd.
        check: If *True*, raise :class:`GitError` on a non-zero exit code.
        timeout: Maximum seconds to wait before killing the process.

    Returns:
        A :class:`GitResult` with the captured output.

    Raises:
        GitNotFoundError: If the ``git`` binary cannot be found.
        GitError: If *check* is True and the command exits non-zero, or if
            the process times out.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise GitNotFoundError(
            "Git is not installed or not found on PATH. "
            "Install it from https://git-scm.com/downloads"
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"Git command timed out after {timeout}s: git {' '.join(args)}"
        ) from exc

    git_result = GitResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    if check and not git_result.success:
        raise GitError(
            f"Git command failed (exit {result.returncode}): "
            f"git {' '.join(args)}\n{result.stderr.strip()}"
        )

    return git_result
