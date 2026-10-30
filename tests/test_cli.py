"""CLI integration tests using Typer's CliRunner.

Tests cover: --help, --version, analyze (valid, empty, non-git),
and error handling for missing git and timeouts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from commit_surgeon import __version__
from commit_surgeon.cli import app
from commit_surgeon.git import GitError, GitNotFoundError, run_git

runner = CliRunner()


# =========================================================================
# surgeon --help
# =========================================================================


class TestCliHelp:
    """``surgeon --help`` integration tests."""

    def test_exit_code_is_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_output_mentions_app(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Commit Surgeon" in result.stdout

    def test_output_lists_analyze(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "analyze" in result.stdout


# =========================================================================
# surgeon --version
# =========================================================================


class TestCliVersion:
    """``surgeon --version`` integration tests."""

    def test_exit_code_is_zero(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0

    def test_output_contains_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert __version__ in result.stdout


# =========================================================================
# surgeon analyze
# =========================================================================


class TestCliAnalyze:
    """``surgeon analyze`` integration tests."""

    def test_valid_repo_exits_zero(self, tmp_git_repo: Path) -> None:
        result = runner.invoke(app, ["analyze", "--path", str(tmp_git_repo)])
        assert result.exit_code == 0

    def test_non_git_dir_exits_one(self, tmp_non_git_dir: Path) -> None:
        result = runner.invoke(app, ["analyze", "--path", str(tmp_non_git_dir)])
        assert result.exit_code == 1

    def test_empty_repo_exits_zero(self, tmp_empty_git_repo: Path) -> None:
        result = runner.invoke(app, ["analyze", "--path", str(tmp_empty_git_repo)])
        assert result.exit_code == 0


# =========================================================================
# Git error handling (unit-level, no real repos needed)
# =========================================================================


class TestGitErrorHandling:
    """Edge-case error handling in the git runner."""

    def test_timeout_raises_git_error(self) -> None:
        """A timed-out subprocess must raise ``GitError``."""
        with patch(
            "commit_surgeon.git.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
        ):
            with pytest.raises(GitError, match="timed out"):
                run_git("status")

    def test_missing_git_raises_not_found_error(self) -> None:
        """A missing ``git`` binary must raise ``GitNotFoundError``."""
        with patch(
            "commit_surgeon.git.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(GitNotFoundError, match="not installed"):
                run_git("status")

    def test_check_flag_raises_on_nonzero_exit(self, tmp_non_git_dir: Path) -> None:
        """``run_git(..., check=True)`` raises when the command fails."""
        with pytest.raises(GitError, match="failed"):
            run_git("rev-parse", "HEAD", cwd=tmp_non_git_dir, check=True)
