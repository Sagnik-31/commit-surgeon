"""Tests for the change analyzer.

These tests construct :class:`FileDiff` objects directly so they can
exercise the classification and counterpart logic without needing a
real Git repository.
"""

from __future__ import annotations

import pytest

from commit_surgeon.core.change_analyzer import (
    AnalysisResult,
    CategorySuggestion,
    Confidence,
    FileClassification,
    analyze_changes,
    classify_file,
    find_test_counterpart,
)
from commit_surgeon.core.diff_parser import ChangeSet, FileDiff
from commit_surgeon.git.status import FileStatus


# =========================================================================
# File classification
# =========================================================================


class TestClassifyFile:
    """Unit tests for classify_file()."""

    # --- source ---
    def test_python_source(self) -> None:
        assert classify_file("src/auth.py") == FileClassification.SOURCE

    def test_javascript_source(self) -> None:
        assert classify_file("lib/index.js") == FileClassification.SOURCE

    def test_rust_source(self) -> None:
        assert classify_file("src/main.rs") == FileClassification.SOURCE

    # --- tests ---
    def test_python_test_prefix(self) -> None:
        assert classify_file("test_auth.py") == FileClassification.TEST

    def test_python_test_suffix(self) -> None:
        assert classify_file("auth_test.py") == FileClassification.TEST

    def test_python_test_in_tests_dir(self) -> None:
        assert classify_file("tests/test_auth.py") == FileClassification.TEST

    def test_file_in_tests_dir(self) -> None:
        assert classify_file("tests/helpers.py") == FileClassification.TEST

    def test_js_test(self) -> None:
        assert classify_file("Button.test.jsx") == FileClassification.TEST

    def test_ts_spec(self) -> None:
        assert classify_file("api.spec.ts") == FileClassification.TEST

    # --- documentation ---
    def test_markdown(self) -> None:
        assert classify_file("docs/guide.md") == FileClassification.DOCUMENTATION

    def test_readme(self) -> None:
        assert classify_file("README.md") == FileClassification.DOCUMENTATION

    def test_changelog_rst(self) -> None:
        assert classify_file("CHANGELOG.rst") == FileClassification.DOCUMENTATION

    def test_license(self) -> None:
        assert classify_file("LICENSE") == FileClassification.DOCUMENTATION

    def test_docs_directory(self) -> None:
        assert classify_file("docs/api/reference.html") == FileClassification.DOCUMENTATION

    # --- configuration ---
    def test_toml(self) -> None:
        assert classify_file("pyproject.toml") == FileClassification.CONFIGURATION

    def test_yaml(self) -> None:
        assert classify_file("config.yaml") == FileClassification.CONFIGURATION

    def test_json(self) -> None:
        assert classify_file("package.json") == FileClassification.CONFIGURATION

    def test_gitignore(self) -> None:
        assert classify_file(".gitignore") == FileClassification.CONFIGURATION

    # --- build / CI ---
    def test_github_workflow(self) -> None:
        assert classify_file(".github/workflows/ci.yml") == FileClassification.BUILD

    def test_setup_py(self) -> None:
        assert classify_file("setup.py") == FileClassification.BUILD

    def test_tox_ini(self) -> None:
        assert classify_file("tox.ini") == FileClassification.BUILD

    # --- assets ---
    def test_png(self) -> None:
        assert classify_file("icon.png") == FileClassification.ASSET

    def test_svg(self) -> None:
        assert classify_file("logo.svg") == FileClassification.ASSET

    # --- other ---
    def test_no_extension(self) -> None:
        assert classify_file("Makefile") == FileClassification.CONFIGURATION

    def test_truly_unknown(self) -> None:
        """A file with no extension and no known name → OTHER."""
        assert classify_file("UNKNOWN") == FileClassification.OTHER


# =========================================================================
# Category suggestion
# =========================================================================


