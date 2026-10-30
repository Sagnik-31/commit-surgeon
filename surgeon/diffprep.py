"""Shorten diffs so a small model can read them."""
from surgeon.contracts import Change

MAX_LINE = 160


def condense(change: Change, max_chars: int = 1200) -> str:
    """Keep hunk headers and changed lines only, capped at max_chars."""
    if change.skipped:
        return f"(file not shown: {change.skip_reason})"
    stats = f"(+{change.additions} -{change.deletions})"
    kept = [f"{change.status} {change.path} {stats}"]
    for line in change.diff.splitlines():
        if line.startswith(("+++", "---", "diff --git", "index ")):
            continue
        if line.startswith("@@") or line[:1] in ("+", "-"):
            kept.append(line[:MAX_LINE])
    out, size = [], 0
    for i, line in enumerate(kept):
        if size + len(line) + 1 > max_chars and i > 1:
            left = len(kept) - i
            out.append(f"[... {left} more changed lines omitted ...]")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def budget_split(changes: list, total_chars: int = 3500) -> dict:
    """Give every file an equal share of the prompt budget."""
    if not changes:
        return {}
    share = max(300, total_chars // len(changes))
    return {c.path: condense(c, share) for c in changes}