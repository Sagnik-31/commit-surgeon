"""Commit planner — transforms change groups into a structured commit plan.

Consumes the :class:`~commit_surgeon.core.change_grouper.ChangeAnalysis`
produced by Phase 2 and emits a :class:`CommitPlan` that is:

* **Staged-diff-first** — only changes in the Git index appear as
  proposed commit contents.  Unstaged modifications and untracked files
  are reported in separate sections.
* **Serialisable** to JSON via ``plan.model_dump_json(indent=2)``.
* **Reviewable** — every plan item carries the group explanation,
  confidence, warnings, and a ``requires_manual_review`` flag.
* **Safe** — files with both staged and unstaged modifications are
  flagged, nothing is ever staged or committed.

No AI or cloud dependencies.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from commit_surgeon.core.change_analyzer import AnalyzedChange, Confidence
from commit_surgeon.core.change_grouper import ChangeAnalysis, ChangeGroup
from commit_surgeon.core.commit_message import CommitMessage, generate_message
from commit_surgeon.git.status import FileStatus


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class PlanFile(BaseModel):
    """File representation for JSON export — preserves analysis metadata."""

    path: str
    old_path: str | None = None
    status: str
    additions: int = 0
    deletions: int = 0
    is_staged: bool = False
    is_untracked: bool = False
    is_binary: bool = False
    has_unstaged_changes: bool = False
    classification: str | None = None
    test_counterpart: str | None = None


class CommitPlanItem(BaseModel):
    """A single proposed commit within the plan."""

    commit_id: str
    group_id: str
    message: CommitMessage
    files: list[PlanFile]
    summary: str
    explanation: str
    group_confidence: str
    requires_manual_review: bool = False
    warnings: list[str] = []
    approved: bool = False


class CommitPlan(BaseModel):
    """Top-level commit plan — JSON-serialisable.

    ``commits`` contains **only staged changes** (the Git index snapshot).
    Unstaged and untracked files are reported separately.
    """

    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    repository: str
    branch: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    commits: list[CommitPlanItem] = []
    unassigned_staged: list[PlanFile] = []
    unstaged_changes: list[PlanFile] = []
    untracked_files: list[PlanFile] = []
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_plan(analysis: ChangeAnalysis) -> CommitPlan:
    """Create a commit plan from a :class:`ChangeAnalysis`.

    **Staged-diff-first**: only files present in the Git index appear as
    proposed commit contents.  Unstaged modifications and untracked files
    are reported in separate sections.

    Safety checks:

    1. Detect files with **both** staged and unstaged modifications.
    2. Only staged changes go into proposed commits.
    3. Unstaged modifications reported separately.
    4. Untracked files reported separately.
    5. Ungrouped staged files listed as unassigned.
    6. Deleted and renamed files noted with warnings.
    7. Empty / no-staged plans handled gracefully.
    """
    plan_warnings: list[str] = list(analysis.warnings)

    # -- Detect staged/unstaged conflicts -----------------------------------
    conflicts = _detect_staged_unstaged_conflicts(analysis)
    if conflicts:
        plan_warnings.append(
            f"{len(conflicts)} file(s) have both staged and unstaged "
            f"modifications: {', '.join(sorted(conflicts))}. "
            "Only the staged snapshot will be committed."
        )

    # -- Process groups: split staged / unstaged / untracked ----------------
    commits: list[CommitPlanItem] = []
    all_unstaged: list[AnalyzedChange] = []
    all_untracked: list[AnalyzedChange] = []

    for group in analysis.groups:
        staged = [ac for ac in group.files if ac.diff.is_staged]
        unstaged = [
            ac for ac in group.files
            if not ac.diff.is_staged and not ac.diff.is_untracked
        ]
        untracked = [ac for ac in group.files if ac.diff.is_untracked]

        all_unstaged.extend(unstaged)
        all_untracked.extend(untracked)

        if staged:
            item = _group_to_plan_item(
                group, len(commits) + 1, staged, conflicts
            )
            commits.append(item)

    # -- Process ungrouped files --------------------------------------------
    unassigned_staged: list[PlanFile] = []
    for ac in analysis.ungrouped_files:
        if ac.diff.is_staged:
            unassigned_staged.append(_to_plan_file(ac, conflicts))
        elif ac.diff.is_untracked:
            all_untracked.append(ac)
        else:
            all_unstaged.append(ac)

    unstaged_plan = [_to_plan_file(ac, conflicts) for ac in all_unstaged]
    untracked_plan = [_to_plan_file(ac, conflicts) for ac in all_untracked]

    # -- Warnings -----------------------------------------------------------
    if not commits and not unassigned_staged:
        if unstaged_plan or untracked_plan:
            plan_warnings.append(
                "No staged changes found. Stage changes with "
                "'git add' before planning commits."
            )
        else:
            plan_warnings.append("Working tree is clean — nothing to plan.")

    if unassigned_staged:
        plan_warnings.append(
            f"{len(unassigned_staged)} staged file(s) are not assigned "
            "to any commit group."
        )

    if untracked_plan:
        plan_warnings.append(
            f"{len(untracked_plan)} untracked file(s) must be staged "
            "with 'git add' before committing."
        )

    return CommitPlan(
        repository=analysis.repository.root.name,
        branch=analysis.repository.branch or "unknown",
        commits=commits,
        unassigned_staged=unassigned_staged,
        unstaged_changes=unstaged_plan,
        untracked_files=untracked_plan,
        warnings=plan_warnings,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _group_to_plan_item(
    group: ChangeGroup,
    index: int,
    staged_files: list[AnalyzedChange],
    conflicts: set[str],
) -> CommitPlanItem:
    """Convert a :class:`ChangeGroup` into a :class:`CommitPlanItem`.

    Only *staged_files* are included in the commit.
    """
    message = generate_message(group)
    files = _deduplicate_plan_files(
        [_to_plan_file(ac, conflicts) for ac in staged_files]
    )
    warnings: list[str] = []

    # Staged/unstaged conflict.
    conflict_paths = [f.path for f in files if f.has_unstaged_changes]
    if conflict_paths:
        warnings.append(
            f"File(s) have both staged and unstaged changes: "
            f"{', '.join(conflict_paths)}. "
            "Only the staged snapshot will be committed."
        )

    # Deleted files.
    deleted = [f for f in files if f.status == FileStatus.DELETED.value]
    if deleted:
        warnings.append(
            f"File(s) will be removed: {', '.join(f.path for f in deleted)}"
        )

    # Renamed files.
    renamed = [f for f in files if f.status == FileStatus.RENAMED.value]
    if renamed:
        renames = [f"{f.old_path} -> {f.path}" for f in renamed if f.old_path]
        if renames:
            warnings.append(f"File(s) renamed: {', '.join(renames)}")

    # Determine review status.
    needs_review = (
        message.requires_manual_review
        or message.type is None
        or bool(conflict_paths)
        or group.requires_manual_review
    )

    summary = _generate_summary(files, message, needs_review)

    return CommitPlanItem(
        commit_id=f"commit-{index}",
        group_id=group.group_id,
        message=message,
        files=files,
        summary=summary,
        explanation=group.explanation,
        group_confidence=group.confidence,
        requires_manual_review=needs_review,
        warnings=warnings,
    )


def _to_plan_file(ac: AnalyzedChange, conflicts: set[str]) -> PlanFile:
    """Convert an :class:`AnalyzedChange` to a :class:`PlanFile`.

    Preserves classification, binary status, and test counterpart.
    """
    return PlanFile(
        path=ac.diff.path,
        old_path=ac.diff.old_path,
        status=ac.diff.status.value,
        additions=ac.diff.additions,
        deletions=ac.diff.deletions,
        is_staged=ac.diff.is_staged,
        is_untracked=ac.diff.is_untracked,
        is_binary=ac.diff.is_binary,
        has_unstaged_changes=ac.diff.path in conflicts,
        classification=ac.classification.value if ac.classification else None,
        test_counterpart=ac.test_counterpart,
    )


def _deduplicate_plan_files(files: list[PlanFile]) -> list[PlanFile]:
    """Ensure unique paths within a commit item (P2-3).

    If the same path appears twice (shouldn't happen for staged-only),
    keep the first occurrence.
    """
    seen: set[str] = set()
    result: list[PlanFile] = []
    for f in files:
        if f.path not in seen:
            seen.add(f.path)
            result.append(f)
    return result


def _detect_staged_unstaged_conflicts(analysis: ChangeAnalysis) -> set[str]:
    """Find files that appear in **both** staged and unstaged changes."""
    all_changes: list[AnalyzedChange] = []
    for group in analysis.groups:
        all_changes.extend(group.files)
    all_changes.extend(analysis.ungrouped_files)

    staged_paths: set[str] = set()
    unstaged_paths: set[str] = set()

    for ac in all_changes:
        if ac.diff.is_staged:
            staged_paths.add(ac.diff.path)
        elif not ac.diff.is_untracked:
            unstaged_paths.add(ac.diff.path)

    return staged_paths & unstaged_paths


def _generate_summary(
    files: list[PlanFile],
    message: CommitMessage,
    needs_review: bool,
) -> str:
    """Produce a human-readable one-liner for a plan item."""
    status_counts = Counter(f.status for f in files)
    parts = [f"{count} {status}" for status, count in sorted(status_counts.items())]
    file_summary = ", ".join(parts)

    review = " — needs review" if needs_review else ""
    return f"{len(files)} file(s) ({file_summary}){review}"
