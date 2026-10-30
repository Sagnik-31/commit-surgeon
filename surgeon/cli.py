"""Command line interface and approval flow."""
import os
import re
import threading
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from surgeon import diffprep, gitops, grouping, llm, messages, prdraft
from surgeon.gitops import GitError

app = typer.Typer(add_completion=False,
                  help="Split a messy working tree into clean commits, offline.")
console = Console()
PARTS = re.compile(r"^(\w+)(?:\(([^)]*)\))?: (.+)$")


def _fail(msg: str):
    console.print(f"[red]Error:[/red] {msg}")
    raise typer.Exit(1)


def _ask(question: str, choices: list, yes: bool) -> str:
    if yes:
        console.print(f"{question} -> y (auto-approved by --yes)")
        return "y"
    return Prompt.ask(question, choices=choices, default="y")


def _message(plan) -> str:
    text = plan.header()
    if plan.body:
        text += "\n\n" + "\n".join(f"- {b}" for b in plan.body)
    return text + "\n"


def _edit(plan):
    new = Prompt.ask("New header", default=plan.header()).strip()
    m = PARTS.match(new)
    if m and messages.HEADER_RE.match(new):
        plan.type, plan.scope, plan.subject = m.group(1), m.group(2) or "", m.group(3)
    else:
        console.print("[yellow]Not a valid Conventional Commit header. "
                      "Keeping the original.[/yellow]")
    return plan


def _preflight(repo: Path) -> str:
    try:
        gitops._git(repo, "rev-parse", "--is-inside-work-tree")
        gitops._git(repo, "rev-parse", "HEAD")
    except GitError:
        _fail("Not a git repo with at least one commit.")
    except OSError:
        _fail(f"Folder not found: {repo}")
    top = gitops._git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if os.path.normcase(os.path.realpath(top)) != \
            os.path.normcase(os.path.realpath(str(repo))):
        _fail(f"{repo} is not the root of a git repo (found {top}). "
              "Use --repo with the repo's root folder.")
    gitops._check_state(repo)
    email = gitops._git(repo, "config", "user.email", check=False).stdout.strip()
    if not email:
        _fail("Git identity missing. Run: git config user.name / user.email")
    return gitops._git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _run(repo, dry_run, yes, split_branch):
    t_all = time.time()
    branch = _preflight(repo)
    console.print(f"Repo: {repo.resolve()}  branch: {branch}")

    if os.environ.get("SURGEON_MOCK") != "1":
        threading.Thread(target=llm.warmup, daemon=True).start()

    if split_branch:
        if dry_run:
            _fail("--split-branch cannot be combined with --dry-run.")
        backup_branch = gitops.split_branch(repo, split_branch)
        console.print(f"Backup branch created: [bold]{backup_branch}[/bold]")

    staged = gitops._git(repo, "diff", "--cached", "--name-only").stdout.split()
    if staged and not yes and not dry_run:
        console.print(f"[yellow]Already staged: {', '.join(staged)}[/yellow]")
        if not Confirm.ask("Continue?"):
            raise typer.Exit()

    t = time.time()
    changes = gitops.scan_changes(repo)
    usable = [c for c in changes if not c.skipped]
    if not usable:
        console.print("No changes to commit.")
        raise typer.Exit()
    for c in usable:
        c.condensed = diffprep.condense(c)
    by_path = {c.path: c for c in changes}
    groups = grouping.group_changes(changes, use_llm=True)
    console.print(f"[dim]scan + group: {time.time() - t:.1f}s[/dim]")

    table = Table(title=f"Plan: {len(groups)} commit(s)")
    for col in ("#", "Type", "Files", "Why"):
        table.add_column(col)
    for g in groups:
        table.add_row(str(g.id), g.type_hint, "\n".join(g.files), g.reason)
    console.print(table)
    if _ask("Approve this plan?", ["y", "q"], yes) == "q":
        raise typer.Exit()

    approved = []
    for n, g in enumerate(groups, 1):
        t = time.time()
        with console.status(f"Writing message {n}/{len(groups)}..."):
            plan = messages.generate_commit(g, by_path)
        console.print(f"[dim]message {n}: {time.time() - t:.1f}s[/dim]")
        while True:
            note = "\n[yellow](fallback message, no AI)[/yellow]" if plan.used_fallback else ""
            console.print(Panel(_message(plan) + "\nFiles: " + ", ".join(plan.files) + note,
                                title=f"Commit {n}/{len(groups)}"))
            ans = _ask("Approve (y)es / (e)dit / (s)kip / (q)uit",
                       ["y", "e", "s", "q"], yes)
            if ans == "e":
                plan = _edit(plan)
                continue
            break
        if ans == "q":
            console.print("Quit. Nothing was committed.")
            raise typer.Exit()
        if ans == "y":
            approved.append(plan)

    if not approved:
        console.print("Nothing approved. Nothing changed.")
        raise typer.Exit()

    if dry_run:
        console.print("[bold]DRY RUN: nothing was changed[/bold]")
    else:
        info = gitops.make_backup(repo)
        console.print(f"Backup saved: {info.patch_path}")
        for plan in approved:
            sha = gitops.commit_group(repo, plan.files, _message(plan))
            console.print(f"  committed {sha} {plan.header()}")

    t = time.time()
    with console.status("Writing PR description..."):
        pr = prdraft.generate_pr(approved, {
            "branch": branch, "test_cmd": prdraft.detect_test_cmd(repo)})
    console.print(f"[dim]PR draft: {time.time() - t:.1f}s[/dim]")
    console.print(Panel(pr.markdown, title="PR description"
                        + (" (template)" if pr.used_fallback else "")))
    if _ask("Save PR description?", ["y", "n"], yes) == "y" and not dry_run:
        path = gitops._surgeon_dir(repo) / "PR_DESCRIPTION.md"
        path.write_text(pr.markdown, encoding="utf-8", newline="\n")
        console.print(f"Saved: {path}")

    if not dry_run:
        log = gitops._git(repo, "log", "--oneline", "-n",
                          str(len(approved) + 1)).stdout
        console.print(log)
        console.print(f"To undo: python -m surgeon undo --repo {repo}")
    console.print(f"[bold]Total: {time.time() - t_all:.1f}s[/bold]")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    repo: Path = typer.Option(Path("."), "--repo", help="Repo folder."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Change nothing."),
    mock: bool = typer.Option(False, "--mock", help="Canned AI replies."),
    model: str = typer.Option("", "--model", help="Ollama model name."),
    cpu: bool = typer.Option(False, "--cpu", help="Force CPU."),
    yes: bool = typer.Option(False, "--yes", help="Auto-approve every step."),
    split_branch: str = typer.Option("", "--split-branch",
                                     help="Re-split commits made since BASE."),
):
    """Run Commit Surgeon (default)."""
    if ctx.invoked_subcommand is not None:
        return
    if mock:
        os.environ["SURGEON_MOCK"] = "1"
    if model:
        os.environ["SURGEON_MODEL"] = model
    if cpu:
        os.environ["SURGEON_CPU"] = "1"
    try:
        _run(repo, dry_run, yes, split_branch)
    except (llm.LlmError, GitError) as e:
        _fail(str(e))


@app.command()
def undo(repo: Path = typer.Option(Path("."), "--repo")):
    """Remove the commits made by the last run; keep all file edits."""
    try:
        gitops.undo(repo)
    except GitError as e:
        _fail(str(e))