"""Group changed files into logical commits using simple rules."""
import re
from pathlib import Path

from surgeon.contracts import Change, Group

ORDER = ["refactor", "fix", "feat", "test", "docs", "chore"]
GUARD = re.compile(r"^\+\s*(if|elif|try|except|raise|assert|guard|else)\b")
LOGIC = re.compile(r"^\+\s*(if|elif|try|except|raise)\b|^\+.*\b(is None|== 0|!= 0)\b")


def _stem(path: str) -> str:
    name = Path(path).stem.lower()
    for pre in ("test_",):
        if name.startswith(pre):
            name = name[len(pre):]
    if name.endswith("_test"):
        name = name[:-5]
    return name


def guess_type(change: Change) -> str:
    """Heuristic: fix, refactor or feat. Returns '' when unsure."""
    if change.status == "A" or change.status == "U":
        return "feat"
    if change.status == "D":
        return "refactor"
    added = [ln for ln in change.diff.splitlines() if ln.startswith("+")
             and not ln.startswith("+++")]
    if any(LOGIC.match(ln) for ln in added):
        return "fix"
    if change.additions and change.deletions:
        ratio = change.additions / max(change.deletions, 1)
        if 0.5 <= ratio <= 2.0:
            return "refactor"
    return ""


def group_changes(changes: list, use_llm: bool = False) -> list:
    buckets: dict = {}
    warnings = []
    source_stems: dict = {}

    def add(kind, change):
        buckets.setdefault(kind, []).append(change.path)
        if change.old_path:
            buckets[kind].append(change.old_path)

    usable = [c for c in changes if not c.skipped]
    for c in changes:
        if c.skipped:
            warnings.append(f"skipped {c.path}: {c.skip_reason}")

    # docs and config first
    for c in usable:
        if c.category == "docs":
            add("docs", c)
        elif c.category == "config":
            add("chore", c)

    # source files
    for c in usable:
        if c.category in ("source", "other"):
            kind = guess_type(c)
            if not kind and use_llm:
                try:
                    from surgeon.messages import classify_file
                    kind = classify_file(c)
                except Exception:
                    kind = ""
            kind = kind if kind in ORDER else "refactor"
            add(kind, c)
            source_stems[_stem(c.path)] = kind

    # tests follow their source file, otherwise form a test group
    for c in usable:
        if c.category == "test":
            kind = source_stems.get(_stem(c.path), "test")
            add(kind, c)

    groups = []
    for kind in ORDER:
        files = buckets.get(kind)
        if not files:
            continue
        files = list(dict.fromkeys(files))
        groups.append(Group(
            id=len(groups) + 1,
            label=kind,
            type_hint=kind,
            files=files,
            reason=f"{len(files)} file(s) classified as {kind}",
        ))
    for w in warnings:
        print(f"[warn] {w}")
    return groups