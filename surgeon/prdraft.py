"""Pull request description from approved commit plans."""
import json
import re
from pathlib import Path

from surgeon import llm
from surgeon.contracts import PRDraft

HEADINGS = ["## Summary", "## What changed", "## Why", "## How to test"]

PR_SYSTEM = """You write pull request descriptions for open-source maintainers.
Output ONLY Markdown with exactly these four sections in this order:
## Summary
## What changed
## Why
## How to test
Rules:
- Summary: one sentence.
- What changed: one bullet per commit, in order, using the commit subject.
- Why: 1 to 2 sentences. Restate only what the commit subjects and bodies say. Never add motives such as clarity, maintainability, organization, discoverability or performance unless a commit body says so. Never call code deprecated or legacy unless a commit says so. A fix commit means a bug was fixed; say no more than that.
- How to test: 2 to 5 numbered steps. If a test command is given, use it. Otherwise give manual steps that mention the changed files.
- No other text before or after."""


def detect_test_cmd(repo) -> str:
    repo = Path(repo)
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if "test" in data.get("scripts", {}):
                return "npm test"
        except (OSError, ValueError):
            pass
    if (repo / "tests").exists() or (repo / "pyproject.toml").exists():
        return "pytest"
    return ""


def _clean(raw: str) -> str:
    raw = re.sub(r"^```(?:markdown|md)?|```$", "", raw.strip(), flags=re.M)
    start = raw.find("## Summary")
    return raw[start:].strip() if start >= 0 else raw.strip()


def _valid(md: str) -> bool:
    last = -1
    for h in HEADINGS:
        i = md.find(h)
        if i <= last:
            return False
        last = i
    return True


def _template(plans: list, test_cmd: str) -> str:
    kinds = ", ".join(sorted({p.type for p in plans}))
    bullets = "\n".join(f"- {p.header()}" for p in plans)
    if test_cmd:
        steps = (f"1. Run `{test_cmd}`.\n"
                 "2. Run `git show --stat` on each commit and confirm it "
                 "only touches its own files.")
    else:
        steps = ("1. Run `git show --stat` on each commit.\n"
                 "2. Exercise the changed files manually.")
    return (f"## Summary\nThis change is split into {len(plans)} focused "
            f"commit(s) ({kinds}).\n\n"
            f"## What changed\n{bullets}\n\n"
            "## Why\nA mixed set of changes was separated into small commits "
            "so each one can be reviewed on its own.\n\n"
            f"## How to test\n{steps}\n")


def generate_pr(plans: list, repo_info: dict) -> PRDraft:
    test_cmd = repo_info.get("test_cmd", "")
    lines = [f"Branch: {repo_info.get('branch', '')}",
             f"Test command detected: {test_cmd or 'none'}", "Commits:"]
    for i, p in enumerate(plans, 1):
        lines.append(f"{i}. {p.header()}")
        lines += [f"   {b}" for b in p.body]
        lines.append(f"   Files: {', '.join(p.files)}")
    try:
        raw = llm.chat(PR_SYSTEM, "\n".join(lines), max_tokens=400)
        md = _clean(raw)
        if _valid(md):
            return PRDraft(md + "\n", False)
    except llm.LlmError:
        pass
    return PRDraft(_template(plans, test_cmd), True)