"""Conventional Commit message generation.

Generates structured commit messages from :class:`ChangeGroup` analysis.
All generation is **deterministic and rule-based** — no AI is used.

Message format::

    type(scope): description

When ``type`` is ``None`` (ambiguous), the placeholder ``<type>`` is used
and the message is flagged for manual review so the user can select
``feat``, ``fix``, or ``refactor``.

Supported types: feat, fix, refactor, docs, test, chore, style, perf, build, ci.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath

from pydantic import BaseModel, computed_field

from commit_surgeon.core.change_analyzer import (
    AnalyzedChange,
    Confidence,
    FileClassification,
)
from commit_surgeon.core.change_grouper import ChangeGroup
from commit_surgeon.git.status import FileStatus


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CommitType(str, Enum):
    """All Conventional Commit types supported by Commit Surgeon."""

    FEAT = "feat"
    FIX = "fix"
    REFACTOR = "refactor"
    DOCS = "docs"
    TEST = "test"
    CHORE = "chore"
    STYLE = "style"
    PERF = "perf"
    BUILD = "build"
    CI = "ci"


class CommitMessage(BaseModel):
    """A structured Conventional Commit message.

    When ``type`` is ``None``, the change is **ambiguous** — the user
    must select a type (feat / fix / refactor) during review.
    """

    type: CommitType | None = None
    scope: str | None = None
    description: str
    confidence: Confidence
    requires_manual_review: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_message(self) -> str:
        """The formatted ``type(scope): description`` string.

        Uses ``<type>`` as placeholder when the type is unresolved.
        """
        type_str = self.type.value if self.type is not None else "<type>"
        if self.scope:
            return f"{type_str}({self.scope}): {self.description}"
        return f"{type_str}: {self.description}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SCOPE_RE = re.compile(r"[^a-z0-9-]")

# Types whose classification is unambiguous without content analysis.
_DEFINITIVE_TYPES: frozenset[str] = frozenset({"docs", "test", "chore", "build", "ci"})


def generate_message(group: ChangeGroup) -> CommitMessage:
    """Generate a conventional commit message for *group*.

    When the available metadata is insufficient to determine intent the
    message is **conservative** and flagged for manual review.
    """
    if not group.files:
        return CommitMessage(
            type=None,
            description="update code",
            confidence=Confidence.LOW,
            requires_manual_review=True,
        )

    commit_type = _resolve_type(group)
    scope = _infer_scope(group)
    description = _generate_description(group, commit_type)
    confidence = _assess_confidence(group, commit_type)

    needs_review = (
        commit_type is None  # ambiguous type always needs review
        or confidence != Confidence.HIGH
        or group.requires_manual_review
        or group.confidence == "low"
    )

    return CommitMessage(
        type=commit_type,
        scope=scope,
        description=description,
        confidence=confidence,
        requires_manual_review=needs_review,
    )


# ---------------------------------------------------------------------------
# Type resolution
# ---------------------------------------------------------------------------


def _resolve_type(group: ChangeGroup) -> CommitType | None:
    """Map the group's category to a :class:`CommitType`.

    Returns ``None`` for ambiguous source-code modifications where we
    cannot distinguish feat / fix / refactor without content analysis.

    **Definitive** classifications (always resolved):
    docs, test, chore, build, ci.

    **Evidence-based** source classifications:
    - All non-test files ADDED → ``feat``
    - All non-test files DELETED or RENAMED → ``refactor``
    - Otherwise → ``None`` (ambiguous)
    """
    category = group.category.lower()

    # Definitive categories — classification is unambiguous.
    if category in _DEFINITIVE_TYPES:
        try:
            return CommitType(category)
        except ValueError:
            return CommitType.CHORE

    # Source-code categories need evidence.
    non_test = [
        f for f in group.files if f.classification != FileClassification.TEST
    ]

    if not non_test:
        # All files are tests.
        return CommitType.TEST

    # All source files newly added → likely a new feature.
    if all(f.diff.status == FileStatus.ADDED for f in non_test):
        return CommitType.FEAT

    # All source files deleted → removal / refactor.
    if all(f.diff.status == FileStatus.DELETED for f in non_test):
        return CommitType.REFACTOR

    # All source files renamed → refactor.
    if all(f.diff.status == FileStatus.RENAMED for f in non_test):
        return CommitType.REFACTOR

    # Modified source files → ambiguous.
    return None


# ---------------------------------------------------------------------------
# Scope inference
# ---------------------------------------------------------------------------


def _infer_scope(group: ChangeGroup) -> str | None:
    """Infer a scope from the group context.

    Returns ``None`` when a scope would be redundant with the commit
    type (e.g. ``docs`` type does not need a ``documentation`` scope).
    """
    title = group.title

    # Generic titles — scope is redundant with the type.
    if title in ("Documentation", "Configuration & Build"):
        if len(group.files) == 1:
            stem = PurePosixPath(group.files[0].diff.path).stem.lower()
            return _clean_scope(stem)
        return None

    return _clean_scope(title)


def _clean_scope(raw: str) -> str | None:
    """Sanitise *raw* into a valid scope (lowercase, alphanumeric + hyphens)."""
    scope = raw.lower().replace(" ", "-").replace("_", "-")
    scope = _SCOPE_RE.sub("", scope)
    scope = scope.strip("-")

    if len(scope) > 20:
        scope = scope[:20].rstrip("-")

    return scope if scope else None


# ---------------------------------------------------------------------------
# Description generation
# ---------------------------------------------------------------------------


def _generate_description(
    group: ChangeGroup, commit_type: CommitType | None
) -> str:
    """Generate a concise, imperative description."""
    files = group.files

    if len(files) == 1:
        return _single_file_description(files[0])

    # Test–implementation pair.
    if "pair" in group.group_id:
        return _pair_description(files, group.title)

    # Documentation.
    if commit_type == CommitType.DOCS:
        return _docs_description(files)

    # Config / build / CI.
    if commit_type in (CommitType.CHORE, CommitType.BUILD, CommitType.CI):
        return _config_description(files)

    # Source-file group (including None / ambiguous type).
    return _source_group_description(files, group.title)


def _single_file_description(ac: AnalyzedChange) -> str:
    f = ac.diff
    name = PurePosixPath(f.path).stem.replace("_", " ")

    if f.status == FileStatus.ADDED:
        return f"add {name}"
    if f.status == FileStatus.DELETED:
        return f"remove {name}"
    if f.status == FileStatus.RENAMED:
        old_name = (
            PurePosixPath(f.old_path).stem.replace("_", " ")
            if f.old_path
            else "file"
        )
        return f"rename {old_name} to {name}"
    return f"update {name}"


def _pair_description(files: list[AnalyzedChange], title: str) -> str:
    concept = title.lower()

    impl_files = [f for f in files if f.classification != FileClassification.TEST]
    test_files = [f for f in files if f.classification == FileClassification.TEST]
    impl_statuses = {f.diff.status for f in impl_files} if impl_files else set()

    if impl_statuses == {FileStatus.ADDED}:
        return f"add {concept} with tests" if test_files else f"add {concept}"
    if impl_statuses == {FileStatus.DELETED}:
        return f"remove {concept}"
    if FileStatus.RENAMED in impl_statuses:
        return f"rename {concept} and update tests"

    # Default: modified.
    return f"update {concept} and tests" if test_files else f"update {concept}"


def _docs_description(files: list[AnalyzedChange]) -> str:
    if len(files) == 1:
        return f"update {PurePosixPath(files[0].diff.path).stem.lower()}"

    names = [PurePosixPath(f.diff.path).stem.lower() for f in files]
    if len(names) <= 3:
        return "update " + ", ".join(names)
    return "update project documentation"


def _config_description(files: list[AnalyzedChange]) -> str:
    if len(files) == 1:
        return f"update {PurePosixPath(files[0].diff.path).name.lower()}"
    return "update project configuration"


def _source_group_description(files: list[AnalyzedChange], title: str) -> str:
    concept = title.lower()
    statuses = {f.diff.status for f in files}

    if statuses == {FileStatus.ADDED}:
        return f"add {concept} modules"
    if statuses == {FileStatus.DELETED}:
        return f"remove {concept} modules"
    return f"update {concept} modules"


# ---------------------------------------------------------------------------
# Confidence assessment
# ---------------------------------------------------------------------------


def _assess_confidence(
    group: ChangeGroup, commit_type: CommitType | None
) -> Confidence:
    """Rate how confident the generated message is.

    * ``None`` type → always LOW (ambiguous).
    * Definitive types (docs, test, chore, build, ci) → HIGH or MEDIUM.
    * Evidence-based source types → MEDIUM.
    """
    if commit_type is None:
        return Confidence.LOW

    # Documentation and test types are unambiguous.
    if commit_type in (CommitType.DOCS, CommitType.TEST):
        return Confidence.HIGH if group.confidence == "high" else Confidence.MEDIUM

    # Build / CI — fairly reliable.
    if commit_type in (CommitType.BUILD, CommitType.CI):
        return Confidence.MEDIUM

    # Chore (config).
    if commit_type == CommitType.CHORE:
        return Confidence.MEDIUM

    # Evidence-based source types (e.g. all-added → FEAT).
    if group.confidence == "high":
        return Confidence.MEDIUM
    return Confidence.LOW
