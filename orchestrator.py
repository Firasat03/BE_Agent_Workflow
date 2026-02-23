"""
orchestrator.py — Central Pipeline Controller for BE Multi-Agent Workflow

The Orchestrator is NOT an agent (it makes no LLM calls itself).
It is the rule-based controller that:
  1. Loads user rules (RULES.md)
  2. Drives agents in the correct order
  3. Pauses at the Human Plan Approval gate (shows Task Checklist + Plan)
  4. Manages the Reviewer retry loop (Coder → Reviewer → Coder if REJECT)
  5. Manages the Debugger retry loop (Tester → Debugger → Coder if FAIL)
  6. Saves checkpoints after each agent
  7. Escalates to human on max retry exceeded
  8. Optionally runs the DevOps agent when state.devops_mode is set

Agent execution order:
  Architect → [Human Gate] → Coder → Reviewer → Tester → Debugger → Writer → DevOps(opt-in)
"""

from __future__ import annotations

import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

from config import MAX_DEBUG_RETRIES, MAX_INTEGRATION_RETRIES, MAX_REVIEW_RETRIES, Status
from state import PipelineState
from tools.rules_loader import load_rules
from tools.checkpoint_tools import save_checkpoint

from agents.architect_agent import ArchitectAgent
from agents.coder_agent import CoderAgent
from agents.reviewer_agent import ReviewerAgent
from agents.tester_agent import TesterAgent
from agents.debugger_agent import DebuggerAgent
from agents.integration_agent import IntegrationAgent
from agents.writer_agent import WriterAgent
from agents.devops_agent import DevOpsAgent

console = Console()
_step = 0  # global step counter for checkpoint naming


def _checkpoint(state: PipelineState, agent_name: str) -> None:
    global _step
    _step += 1
    path = save_checkpoint(state, agent_name, _step)
    console.log(f"[dim]💾 Checkpoint saved: {path}[/dim]")


# ─── Human Plan Approval Gate ─────────────────────────────────────────────────

def _human_plan_approval(state: PipelineState) -> PipelineState:
    """
    Present the Architect's Task Checklist + structured plan to the user.
    Wait for approval (A), revision request (C), or abort (X).
    Returns state with plan_approved=True, or with user_feedback set for re-planning.
    """

    # ── Task Checklist ────────────────────────────────────────────────────
    if state.task_checklist:
        console.print(Panel(
            f"[bold cyan]📋 IMPLEMENTATION TASK CHECKLIST[/bold cyan]\n\n"
            f"{state.task_checklist}",
            title=f"[bold]Architect's Task Breakdown — Run {state.run_id}[/bold]",
            border_style="cyan",
        ))

    # ── Plan Summary ──────────────────────────────────────────────────────
    console.print(Panel(
        f"[bold yellow]🗺️  ARCHITECT'S PLAN SUMMARY[/bold yellow]\n\n{state.plan_summary}",
        title="[bold]Plan Summary[/bold]",
        border_style="yellow",
    ))

    # ── Structured plan table ─────────────────────────────────────────────
    table = Table(title="Files to be created / modified", show_lines=True, min_width=80)
    table.add_column("Action",       style="cyan",  width=8)
    table.add_column("File",         style="white")
    table.add_column("API Contract", style="green")
    table.add_column("Scope",        style="dim",   width=10)
    table.add_column("Description",  style="dim")
    for item in state.plan:
        table.add_row(
            item.action,
            item.file,
            item.api_contract or "—",
            item.scope_estimate or "—",
            item.description[:80] + ("..." if len(item.description) > 80 else ""),
        )
    console.print(table)

    # ── Human decision loop ───────────────────────────────────────────────
    while True:
        console.print("\n[bold]What would you like to do?[/bold]")
        console.print("  [green][A][/green] Approve plan and start coding")
        console.print("  [yellow][C][/yellow] Request changes to the plan")
        console.print("  [red][X][/red]   Abort workflow")
        choice = input("\nYour choice (A/C/X): ").strip().upper()

        if choice == "A":
            state.plan_approved = True
            state.user_feedback = None
            console.print("[green]✅ Plan approved! Starting Coder agent...[/green]")
            return state

        elif choice == "C":
            console.print("\nDescribe the changes you want (be specific):")
            lines = []
            console.print("[dim](Press Enter twice to submit)[/dim]")
            while True:
                line = input("> ")
                if line == "" and lines and lines[-1] == "":
                    break
                lines.append(line)
            feedback = "\n".join(lines).strip()
            state.user_feedback = feedback
            state.plan_approved = False
            console.print("[yellow]✏️  Feedback recorded. Re-running Architect...[/yellow]")
            return state

        elif choice == "X":
            console.print("[red]🛑 Workflow aborted by user.[/red]")
            state.status = Status.ABORTED
            return state

        else:
            console.print("[red]❌ Invalid choice. Enter A, C, or X.[/red]")


