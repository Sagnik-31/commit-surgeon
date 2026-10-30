"""Git plumbing: scan, backup, commit, undo. No destructive commands."""
import difflib
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from surgeon.contracts import BackupInfo, Change

MAX_BYTES = 200_000
UNTRACKED_LINES = 200
SKIP_NAMES = {"package-lock.json", "yarn.lock", "poetry.lock",
              "pnpm-lock.yaml", "pipfile.lock"}
SECRET_NAMES = {".env", "id_rsa", "id_ed25519"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
            ".cs", ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".sh",
            ".html", ".css", ".sql"}


class GitError(Exception):
    """A git problem, with a message that says what to do."""


def _git(repo, *args, input=None, env=None, check=True):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    r = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(repo), capture_output=True, encoding="utf-8",
        errors="replace", input=input, env=full_env,
    )
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def _git_dir(repo) -> Path:
    return Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())


def _surgeon_dir(repo) -> Path:
    d = _git_dir(repo) / "surgeon"
    d.mkdir(exist_ok=True)
    return d


def _check_state(repo) -> None:
    gd = _git_dir(repo)
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                 "rebase-merge", "rebase-apply"):
        if (gd / name).exists():
            raise GitError("A merge, rebase or cherry-pick is in progress. "
                           "Finish or abort it first.")


def _category(path: str) -> str:
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    if p.endswith((".md", ".rst")) or p.startswith("docs/") \
            or name.startswith("readme"):
        return "docs"
    if p.startswith("tests/") or "/tests/" in p \
            or name.startswith("test_") or "_test." in name:
        return "test"
    if (name in {"package.json", "pyproject.toml", "dockerfile",
                 "setup.py", "setup.cfg"}
            or name.startswith("requirements")
            or p.startswith(".github/")
            or name.endswith((".yml", ".yaml", ".toml", ".ini", ".cfg"))):
        return "config"
    if Path(name).suffix in CODE_EXT:
        return "source"
    return "other"


def _skip_reason(path: str) -> str:
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    if name in SKIP_NAMES or name.endswith(".min.js"):
        return "lockfile or minified file"
    if (name in SECRET_NAMES or name.startswith(".env")
            or name.endswith(".pem") or "node_modules/" in p):
        return "possible secret or dependency folder"
    return ""


def _status_letter(xy: str) -> str:
    if xy == "??":
        return "U"
    if "D" in xy:
        return "D"
    if "R" in xy:
        return "R"
    if "A" in xy:
        return "A"
    return "M"


def _parse_status(out: str) -> list:
    parts = out.split("\0")
    items, i = [], 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path, old = entry[:2], entry[3:], ""
        if xy[0] in "RC" and i < len(parts):
            old = parts[i]
            i += 1
        items.append((xy, path, old))
    return items


def _skip(ch: Change, reason: str) -> Change:
    ch.skipped, ch.skip_reason = True, reason
    return ch


def _build_change(repo, xy: str, path: str, old: str) -> Change:
    ch = Change(path=path, status=_status_letter(xy), old_path=old,
                category=_category(path))
    reason = _skip_reason(path)
    if reason:
        return _skip(ch, reason)

    if xy == "??":
        f = Path(repo) / path
        try:
            if f.stat().st_size > MAX_BYTES:
                return _skip(ch, "file too large")
            raw = f.read_bytes()
        except OSError as e:
            return _skip(ch, f"cannot read: {e}")
        if b"\0" in raw[:8000]:
            ch.is_binary = True
            return _skip(ch, "binary file")
        lines = raw.decode("utf-8", "replace").splitlines()[:UNTRACKED_LINES]
        ch.diff = f"new file: {path}\n@@ new file @@\n" \
                  + "\n".join("+" + ln for ln in lines) + "\n"
        ch.additions = len(lines)
        return ch

    paths = [path] + ([old] if old else [])
    diff = _git(repo, "diff", "HEAD", "--", *paths).stdout
    if "Binary files" in diff or "GIT binary patch" in diff:
        ch.is_binary = True
        return _skip(ch, "binary file")
    if len(diff) > MAX_BYTES:
        return _skip(ch, "diff too large")
    ch.diff = diff
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            ch.additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            ch.deletions += 1
    return ch


