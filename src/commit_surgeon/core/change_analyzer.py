"""Change analyzer — classifies files and identifies relationships.

Given a :class:`~commit_surgeon.core.diff_parser.ChangeSet`, this module
produces an :class:`AnalysisResult` where every file has:

* A **classification** (source, test, documentation, configuration, …).
* One or more **category hints** (feat, fix, docs, test, chore, …) with
  an explicit confidence level.
* A **test counterpart** path when one exists in the change set.

All heuristics are deterministic and rule-based — no AI is used.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath

from pydantic import BaseModel

from commit_surgeon.core.diff_parser import ChangeSet, FileDiff
from commit_surgeon.git.status import FileStatus


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FileClassification(str, Enum):
    """High-level role of a file in the repository."""

    SOURCE = "source"
    TEST = "test"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    BUILD = "build"
    ASSET = "asset"
    OTHER = "other"


class CategorySuggestion(str, Enum):
    """Conventional-commit category."""

    FEAT = "feat"
    FIX = "fix"
    REFACTOR = "refactor"
    DOCS = "docs"
    TEST = "test"
    CHORE = "chore"
    STYLE = "style"
    PERF = "perf"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CategoryHint(BaseModel):
    """A single category suggestion with justification."""

    category: CategorySuggestion
    confidence: Confidence
    reason: str


class AnalyzedChange(BaseModel):
    """A :class:`FileDiff` enriched with classification and hints."""

    diff: FileDiff
    classification: FileClassification
    category_hints: list[CategoryHint] = []
    test_counterpart: str | None = None
    """Path of the matching test / implementation file (if present in the
    change set).  ``None`` when no counterpart was changed."""


class AnalysisResult(BaseModel):
    """Output of :func:`analyze_changes`."""

    analyzed: list[AnalyzedChange] = []
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------

_TEST_FILE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r".*\.test\.[jt]sx?$"),
    re.compile(r".*\.spec\.[jt]sx?$"),
    re.compile(r".*Test\.java$"),
    re.compile(r".*Test\.kt$"),
    re.compile(r".*_test\.go$"),
    re.compile(r".*_test\.rs$"),
]

_TEST_DIRECTORIES: frozenset[str] = frozenset(
    {"tests", "test", "__tests__", "spec", "specs"}
)

_DOC_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".rst", ".txt", ".adoc", ".asciidoc"}
)

_DOC_STEMS: frozenset[str] = frozenset(
    {
        "readme",
        "changelog",
        "license",
        "licence",
        "contributing",
        "authors",
        "history",
        "changes",
        "news",
        "todo",
        "code_of_conduct",
    }
)

_DOC_DIRECTORIES: frozenset[str] = frozenset({"docs", "doc", "documentation"})

_CONFIG_EXTENSIONS: frozenset[str] = frozenset(
    {".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".env", ".conf"}
)

_CONFIG_NAMES: frozenset[str] = frozenset(
    {
        "makefile",
        "dockerfile",
        "vagrantfile",
        "procfile",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".dockerignore",
        ".eslintrc",
        ".prettierrc",
        "pipfile",
        "gemfile",
        "rakefile",
    }
)

_BUILD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\.github/"),
    re.compile(r"^\.circleci/"),
    re.compile(r"^\.travis\.yml$"),
    re.compile(r"^Jenkinsfile$"),
    re.compile(r"^tox\.ini$"),
    re.compile(r"^setup\.(py|cfg)$"),
]

_ASSET_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
        ".woff", ".woff2", ".ttf", ".eot",
        ".mp3", ".mp4", ".wav", ".ogg",
        ".pdf",
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_changes(change_set: ChangeSet) -> AnalysisResult:
    """Classify every file in *change_set* and detect relationships.

    Returns:
        An :class:`AnalysisResult` with one :class:`AnalyzedChange` per
        file-diff entry.
    """
    all_diffs = change_set.all_files
    all_paths: set[str] = {d.path for d in all_diffs}

    analyzed: list[AnalyzedChange] = []
    warnings: list[str] = []

    for diff in all_diffs:
        classification = classify_file(diff.path)
        hints = _suggest_categories(diff, classification)
        counterpart = find_test_counterpart(diff.path, all_paths, classification)

        analyzed.append(
            AnalyzedChange(
                diff=diff,
                classification=classification,
                category_hints=hints,
                test_counterpart=counterpart,
            )
        )

    return AnalysisResult(analyzed=analyzed, warnings=warnings)


def classify_file(path: str) -> FileClassification:
    """Classify a file purely by its path, name, and extension.

    The order of checks matters — e.g. a ``.py`` file inside ``tests/``
    is classified as :attr:`~FileClassification.TEST` before the generic
    source-file fallback.
    """
    p = PurePosixPath(path)
    name_lower = p.name.lower()
    stem_lower = p.stem.lower()
    ext_lower = p.suffix.lower()
    dir_parts = [part.lower() for part in p.parts[:-1]]

    # --- tests ---
    for pattern in _TEST_FILE_PATTERNS:
        if pattern.match(name_lower):
            return FileClassification.TEST

    if any(part in _TEST_DIRECTORIES for part in dir_parts):
        return FileClassification.TEST

    # --- documentation ---
    if ext_lower in _DOC_EXTENSIONS:
        return FileClassification.DOCUMENTATION
    if stem_lower in _DOC_STEMS:
        return FileClassification.DOCUMENTATION
    if any(part in _DOC_DIRECTORIES for part in dir_parts):
        return FileClassification.DOCUMENTATION

    # --- build / CI ---
    for pattern in _BUILD_PATTERNS:
        if pattern.match(path):
            return FileClassification.BUILD

    # --- configuration ---
    if ext_lower in _CONFIG_EXTENSIONS:
        return FileClassification.CONFIGURATION
    if name_lower in _CONFIG_NAMES:
        return FileClassification.CONFIGURATION

    # --- assets ---
    if ext_lower in _ASSET_EXTENSIONS:
        return FileClassification.ASSET

    # --- fallback ---
    if ext_lower:
        return FileClassification.SOURCE

    return FileClassification.OTHER


# ---------------------------------------------------------------------------
# Category suggestion
# ---------------------------------------------------------------------------


def _suggest_categories(
    diff: FileDiff,
    classification: FileClassification,
) -> list[CategoryHint]:
    """Produce one or more category hints for a single file change."""
    hints: list[CategoryHint] = []

    if classification == FileClassification.TEST:
        hints.append(
            CategoryHint(
                category=CategorySuggestion.TEST,
                confidence=Confidence.HIGH,
                reason="File is classified as a test file",
            )
        )

    elif classification == FileClassification.DOCUMENTATION:
        hints.append(
            CategoryHint(
                category=CategorySuggestion.DOCS,
                confidence=Confidence.HIGH,
                reason="File is classified as documentation",
            )
        )

    elif classification in (FileClassification.CONFIGURATION, FileClassification.BUILD):
        hints.append(
            CategoryHint(
                category=CategorySuggestion.CHORE,
                confidence=Confidence.HIGH
                if classification == FileClassification.BUILD
                else Confidence.MEDIUM,
                reason=f"File is classified as {classification.value}",
            )
        )

    elif classification == FileClassification.ASSET:
        hints.append(
            CategoryHint(
                category=CategorySuggestion.CHORE,
                confidence=Confidence.MEDIUM,
                reason="File is a static asset",
            )
        )

    elif classification == FileClassification.SOURCE:
        hints.extend(_hint_source_category(diff))

    return hints


def _hint_source_category(diff: FileDiff) -> list[CategoryHint]:
    """Category hints specific to source-code files."""
    hints: list[CategoryHint] = []

    if diff.status == FileStatus.ADDED:
        hints.append(
            CategoryHint(
                category=CategorySuggestion.FEAT,
                confidence=Confidence.MEDIUM,
                reason="New source file added",
            )
        )
    elif diff.status == FileStatus.DELETED:
        hints.append(
            CategoryHint(
                category=CategorySuggestion.REFACTOR,
                confidence=Confidence.LOW,
                reason="Source file deleted — could be refactor or cleanup",
            )
        )
    elif diff.status == FileStatus.RENAMED:
        hints.append(
            CategoryHint(
                category=CategorySuggestion.REFACTOR,
                confidence=Confidence.MEDIUM,
                reason="Source file renamed",
            )
        )
    elif diff.status == FileStatus.MODIFIED:
        hints.append(
            CategoryHint(
                category=CategorySuggestion.FEAT,
                confidence=Confidence.LOW,
                reason="Source file modified — could be feature, fix, or refactor",
            )
        )

    return hints


# ---------------------------------------------------------------------------
# Test-counterpart detection
# ---------------------------------------------------------------------------


def find_test_counterpart(
    path: str,
    all_paths: set[str],
    classification: FileClassification | None = None,
) -> str | None:
    """Find the test (or implementation) counterpart for *path*.

    * If *path* is a test file, search for its implementation.
    * If *path* is a source file, search for its test.

    Returns ``None`` when no counterpart exists in *all_paths*.
    """
    if classification is None:
        classification = classify_file(path)

    if classification == FileClassification.TEST:
        return _find_source_for_test(path, all_paths)
    if classification == FileClassification.SOURCE:
        return _find_test_for_source(path, all_paths)

    return None


def _find_test_for_source(path: str, all_paths: set[str]) -> str | None:
    """Given a source file, find a matching test file in *all_paths*."""
    p = PurePosixPath(path)
    stem = p.stem
    ext = p.suffix
    directory = str(p.parent) if str(p.parent) != "." else ""

    candidates: list[str] = []

    # test_<stem>.py in the same directory
    if directory:
        candidates.append(f"{directory}/test_{stem}{ext}")
    else:
        candidates.append(f"test_{stem}{ext}")

    # test_<stem>.py in tests/ (root-level)
    candidates.append(f"tests/test_{stem}{ext}")
    candidates.append(f"test/test_{stem}{ext}")

    # <stem>_test.py variants
    if directory:
        candidates.append(f"{directory}/{stem}_test{ext}")
    else:
        candidates.append(f"{stem}_test{ext}")
    candidates.append(f"tests/{stem}_test{ext}")

    # src/pkg/foo.py → tests/pkg/test_foo.py
    if directory:
        parts = PurePosixPath(directory).parts
        if parts and parts[0] == "src":
            test_dir = "/".join(("tests", *parts[1:]))
            candidates.append(f"{test_dir}/test_{stem}{ext}")
            candidates.append(f"{test_dir}/{stem}_test{ext}")

    for candidate in candidates:
        if candidate in all_paths:
            return candidate
    return None


def _find_source_for_test(path: str, all_paths: set[str]) -> str | None:
    """Given a test file, find a matching source file in *all_paths*."""
    p = PurePosixPath(path)
    stem = p.stem
    ext = p.suffix
    directory = str(p.parent) if str(p.parent) != "." else ""

    impl_stem = _strip_test_affixes(stem)
    if impl_stem is None:
        return None

    candidates: list[str] = []

    # Same directory
    if directory:
        candidates.append(f"{directory}/{impl_stem}{ext}")
    else:
        candidates.append(f"{impl_stem}{ext}")

    # src/ root
    candidates.append(f"src/{impl_stem}{ext}")

    # tests/pkg/test_foo.py → src/pkg/foo.py
    if directory:
        parts = PurePosixPath(directory).parts
        if parts and parts[0] in ("tests", "test"):
            src_dir = "/".join(("src", *parts[1:]))
            candidates.append(f"{src_dir}/{impl_stem}{ext}")

    for candidate in candidates:
        if candidate in all_paths:
            return candidate
    return None


def _strip_test_affixes(stem: str) -> str | None:
    """Strip ``test_`` prefix or ``_test`` suffix from a stem."""
    if stem.startswith("test_"):
        return stem[5:]
    if stem.endswith("_test"):
        return stem[:-5]
    return None