# ─── Main Orchestrator ────────────────────────────────────────────────────────

def run(
    task_prompt: str,
    project_root: str,
    rules_file: str | None = None,
    existing_state: PipelineState | None = None,
    devops_mode: str | None = None,
    language: str = "auto",
) -> PipelineState:
    """
    Run the full multi-agent BE pipeline.

    Agent order:
        Architect → [Human Approval Gate] → Coder → Reviewer
        → Tester → Debugger (loop) → Writer → DevOps (opt-in)

    Args:
        task_prompt:    The user's natural language task description.
        project_root:   Absolute path to the target project directory.
        rules_file:     Optional path to a RULES.md file. Falls back to default.
        existing_state: Pre-loaded state for --resume mode.
        devops_mode:    "docker" | "k8s" | "all" | None (skip DevOps agent).
        language:       Target language: python|java|nodejs|go|... or "auto".

    Returns:
        Final PipelineState.
    """
    global _step
    _step = 0

    # ── Initialise state ──────────────────────────────────────────────────
    if existing_state:
        state = existing_state
        # Honour CLI overrides even on resume
        if devops_mode and not state.devops_mode:
            state.devops_mode = devops_mode
        if language and language != "auto" and state.language == "auto":
            state.language = language
        console.print(f"[cyan]▶ Resuming run {state.run_id} from status: {state.status}[/cyan]")
    else:
        state = PipelineState(
            task_prompt=task_prompt,
            project_root=project_root,
            devops_mode=devops_mode,
            language=language,
        )
        state.user_rules = load_rules(rules_file)
        state.active_rules_file = str(rules_file or "rules/RULES.md")

    console.print(Panel(
        f"[bold cyan]🚀 Multi-Agent BE Workflow[/bold cyan]\n"
        f"Run ID:   [bold]{state.run_id}[/bold]\n"
        f"Task:     {task_prompt[:120]}\n"
        f"Language: {state.language}\n"
        f"Rules:    {state.active_rules_file}\n"
        f"DevOps:   {state.devops_mode or 'disabled (use --devops to enable)'}",
        border_style="cyan",
    ))

    # ── Instantiate agents ────────────────────────────────────────────────
    architect   = ArchitectAgent()
    coder        = CoderAgent()
    reviewer     = ReviewerAgent()
    tester       = TesterAgent()
    debugger     = DebuggerAgent()
    integrator   = IntegrationAgent()
    writer       = WriterAgent()
    devops       = DevOpsAgent()

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 1 — Architect → Human Plan Approval Gate
    # ═══════════════════════════════════════════════════════════════════════
    if state.status in (Status.INIT, Status.ARCHITECT, Status.PLAN_REVIEW):
        while True:
            console.rule("[bold blue]🏛️  Stage 1 — Architect Agent[/bold blue]")
            state = architect.run(state)
            _checkpoint(state, "architect")

            # Human approval gate — shows checklist + plan
            state = _human_plan_approval(state)
            state.status = Status.PLAN_REVIEW
            _checkpoint(state, "plan_review")

            if state.status == Status.ABORTED:
                _print_summary(state)
                return state

            if state.plan_approved:
                break  # ✅ proceed to Coder
            # else: user requested changes → loop back to Architect

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 2 — Coder → Reviewer (with MAX_REVIEW_RETRIES retry)
    # ═══════════════════════════════════════════════════════════════════════
    if state.status in (Status.PLAN_REVIEW, Status.CODING, Status.REVIEWING):
        review_attempts = state.review_retry_count

        while True:
            console.rule("[bold green]💻 Stage 2a — Coder Agent[/bold green]")
            state = coder.run(state)
            _checkpoint(state, "coder")

            console.rule("[bold magenta]🔍 Stage 2b — Reviewer Agent[/bold magenta]")
            state = reviewer.run(state)
            _checkpoint(state, "reviewer")

            verdict = _reviewer_verdict(state.review_notes or "")

            if verdict == "PASS":
                console.print("[green]✅ Code review passed![/green]")
                break
            elif review_attempts >= MAX_REVIEW_RETRIES:
                console.print(
                    f"[yellow]⚠ Reviewer rejected code {review_attempts + 1}x. "
                    "Proceeding to Tester anyway.[/yellow]"
                )
                break
            else:
                review_attempts += 1
                state.review_retry_count = review_attempts
                console.print(
                    f"[yellow]🔄 Reviewer REJECT #{review_attempts}. "
                    "Sending back to Coder with review notes...[/yellow]"
                )
                state.fix_instructions = (
                    f"The code reviewer rejected the code. Fix ALL of these issues:\n"
                    f"{state.review_notes}"
                )

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 3 — Tester (unit tests) → Debugger Loop
    # Only enter if coming from REVIEWING/CODING or already in test/debug cycle.
    # ══════════════════════════════════════════════════════════════════════
    if state.status in (Status.REVIEWING, Status.CODING, Status.TESTING,
                        Status.DEBUGGING, Status.INTEGRATION):
        attempts = state.retry_count

        while True:
            # ── 3a: Unit / static tests ───────────────────────────────────
            console.rule(
                f"[bold blue]Unit Tests — attempt {attempts + 1}/{MAX_DEBUG_RETRIES + 1}[/bold blue]"
            )
            state = tester.run(state)
            _checkpoint(state, f"tester_attempt{attempts + 1}")

            if not state.test_passed():
                # Unit tests failed → Debugger → Coder → retry
                if attempts >= MAX_DEBUG_RETRIES:
                    console.print(
                        f"[red]Max debug retries ({MAX_DEBUG_RETRIES}) reached after unit "
                        "tests. Escalating to human review.[/red]"
                    )
                    state.status = Status.FAILED
                    _print_summary(state)
                    return state

                console.print("[red]Unit tests failed — invoking Debugger...[/red]")
                console.rule(f"[bold red]Debugger — cycle {attempts + 1}[/bold red]")
                state = debugger.run(state)
                _checkpoint(state, f"debugger_unit_{attempts + 1}")

                if state.status == Status.FAILED:
                    _print_summary(state)
                    return state

                console.print("[yellow]Applying fix via Coder...[/yellow]")
                state = coder.run(state)
                _checkpoint(state, f"coder_fix_unit_{attempts + 1}")
                attempts = state.retry_count
                continue  # back to unit tests

            # Unit tests passed
            console.print("[green]All unit / static tests passed.[/green]")

            # ── 3b: Integration tests (build → run server → curl) ────────
            console.rule("[bold cyan]Integration Tests — Build + Live Endpoint Check[/bold cyan]")
            state = integrator.run(state)
            _checkpoint(state, f"integration_attempt{attempts + 1}")

            if state.integration_passed:
                console.print("[green bold]All integration tests passed.[/green bold]")
                break  # proceed to Writer

            # Integration failed → Debugger → Coder → back to top
            if attempts >= MAX_DEBUG_RETRIES:
                console.print(
                    f"[red]Max retries ({MAX_DEBUG_RETRIES}) reached after integration "
                    "tests. Escalating to human review.[/red]"
                )
                state.status = Status.FAILED
                _print_summary(state)
                return state

            console.print("[red]Integration tests failed — invoking Debugger...[/red]")
            console.rule(f"[bold red]Debugger — integration cycle {attempts + 1}[/bold red]")
            state = debugger.run(state)
            _checkpoint(state, f"debugger_integration_{attempts + 1}")

            if state.status == Status.FAILED:
                _print_summary(state)
                return state

            console.print("[yellow]Applying integration fix via Coder...[/yellow]")
            state = coder.run(state)
            _checkpoint(state, f"coder_fix_integration_{attempts + 1}")
            attempts = state.retry_count
            # Reset integration_passed so 3b re-runs
            state.integration_passed = None

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 4 — Writer (docs, README, CHANGELOG, git commit)
    # ═══════════════════════════════════════════════════════════════════════
    if state.status not in (Status.WRITING, Status.DEVOPS, Status.DONE, Status.FAILED, Status.ABORTED):
        console.rule("[bold]📝 Stage 4 — Writer Agent[/bold]")
        state = writer.run(state)
        _checkpoint(state, "writer_final")

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 5 — DevOps Agent (OPT-IN via --devops flag)
    # ═══════════════════════════════════════════════════════════════════════
    if state.devops_mode and state.status not in (Status.DONE, Status.FAILED, Status.ABORTED):
        console.rule(
            f"[bold cyan]🐳 Stage 5 — DevOps Agent "
            f"(mode: {state.devops_mode})[/bold cyan]"
        )
        state = devops.run(state)
        _checkpoint(state, "devops_final")

    # ── Mark done ─────────────────────────────────────────────────────────
    if state.status not in (Status.FAILED, Status.ABORTED):
        state.status = Status.DONE

    _print_summary(state)
    return state


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _reviewer_verdict(review_notes: str) -> str:
    import re
    match = re.search(r"VERDICT:\s*(PASS|REJECT)", review_notes, re.IGNORECASE)
    return match.group(1).upper() if match else "PASS"


