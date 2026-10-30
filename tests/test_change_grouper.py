"""Tests for the change grouper.

Unit tests construct :class:`AnalyzedChange` objects directly.
Integration tests use the ``tmp_messy_repo`` fixture to exercise the
full pipeline (parse → analyze → group).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from commit_surgeon.core.change_analyzer import (
    AnalysisResult,
    AnalyzedChange,
    CategoryHint,
    CategorySuggestion,
    Confidence,
    FileClassification,
    analyze_changes,
)
from commit_surgeon.core.change_grouper import (
    ChangeAnalysis,
    ChangeGroup,
    group_changes,
    _extract_concept,
)
from commit_surgeon.core.diff_parser import ChangeSet, FileDiff, parse_changes
from commit_surgeon.core.repository import RepositoryInfo, detect_repository
from commit_surgeon.git.status import FileStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analyzed(
    path: str,
    status: FileStatus = FileStatus.MODIFIED,
    classification: FileClassification = FileClassification.SOURCE,
    counterpart: str | None = None,
    is_staged: bool = False,
    is_untracked: bool = False,
    category: CategorySuggestion = CategorySuggestion.FEAT,
) -> AnalyzedChange:
    """Shorthand for building an AnalyzedChange in tests."""
    return AnalyzedChange(
        diff=FileDiff(
            path=path,
            status=status,
            is_staged=is_staged,
            is_untracked=is_untracked,
        ),
        classification=classification,
        category_hints=[
            CategoryHint(
                category=category,
                confidence=Confidence.MEDIUM,
                reason="test",
            )
        ],
        test_counterpart=counterpart,
    )


def _fake_repo_info() -> RepositoryInfo:
    return RepositoryInfo(root=Path("/fake"), branch="main", commit_hash="abc123" * 7)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


# =========================================================================
# _extract_concept
# =========================================================================


class TestExtractConcept:
    """Unit tests for the concept extractor."""

    def test_simple_name(self) -> None:
        assert _extract_concept("auth.py") == "auth"

    def test_strip_model_suffix(self) -> None:
        assert _extract_concept("user_model.py") == "user"

    def test_strip_handler_suffix(self) -> None:
        assert _extract_concept("auth_handler.py") == "auth"

    def test_multi_segment(self) -> None:
        assert _extract_concept("payment_gateway.py") == "payment"

    def test_generic_name_returns_none(self) -> None:
        assert _extract_concept("utils.py") is None
        assert _extract_concept("__init__.py") is None
        assert _extract_concept("conftest.py") is None

    def test_test_prefix_stripped(self) -> None:
        assert _extract_concept("test_auth.py") == "auth"


# =========================================================================
# Group: test–implementation pairs
# =========================================================================


class TestGroupTestPairs:
    """Pass 1: test ↔ implementation pairing."""

    def test_source_and_test_grouped(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed("src/auth.py", counterpart="tests/test_auth.py"),
                _make_analyzed(
                    "tests/test_auth.py",
                    classification=FileClassification.TEST,
                    counterpart="src/auth.py",
                    category=CategorySuggestion.TEST,
                ),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) == 1
        assert len(result.ungrouped_files) == 0
        grp = result.groups[0]
        assert grp.confidence == "high"
        assert len(grp.files) == 2

    def test_no_counterpart_stays_ungrouped(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed("src/auth.py"),
                _make_analyzed("src/user.py"),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        # Neither file has a counterpart so both should be ungrouped
        # (unless naming convention groups them)
        # They don't share a concept, so they stay ungrouped.
        assert len(result.ungrouped_files) == 2


# =========================================================================
# Group: documentation
# =========================================================================


class TestGroupDocumentation:
    """Pass 2: documentation clustering."""

    def test_docs_grouped(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed(
                    "README.md",
                    classification=FileClassification.DOCUMENTATION,
                    category=CategorySuggestion.DOCS,
                ),
                _make_analyzed(
                    "docs/guide.md",
                    classification=FileClassification.DOCUMENTATION,
                    category=CategorySuggestion.DOCS,
                ),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) == 1
        assert result.groups[0].title == "Documentation"
        assert result.groups[0].category == "docs"


# =========================================================================
# Group: configuration
# =========================================================================


class TestGroupConfiguration:
    """Pass 3: config / build clustering."""

    def test_config_grouped(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed(
                    "pyproject.toml",
                    classification=FileClassification.CONFIGURATION,
                    category=CategorySuggestion.CHORE,
                ),
                _make_analyzed(
                    ".github/workflows/ci.yml",
                    classification=FileClassification.BUILD,
                    category=CategorySuggestion.CHORE,
                ),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) == 1
        assert result.groups[0].title == "Configuration & Build"


# =========================================================================
# Group: naming convention
# =========================================================================


class TestGroupNamingConvention:
    """Pass 4: naming-convention clusters."""

    def test_shared_concept_grouped(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed("src/user.py"),
                _make_analyzed("src/user_model.py"),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) == 1
        grp = result.groups[0]
        assert grp.confidence == "medium"
        assert grp.requires_manual_review is True

    def test_different_concepts_not_grouped(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed("src/auth.py"),
                _make_analyzed("src/payment.py"),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) == 0
        assert len(result.ungrouped_files) == 2


# =========================================================================
# Edge cases
# =========================================================================


class TestGroupEdgeCases:
    """Edge cases and invariants."""

    def test_empty_analysis(self) -> None:
        analysis = AnalysisResult(analyzed=[])
        result = group_changes(analysis, _fake_repo_info())

        assert result.total_changed_files == 0
        assert len(result.groups) == 0
        assert len(result.ungrouped_files) == 0

    def test_single_file_ungrouped(self) -> None:
        analysis = AnalysisResult(
            analyzed=[_make_analyzed("src/solo.py")]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) == 0
        assert len(result.ungrouped_files) == 1

    def test_deleted_file_handled(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed("src/old.py", status=FileStatus.DELETED),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        # Should not crash; file ends up ungrouped.
        assert len(result.ungrouped_files) == 1

    def test_renamed_file_handled(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed("src/new_name.py", status=FileStatus.RENAMED),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())
        assert len(result.ungrouped_files) == 1

    def test_untracked_file_handled(self) -> None:
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed(
                    "src/scratch.py",
                    status=FileStatus.ADDED,
                    is_untracked=True,
                ),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())
        assert len(result.ungrouped_files) == 1

    def test_mixed_staged_unstaged_same_file(self) -> None:
        """Both staged and unstaged entries for the same path should
        end up in the same group (if they have a test counterpart)."""
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed(
                    "src/auth.py",
                    is_staged=True,
                    counterpart="tests/test_auth.py",
                ),
                _make_analyzed(
                    "src/auth.py",
                    is_staged=False,
                    counterpart="tests/test_auth.py",
                ),
                _make_analyzed(
                    "tests/test_auth.py",
                    classification=FileClassification.TEST,
                    counterpart="src/auth.py",
                    category=CategorySuggestion.TEST,
                ),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) == 1
        assert len(result.groups[0].files) == 3

    def test_no_files_discarded(self) -> None:
        """Every input file must appear in groups OR ungrouped."""
        analysis = AnalysisResult(
            analyzed=[
                _make_analyzed("a.py"),
                _make_analyzed("b.py"),
                _make_analyzed("c.py"),
                _make_analyzed(
                    "README.md",
                    classification=FileClassification.DOCUMENTATION,
                    category=CategorySuggestion.DOCS,
                ),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        grouped_count = sum(len(g.files) for g in result.groups)
        total = grouped_count + len(result.ungrouped_files)
        assert total == 4

    def test_multiple_groups(self) -> None:
        """Distinct clusters should produce distinct groups."""
        analysis = AnalysisResult(
            analyzed=[
                # Pair 1: auth + test
                _make_analyzed("src/auth.py", counterpart="tests/test_auth.py"),
                _make_analyzed(
                    "tests/test_auth.py",
                    classification=FileClassification.TEST,
                    counterpart="src/auth.py",
                    category=CategorySuggestion.TEST,
                ),
                # Docs
                _make_analyzed(
                    "README.md",
                    classification=FileClassification.DOCUMENTATION,
                    category=CategorySuggestion.DOCS,
                ),
                # Standalone
                _make_analyzed("src/solo.py"),
            ]
        )
        result = group_changes(analysis, _fake_repo_info())

        assert len(result.groups) >= 2  # pair + docs
        assert len(result.ungrouped_files) >= 1  # solo.py


# =========================================================================
# Integration test — full pipeline with a real repo
# =========================================================================


class TestGroupIntegration:
    """Full pipeline: parse → analyze → group against a real repo."""

    def test_messy_repo(self, tmp_messy_repo: Path) -> None:
        """The messy-repo fixture should produce at least one test-impl
        pair group and a documentation group."""
        repo_info = detect_repository(tmp_messy_repo)
        change_set = parse_changes(tmp_messy_repo)
        analysis = analyze_changes(change_set)
        result = group_changes(analysis, repo_info)

        # Verify nothing was lost.
        grouped = sum(len(g.files) for g in result.groups)
        total = grouped + len(result.ungrouped_files)
        assert total == len(analysis.analyzed)

        # Check we found at least a test-impl pair.
        pair_groups = [g for g in result.groups if "pair" in g.group_id]
        assert len(pair_groups) >= 1

        # Check docs were grouped.
        doc_groups = [g for g in result.groups if g.category == "docs"]
        assert len(doc_groups) >= 1

    def test_clean_repo_produces_empty_analysis(self, tmp_git_repo: Path) -> None:
        repo_info = detect_repository(tmp_git_repo)
        change_set = parse_changes(tmp_git_repo)
        analysis = analyze_changes(change_set)
        result = group_changes(analysis, repo_info)

        assert result.total_changed_files == 0
        assert len(result.groups) == 0
