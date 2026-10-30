"""Acceptance test for gitops on ./sandbox_messy."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from surgeon import gitops  # noqa: E402

repo = ROOT / "sandbox_messy"


def sh(*a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True,
                          encoding="utf-8").stdout


head0 = sh("rev-parse", "HEAD").strip()
status0 = sh("status", "--short")

changes = gitops.scan_changes(repo)
for c in changes:
    print(c.status, c.category, f"+{c.additions} -{c.deletions}", c.path)
assert len(changes) >= 3

gitops.make_backup(repo)
sha = gitops.commit_group(repo, ["app/utils.py"], "refactor(utils): test commit")
files = sh("show", "--name-only", "--format=", sha).split()
assert files == ["app/utils.py"], files
print("commit touched only:", files)

gitops.undo(repo)
assert sh("rev-parse", "HEAD").strip() == head0
assert sh("status", "--short") == status0
print("GITOPS OK")