class TestCategorySuggestion:
    """Verify that category hints match file classification and status."""

    def _make_diff(self, path: str, status: FileStatus) -> FileDiff:
        return FileDiff(path=path, status=status)

    def test_new_source_suggests_feat(self) -> None:
        cs = ChangeSet(staged=[self._make_diff("src/new.py", FileStatus.ADDED)])
        result = analyze_changes(cs)
        hints = result.analyzed[0].category_hints
        assert any(h.category == CategorySuggestion.FEAT for h in hints)

    def test_doc_suggests_docs(self) -> None:
        cs = ChangeSet(unstaged=[self._make_diff("README.md", FileStatus.MODIFIED)])
        result = analyze_changes(cs)
        hints = result.analyzed[0].category_hints
        assert any(h.category == CategorySuggestion.DOCS for h in hints)

    def test_test_file_suggests_test(self) -> None:
        cs = ChangeSet(unstaged=[self._make_diff("test_foo.py", FileStatus.MODIFIED)])
        result = analyze_changes(cs)
        hints = result.analyzed[0].category_hints
        assert any(h.category == CategorySuggestion.TEST for h in hints)

    def test_config_suggests_chore(self) -> None:
        cs = ChangeSet(staged=[self._make_diff("pyproject.toml", FileStatus.MODIFIED)])
        result = analyze_changes(cs)
        hints = result.analyzed[0].category_hints
        assert any(h.category == CategorySuggestion.CHORE for h in hints)

    def test_deleted_source_suggests_refactor(self) -> None:
        cs = ChangeSet(unstaged=[self._make_diff("old.py", FileStatus.DELETED)])
        result = analyze_changes(cs)
        hints = result.analyzed[0].category_hints
        assert any(h.category == CategorySuggestion.REFACTOR for h in hints)

    def test_renamed_source_suggests_refactor(self) -> None:
        cs = ChangeSet(staged=[self._make_diff("new.py", FileStatus.RENAMED)])
        result = analyze_changes(cs)
        hints = result.analyzed[0].category_hints
        assert any(h.category == CategorySuggestion.REFACTOR for h in hints)

    def test_modified_source_low_confidence(self) -> None:
        cs = ChangeSet(unstaged=[self._make_diff("core.py", FileStatus.MODIFIED)])
        result = analyze_changes(cs)
        hints = result.analyzed[0].category_hints
        assert all(h.confidence == Confidence.LOW for h in hints)


# =========================================================================
# Test-counterpart detection
# =========================================================================


class TestFindTestCounterpart:
    """Verify test ↔ implementation pairing across layouts."""

    def test_source_finds_test_prefix(self) -> None:
        paths = {"auth.py", "test_auth.py"}
        assert find_test_counterpart("auth.py", paths) == "test_auth.py"

    def test_test_finds_source(self) -> None:
        paths = {"auth.py", "test_auth.py"}
        assert find_test_counterpart("test_auth.py", paths) == "auth.py"

    def test_source_finds_test_in_tests_dir(self) -> None:
        paths = {"src/auth.py", "tests/test_auth.py"}
        assert find_test_counterpart("src/auth.py", paths) == "tests/test_auth.py"

    def test_test_in_tests_dir_finds_src(self) -> None:
        paths = {"src/auth.py", "tests/test_auth.py"}
        assert find_test_counterpart("tests/test_auth.py", paths) == "src/auth.py"

    def test_suffix_convention(self) -> None:
        paths = {"auth.py", "auth_test.py"}
        assert find_test_counterpart("auth.py", paths) == "auth_test.py"

    def test_no_counterpart(self) -> None:
        paths = {"auth.py", "user.py"}
        assert find_test_counterpart("auth.py", paths) is None

    def test_doc_file_no_counterpart(self) -> None:
        paths = {"README.md", "test_readme.py"}
        # classify_file("README.md") → DOCUMENTATION, not SOURCE
        assert find_test_counterpart("README.md", paths) is None

    def test_nested_src_tests_layout(self) -> None:
        """``src/pkg/foo.py`` ↔ ``tests/pkg/test_foo.py``."""
        paths = {"src/pkg/foo.py", "tests/pkg/test_foo.py"}
        assert find_test_counterpart("src/pkg/foo.py", paths) == "tests/pkg/test_foo.py"
