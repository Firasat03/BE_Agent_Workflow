"""
orchestrator.py — Central Pipeline Controller for BE Multi-Agent Workflow

The Orchestrator is NOT an agent (it makes no LLM calls itself).
It is the rule-based controller that:
  1. Loads user rules (RULES.md)
  2. Drives agents in the correct order
  3. Pauses at the Human Plan Approval gate
  4. Manages the Debugger retry loop
  5. Saves checkpoints after each agent
  6. Escalates to human on max retry exceeded
"""

from __future__ import annotations

import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

from config import MAX_DEBUG_RETRIES, MAX_REVIEW_RETRIES, Status
from state import PipelineState
from tools.rules_loader import load_rules
from tools.checkpoint_tools import save_checkpoint

from agents.architect_agent import ArchitectAgent
from agents.coder_agent import CoderAgent
from agents.reviewer_agent import ReviewerAgent
from agents.tester_agent import TesterAgent
from agents.debugger_agent import DebuggerAgent
from agents.writer_agent import WriterAgent

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
    Present the Architect's plan to the user and wait for approval or feedback.
    Returns state with plan_approved=True, or with user_feedback set for re-planning.
    """
    console.print(Panel(
        f"[bold yellow]📋 ARCHITECT'S PLAN[/bold yellow]\n\n{state.plan_summary}",
        title=f"[bold]Review Plan — Run {state.run_id}[/bold]",
        border_style="yellow",
    ))

    # Print structured plan table
    table = Table(title="Files to be created / modified", show_lines=True)
    table.add_column("Action", style="cyan", width=8)
    table.add_column("File", style="white")
    table.add_column("API Contract", style="green")
    table.add_column("Scope", style="dim")
    for item in state.plan:
        table.add_row(item.action, item.file, item.api_contract or "—", item.scope_estimate or "—")
    console.print(table)

    while True:
        console.print("\n[bold]What would you like to do?[/bold]")
        console.print("  [green][A][/green] Approve and proceed")
        console.print("  [yellow][C][/yellow] Request changes")
        console.print("  [red][X][/red] Abort")
        choice = input("\nYour choice (A/C/X): ").strip().upper()

        if choice == "A":
            state.plan_approved = True
            state.user_feedback = None
            console.print("[green]✅ Plan approved. Starting Coder...[/green]")
            return state

        elif choice == "C":
            feedback = input("Describe your changes (press Enter twice when done):\n> ").strip()
            state.user_feedback = feedback
            state.plan_approved = False
            console.print("[yellow]✏️  Feedback noted. Re-running Architect...[/yellow]")
            return state

        elif choice == "X":
            console.print("[red]🛑 Aborted by user.[/red]")
            state.status = Status.ABORTED
            return state

        else:
            console.print("[red]Invalid choice. Enter A, C, or X.[/red]")


# ─── Main Orchestrator ────────────────────────────────────────────────────────

def run(
    task_prompt: str,
    project_root: str,
    rules_file: str | None = None,
    existing_state: PipelineState | None = None,
) -> PipelineState:
    """
    Run the full multi-agent BE pipeline.

    Args:
        task_prompt:    The user's natural language task description.
        project_root:   Absolute path to the target project directory.
        rules_file:     Optional path to a RULES.md file. Falls back to default.
        existing_state: Pre-loaded state for --resume mode.

    Returns:
        Final PipelineState.
    """
    global _step
    _step = 0

    # ── Initialise state ──────────────────────────────────────────────────
    if existing_state:
        state = existing_state
        console.print(f"[cyan]▶ Resuming run {state.run_id} from {state.status}[/cyan]")
    else:
        state = PipelineState(
            task_prompt=task_prompt,
            project_root=project_root,
        )
        state.user_rules = load_rules(rules_file)
        state.active_rules_file = str(rules_file or "rules/RULES.md")

    console.print(Panel(
        f"[bold cyan]🚀 Multi-Agent BE Workflow[/bold cyan]\n"
        f"Run ID: [bold]{state.run_id}[/bold]\n"
        f"Task:   {task_prompt[:120]}\n"
        f"Rules:  {state.active_rules_file}",
        border_style="cyan",
    ))

    # ── Instantiate agents ────────────────────────────────────────────────
    architect  = ArchitectAgent()
    coder      = CoderAgent()
    reviewer   = ReviewerAgent()
    tester     = TesterAgent()
    debugger   = DebuggerAgent()
    writer     = WriterAgent()

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 1 — Architect → Human Plan Approval Gate
    # ═══════════════════════════════════════════════════════════════════════
    if state.status in (Status.INIT, Status.ARCHITECT, Status.PLAN_REVIEW):
        while True:
            console.rule("[bold blue]🏛️  Architect Agent[/bold blue]")
            state = architect.run(state)
            _checkpoint(state, "architect")

            # Human gate
            state = _human_plan_approval(state)
            _checkpoint(state, "plan_review")

            if state.status == Status.ABORTED:
                _print_summary(state)
                return state

            if state.plan_approved:
                break  # Proceed to Coder
            # else: user requested changes → loop back to Architect

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 2 — Coder → Reviewer (with 1 review retry)
    # ═══════════════════════════════════════════════════════════════════════
    if state.status in (Status.PLAN_REVIEW, Status.CODING, Status.REVIEWING):
        review_attempts = state.review_retry_count

        while True:
            console.rule("[bold green]💻 Coder Agent[/bold green]")
            state = coder.run(state)
            _checkpoint(state, "coder")

            console.rule("[bold magenta]🔍 Reviewer Agent[/bold magenta]")
            state = reviewer.run(state)
            _checkpoint(state, "reviewer")

            verdict = _reviewer_verdict(state.review_notes or "")

            if verdict == "PASS":
                break
            elif review_attempts >= MAX_REVIEW_RETRIES:
                console.print(
                    f"[yellow]⚠ Reviewer rejected {review_attempts + 1}x. "
                    "Proceeding to Tester anyway.[/yellow]"
                )
                break
            else:
                review_attempts += 1
                console.print(
                    f"[yellow]🔄 Reviewer REJECT #{review_attempts}. "
                    "Sending back to Coder...[/yellow]"
                )
                # Inject review notes as fix context
                state.fix_instructions = (
                    f"The code reviewer rejected the code. Fix these issues:\n"
                    f"{state.review_notes}"
                )

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 3 — Tester → Debugger Loop
    # ═══════════════════════════════════════════════════════════════════════
    if state.status not in (Status.WRITING, Status.DONE, Status.FAILED, Status.ABORTED):
        attempts = state.retry_count

        while True:
            console.rule(f"[bold blue]🧪 Tester Agent (attempt {attempts + 1})[/bold blue]")
            state = tester.run(state)
            _checkpoint(state, f"tester_attempt{attempts + 1}")

            if state.test_passed():
                console.print("[green]✅ All tests passed![/green]")
                break

            # Tests failed
            if attempts >= MAX_DEBUG_RETRIES:
                console.print(
                    f"[red]❌ Max debug retries ({MAX_DEBUG_RETRIES}) reached. "
                    "Escalating to human review.[/red]"
                )
                state.status = Status.FAILED
                _print_summary(state)
                return state

            console.rule(f"[bold red]🐛 Debugger Agent (attempt {attempts + 1})[/bold red]")
            state = debugger.run(state)
            _checkpoint(state, f"debugger_attempt{attempts + 1}")

            if state.status == Status.FAILED:
                # Low confidence — escalate
                console.print("[red]❌ Debugger low confidence. Escalating to human.[/red]")
                _print_summary(state)
                return state

            console.print("[yellow]🔄 Sending fix to Coder...[/yellow]")
            state = coder.run(state)
            _checkpoint(state, f"coder_retry{attempts + 1}")
            attempts = state.retry_count

    # ═══════════════════════════════════════════════════════════════════════
    # STAGE 4 — Writer
    # ═══════════════════════════════════════════════════════════════════════
    if state.status not in (Status.DONE, Status.FAILED, Status.ABORTED):
        console.rule("[bold]📝 Writer Agent[/bold]")
        state = writer.run(state)
        _checkpoint(state, "writer_final")

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

    console.print(Panel(
        f"[bold {colour}]Pipeline {state.status}[/bold {colour}]\n\n"
        f"Run ID:       {state.run_id}\n"
        f"Task:         {state.task_prompt[:80]}\n"
        f"Files made:   {len(state.generated_files)}\n"
        f"Debug cycles: {state.retry_count}\n"
        f"Agents ran:   {len(state.audit_trail)}",
        title="[bold]Workflow Summary[/bold]",
        border_style=colour,
    ))

    # Audit trail table
    table = Table(title="Agent Audit Trail", show_lines=True)
    table.add_column("Agent",    style="cyan")
    table.add_column("Status",   style="white")
    table.add_column("Tokens",   style="green")
    table.add_column("Duration", style="dim")
    table.add_column("Notes",    style="dim")
    for entry in state.audit_trail:
        table.add_row(
            entry.agent,
            entry.status,
            str(entry.tokens_used),
            f"{entry.duration_ms}ms",
            entry.notes[:60],
        )
    console.print(table)
