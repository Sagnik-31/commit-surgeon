"""Commit Surgeon CLI — premium terminal experience.

Entry-point:  ``surgeon`` (registered via ``[project.scripts]``).

Commands
--------
surgeon --help          Show usage.
surgeon --version       Print version and exit.
surgeon analyze         Inspect the current repository and working tree.
surgeon group           Analyze changes and propose logical commit groups.
surgeon plan            Generate a reviewable commit plan with messages.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from commit_surgeon import __version__
from commit_surgeon.core.change_analyzer import (
    AnalyzedChange,
    analyze_changes,
)
from commit_surgeon.core.change_grouper import (
    ChangeAnalysis,
    ChangeGroup,
    group_changes,
)
from commit_surgeon.core.commit_message import CommitMessage
from commit_surgeon.core.commit_planner import (
    CommitPlan,
    CommitPlanItem,
    PlanFile,
    create_plan,
)
from commit_surgeon.core.diff_parser import ChangeSet, FileDiff, parse_changes
from commit_surgeon.core.repository import (
    RepositoryInfo,
    RepositoryNotFoundError,
    detect_repository,
)
from commit_surgeon.git import GitNotFoundError
from commit_surgeon.git.status import WorkingTreeStatus, get_working_tree_status

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="surgeon",
    help="⚕  Commit Surgeon — Intelligent Git Workflow Assistant",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

_ACCENT = "cyan"
_OK = "green"
_WARN = "yellow"
_ERR = "bold red"
_DIM = "dim"


# ---------------------------------------------------------------------------
# Version callback
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    """Print the version string and exit."""
    if value:
        console = Console()
        console.print(
            Panel(
                f"[bold {_ACCENT}]v{__version__}[/]",
                title="[bold]⚕ Commit Surgeon[/bold]",
                border_style=_ACCENT,
                padding=(0, 2),
            )
        )
        raise typer.Exit()


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(  # noqa: UP007
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """⚕  Commit Surgeon — Intelligent Git Workflow Assistant."""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    path: Optional[Path] = typer.Option(  # noqa: UP007
        None,
        "--path",
        "-p",
        help="Path to the Git repository.  Defaults to the current directory.",
        exists=False,
    ),
) -> None:
    """Analyze the current Git repository and working tree status."""
    console = Console()

    repo_info = _detect_or_exit(path, console)
    status = get_working_tree_status(repo_info.root)
    change_set = parse_changes(repo_info.root)

    _render_header(console)
    _render_repo_panel(console, repo_info)
    _render_status_panel(console, repo_info, status)
    _render_file_details(console, change_set)
    console.print()


@app.command()
def group(
    path: Optional[Path] = typer.Option(  # noqa: UP007
        None,
        "--path",
        "-p",
        help="Path to the Git repository.  Defaults to the current directory.",
        exists=False,
    ),
) -> None:
    """Propose logical commit groups for the current changes."""
    console = Console()

    repo_info = _detect_or_exit(path, console)
    change_set = parse_changes(repo_info.root)

    if change_set.is_empty:
        _render_header(console)
        _render_repo_panel(console, repo_info)
        console.print()
        console.print(
            Panel(
                f"[{_OK}]✓ Working tree is clean — nothing to group.[/]",
                border_style=_OK,
                padding=(1, 2),
            )
        )
        console.print()
        return

    analysis_result = analyze_changes(change_set)
    result = group_changes(analysis_result, repo_info)

    _render_header(console)
    _render_repo_panel(console, repo_info)
    _render_groups(console, result)
    _render_ungrouped(console, result)
    _render_warnings(console, result)
    console.print()


@app.command()
def plan(
    path: Optional[Path] = typer.Option(  # noqa: UP007
        None,
        "--path",
        "-p",
        help="Path to the Git repository.  Defaults to the current directory.",
        exists=False,
    ),
    output: Optional[Path] = typer.Option(  # noqa: UP007
        None,
        "--output",
        "-o",
        help="Export the plan to a JSON file.",
    ),
) -> None:
    """Generate a reviewable commit plan with conventional commit messages."""
    console = Console()

    repo_info = _detect_or_exit(path, console)
    change_set = parse_changes(repo_info.root)

    if change_set.is_empty:
        _render_header(console)
        _render_repo_panel(console, repo_info)
        console.print()
        console.print(
            Panel(
                f"[{_OK}]✓ Working tree is clean — nothing to plan.[/]",
                border_style=_OK,
                padding=(1, 2),
            )
        )
        console.print()
        return

    analysis_result = analyze_changes(change_set)
    change_analysis = group_changes(analysis_result, repo_info)
    commit_plan = create_plan(change_analysis)

    # --- JSON export -------------------------------------------------------
    if output is not None:
        if output.exists():
            console.print(
                f"[{_ERR}]✗ Output file already exists: {output}[/]"
            )
            console.print(
                "  Remove it first or choose a different path."
            )
            raise typer.Exit(code=1)

        json_data = commit_plan.model_dump_json(indent=2)
        output.write_text(json_data, encoding="utf-8")
        console.print(f"[{_OK}]✓ Plan exported to {output}[/]")
        console.print()

    # --- Terminal rendering ------------------------------------------------
    _render_header(console)
    _render_repo_panel(console, repo_info)
    _render_plan_items(console, commit_plan)
    _render_plan_unassigned_staged(console, commit_plan)
    _render_plan_unstaged(console, commit_plan)
    _render_plan_untracked(console, commit_plan)
    _render_plan_summary(console, commit_plan)
    console.print()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------



def _detect_or_exit(path: Path | None, console: Console) -> RepositoryInfo:
    """Detect the repository or print an error and exit."""
    try:
        return detect_repository(path)
    except GitNotFoundError as exc:
        console.print(f"[{_ERR}]✗ {exc}[/]")
        raise typer.Exit(code=1) from exc
    except RepositoryNotFoundError as exc:
        console.print(f"[{_ERR}]✗ {exc}[/]")
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# Renderers — shared
# ---------------------------------------------------------------------------


def _render_header(console: Console) -> None:
    """Top-level banner."""
    console.print()
    console.print(
        Panel(
            "[bold]Intelligent Git Workflow Assistant[/bold]",
            title=f"[bold {_ACCENT}]⚕ Commit Surgeon[/bold {_ACCENT}]",
            border_style=_ACCENT,
            padding=(1, 2),
        )
    )


def _render_repo_panel(console: Console, info: RepositoryInfo) -> None:
    """Repository metadata panel."""
    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column("Key", style="bold dim", width=14)
    tbl.add_column("Value")

    tbl.add_row("Repository", f"[bold]{info.root.name}[/bold]")

    if info.is_empty:
        branch_display = info.branch or "main"
        tbl.add_row("Branch", f"[{_WARN}]{branch_display}[/] [{_DIM}](empty)[/]")
    elif info.is_detached:
        short = info.commit_hash[:8] if info.commit_hash else "unknown"
        tbl.add_row("HEAD", f"[{_WARN}]{short}[/] [{_DIM}](detached)[/]")
    else:
        tbl.add_row("Branch", f"[{_OK}]{info.branch}[/]")

    if info.commit_hash:
        tbl.add_row("Commit", f"[{_DIM}]{info.commit_hash[:12]}[/]")

    console.print()
    console.print(
        Panel(tbl, title="[bold]Repository[/bold]", border_style="blue", padding=(1, 2))
    )


# ---------------------------------------------------------------------------
# Renderers — analyze
# ---------------------------------------------------------------------------


def _render_status_panel(
    console: Console,
    info: RepositoryInfo,
    status: WorkingTreeStatus,
) -> None:
    """Working-tree status summary panel."""
    if info.is_empty and status.is_clean:
        console.print()
        console.print(
            Panel(
                f"[{_DIM}]Empty repository — no commits yet.[/]",
                title="[bold]Working Tree[/bold]",
                border_style=_WARN,
                padding=(1, 2),
            )
        )
        return

    if status.is_clean:
        console.print()
        console.print(
            Panel(
                f"[{_OK}]✓ Working tree is clean[/]",
                title="[bold]Working Tree[/bold]",
                border_style=_OK,
                padding=(1, 2),
            )
        )
        return

    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column("Category", style="bold", width=14)
    tbl.add_column("Count", justify="right", width=6)
    tbl.add_column("Details", style="dim")

    _add_status_row(tbl, "Staged", status.staged, _OK)

    modified = [c for c in status.unstaged if c.status.value != "deleted"]
    deleted = [c for c in status.unstaged if c.status.value == "deleted"]

    _add_count_row(tbl, "Modified", len(modified), _WARN)
    _add_count_row(tbl, "Deleted", len(deleted), "red")
    _add_count_row(tbl, "Untracked", len(status.untracked), "blue")
    _add_count_row(tbl, "Conflicts", len(status.conflicts), _ERR)

    total = status.total_changed
    border = "red" if status.conflicts else _WARN

    console.print()
    console.print(
        Panel(
            tbl,
            title="[bold]Working Tree[/bold]",
            subtitle=f"[{_DIM}]{total} total change{'s' if total != 1 else ''}[/]",
            border_style=border,
            padding=(1, 2),
        )
    )


def _render_file_details(console: Console, cs: ChangeSet) -> None:
    """Render individual changed files with diff stats."""
    if cs.is_empty:
        return

    tbl = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    tbl.add_column("Marker", width=3)
    tbl.add_column("File")
    tbl.add_column("Stats", justify="right", style="dim", width=12)

    # Staged
    if cs.staged:
        tbl.add_row(f"[{_DIM}]───[/]", f"[bold {_DIM}]Staged[/]", "")
        for f in cs.staged:
            _add_file_row(tbl, f, _OK)

    # Unstaged
    if cs.unstaged:
        tbl.add_row(f"[{_DIM}]───[/]", f"[bold {_DIM}]Unstaged[/]", "")
        for f in cs.unstaged:
            color = "red" if f.status.value == "deleted" else _WARN
            _add_file_row(tbl, f, color)

    # Untracked
    if cs.untracked:
        tbl.add_row(f"[{_DIM}]───[/]", f"[bold {_DIM}]Untracked[/]", "")
        for f in cs.untracked:
            tbl.add_row("[blue]?[/]", f"[blue]{f.path}[/]", "")

    console.print()
    console.print(
        Panel(tbl, title="[bold]Changed Files[/bold]", border_style="magenta", padding=(1, 2))
    )


# ---------------------------------------------------------------------------
# Renderers — group
# ---------------------------------------------------------------------------


def _render_groups(console: Console, result: ChangeAnalysis) -> None:
    """Render each change group as a Rich panel."""
    if not result.groups:
        console.print()
        console.print(
            Panel(
                f"[{_WARN}]No logical groups could be formed.[/]",
                title="[bold]Groups[/bold]",
                border_style=_WARN,
                padding=(1, 2),
            )
        )
        return

    for grp in result.groups:
        _render_single_group(console, grp)


def _render_single_group(console: Console, grp: ChangeGroup) -> None:
    """Render one group panel."""
    tbl = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    tbl.add_column("Marker", width=3)
    tbl.add_column("File")
    tbl.add_column("Stats", justify="right", style="dim", width=12)

    for ac in grp.files:
        f = ac.diff
        if f.is_staged:
            color = _OK
        elif f.is_untracked:
            color = "blue"
        else:
            color = _WARN
        _add_file_row(tbl, f, color)

    # Metadata line
    meta_parts: list[str] = [
        f"[bold]Category:[/] {grp.category}",
        f"[bold]Confidence:[/] {grp.confidence}",
    ]
    if grp.requires_manual_review:
        meta_parts.append(f"[{_WARN}]⚠ Needs manual review[/]")

    meta = "  │  ".join(meta_parts)

    review_marker = f"  [{_WARN}]⚠[/]" if grp.requires_manual_review else ""
    title_text = f"[bold]{grp.title}[/bold]  [{_DIM}][{grp.category}][/]{review_marker}"

    content_parts = [tbl, "", f"[{_DIM}]{grp.explanation}[/]", meta]

    # Build a renderable group
    from rich.text import Text
    from rich.console import Group as RichGroup

    renderables = [
        tbl,
        Text(),
        Text(grp.explanation, style="dim"),
        Text.from_markup(meta),
    ]

    console.print()
    console.print(
        Panel(
            RichGroup(*renderables),
            title=title_text,
            border_style=_ACCENT,
            padding=(1, 2),
        )
    )


def _render_ungrouped(console: Console, result: ChangeAnalysis) -> None:
    """Render ungrouped files."""
    if not result.ungrouped_files:
        return

    tbl = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    tbl.add_column("Marker", width=3)
    tbl.add_column("File")
    tbl.add_column("Stats", justify="right", style="dim", width=12)

    for ac in result.ungrouped_files:
        f = ac.diff
        color = _OK if f.is_staged else ("blue" if f.is_untracked else _WARN)
        _add_file_row(tbl, f, color)

    console.print()
    console.print(
        Panel(
            tbl,
            title=f"[bold]Ungrouped Files[/bold]  [{_DIM}]({len(result.ungrouped_files)})[/]",
            border_style=_WARN,
            padding=(1, 2),
        )
    )


def _render_warnings(console: Console, result: ChangeAnalysis) -> None:
    """Render any warnings."""
    for warning in result.warnings:
        console.print()
        console.print(f"  [{_WARN}]⚠ {warning}[/]")


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

_STATUS_MARKERS: dict[str, str] = {
    "modified": "M",
    "added": "A",
    "deleted": "D",
    "renamed": "R",
    "copied": "C",
    "type_changed": "T",
}


def _add_file_row(tbl: Table, f: FileDiff, color: str) -> None:
    """Add one file row with status marker and optional diff stats."""
    marker = _STATUS_MARKERS.get(f.status.value, "?")
    path_display = f.path
    if f.old_path:
        path_display = f"{f.old_path} → {f.path}"

    stats = ""
    if f.additions or f.deletions:
        stats = f"[green]+{f.additions}[/] [red]-{f.deletions}[/]"

    tbl.add_row(f"[{color}]{marker}[/]", f"[{color}]{path_display}[/]", stats)


def _add_status_row(
    tbl: Table,
    label: str,
    changes: list,
    style: str,
) -> None:
    """Add a row with a count and breakdown of change types."""
    count = len(changes)
    active = style if count > 0 else _DIM
    details = ""
    if count > 0:
        counts = Counter(c.status.value for c in changes)
        details = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    tbl.add_row(f"[{active}]{label}[/]", f"[{active}]{count}[/]", details)


def _add_count_row(tbl: Table, label: str, count: int, style: str) -> None:
    """Add a simple count row."""
    active = style if count > 0 else _DIM
    tbl.add_row(f"[{active}]{label}[/]", f"[{active}]{count}[/]", "")


# ---------------------------------------------------------------------------
# Renderers — plan
# ---------------------------------------------------------------------------

_CONFIDENCE_COLORS: dict[str, str] = {
    "high": _OK,
    "medium": _WARN,
    "low": "red",
}


def _render_plan_items(console: Console, plan: CommitPlan) -> None:
    """Render each proposed commit (staged changes only)."""
    if not plan.commits:
        msg = f"[{_WARN}]No staged changes to propose as commits.[/]"
        if plan.unstaged_changes or plan.untracked_files:
            msg += (
                f"\n\n[{_DIM}]Stage changes with 'git add' to include "
                "them in a commit plan.[/]"
            )
        console.print()
        console.print(
            Panel(
                msg,
                title="[bold]Proposed Commits[/bold]",
                border_style=_WARN,
                padding=(1, 2),
            )
        )
        return

    for i, item in enumerate(plan.commits, start=1):
        _render_plan_item(console, item, i)


def _render_plan_item(console: Console, item: CommitPlanItem, index: int) -> None:
    """Render a single plan item as a Rich panel."""
    from rich.text import Text
    from rich.console import Group as RichGroup

    msg = item.message
    conf_color = _CONFIDENCE_COLORS.get(msg.confidence.value, _DIM)

    # File table.
    tbl = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    tbl.add_column("Marker", width=3)
    tbl.add_column("File")
    tbl.add_column("Stats", justify="right", style="dim", width=12)

    for pf in item.files:
        marker = _STATUS_MARKERS.get(pf.status, "?")
        color = _OK
        if pf.has_unstaged_changes:
            color = _WARN
        path_display = pf.path
        if pf.old_path:
            path_display = f"{pf.old_path} -> {pf.path}"
        stats = ""
        if pf.additions or pf.deletions:
            stats = f"[green]+{pf.additions}[/] [red]-{pf.deletions}[/]"
        tbl.add_row(f"[{color}]{marker}[/]", f"[{color}]{path_display}[/]", stats)

    # Status line.
    review_status = (
        f"[{_WARN}]Needs review[/]"
        if item.requires_manual_review
        else f"[{_OK}]Ready[/]"
    )
    conf_label = f"[{conf_color}]{msg.confidence.value.title()}[/]"
    meta = Text.from_markup(
        f"[bold]Confidence:[/] {conf_label}  |  [bold]Status:[/] {review_status}"
    )

    # Warnings.
    warn_lines: list[Text] = []
    for w in item.warnings:
        warn_lines.append(Text.from_markup(f"  [{_WARN}]! {w}[/]"))

    renderables = [tbl, Text(), Text(item.summary, style="dim"), meta]
    renderables.extend(warn_lines)

    # Title — handle None type.
    if msg.type is None:
        title_text = (
            f"[bold][{index}] {msg.full_message}[/bold]"
            f"  [{_WARN}]← select: feat | fix | refactor[/]"
        )
    else:
        title_text = f"[bold][{index}] {msg.full_message}[/bold]"

    console.print()
    console.print(
        Panel(
            RichGroup(*renderables),
            title=title_text,
            border_style=conf_color,
            padding=(1, 2),
        )
    )


def _render_plan_unassigned_staged(console: Console, plan: CommitPlan) -> None:
    """Render staged files not assigned to any group."""
    if not plan.unassigned_staged:
        return

    tbl = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    tbl.add_column("Marker", width=3)
    tbl.add_column("File")

    for pf in plan.unassigned_staged:
        marker = _STATUS_MARKERS.get(pf.status, "?")
        tbl.add_row(f"[{_OK}]{marker}[/]", f"[{_OK}]{pf.path}[/]")

    console.print()
    console.print(
        Panel(
            tbl,
            title=(
                f"[bold]Unassigned Staged Files[/bold]"
                f"  [{_DIM}]({len(plan.unassigned_staged)})[/]"
            ),
            border_style=_WARN,
            padding=(1, 2),
        )
    )


def _render_plan_unstaged(console: Console, plan: CommitPlan) -> None:
    """Render unstaged changes excluded from commits."""
    if not plan.unstaged_changes:
        return

    from rich.text import Text
    from rich.console import Group as RichGroup

    tbl = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    tbl.add_column("Marker", width=3)
    tbl.add_column("File")

    for pf in plan.unstaged_changes:
        marker = _STATUS_MARKERS.get(pf.status, "?")
        tbl.add_row(f"[{_WARN}]{marker}[/]", f"[{_WARN}]{pf.path}[/]")

    note = Text.from_markup(
        f"\n[{_DIM}]These changes are not in the Git index and are "
        "excluded from proposed commits.[/]"
    )

    console.print()
    console.print(
        Panel(
            RichGroup(tbl, note),
            title=(
                f"[bold]Unstaged Changes[/bold]"
                f"  [{_DIM}]({len(plan.unstaged_changes)})[/]"
            ),
            border_style=_WARN,
            padding=(1, 2),
        )
    )


def _render_plan_untracked(console: Console, plan: CommitPlan) -> None:
    """Render untracked files."""
    if not plan.untracked_files:
        return

    from rich.text import Text
    from rich.console import Group as RichGroup

    tbl = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    tbl.add_column("Marker", width=3)
    tbl.add_column("File")

    for pf in plan.untracked_files:
        tbl.add_row(f"[blue]?[/]", f"[blue]{pf.path}[/]")

    note = Text.from_markup(
        f"\n[{_DIM}]Stage with 'git add' to include in a commit plan.[/]"
    )

    console.print()
    console.print(
        Panel(
            RichGroup(tbl, note),
            title=(
                f"[bold]Untracked Files[/bold]"
                f"  [{_DIM}]({len(plan.untracked_files)})[/]"
            ),
            border_style="blue",
            padding=(1, 2),
        )
    )


def _render_plan_summary(console: Console, plan: CommitPlan) -> None:
    """Plan summary panel with safety footer."""
    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column("Key", style="bold", width=20)
    tbl.add_column("Value", justify="right")

    tbl.add_row("Proposed commits", f"[bold]{len(plan.commits)}[/]")
    tbl.add_row(
        "Unassigned staged",
        f"[{_WARN if plan.unassigned_staged else _DIM}]"
        f"{len(plan.unassigned_staged)}[/]",
    )
    tbl.add_row(
        "Unstaged changes",
        f"[{_WARN if plan.unstaged_changes else _DIM}]"
        f"{len(plan.unstaged_changes)}[/]",
    )
    tbl.add_row(
        "Untracked files",
        f"[{_WARN if plan.untracked_files else _DIM}]"
        f"{len(plan.untracked_files)}[/]",
    )
    tbl.add_row(
        "Warnings",
        f"[{_WARN if plan.warnings else _DIM}]{len(plan.warnings)}[/]",
    )

    from rich.text import Text
    from rich.console import Group as RichGroup

    footer = Text.from_markup(
        f"\n[{_OK}]✓ Nothing has been staged or committed.[/]\n"
        f"[{_DIM}]Commit plans represent the currently staged snapshot. "
        "Unstaged changes are excluded.[/]"
    )

    warn_items: list[Text] = []
    for w in plan.warnings:
        warn_items.append(Text.from_markup(f"  [{_WARN}]! {w}[/]"))

    parts = [tbl]
    if warn_items:
        parts.append(Text())
        parts.extend(warn_items)
    parts.append(footer)

    console.print()
    console.print(
        Panel(
            RichGroup(*parts),
            title="[bold]Plan Summary[/bold]",
            border_style="blue",
            padding=(1, 2),
        )
    )

