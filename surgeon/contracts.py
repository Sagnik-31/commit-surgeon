"""Shared data types. Every stage passes these around."""
from dataclasses import dataclass, field


@dataclass
class Change:
    path: str
    status: str = "M"            # M, A, D, R, U
    old_path: str = ""
    category: str = "other"      # docs, test, config, source, other
    additions: int = 0
    deletions: int = 0
    diff: str = ""
    condensed: str = ""
    is_binary: bool = False
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class Group:
    id: int
    label: str
    type_hint: str               # feat, fix, docs, refactor, test, chore
    files: list = field(default_factory=list)
    reason: str = ""


@dataclass
class CommitPlan:
    group_id: int
    type: str
    scope: str
    subject: str
    files: list = field(default_factory=list)
    body: list = field(default_factory=list)
    used_fallback: bool = False

    def header(self) -> str:
        scope = f"({self.scope})" if self.scope else ""
        return f"{self.type}{scope}: {self.subject}"


@dataclass
class PRDraft:
    markdown: str
    used_fallback: bool = False


@dataclass
class BackupInfo:
    orig_head: str
    branch: str
    patch_path: str
    created_at: str