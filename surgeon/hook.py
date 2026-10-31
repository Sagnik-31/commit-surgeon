"""Non-blocking pre-commit hook that suggests running surgeon."""
from pathlib import Path

from surgeon import gitops

MARKER = "# surgeon-hook"
SCRIPT = r"""#!/bin/sh
# surgeon-hook
[ "$SURGEON_ACTIVE" = "1" ] && exit 0
files=$(git diff --cached --name-only)
count=$(printf '%s\n' "$files" | grep -c .)
docs=$(printf '%s\n' "$files" | grep -E '\.(md|rst)$|^docs/' | head -n 1)
src=$(printf '%s\n' "$files" | grep -Ev '\.(md|rst)$|^docs/' | grep . | head -n 1)
if [ "$count" -gt 8 ] || { [ -n "$docs" ] && [ -n "$src" ]; }; then
  echo "This commit mixes several concerns. Try: python -m surgeon" >&2
fi
exit 0
"""


def _hook_path(repo) -> Path:
    return gitops._git_dir(repo) / "hooks" / "pre-commit"


def install_hook(repo) -> str:
    """Write the hook. Refuse to overwrite a hook surgeon did not create."""
    path = _hook_path(repo)
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="replace")
        if MARKER not in old:
            raise gitops.GitError(
                "A pre-commit hook already exists and was not made by "
                "surgeon. Not overwriting it.")
    path.write_text(SCRIPT, encoding="utf-8", newline="\n")
    try:
        path.chmod(0o755)
    except OSError:
        pass
    return str(path)


def uninstall_hook(repo) -> bool:
    """Remove the hook only if surgeon made it."""
    path = _hook_path(repo)
    if path.exists() and MARKER in path.read_text(
            encoding="utf-8", errors="replace"):
        path.unlink()
        return True
    return False