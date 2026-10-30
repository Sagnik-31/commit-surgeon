"""Tests for the commit message generator — Phase 3.1 hardened.

Covers P1-2 (ambiguous source classification), type resolution,
scope inference, description generation, and confidence assessment.
"""

from __future__ import annotations

import pytest

from commit_surgeon.core.change_analyzer import (
    AnalyzedChange,
    CategoryHint,
    CategorySuggestion,
    Confidence,
    FileClassification,
)
from commit_surgeon.core.change_grouper import ChangeGroup
from commit_surgeon.core.commit_message import (
    CommitMessage,
    CommitType,
    generate_message,
    _clean_scope,
    _resolve_type,
    _infer_scope,
    _assess_confidence,
)
from commit_surgeon.core.diff_parser import FileDiff
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
    old_path: str | None = None,
    category: CategorySuggestion = CategorySuggestion.FEAT,
) -> AnalyzedChange:
    return AnalyzedChange(
        diff=FileDiff(
            path=path,
            status=status,
            is_staged=is_staged,
            is_untracked=is_untracked,
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
    explanation: str = "Test group",
    confidence: str = "high",
    requires_manual_review: bool = False,
) -> ChangeGroup:
    if files is None:
        files = [_make_analyzed("src/auth.py")]
    return ChangeGroup(
        group_id=group_id,
        title=title,
        category=category,
        files=files,
        explanation=explanation,
        confidence=confidence,
        requires_manual_review=requires_manual_review,
    )


# =========================================================================
# CommitMessage model
# =========================================================================


class TestCommitMessage:
    """Unit tests for the CommitMessage Pydantic model."""

    def test_full_message_with_scope_and_type(self) -> None:
        msg = CommitMessage(
            type=CommitType.FEAT,
            scope="auth",
            description="add login handler",
            confidence=Confidence.HIGH,
        )
        assert msg.full_message == "feat(auth): add login handler"

    def test_full_message_without_scope(self) -> None:
        msg = CommitMessage(
            type=CommitType.DOCS,
            scope=None,
            description="update installation guide",
            confidence=Confidence.HIGH,
        )
        assert msg.full_message == "docs: update installation guide"

    def test_full_message_with_none_type(self) -> None:
        """P1-2: None type uses <type> placeholder."""
        msg = CommitMessage(
            type=None,
            scope="auth",
            description="update auth",
            confidence=Confidence.LOW,
        )
        assert msg.full_message == "<type>(auth): update auth"

    def test_full_message_none_type_no_scope(self) -> None:
        msg = CommitMessage(
            type=None,
            description="update code",
            confidence=Confidence.LOW,
        )
        assert msg.full_message == "<type>: update code"

    def test_all_commit_types(self) -> None:
        """Every CommitType produces a valid message."""
        for ct in CommitType:
            msg = CommitMessage(
                type=ct,
                description="test description",
                confidence=Confidence.MEDIUM,
            )
            assert msg.full_message.startswith(ct.value + ": ")

    def test_json_serialisation_with_type(self) -> None:
        msg = CommitMessage(
            type=CommitType.FIX,
            scope="api",
            description="handle null response",
            confidence=Confidence.HIGH,
        )
        data = msg.model_dump()
        assert data["full_message"] == "fix(api): handle null response"
        assert data["type"] == "fix"

    def test_json_serialisation_with_none_type(self) -> None:
        """P1-2: None type serialises to null."""
        msg = CommitMessage(
            type=None,
            description="update code",
            confidence=Confidence.LOW,
            requires_manual_review=True,
        )
        data = msg.model_dump()
        assert data["type"] is None
        assert data["requires_manual_review"] is True


# =========================================================================
# P1-2: Ambiguous source type resolution
# =========================================================================


class TestResolveType:
    """P1-2: Ambiguous source changes must NOT be auto-classified as feat."""

    def test_docs_category_definitive(self) -> None:
        grp = _make_group(category="docs")
        assert _resolve_type(grp) == CommitType.DOCS

    def test_test_category_definitive(self) -> None:
        grp = _make_group(category="test")
        assert _resolve_type(grp) == CommitType.TEST

    def test_chore_category_definitive(self) -> None:
        grp = _make_group(category="chore")
        assert _resolve_type(grp) == CommitType.CHORE

    def test_build_category_definitive(self) -> None:
        grp = _make_group(category="build")
        assert _resolve_type(grp) == CommitType.BUILD

    def test_ci_category_definitive(self) -> None:
        grp = _make_group(category="ci")
        assert _resolve_type(grp) == CommitType.CI

    def test_unknown_category_defaults_to_chore(self) -> None:
        grp = _make_group(category="yolo")
        # "yolo" is not in _DEFINITIVE_TYPES, and files are MODIFIED source
        # → None (ambiguous)
        assert _resolve_type(grp) is None

    def test_modified_source_is_ambiguous(self) -> None:
        """P1-2 CORE: Modified src/auth.py must NOT become feat."""
        grp = _make_group(
            category="feat",
            files=[_make_analyzed("src/auth.py", status=FileStatus.MODIFIED)],
        )
        assert _resolve_type(grp) is None

    def test_added_source_is_feat(self) -> None:
        """Newly added source files → feat (evidence-based)."""
        grp = _make_group(
            category="feat",
            files=[_make_analyzed("src/auth.py", status=FileStatus.ADDED)],
        )
        assert _resolve_type(grp) == CommitType.FEAT

    def test_deleted_source_is_refactor(self) -> None:
        grp = _make_group(
            files=[_make_analyzed("src/old.py", status=FileStatus.DELETED)],
        )
        assert _resolve_type(grp) == CommitType.REFACTOR

    def test_renamed_source_is_refactor(self) -> None:
        grp = _make_group(
            files=[_make_analyzed("src/new.py", status=FileStatus.RENAMED, old_path="src/old.py")],
        )
        assert _resolve_type(grp) == CommitType.REFACTOR

    def test_mixed_modified_added_is_ambiguous(self) -> None:
        grp = _make_group(
            files=[
                _make_analyzed("src/auth.py", status=FileStatus.MODIFIED),
                _make_analyzed("src/new.py", status=FileStatus.ADDED),
            ],
        )
        assert _resolve_type(grp) is None

    def test_all_test_files_returns_test(self) -> None:
        grp = _make_group(
            files=[
                _make_analyzed("tests/test_auth.py", classification=FileClassification.TEST),
                _make_analyzed("tests/test_user.py", classification=FileClassification.TEST),
            ],
        )
        assert _resolve_type(grp) == CommitType.TEST

    def test_pair_with_modified_impl_is_ambiguous(self) -> None:
        """P1-2: test-impl pair with MODIFIED impl → None, not feat."""
        grp = _make_group(
            group_id="pair-1",
            title="Auth",
            category="feat",
            files=[
                _make_analyzed("src/auth.py", status=FileStatus.MODIFIED),
                _make_analyzed(
                    "tests/test_auth.py",
                    classification=FileClassification.TEST,
                    status=FileStatus.MODIFIED,
                ),
            ],
        )
        assert _resolve_type(grp) is None

    def test_pair_with_added_impl_is_feat(self) -> None:
        grp = _make_group(
            group_id="pair-1",
            files=[
                _make_analyzed("src/auth.py", status=FileStatus.ADDED),
                _make_analyzed(
                    "tests/test_auth.py",
                    classification=FileClassification.TEST,
                    status=FileStatus.ADDED,
                ),
            ],
        )
        assert _resolve_type(grp) == CommitType.FEAT


# =========================================================================
# Scope inference
# =========================================================================


class TestInferScope:
    """Tests for scope inference logic."""

    def test_pair_group_uses_title(self) -> None:
        grp = _make_group(group_id="pair-1", title="Auth Handler")
        assert _infer_scope(grp) == "auth-handler"

    def test_documentation_single_file(self) -> None:
        grp = _make_group(
            title="Documentation",
            files=[_make_analyzed("README.md", classification=FileClassification.DOCUMENTATION)],
        )
        assert _infer_scope(grp) == "readme"

    def test_documentation_multiple_files_no_scope(self) -> None:
        grp = _make_group(
            title="Documentation",
            files=[
                _make_analyzed("README.md", classification=FileClassification.DOCUMENTATION),
                _make_analyzed("docs/guide.md", classification=FileClassification.DOCUMENTATION),
            ],
        )
        assert _infer_scope(grp) is None

    def test_config_build_multiple_no_scope(self) -> None:
        grp = _make_group(
            title="Configuration & Build",
            files=[
                _make_analyzed("pyproject.toml", classification=FileClassification.CONFIGURATION),
                _make_analyzed(".gitignore", classification=FileClassification.CONFIGURATION),
            ],
        )
        assert _infer_scope(grp) is None


class TestCleanScope:
    """Tests for _clean_scope."""

    def test_lowercase_hyphens(self) -> None:
        assert _clean_scope("Auth Handler") == "auth-handler"

    def test_strips_special_chars(self) -> None:
        assert _clean_scope("user@model!") == "usermodel"

    def test_truncates_long_scopes(self) -> None:
        result = _clean_scope("very-long-scope-name-that-exceeds-limit")
        assert result is not None
        assert len(result) <= 20

    def test_empty_after_cleaning_returns_none(self) -> None:
        assert _clean_scope("!!!") is None

    def test_underscores_to_hyphens(self) -> None:
        assert _clean_scope("user_model") == "user-model"


# =========================================================================
# Description generation
# =========================================================================


class TestDescriptionGeneration:
    """Tests for description generation via generate_message."""

    def test_single_added_file(self) -> None:
        grp = _make_group(files=[_make_analyzed("src/auth.py", status=FileStatus.ADDED)])
        msg = generate_message(grp)
        assert "add" in msg.description
        assert "auth" in msg.description

    def test_single_modified_file(self) -> None:
        grp = _make_group(files=[_make_analyzed("src/auth.py")])
        msg = generate_message(grp)
        assert "update" in msg.description

    def test_single_deleted_file(self) -> None:
        grp = _make_group(files=[_make_analyzed("src/old.py", status=FileStatus.DELETED)])
        msg = generate_message(grp)
        assert "remove" in msg.description

    def test_single_renamed_file(self) -> None:
        grp = _make_group(
            files=[_make_analyzed(
                "src/new.py",
                status=FileStatus.RENAMED,
                old_path="src/old.py",
            )]
        )
        msg = generate_message(grp)
        assert "rename" in msg.description

    def test_pair_modified(self) -> None:
        grp = _make_group(
            group_id="pair-1",
            title="Auth",
            files=[
                _make_analyzed("src/auth.py"),
                _make_analyzed(
                    "tests/test_auth.py",
                    classification=FileClassification.TEST,
                    category=CategorySuggestion.TEST,
                ),
            ],
        )
        msg = generate_message(grp)
        assert "update" in msg.description
        assert "auth" in msg.description

    def test_docs_multiple_files(self) -> None:
        grp = _make_group(
            group_id="class-2",
            title="Documentation",
            category="docs",
            files=[
                _make_analyzed("README.md", classification=FileClassification.DOCUMENTATION),
                _make_analyzed("docs/guide.md", classification=FileClassification.DOCUMENTATION),
            ],
        )
        msg = generate_message(grp)
        assert msg.type == CommitType.DOCS

    def test_config_single_file(self) -> None:
        grp = _make_group(
            group_id="class-3",
            title="Configuration & Build",
            category="chore",
            files=[_make_analyzed("pyproject.toml", classification=FileClassification.CONFIGURATION)],
        )
        msg = generate_message(grp)
        assert "pyproject" in msg.description

    def test_empty_group_defensive(self) -> None:
        grp = _make_group(files=[])
        msg = generate_message(grp)
        assert msg.type is None
        assert msg.requires_manual_review is True
        assert msg.confidence == Confidence.LOW

    def test_description_uses_imperative_mood(self) -> None:
        grp = _make_group(files=[_make_analyzed("src/handler.py")])
        msg = generate_message(grp)
        first_word = msg.description.split()[0]
        assert first_word in ("add", "update", "remove", "rename")


# =========================================================================
# Confidence assessment
# =========================================================================


class TestConfidenceAssessment:
    """Tests for _assess_confidence."""

    def test_none_type_always_low(self) -> None:
        """P1-2: Ambiguous type → LOW confidence."""
        grp = _make_group(confidence="high")
        assert _assess_confidence(grp, None) == Confidence.LOW

    def test_docs_high_group_high(self) -> None:
        grp = _make_group(category="docs", confidence="high")
        assert _assess_confidence(grp, CommitType.DOCS) == Confidence.HIGH

    def test_docs_medium_group_medium(self) -> None:
        grp = _make_group(category="docs", confidence="medium")
        assert _assess_confidence(grp, CommitType.DOCS) == Confidence.MEDIUM

    def test_chore_always_medium(self) -> None:
        grp = _make_group(category="chore", confidence="high")
        assert _assess_confidence(grp, CommitType.CHORE) == Confidence.MEDIUM

    def test_build_always_medium(self) -> None:
        grp = _make_group(category="build", confidence="high")
        assert _assess_confidence(grp, CommitType.BUILD) == Confidence.MEDIUM

    def test_feat_evidence_based_medium(self) -> None:
        """Evidence-based feat (all added) → MEDIUM."""
        grp = _make_group(confidence="high")
        assert _assess_confidence(grp, CommitType.FEAT) == Confidence.MEDIUM


# =========================================================================
# P1-2: Full message format with ambiguous types
# =========================================================================


class TestConventionalCommitFormat:
    """Verify messages follow Conventional Commit format."""

    def test_definitive_docs_format(self) -> None:
        grp = _make_group(
            group_id="class-2",
            title="Documentation",
            category="docs",
            files=[
                _make_analyzed("README.md", classification=FileClassification.DOCUMENTATION),
                _make_analyzed("docs/guide.md", classification=FileClassification.DOCUMENTATION),
            ],
        )
        msg = generate_message(grp)
        assert msg.full_message.startswith("docs: ")
        assert msg.type == CommitType.DOCS

    def test_ambiguous_source_format(self) -> None:
        """P1-2: Modified source → type=None, <type> placeholder."""
        grp = _make_group(
            group_id="pair-1",
            title="Auth",
            category="feat",
            files=[_make_analyzed("src/auth.py", status=FileStatus.MODIFIED)],
        )
        msg = generate_message(grp)
        assert msg.type is None
        assert msg.full_message.startswith("<type>(auth):")
        assert msg.requires_manual_review is True
        assert msg.confidence == Confidence.LOW

    def test_added_source_gets_type(self) -> None:
        """Newly added source → feat (evidence-based)."""
        grp = _make_group(
            group_id="pair-1",
            title="Auth",
            files=[_make_analyzed("src/auth.py", status=FileStatus.ADDED)],
        )
        msg = generate_message(grp)
        assert msg.type == CommitType.FEAT
        assert msg.full_message.startswith("feat(auth):")

    def test_review_flag_for_ambiguous_type(self) -> None:
        """P1-2: Ambiguous type always needs review."""
        grp = _make_group(
            group_id="naming-1",
            title="User",
            category="feat",
            confidence="high",
            files=[_make_analyzed("src/user.py", status=FileStatus.MODIFIED)],
        )
        msg = generate_message(grp)
        assert msg.type is None
        assert msg.requires_manual_review is True

    def test_review_flag_for_low_confidence_group(self) -> None:
        grp = _make_group(
            group_id="naming-1",
            title="User",
            category="feat",
            confidence="low",
            requires_manual_review=True,
            files=[_make_analyzed("src/user.py", status=FileStatus.ADDED)],
        )
        msg = generate_message(grp)
        assert msg.requires_manual_review is True
