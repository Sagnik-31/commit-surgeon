"""Acceptance test: real (or mock) commit messages for the sandbox."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from surgeon import diffprep, gitops, grouping, messages  # noqa: E402

repo = ROOT / "sandbox_messy"
changes = gitops.scan_changes(repo)
for c in changes:
    c.condensed = diffprep.condense(c)
by_path = {c.path: c for c in changes}
t0 = time.time()
for g in grouping.group_changes(changes):
    plan = messages.generate_commit(g, by_path)
    print(plan.header(), "| fallback:", plan.used_fallback)
    print("   body:", plan.body)
    assert messages.HEADER_RE.match(plan.header())
print(f"total {time.time() - t0:.1f}s")
print("MESSAGES OK")