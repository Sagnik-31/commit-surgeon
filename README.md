# ⚕ Commit Surgeon

**Intelligent Git Workflow Assistant** — analyzes your messy working tree,
groups related changes, and proposes well-structured conventional commits.

> **Status:** Phase 3 complete.  Deterministic commit planning with
> conventional commit messages.  No AI / cloud dependencies.

---

## Installation

```bash
git clone https://github.com/Sagnik-31/commit-surgeon.git
cd commit-surgeon

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires **Python 3.11+** and **Git ≥ 2.11**.

---

## Usage

```bash
# Show help
surgeon --help

# Show version
surgeon --version

# Analyze the repository and list all changes
surgeon analyze
surgeon analyze --path /path/to/repo

# Propose logical change groups
surgeon group
surgeon group --path /path/to/repo

# Generate a commit plan with conventional commit messages
surgeon plan
surgeon plan --path /path/to/repo

# Export the plan to JSON
surgeon plan --output commit-plan.json
```

### `surgeon analyze`

Inspects the repository and lists every changed file with diff statistics
(additions / deletions), split by staged, unstaged, and untracked.

### `surgeon group`

Groups related files into logical clusters using 4 deterministic passes:
test–impl pairs, documentation, config/build, naming conventions.

### `surgeon plan`

Generates a complete commit plan with conventional commit messages:

```
[1] feat(auth): update auth and tests
    Files: 2 (2 modified)
    Confidence: Medium
    Status: Needs review

[2] docs: update readme, guide
    Files: 2 (2 modified)
    Confidence: High
    Status: Ready

[3] feat(user): update user modules
    Files: 2 (2 modified)
    Confidence: Low
    Status: Needs review

Plan Summary:
    Proposed commits:   3
    Unassigned files:   1
    Warnings:           3

    ✓ Nothing has been staged or committed.
```

### Conventional Commit Format

Messages follow the [Conventional Commits](https://www.conventionalcommits.org/) spec:

```
type(scope): description
```

Supported types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`, `perf`, `build`, `ci`.

**How messages are generated:**

| Source | Type | Scope | Confidence |
|---|---|---|---|
| Test-impl pair (modified) | `feat`* | from group title | Medium |
| Test-impl pair (added) | `feat` | from group title | Medium |
| Documentation files | `docs` | file stem or none | High |
| Config / build files | `chore` | file stem or none | Medium |
| Naming-convention group | `feat`* | from concept | Low |

\* Source modifications are **never** automatically classified as `feat` with
high confidence.  They are flagged for manual review because we cannot
distinguish `feat` vs `fix` vs `refactor` without content analysis.

### JSON Export

```bash
surgeon plan --output commit-plan.json
```

The export will **not** overwrite an existing file.  The JSON structure:

```json
{
  "plan_id": "75274252",
  "repository": "my-project",
  "branch": "main",
  "generated_at": "2026-10-30T19:59:22.955880+00:00",
  "commits": [
    {
      "commit_id": "commit-1",
      "group_id": "pair-1",
      "message": {
        "type": "feat",
        "scope": "auth",
        "description": "update auth and tests",
        "confidence": "medium",
        "requires_manual_review": true,
        "full_message": "feat(auth): update auth and tests"
      },
      "files": [...],
      "summary": "2 file(s) (2 modified) — needs review",
      "warnings": [],
      "approved": false
    }
  ],
  "unassigned_files": [...],
  "warnings": [...]
}
```

---

## Architecture

```
surgeon plan
    │
    ▼
detect_repository(path)              ← git rev-parse (read-only)
    │  RepositoryInfo
    ▼
parse_changes(root)                  ← git status --porcelain=v2
    │  ChangeSet                       + git diff --numstat
    ▼
analyze_changes(change_set)          ← deterministic classification
    │  AnalysisResult
    ▼
group_changes(analysis, info)        ← 4-pass rule-based grouper
    │  ChangeAnalysis
    ▼
create_plan(change_analysis)         ← conventional commit messages
    │  CommitPlan                      + safety checks + warnings
    ▼
Rich CLI rendering / JSON export
```

### Deterministic vs Future LLM-Assisted Generation

Currently, all message generation is **rule-based and deterministic**:
- Scope is inferred from group title or file path
- Description is derived from file statuses and naming
- Type is mapped from the analyzer's category hints
- Confidence reflects how ambiguous the classification is

In a future phase, a **local LLM** will enhance this by:
- Analyzing actual diff content to determine intent (feat vs fix vs refactor)
- Generating more descriptive commit messages
- Providing higher-confidence type classification
- Suggesting body text for complex changes

The deterministic engine serves as a reliable fallback and validation
layer — LLM suggestions will be compared against rule-based expectations.

---

## Project Structure

```
commit-surgeon/
├── pyproject.toml
├── README.md
├── .gitignore
├── src/
│   └── commit_surgeon/
│       ├── __init__.py
│       ├── cli.py                     # surgeon analyze/group/plan
│       ├── core/
│       │   ├── __init__.py
│       │   ├── repository.py          # Phase 1: repo detection
│       │   ├── diff_parser.py         # Phase 2: diff enrichment
│       │   ├── change_analyzer.py     # Phase 2: file classification
│       │   ├── change_grouper.py      # Phase 2: grouping engine
│       │   ├── commit_message.py      # Phase 3: message generation
│       │   └── commit_planner.py      # Phase 3: plan creation
│       ├── git/
│       │   ├── __init__.py            # safe subprocess runner
│       │   └── status.py              # porcelain v2 parser
│       ├── config/__init__.py
│       └── utils/__init__.py
└── tests/
    ├── conftest.py                    # shared fixtures
    ├── test_repository.py             # 19 tests
    ├── test_cli.py                    # 11 tests
    ├── test_diff_parser.py            # 13 tests
    ├── test_change_analyzer.py        # 30 tests
    ├── test_change_grouper.py         # 22 tests
    ├── test_commit_message.py         # 37 tests
    └── test_commit_planner.py         # 30 tests
```

---

## Testing

```bash
# Run all tests
pytest

# Verbose with coverage
pytest -v --cov=commit_surgeon --cov-report=term-missing
```

---

## Known Limitations

1. **File-level grouping only.**  A single file with multiple unrelated
   logical changes is treated as one unit.
2. **No AI inference.**  Message types (feat/fix/refactor) are heuristic.
   Modified source files always get flagged for review.
3. **Naming-convention grouping is conservative.**  Only files sharing
   the same stem segment are clustered.
4. **No commit execution.**  Plans are proposed but not executed.
5. **No import/dependency analysis.**  Grouping doesn't parse source code.

---

## License

MIT
