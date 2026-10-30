"""Acceptance test: grouping on ./sandbox_messy."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from surgeon import diffprep, gitops, grouping  # noqa: E402

repo = ROOT / "sandbox_messy"
changes = gitops.scan_changes(repo)
for c in changes:
    c.condensed = diffprep.condense(c)
groups = grouping.group_changes(changes, use_llm=False)
for g in groups:
    print(g.id, g.type_hint, g.files)
print()
print(changes[0].condensed)
types = [g.type_hint for g in groups]
assert types == ["refactor", "fix", "docs"], types
print("GROUPING OK")