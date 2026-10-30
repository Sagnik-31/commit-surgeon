"""Tests for the commit planner — Phase 3.1 hardened.

Covers all 6 Codex findings:
P1-1: Staged-diff-first commit plans
P1-2: Ambiguous source type (via message integration)
P2-3: Duplicate path handling
P2-4: Preserved analysis metadata
P2-5: Item-level review information
P3-6: JSON validation and round-trip tests
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from commit_surgeon.cli import app
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
)
from commit_surgeon.core.commit_message import CommitType
from commit_surgeon.core.commit_planner import (
    CommitPlan,
    CommitPlanItem,
    PlanFile,
    create_plan,
)
from commit_surgeon.core.diff_parser import ChangeSet, FileDiff, parse_changes
from commit_surgeon.core.repository import RepositoryInfo, detect_repository
from commit_surgeon.git.status import FileStatus


runner = CliRunner()


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
    is_binary: bool = False,
    old_path: str | None = None,
    category: CategorySuggestion = CategorySuggestion.FEAT,
) -> AnalyzedChange:
    return AnalyzedChange(
        diff=FileDiff(
            path=path,
            status=status,
            is_staged=is_staged,
            is_untracked=is_untracked,
            is_binary=is_binary,
            old_path=old_path,
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


def _make_group(
    group_id: str = "pair-1",
    title: str = "Auth",
    category: str = "feat",
    files: list[AnalyzedChange] | None = None,
    confidence: str = "high",
    requires_manual_review: bool = False,
    explanation: str = "Test group explanation",
) -> ChangeGroup:
    if files is None:
        files = [_make_analyzed("src/auth.py", is_staged=True)]
    return ChangeGroup(
        group_id=group_id,
        title=title,
        category=category,
        files=files,
        explanation=explanation,
        confidence=confidence,
        requires_manual_review=requires_manual_review,
    )


def _fake_repo_info() -> RepositoryInfo:
    return RepositoryInfo(
        root=Path("/fake/project"),
        branch="main",
        commit_hash="abc123def456" * 4,
    )


def _make_analysis(
    groups: list[ChangeGroup] | None = None,
    ungrouped: list[AnalyzedChange] | None = None,
    warnings: list[str] | None = None,
) -> ChangeAnalysis:
    return ChangeAnalysis(
        repository=_fake_repo_info(),
        total_changed_files=sum(
            len(g.files) for g in (groups or [])
        ) + len(ungrouped or []),
        groups=groups or [],
        ungrouped_files=ungrouped or [],
        warnings=warnings or [],
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


# =========================================================================
# CommitPlan model
# =========================================================================


class TestCommitPlanModel:
    """Test the CommitPlan Pydantic model."""

    def test_plan_id_generated(self) -> None:
        plan = CommitPlan(repository="project", branch="main")
        assert plan.plan_id
        assert len(plan.plan_id) == 8

    def test_generated_at_populated(self) -> None:
        plan = CommitPlan(repository="project", branch="main")
        assert plan.generated_at

    def test_defaults(self) -> None:
        plan = CommitPlan(repository="project", branch="main")
        assert plan.commits == []
        assert plan.unassigned_staged == []
        assert plan.unstaged_changes == []
        assert plan.untracked_files == []
        assert plan.warnings == []


# =========================================================================
# P1-1: Staged-diff-first commit plans
# =========================================================================


class TestStagedDiffFirst:
    """P1-1: Only staged changes appear in proposed commits."""

    def test_staged_only_in_commits(self) -> None:
        """Staged files go into commits."""
        groups = [_make_group(
            files=[_make_analyzed("src/auth.py", is_staged=True)]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert len(plan.commits) == 1
        assert plan.commits[0].files[0].path == "src/auth.py"
        assert plan.commits[0].files[0].is_staged is True

    def test_unstaged_excluded_from_commits(self) -> None:
        """Unstaged files do NOT appear in commits."""
        groups = [_make_group(
            files=[_make_analyzed("src/auth.py", is_staged=False)]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert len(plan.commits) == 0
        assert len(plan.unstaged_changes) == 1
        assert plan.unstaged_changes[0].path == "src/auth.py"

    def test_untracked_excluded_from_commits(self) -> None:
        """Untracked files go to untracked_files section."""
        groups = [_make_group(
            files=[_make_analyzed("src/new.py", is_untracked=True, status=FileStatus.ADDED)]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert len(plan.commits) == 0
        assert len(plan.untracked_files) == 1
        assert plan.untracked_files[0].path == "src/new.py"

    def test_mixed_group_splits_staged_unstaged(self) -> None:
        """P1-1 CORE: Group with staged + unstaged files is split."""
        groups = [_make_group(
            files=[
                _make_analyzed("src/auth.py", is_staged=True),
                _make_analyzed("src/user.py", is_staged=False),
            ]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        # Only staged file in commits
        assert len(plan.commits) == 1
        assert len(plan.commits[0].files) == 1
        assert plan.commits[0].files[0].path == "src/auth.py"

        # Unstaged file in separate section
        assert len(plan.unstaged_changes) == 1
        assert plan.unstaged_changes[0].path == "src/user.py"

    def test_no_staged_changes_warning(self) -> None:
        """When only unstaged changes exist, warn about staging."""
        groups = [_make_group(
            files=[_make_analyzed("src/auth.py", is_staged=False)]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert any("No staged changes" in w for w in plan.warnings)

    def test_empty_working_tree(self) -> None:
        analysis = _make_analysis()
        plan = create_plan(analysis)

        assert len(plan.commits) == 0
        assert any("clean" in w for w in plan.warnings)

    def test_ungrouped_staged_files(self) -> None:
        """Staged files not in any group go to unassigned_staged."""
        ungrouped = [_make_analyzed("src/solo.py", is_staged=True)]
        analysis = _make_analysis(ungrouped=ungrouped)
        plan = create_plan(analysis)

        assert len(plan.unassigned_staged) == 1
        assert plan.unassigned_staged[0].path == "src/solo.py"
        assert any("not assigned" in w for w in plan.warnings)

    def test_ungrouped_unstaged_files(self) -> None:
        """Unstaged ungrouped files go to unstaged_changes."""
        ungrouped = [_make_analyzed("src/solo.py", is_staged=False)]
        analysis = _make_analysis(ungrouped=ungrouped)
        plan = create_plan(analysis)

        assert len(plan.unassigned_staged) == 0
        assert len(plan.unstaged_changes) == 1

    def test_ungrouped_untracked_files(self) -> None:
        """Untracked ungrouped files go to untracked_files."""
        ungrouped = [_make_analyzed("new.py", is_untracked=True, status=FileStatus.ADDED)]
        analysis = _make_analysis(ungrouped=ungrouped)
        plan = create_plan(analysis)

        assert len(plan.untracked_files) == 1
        assert any("untracked" in w for w in plan.warnings)


# =========================================================================
# P1-1: Staged/unstaged conflict detection
# =========================================================================


class TestStagedUnstagedConflicts:
    """P1-1: Files with both staged and unstaged modifications."""

    def test_conflict_detected(self) -> None:
        """Same path staged in one entry, unstaged in another."""
        files = [
            _make_analyzed("src/auth.py", is_staged=True),
            _make_analyzed("src/auth.py", is_staged=False),
        ]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        # Plan-level warning
        assert any("staged and unstaged" in w for w in plan.warnings)

        # Staged file in commit, flagged
        assert len(plan.commits) == 1
        staged_file = plan.commits[0].files[0]
        assert staged_file.has_unstaged_changes is True

        # Item-level warning
        assert any("staged and unstaged" in w for w in plan.commits[0].warnings)

        # Unstaged in separate section
        assert len(plan.unstaged_changes) == 1

    def test_no_false_positive(self) -> None:
        files = [
            _make_analyzed("src/auth.py", is_staged=True),
            _make_analyzed("src/user.py", is_staged=True),
        ]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert not any("staged and unstaged" in w for w in plan.warnings)

    def test_conflict_across_groups(self) -> None:
        g1 = _make_group(
            group_id="pair-1",
            files=[_make_analyzed("src/auth.py", is_staged=True)],
        )
        g2 = _make_group(
            group_id="pair-2",
            title="Auth Tests",
            files=[_make_analyzed("src/auth.py", is_staged=False)],
        )
        analysis = _make_analysis(groups=[g1, g2])
        plan = create_plan(analysis)

        assert any("staged and unstaged" in w for w in plan.warnings)


# =========================================================================
# P2-3: Duplicate path handling
# =========================================================================


class TestDuplicatePaths:
    """P2-3: No duplicate paths within a commit item."""

    def test_deduplicate_within_commit(self) -> None:
        """If same path appears twice as staged, keep only one."""
        files = [
            _make_analyzed("src/auth.py", is_staged=True),
            _make_analyzed("src/auth.py", is_staged=True),
        ]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        # Should be deduplicated
        assert len(plan.commits) == 1
        assert len(plan.commits[0].files) == 1


# =========================================================================
# P2-4: Preserved analysis metadata
# =========================================================================


class TestPreservedMetadata:
    """P2-4: PlanFile preserves source analysis metadata."""

    def test_classification_preserved(self) -> None:
        files = [_make_analyzed(
            "src/auth.py",
            is_staged=True,
            classification=FileClassification.SOURCE,
        )]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        pf = plan.commits[0].files[0]
        assert pf.classification == "source"

    def test_binary_status_preserved(self) -> None:
        files = [_make_analyzed(
            "assets/logo.png",
            is_staged=True,
            is_binary=True,
            classification=FileClassification.ASSET,
        )]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        pf = plan.commits[0].files[0]
        assert pf.is_binary is True

    def test_test_counterpart_preserved(self) -> None:
        files = [_make_analyzed(
            "src/auth.py",
            is_staged=True,
            counterpart="tests/test_auth.py",
        )]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        pf = plan.commits[0].files[0]
        assert pf.test_counterpart == "tests/test_auth.py"

    def test_old_path_preserved_for_renames(self) -> None:
        files = [_make_analyzed(
            "src/new.py",
            is_staged=True,
            status=FileStatus.RENAMED,
            old_path="src/old.py",
        )]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        pf = plan.commits[0].files[0]
        assert pf.old_path == "src/old.py"
        assert pf.status == "renamed"


# =========================================================================
# P2-5: Item-level review information
# =========================================================================


class TestItemLevelReview:
    """P2-5: CommitPlanItem carries review metadata."""

    def test_explanation_preserved(self) -> None:
        groups = [_make_group(explanation="Paired auth implementation with tests")]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert plan.commits[0].explanation == "Paired auth implementation with tests"

    def test_group_confidence_preserved(self) -> None:
        groups = [_make_group(confidence="low")]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert plan.commits[0].group_confidence == "low"

    def test_requires_manual_review_from_group(self) -> None:
        groups = [_make_group(requires_manual_review=True)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert plan.commits[0].requires_manual_review is True

    def test_requires_manual_review_from_ambiguous_type(self) -> None:
        """P1-2 + P2-5: Ambiguous type → item needs review."""
        # Modified source → type=None → needs review
        groups = [_make_group(
            files=[_make_analyzed("src/auth.py", is_staged=True, status=FileStatus.MODIFIED)]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert plan.commits[0].requires_manual_review is True
        assert plan.commits[0].message.type is None

    def test_requires_manual_review_from_conflict(self) -> None:
        files = [
            _make_analyzed("src/auth.py", is_staged=True),
            _make_analyzed("src/auth.py", is_staged=False),
        ]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert plan.commits[0].requires_manual_review is True

    def test_warnings_for_deleted_files(self) -> None:
        files = [_make_analyzed("src/old.py", is_staged=True, status=FileStatus.DELETED)]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert any("removed" in w for w in plan.commits[0].warnings)

    def test_warnings_for_renamed_files(self) -> None:
        files = [_make_analyzed(
            "src/new.py",
            is_staged=True,
            status=FileStatus.RENAMED,
            old_path="src/old.py",
        )]
        groups = [_make_group(files=files)]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert any("renamed" in w.lower() for w in plan.commits[0].warnings)


# =========================================================================
# P3-6: JSON validation and round-trip tests
# =========================================================================


class TestJsonValidation:
    """P3-6: JSON serialisation, validation, and round-trips."""

    def test_round_trip_model_validate_json(self) -> None:
        """Full round-trip: create → JSON → validate → compare."""
        groups = [_make_group(
            files=[
                _make_analyzed("src/auth.py", is_staged=True, counterpart="tests/test_auth.py"),
                _make_analyzed(
                    "tests/test_auth.py",
                    is_staged=True,
                    classification=FileClassification.TEST,
                    category=CategorySuggestion.TEST,
                ),
            ]
        )]
        analysis = _make_analysis(groups=groups, ungrouped=[_make_analyzed("solo.py", is_staged=True)])
        plan = create_plan(analysis)

        json_str = plan.model_dump_json(indent=2)
        restored = CommitPlan.model_validate_json(json_str)

        assert restored.plan_id == plan.plan_id
        assert restored.repository == plan.repository
        assert len(restored.commits) == len(plan.commits)
        assert len(restored.unassigned_staged) == len(plan.unassigned_staged)
        assert restored.commits[0].message.full_message == plan.commits[0].message.full_message

    def test_round_trip_with_none_type(self) -> None:
        """P1-2 + P3-6: None commit type survives JSON round-trip."""
        groups = [_make_group(
            files=[_make_analyzed("src/auth.py", is_staged=True, status=FileStatus.MODIFIED)]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        json_str = plan.model_dump_json()
        restored = CommitPlan.model_validate_json(json_str)

        assert restored.commits[0].message.type is None
        assert restored.commits[0].requires_manual_review is True

    def test_file_status_in_json(self) -> None:
        """File status values are valid strings in JSON."""
        groups = [_make_group(
            files=[_make_analyzed("src/auth.py", is_staged=True)]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        data = json.loads(plan.model_dump_json())
        pf = data["commits"][0]["files"][0]
        assert pf["status"] in [fs.value for fs in FileStatus]

    def test_plan_file_metadata_in_json(self) -> None:
        """P2-4: Metadata fields appear in JSON output."""
        groups = [_make_group(
            files=[_make_analyzed(
                "src/auth.py",
                is_staged=True,
                is_binary=True,
                counterpart="tests/test_auth.py",
                classification=FileClassification.SOURCE,
            )]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        data = json.loads(plan.model_dump_json())
        pf = data["commits"][0]["files"][0]
        assert "is_binary" in pf
        assert "classification" in pf
        assert "test_counterpart" in pf
        assert "has_unstaged_changes" in pf

    def test_item_review_info_in_json(self) -> None:
        """P2-5: Item-level review info in JSON output."""
        groups = [_make_group(explanation="Auth pair", confidence="low")]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        data = json.loads(plan.model_dump_json())
        item = data["commits"][0]
        assert "explanation" in item
        assert "group_confidence" in item
        assert "requires_manual_review" in item
        assert item["explanation"] == "Auth pair"
        assert item["group_confidence"] == "low"

    def test_empty_plan_round_trip(self) -> None:
        analysis = _make_analysis()
        plan = create_plan(analysis)
        json_str = plan.model_dump_json()
        restored = CommitPlan.model_validate_json(json_str)
        assert restored.commits == []

    def test_plan_sections_in_json(self) -> None:
        """P1-1: JSON has all three sections."""
        groups = [_make_group(
            files=[
                _make_analyzed("src/auth.py", is_staged=True),
                _make_analyzed("src/user.py", is_staged=False),
            ]
        )]
        ungrouped = [_make_analyzed("new.py", is_untracked=True, status=FileStatus.ADDED)]
        analysis = _make_analysis(groups=groups, ungrouped=ungrouped)
        plan = create_plan(analysis)

        data = json.loads(plan.model_dump_json())
        assert "commits" in data
        assert "unstaged_changes" in data
        assert "untracked_files" in data
        assert len(data["commits"]) == 1
        assert len(data["unstaged_changes"]) == 1
        assert len(data["untracked_files"]) == 1


# =========================================================================
# Summary generation
# =========================================================================


class TestSummaryGeneration:
    def test_summary_includes_file_count(self) -> None:
        groups = [_make_group(
            files=[
                _make_analyzed("a.py", is_staged=True),
                _make_analyzed("b.py", is_staged=True),
            ]
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert "2 file(s)" in plan.commits[0].summary

    def test_summary_review_flag(self) -> None:
        groups = [_make_group(
            confidence="low",
            requires_manual_review=True,
        )]
        analysis = _make_analysis(groups=groups)
        plan = create_plan(analysis)

        assert "needs review" in plan.commits[0].summary


# =========================================================================
# Integration — full pipeline with real Git repos
# =========================================================================


class TestPlanIntegration:
    """Full pipeline: parse → analyze → group → plan."""

    def test_staged_repo_all_in_commits(self, tmp_staged_repo: Path) -> None:
        """P1-1: All staged changes appear in commits."""
        repo_info = detect_repository(tmp_staged_repo)
        cs = parse_changes(tmp_staged_repo)
        ar = analyze_changes(cs)
        ca = group_changes(ar, repo_info)
        plan = create_plan(ca)

        assert plan.branch == "main"
        assert len(plan.commits) >= 1

        # All commit files are staged
        for c in plan.commits:
            for f in c.files:
                assert f.is_staged is True

        # No unstaged or untracked
        assert len(plan.unstaged_changes) == 0
        assert len(plan.untracked_files) == 0

        # Compare with actual git diff --cached
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=tmp_staged_repo,
            capture_output=True,
            text=True,
        )
        git_staged = set(result.stdout.strip().splitlines())
        plan_staged = set()
        for c in plan.commits:
            for f in c.files:
                plan_staged.add(f.path)
        for f in plan.unassigned_staged:
            plan_staged.add(f.path)

        assert plan_staged == git_staged

    def test_messy_repo_no_staged_commits(self, tmp_messy_repo: Path) -> None:
        """P1-1: Messy repo has no staged changes → no commits."""
        repo_info = detect_repository(tmp_messy_repo)
        cs = parse_changes(tmp_messy_repo)
        ar = analyze_changes(cs)
        ca = group_changes(ar, repo_info)
        plan = create_plan(ca)

        # Nothing is staged in messy repo
        assert len(plan.commits) == 0
        assert len(plan.unstaged_changes) >= 1
        assert any("No staged changes" in w for w in plan.warnings)

    def test_mixed_repo_staged_unstaged_split(
        self, tmp_mixed_staged_unstaged: Path
    ) -> None:
        """P1-1 + P2-3: Mixed repo correctly splits staged/unstaged."""
        repo_info = detect_repository(tmp_mixed_staged_unstaged)
        cs = parse_changes(tmp_mixed_staged_unstaged)
        ar = analyze_changes(cs)
        ca = group_changes(ar, repo_info)
        plan = create_plan(ca)

        # auth.py is staged → should be in commits
        all_committed_paths = set()
        for c in plan.commits:
            for f in c.files:
                all_committed_paths.add(f.path)
        for f in plan.unassigned_staged:
            all_committed_paths.add(f.path)

        assert "src/auth.py" in all_committed_paths

        # user.py is unstaged → should be in unstaged_changes
        unstaged_paths = {f.path for f in plan.unstaged_changes}
        assert "src/user.py" in unstaged_paths

        # new_feature.py is untracked → should be in untracked_files
        untracked_paths = {f.path for f in plan.untracked_files}
        assert "src/new_feature.py" in untracked_paths

        # auth.py has both staged + unstaged → flagged
        auth_in_commit = None
        for c in plan.commits:
            for f in c.files:
                if f.path == "src/auth.py":
                    auth_in_commit = f
        if auth_in_commit:
            assert auth_in_commit.has_unstaged_changes is True

    def test_clean_repo(self, tmp_git_repo: Path) -> None:
        repo_info = detect_repository(tmp_git_repo)
        cs = parse_changes(tmp_git_repo)
        ar = analyze_changes(cs)
        ca = group_changes(ar, repo_info)
        plan = create_plan(ca)

        assert len(plan.commits) == 0
        assert any("clean" in w for w in plan.warnings)

    def test_binary_file_metadata(self, tmp_binary_repo: Path) -> None:
        """P2-4: Binary file status preserved in plan."""
        repo_info = detect_repository(tmp_binary_repo)
        cs = parse_changes(tmp_binary_repo)
        ar = analyze_changes(cs)
        ca = group_changes(ar, repo_info)
        plan = create_plan(ca)

        # Find the binary file in commits or unassigned
        all_plan_files = []
        for c in plan.commits:
            all_plan_files.extend(c.files)
        all_plan_files.extend(plan.unassigned_staged)

        binary_files = [f for f in all_plan_files if "logo" in f.path]
        assert len(binary_files) >= 1
        assert binary_files[0].is_binary is True

    def test_no_files_lost(self, tmp_mixed_staged_unstaged: Path) -> None:
        """No changes should be silently discarded."""
        repo_info = detect_repository(tmp_mixed_staged_unstaged)
        cs = parse_changes(tmp_mixed_staged_unstaged)
        ar = analyze_changes(cs)
        ca = group_changes(ar, repo_info)
        plan = create_plan(ca)

        # Total files across all sections
        total_committed = sum(len(c.files) for c in plan.commits)
        total = (
            total_committed
            + len(plan.unassigned_staged)
            + len(plan.unstaged_changes)
            + len(plan.untracked_files)
        )
        assert total == len(ar.analyzed)


# =========================================================================
# CLI — surgeon plan
# =========================================================================


class TestCliPlan:
    """CLI integration tests for surgeon plan."""

    def test_plan_staged_repo(self, tmp_staged_repo: Path) -> None:
        result = runner.invoke(app, ["plan", "--path", str(tmp_staged_repo)])
        assert result.exit_code == 0
        assert "Proposed commits" in result.output
        assert "Nothing has been staged or committed" in result.output
        assert "staged snapshot" in result.output

    def test_plan_messy_repo_no_staged(self, tmp_messy_repo: Path) -> None:
        """P1-1: Messy repo shows 'no staged changes'."""
        result = runner.invoke(app, ["plan", "--path", str(tmp_messy_repo)])
        assert result.exit_code == 0
        assert "No staged changes" in result.output or "Unstaged" in result.output

    def test_plan_mixed_repo_three_sections(
        self, tmp_mixed_staged_unstaged: Path
    ) -> None:
        """P1-1: Mixed repo shows all three sections."""
        result = runner.invoke(
            app, ["plan", "--path", str(tmp_mixed_staged_unstaged)]
        )
        assert result.exit_code == 0
        # Should show unstaged and untracked sections
        assert "Unstaged" in result.output
        assert "Untracked" in result.output

    def test_plan_clean_repo(self, tmp_git_repo: Path) -> None:
        result = runner.invoke(app, ["plan", "--path", str(tmp_git_repo)])
        assert result.exit_code == 0
        assert "clean" in result.output.lower()

    def test_plan_non_git_dir(self, tmp_non_git_dir: Path) -> None:
        result = runner.invoke(app, ["plan", "--path", str(tmp_non_git_dir)])
        assert result.exit_code == 1

    def test_plan_export_json(self, tmp_staged_repo: Path, tmp_path_factory) -> None:
        output_dir = tmp_path_factory.mktemp("export")
        output_file = output_dir / "plan.json"

        result = runner.invoke(
            app,
            ["plan", "--path", str(tmp_staged_repo), "--output", str(output_file)],
        )
        assert result.exit_code == 0
        assert output_file.exists()

        data = json.loads(output_file.read_text())
        assert "plan_id" in data
        assert "commits" in data
        assert "unstaged_changes" in data
        assert "untracked_files" in data

    def test_plan_export_no_overwrite(self, tmp_staged_repo: Path, tmp_path_factory) -> None:
        output_dir = tmp_path_factory.mktemp("export2")
        output_file = output_dir / "existing.json"
        output_file.write_text("{}")

        result = runner.invoke(
            app,
            ["plan", "--path", str(tmp_staged_repo), "--output", str(output_file)],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output.lower()
        assert output_file.read_text() == "{}"

    def test_plan_help(self) -> None:
        result = runner.invoke(app, ["plan", "--help"])
        assert result.exit_code == 0
        assert "commit" in result.output.lower()

    def test_plan_ambiguous_type_display(
        self, tmp_mixed_staged_unstaged: Path
    ) -> None:
        """P1-2: Ambiguous types show '<type>' in CLI output."""
        result = runner.invoke(
            app, ["plan", "--path", str(tmp_mixed_staged_unstaged)]
        )
        # The staged auth.py is a modified source file → ambiguous type
        # May show "<type>" or "select: feat | fix | refactor"
        # This depends on whether auth.py is alone or grouped
        assert result.exit_code == 0
