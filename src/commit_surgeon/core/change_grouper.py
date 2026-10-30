"""Change grouper — deterministic, rule-based grouping of related changes.

Given an :class:`~commit_surgeon.core.change_analyzer.AnalysisResult`,
this module produces a :class:`ChangeAnalysis` containing:

* **Groups** of related files with human-readable explanations.
* **Ungrouped files** that don't clearly belong to any group.
* **Warnings** about ambiguous or unusual situations.

Grouping passes (executed in order):

1. **Test–implementation pairs** — high confidence.
2. **Documentation cluster** — high confidence.
3. **Configuration / build cluster** — medium confidence.
4. **Naming-convention clusters** — medium confidence, flagged for review.
5. **Everything else** → ungrouped.

No file is ever discarded, modified, or staged.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from pydantic import BaseModel

from commit_surgeon.core.change_analyzer import (
    AnalysisResult,
    AnalyzedChange,
    CategorySuggestion,
    Confidence,
    FileClassification,
)
from commit_surgeon.core.repository import RepositoryInfo


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ChangeGroup(BaseModel):
    """A logical group of related file changes."""

    group_id: str
    """Unique identifier for this group."""

    title: str
    """Human-readable title (e.g. ``"Authentication"``)."""

    category: str
    """Suggested conventional-commit type (feat, fix, docs, …)."""

    files: list[AnalyzedChange]
    """All file changes belonging to this group."""

    explanation: str
    """Why these files were grouped together."""

    confidence: str
    """``high``, ``medium``, or ``low``."""

    requires_manual_review: bool = False
    """True when the grouping is ambiguous and needs human confirmation."""


class ChangeAnalysis(BaseModel):
    """Top-level output of the grouping engine."""

    repository: RepositoryInfo
    total_changed_files: int
    groups: list[ChangeGroup] = []
    ungrouped_files: list[AnalyzedChange] = []
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def group_changes(
    analysis: AnalysisResult,
    repo_info: RepositoryInfo,
) -> ChangeAnalysis:
    """Group analyzed changes into logical clusters.

    The algorithm is fully deterministic and rule-based.  Every grouping
    decision carries an explicit explanation and confidence level.

    Args:
        analysis: Output of :func:`~commit_surgeon.core.change_analyzer.analyze_changes`.
        repo_info: Repository metadata from Phase 1.

    Returns:
        A :class:`ChangeAnalysis` with groups, ungrouped files, and warnings.
    """
    items = list(analysis.analyzed)
    warnings = list(analysis.warnings)

    if not items:
        return ChangeAnalysis(
            repository=repo_info,
            total_changed_files=0,
            warnings=warnings,
        )

    # Set of indices already claimed by a group.
    claimed: set[int] = set()
    groups: list[ChangeGroup] = []
    group_counter = 0

    # --- Pass 1: test–implementation pairs --------------------------------
    group_counter = _group_test_pairs(items, claimed, groups, group_counter)

    # --- Pass 2: documentation cluster ------------------------------------
    group_counter = _group_by_classification(
        items,
        claimed,
        groups,
        group_counter,
        target=FileClassification.DOCUMENTATION,
        title="Documentation",
        category=CategorySuggestion.DOCS.value,
        explanation="Documentation files changed together.",
        confidence=Confidence.HIGH.value,
    )

    # --- Pass 3: configuration / build cluster ----------------------------
    group_counter = _group_by_classification(
        items,
        claimed,
        groups,
        group_counter,
        target=(FileClassification.CONFIGURATION, FileClassification.BUILD),
        title="Configuration & Build",
        category=CategorySuggestion.CHORE.value,
        explanation="Configuration and build files changed together.",
        confidence=Confidence.MEDIUM.value,
    )

    # --- Pass 4: naming-convention clusters (source files only) -----------
    group_counter = _group_by_naming_convention(
        items, claimed, groups, group_counter
    )

    # --- Collect ungrouped ------------------------------------------------
    ungrouped = [item for i, item in enumerate(items) if i not in claimed]

    # --- Warnings ---------------------------------------------------------
    if ungrouped:
        paths = [u.diff.path for u in ungrouped]
        warnings.append(
            f"{len(ungrouped)} file(s) could not be automatically grouped: "
            + ", ".join(paths)
        )

    total_paths = len({item.diff.path for item in items})

    return ChangeAnalysis(
        repository=repo_info,
        total_changed_files=total_paths,
        groups=groups,
        ungrouped_files=ungrouped,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Grouping passes
# ---------------------------------------------------------------------------


def _group_test_pairs(
    items: list[AnalyzedChange],
    claimed: set[int],
    groups: list[ChangeGroup],
    counter: int,
) -> int:
    """Pass 1: pair each test file with its implementation counterpart."""
    # Build index from path → list of item indices.
    path_index: dict[str, list[int]] = {}
    for i, item in enumerate(items):
        path_index.setdefault(item.diff.path, []).append(i)

    # Track paths already paired to avoid creating duplicate groups
    # (both sides of a pair see each other as counterpart).
    paired_paths: set[frozenset[str]] = set()

    for i, item in enumerate(items):
        if i in claimed:
            continue
        cp = item.test_counterpart
        if cp is None or cp not in path_index:
            continue

        pair_key = frozenset({item.diff.path, cp})
        if pair_key in paired_paths:
            continue
        paired_paths.add(pair_key)

        # Gather all entries (staged + unstaged) for both paths.
        member_indices: list[int] = []
        for path in (item.diff.path, cp):
            for idx in path_index.get(path, []):
                if idx not in claimed:
                    member_indices.append(idx)

        if not member_indices:
            continue

        members = [items[idx] for idx in member_indices]
        counter += 1

        # Derive a human-friendly title from the implementation file.
        impl_path = (
            cp if item.classification == FileClassification.TEST else item.diff.path
        )
        title = _path_to_title(impl_path)

        groups.append(
            ChangeGroup(
                group_id=f"pair-{counter}",
                title=title,
                category=_pick_primary_category(members),
                files=members,
                explanation=(
                    "Implementation and corresponding test file changed together."
                ),
                confidence=Confidence.HIGH.value,
            )
        )
        claimed.update(member_indices)

    return counter


def _group_by_classification(
    items: list[AnalyzedChange],
    claimed: set[int],
    groups: list[ChangeGroup],
    counter: int,
    *,
    target: FileClassification | tuple[FileClassification, ...],
    title: str,
    category: str,
    explanation: str,
    confidence: str,
) -> int:
    """Group all unclaimed items that match *target* classification(s)."""
    if isinstance(target, FileClassification):
        target = (target,)

    members_idx = [
        (i, item)
        for i, item in enumerate(items)
        if i not in claimed and item.classification in target
    ]

    if not members_idx:
        return counter

    indices, members = zip(*members_idx)
    members = list(members)
    counter += 1

    groups.append(
        ChangeGroup(
            group_id=f"class-{counter}",
            title=title,
            category=category,
            files=members,
            explanation=explanation,
            confidence=confidence,
        )
    )
    claimed.update(indices)
    return counter


def _group_by_naming_convention(
    items: list[AnalyzedChange],
    claimed: set[int],
    groups: list[ChangeGroup],
    counter: int,
) -> int:
    """Pass 4: cluster unclaimed source files that share a naming concept."""
    unclaimed_source = [
        (i, item)
        for i, item in enumerate(items)
        if i not in claimed and item.classification == FileClassification.SOURCE
    ]

    if len(unclaimed_source) < 2:
        return counter

    # Map concept → list of (index, item).
    concept_map: dict[str, list[tuple[int, AnalyzedChange]]] = {}
    for i, item in unclaimed_source:
        concept = _extract_concept(item.diff.path)
        if concept is not None:
            concept_map.setdefault(concept, []).append((i, item))

    for concept, members_data in concept_map.items():
        if len(members_data) < 2:
            continue

        indices = [m[0] for m in members_data]
        members = [m[1] for m in members_data]
        counter += 1

        title = concept.replace("_", " ").title()

        groups.append(
            ChangeGroup(
                group_id=f"naming-{counter}",
                title=title,
                category=_pick_primary_category(members),
                files=members,
                explanation=(
                    f"Source files share the naming concept '{concept}'."
                ),
                confidence=Confidence.MEDIUM.value,
                requires_manual_review=True,
            )
        )
        claimed.update(indices)

    return counter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Suffixes that, when stripped, reveal the underlying domain concept.
_IMPL_SUFFIXES: list[str] = [
    "_model", "_models", "_controller", "_controllers",
    "_view", "_views", "_schema", "_schemas",
    "_service", "_services", "_handler", "_handlers",
    "_middleware", "_utils", "_helpers", "_helper",
    "_factory", "_manager", "_client", "_api",
    "_serializer", "_serializers", "_repository", "_repo",
]

# Generic filenames that should not be used as grouping concepts.
_GENERIC_STEMS: frozenset[str] = frozenset(
    {
        "__init__", "main", "app", "index", "utils", "helpers",
        "constants", "types", "common", "base", "core", "config",
        "settings", "conftest",
    }
)


def _extract_concept(path: str) -> str | None:
    """Extract the primary *domain concept* from a file path.

    Returns ``None`` for generic filenames (``utils.py``, ``__init__.py``, …)
    that should not drive grouping.
    """
    stem = PurePosixPath(path).stem.lower()

    if stem in _GENERIC_STEMS:
        return None

    # Strip test prefixes / suffixes first.
    if stem.startswith("test_"):
        stem = stem[5:]
    elif stem.endswith("_test"):
        stem = stem[:-5]

    # Strip implementation suffixes.
    for suffix in _IMPL_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]

    # Use the first segment as the concept for multi-word names.
    parts = stem.split("_")
    if len(parts) >= 2:
        return parts[0]

    return stem


def _path_to_title(path: str) -> str:
    """Derive a human-friendly group title from a file path.

    ``src/auth_handler.py`` → ``"Auth Handler"``
    """
    stem = PurePosixPath(path).stem
    return stem.replace("_", " ").title()


def _pick_primary_category(changes: list[AnalyzedChange]) -> str:
    """Pick the most common category from a group's collective hints."""
    cats: list[str] = []
    for change in changes:
        for hint in change.category_hints:
            cats.append(hint.category.value)

    if not cats:
        return CategorySuggestion.CHORE.value

    return Counter(cats).most_common(1)[0][0]
