# Commit Surgeon

Commit Surgeon splits a messy git working tree (or one big "fixed stuff" commit) into small Conventional Commits and drafts a PR description. It uses a local Ollama model, runs fully offline, and only calls localhost.

## Requirements and Setup

Requirements (Windows tested):

- Git 2.30+
- Python 3.10+
- Ollama (latest)

Setup:

```powershell
ollama pull qwen3.5:4b
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Note: use `python -m pip`, not `pip`, on locked-down Windows machines.

## Usage

```powershell
python -m surgeon --repo <folder>
```

Run with approval at every step.

### Flags

- `--dry-run` — show everything, commit nothing
- `--mock` — canned replies, no model needed
- `--model` — Ollama model name (default qwen3.5:4b)
- `--cpu` — force CPU
- `--yes` — auto-approve every step, still prints every step
- `--split-branch BASE` — re-split an already-committed messy branch; creates a backup/<branch>-<timestamp> branch first

### Undo

```powershell
python -m surgeon undo --repo <folder>
```

Remove the new commits, keep all file edits.

### Git Hook

```powershell
python -m surgeon install-hook --repo <folder>
python -m surgeon uninstall-hook --repo <folder>
```

Install or remove a pre-commit hint.

### Approval Keys

At each commit: (y)es / (e)dit / (s)kip / (q)uit.

## How It Works

1. **Scan**: `git status -z`
2. **Condense diffs**
3. **Rule-based grouping**: docs and config groups; tests follow their source file; heuristics for fix/refactor/feat; a deleted file plus a similar new file merged into one rename; optional one-word model label when unsure
4. **Plan approval**
5. **Model writes each commit message as JSON**
6. **Validate/auto-fix/retry once/fallback without AI**
7. **Per-commit approval**
8. **Backup**
9. **Commit**: `git add <explicit files>` + `git commit -F -`
10. **PR description**: four headings validated, template fallback
11. **Save**: `.git/surgeon/PR_DESCRIPTION.md`

## The Git Hook

A pre-commit hook that prints "This commit mixes several concerns. Try: python -m surgeon" when more than 8 files are staged or docs and non-docs files are staged together.

- It never blocks a commit (always exits 0).
- Skipped when `SURGEON_ACTIVE=1` (set by surgeon's own commits).
- Refuses to overwrite a hook it did not create.
- Does not call the model; it only suggests running surgeon.

## Keeping a Small Model Reliable

- Thinking off via top-level `"think": false` on `/api/chat`
- JSON schema output
- Two few-shot examples
- Temperature 0.2
- Token caps: 160 (commit), 400 (PR)
- Validation with one retry and a non-AI fallback
- PR prompt forbids invented motives

## Safety

- Only `git add` (explicit paths, never `-A`) and `git commit`
- Backup of HEAD plus a patch before the first commit
- `undo` uses `git reset --mixed` to the saved HEAD so no file is changed
- `--split-branch` makes a backup branch
- Refuses to run during merge/rebase/cherry-pick
- Refuses a folder that is not a git repo root
- Skips lockfiles, binaries, files over 200 KB and likely secrets (`.env`, `*.pem`, `id_rsa`, `node_modules`)
- Never uses `reset --hard`, `clean`, `checkout --`, `restore`, `push` or `rebase`

## Measured Results

Sandbox repo, 3 commits, `--yes`.

| Setup | Total | Commit messages | PR description |
|-------|-------|-----------------|----------------|
| qwen3.5:4b on GPU | 8-10 s | 0.7-1.3 s each | 3-4.4 s |
| qwen3.5:4b on CPU (`--cpu`) | 84.8 s | 27.1 s (includes cold start), 11.5 s, 12.0 s | 32.1 s |
| qwen3.5:2b on CPU (`--cpu`) | 67.7 s | 19.8 s, 7.5 s, 7.9 s | 30.3 s |
| qwen3.5:2b on GPU | 16.6 s | 9.3 s (cold start), 0.5 s, 0.7 s | 4.4 s |

Times are model and git time for a run with `--yes`; approvals are human time. The first call after idle can take 10-20 s while the model loads; the tool warms it in the background. Measured on one Windows laptop; your numbers will differ.

Hardware: CPU:Ryzen 7 8845HS, IGPU:Radeon 780 Graphics, DGPU:RTX 4060

qwen3.5:4b is the recommended model (4B parameters, about 3.3 GB download, 3.1 GB in memory while running with a 4096 context, measured with `ollama ps` on a GPU). 2B is only about 20% faster on CPU, and its output was weaker: it once produced a subject ending in a dangling word, described a fix as "add test", and its PR text invented a test name, a command and motives such as "improves code organization". Treat 2B as an emergency option and read every step. Even 4B occasionally adds a phrase like "for clarity" in the PR "Why" section, which is why the PR is shown for approval.

## Offline Check

The code only calls `http://127.0.0.1:11434` (Ollama). With Wi-Fi off it still works once the model and Python packages are installed.

Before a demo on a machine with a GPU, run `ollama ps` and confirm the model shows 100% GPU and no other model is loaded; a stale CPU-loaded model makes runs about 10 times slower.

## Limitations

- Grouping is per file, not per hunk: two concerns inside one file end up in one commit.
- Two different fixes in the same area merge into one group.
- On CPU, message quality can drop (a fix commit was once described as "add test" instead of the guard).
- Memory use was measured on a GPU run; CPU-only RAM use and a real 8 GB machine are not yet tested.
- Tested on Windows only; macOS and Linux not yet tested.
- The hook only suggests; it does not split commits or call the model.
- A small model can still be wrong, which is why every step needs approval.

## Tests and Sandbox

```powershell
python -m pytest tests -q
python scripts\make_messy_repo.py [--committed] [--hard]
```

Builds `./sandbox_messy`.