def _print_summary(state: PipelineState) -> None:
    colour = {
        Status.DONE:    "green",
        Status.FAILED:  "red",
        Status.ABORTED: "yellow",
    }.get(state.status, "white")

    devops_info = (
        f"DevOps files: {len(state.devops_files)}\n"
        if state.devops_mode else ""
    )

    console.print(Panel(
        f"[bold {colour}]Pipeline {state.status}[/bold {colour}]\n\n"
        f"Run ID:       {state.run_id}\n"
        f"Task:         {state.task_prompt[:80]}\n"
        f"Files made:   {len(state.generated_files)}\n"
        f"{devops_info}"
        f"Debug cycles: {state.retry_count}\n"
        f"Review retries: {state.review_retry_count}\n"
        f"Agents ran:   {len(state.audit_trail)}",
        title="[bold]Workflow Summary[/bold]",
        border_style=colour,
    ))

    # Audit trail table
    table = Table(title="Agent Audit Trail", show_lines=True)
    table.add_column("#",        style="dim",   width=3)
    table.add_column("Agent",    style="cyan")
    table.add_column("Status",   style="white")
    table.add_column("Tokens",   style="green")
    table.add_column("Duration", style="dim")
    table.add_column("Notes",    style="dim")
    for i, entry in enumerate(state.audit_trail, 1):
        table.add_row(
            str(i),
            entry.agent,
            entry.status,
            str(entry.tokens_used),
            f"{entry.duration_ms}ms",
            entry.notes[:60],
        )
    console.print(table)

    if state.devops_files:
        devops_table = Table(title="Generated DevOps Files", show_lines=True)
        devops_table.add_column("File", style="cyan")
        devops_table.add_column("Size", style="dim")
        for path, content in state.devops_files.items():
            devops_table.add_row(path, f"{len(content)} chars")
        console.print(devops_table)
