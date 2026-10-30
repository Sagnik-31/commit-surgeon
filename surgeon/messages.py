"""Turn a Group into a validated CommitPlan using the local model."""
import json
import re
from pathlib import Path

from surgeon import diffprep, llm, prompts
from surgeon.contracts import Change, CommitPlan, Group

HEADER_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore)"
    r"(\([a-z0-9_./-]+\))?: .{1,72}$")


def _scope_from(files: list) -> str:
    stem = Path(files[0]).stem.lower() if files else ""
    return re.sub(r"[^a-z0-9_-]", "", stem)


def _cut(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def _parse(raw: str, group: Group) -> CommitPlan:
    """Parse and auto-fix the model's JSON. Raises ValueError if unusable."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("answer was not valid JSON")
    if not isinstance(data, dict):
        raise ValueError("answer was not a JSON object")

    ctype = str(data.get("type", "")).strip().lower()
    if ctype not in prompts.TYPES:
        raise ValueError(f"bad type {ctype!r}")

    subject = str(data.get("subject", "")).strip().rstrip(".").strip()
    if not subject:
        raise ValueError("empty subject")
    subject = _cut(subject[0].lower() + subject[1:])

    scope = re.sub(r"[^a-z0-9_./-]", "", str(data.get("scope", "")).lower())
    if not scope:
        scope = _scope_from(group.files)

    body = data.get("body", [])
    if not isinstance(body, list):
        body = []
    body = [str(b).strip() for b in body if str(b).strip()][:3]

    plan = CommitPlan(group_id=group.id, type=ctype, scope=scope,
                      subject=subject, files=list(group.files), body=body)
    if not HEADER_RE.match(plan.header()):
        raise ValueError("header is not valid Conventional Commits")
    return plan


def _fallback(group: Group) -> CommitPlan:
    main = Path(group.files[0]).name if group.files else "files"
    ctype = group.type_hint if group.type_hint in prompts.TYPES else "chore"
    return CommitPlan(group_id=group.id, type=ctype,
                      scope=_scope_from(group.files),
                      subject=f"update {main}", files=list(group.files),
                      body=[], used_fallback=True)


def generate_commit(group: Group, changes: dict) -> CommitPlan:
    """Ask the model for a message. Retry once, then fall back."""
    members = [changes[f] for f in group.files if f in changes]
    shown = members[:8]
    diffs = diffprep.budget_split(shown, 3500)
    text = "\n\n".join(diffs.values())
    if len(members) > len(shown):
        extra = ", ".join(c.path for c in members[len(shown):])
        text += f"\n\n(also changed: {extra})"
    user = prompts.COMMIT_USER.format(
        type_hint=group.type_hint,
        file_list=", ".join(group.files),
        condensed_diffs=text or "(no diff available)")

    problem = ""
    for attempt in range(2):
        prompt = user if not problem else (
            user + f"\n\nYour previous answer was rejected: {problem}. "
                   "Reply with one valid JSON object only.")
        try:
            raw = llm.chat(prompts.COMMIT_SYSTEM, prompt,
                           fewshot=prompts.FEWSHOT,
                           schema=prompts.COMMIT_SCHEMA, max_tokens=160)
            return _parse(raw, group)
        except ValueError as e:
            problem = str(e)
        except llm.LlmError as e:
            print(f"[warn] model unavailable: {e}")
            break
    return _fallback(group)


def classify_file(change: Change) -> str:
    """One-word type for a file, or '' on any problem."""
    try:
        user = prompts.CLASSIFY_USER.format(
            path=change.path, condensed_diff=(change.condensed
                                              or diffprep.condense(change))[:1200])
        raw = llm.chat(prompts.CLASSIFY_SYSTEM, user,
                       schema=prompts.CLASSIFY_SCHEMA, max_tokens=8)
        word = raw.strip().strip('"').strip().lower()
        return word if word in prompts.CLASSIFY_WORDS else ""
    except Exception:
        return ""