def _head_text(repo, path):
    r = _git(repo, "show", f"HEAD:{path}", check=False)
    return r.stdout if r.returncode == 0 else None


def _pair_renames(repo, changes: list) -> list:
    """Merge a deleted file and a new file with similar content into one rename."""
    deleted = [c for c in changes if c.status == "D" and not c.skipped]
    added = [c for c in changes if c.status in ("U", "A") and not c.skipped]
    used = set()
    for new in added:
        try:
            new_text = (Path(repo) / new.path).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        best, best_text, best_ratio = None, "", 0.0
        for old in deleted:
            if old.path in used or Path(old.path).suffix != Path(new.path).suffix:
                continue
            old_text = _head_text(repo, old.path)
            if old_text is None:
                continue
            ratio = difflib.SequenceMatcher(None, old_text, new_text).ratio()
            if ratio > best_ratio:
                best, best_text, best_ratio = old, old_text, ratio
        if best is None or best_ratio < 0.6:
            continue
        used.add(best.path)
        new.status, new.old_path = "R", best.path
        lines = difflib.unified_diff(best_text.splitlines(),
                                     new_text.splitlines(),
                                     lineterm="", n=0)
        body = [ln for ln in lines if not ln.startswith(("---", "+++"))]
        new.diff = f"renamed: {best.path} -> {new.path}\n" + "\n".join(body) + "\n"
        new.additions = sum(1 for ln in body if ln.startswith("+"))
        new.deletions = sum(1 for ln in body if ln.startswith("-"))
    return [c for c in changes if c.path not in used]


def scan_changes(repo) -> list:
    """Return one Change per modified, added, deleted or untracked file."""
    out = _git(repo, "status", "--porcelain=v1", "-z",
               "--untracked-files=all").stdout
    changes = [_build_change(repo, xy, p, o) for xy, p, o in _parse_status(out)]
    return _pair_renames(repo, changes)

def make_backup(repo) -> BackupInfo:
    """Save HEAD, branch and a patch before the first commit."""
    repo = Path(repo)
    _check_state(repo)
    sdir = _surgeon_dir(repo)
    state_file = sdir / "state.json"
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if state_file.exists():
        old = json.loads(state_file.read_text(encoding="utf-8"))
        if old.get("pending_split"):      # keep the pre-split HEAD
            head, branch = old["orig_head"], old["branch"]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    patch = sdir / f"backup-{ts}.patch"
    patch.write_text(_git(repo, "diff", "HEAD", "--binary").stdout,
                     encoding="utf-8", newline="\n")
    info = BackupInfo(head, branch, str(patch), ts)
    state_file.write_text(json.dumps(asdict(info)), encoding="utf-8")
    return info


def commit_group(repo, files: list, message: str) -> str:
    """Stage and commit ONLY these files. Returns the short sha.

    For renames, pass both the new and the old path.
    """
    if not files:
        raise GitError("No files given to commit.")
    _git(repo, "add", "--", *files)
    _git(repo, "commit", "-F", "-", "--", *files,
         input=message, env={"SURGEON_ACTIVE": "1"})
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


def undo(repo) -> None:
    """Remove the new commits but keep every file edit."""
    state_file = _surgeon_dir(repo) / "state.json"
    if not state_file.exists():
        raise GitError("Nothing to undo: no surgeon backup found.")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    _git(repo, "reset", "--mixed", state["orig_head"])
    print(f"Restored HEAD to {state['orig_head'][:7]} on {state['branch']}. "
          "All file changes are kept.")


def split_branch(repo, base: str) -> str:
    """Un-commit a messy branch back to `base`, keeping all edits.

    Creates a backup branch first. Returns the backup branch name.
    """
    repo = Path(repo)
    _check_state(repo)
    if _git(repo, "status", "--porcelain").stdout.strip():
        raise GitError("Working tree is not clean. Commit or stash first.")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"backup/{branch}-{ts}"
    _git(repo, "branch", backup, "HEAD")
    state = {"orig_head": head, "branch": branch, "patch_path": "",
             "created_at": ts, "pending_split": True, "backup_branch": backup}
    (_surgeon_dir(repo) / "state.json").write_text(json.dumps(state),
                                                   encoding="utf-8")
    _git(repo, "reset", "--mixed", base)
    return backup