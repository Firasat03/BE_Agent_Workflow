"""
main.py — CLI Entry Point for BE Multi-Agent Workflow

Usage:
  python main.py --task "Add POST /login endpoint"
                 --project-root ./my_project
                 [--rules rules/spring-boot.md]
                 [--max-retries 3]
                 [--model gemini-2.0-flash]
                 [--resume <run-id>]
                 [--list-runs]
"""

import argparse
import os
import sys

from rich.console import Console
from rich.table import Table

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="be-agent-workflow",
        description="Multi-Agent Backend Code Workflow powered by Gemini",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a new feature
  python main.py --task "Add POST /login endpoint" --project-root ./my_api

  # Use a specific rules profile
  python main.py --task "Add payment service" --project-root ./billing --rules rules/spring-boot.md

  # Resume a crashed run
  python main.py --resume a3f2-20260223-0452

  # List all past runs
  python main.py --list-runs
        """,
    )

    parser.add_argument("--task",         type=str, help="Task description for the agents")
    parser.add_argument("--project-root", type=str, default=".", help="Path to the target project (default: current dir)")
    parser.add_argument("--rules",        type=str, default=None, help="Path to RULES.md (default: rules/RULES.md)")
    parser.add_argument("--max-retries",  type=int, default=None, help="Max debug retry cycles (overrides config)")
    parser.add_argument("--model",        type=str, default=None, help="Gemini model name (overrides GEMINI_MODEL env)")
    parser.add_argument("--resume",       type=str, default=None, help="Resume a previous run by run-id")
    parser.add_argument("--list-runs",    action="store_true",    help="List all past workflow runs and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # ── Validate API key (provider-aware) ───────────────────────────────
    provider = os.getenv("LLM_PROVIDER", "gemini").lower().strip()
    _key_map = {
        "gemini":        ("GEMINI_API_KEY",    "set GEMINI_API_KEY=<key>"),
        "openai":        ("OPENAI_API_KEY",     "set OPENAI_API_KEY=<key>"),
        "openai_compat": ("OPENAI_API_KEY",     "set OPENAI_API_KEY=<key>"),
        "anthropic":     ("ANTHROPIC_API_KEY",  "set ANTHROPIC_API_KEY=<key>"),
        "ollama":        (None, None),   # Ollama is local, no key needed
    }
    key_name, key_hint = _key_map.get(provider, ("GEMINI_API_KEY", "set GEMINI_API_KEY=<key>"))
    if key_name and not os.getenv(key_name):
        console.print(f"[red]❌ {key_name} is not set (provider: {provider})[/red]")
        console.print(f"Set it with:  {key_hint}")
        sys.exit(1)

    # ── Override config from CLI ──────────────────────────────────────────
    if args.max_retries is not None:
        import config
        config.MAX_DEBUG_RETRIES = args.max_retries

    if args.model is not None:
        import config
        config.LLM_MODEL = args.model

    # ── --list-runs ───────────────────────────────────────────────────────
    if args.list_runs:
        from tools.checkpoint_tools import list_runs
        runs = list_runs()
        if not runs:
            console.print("[yellow]No past runs found.[/yellow]")
            return
        table = Table(title="Past Workflow Runs", show_lines=True)
        table.add_column("Run ID",          style="cyan")
        table.add_column("Status",          style="white")
        table.add_column("Checkpoints",     style="green")
        table.add_column("Last Checkpoint", style="dim")
        table.add_column("Task",            style="dim")
        for r in runs:
            table.add_row(
                r["run_id"],
                r["status"],
                str(r["checkpoints"]),
                r["last_checkpoint"],
                r["task_prompt"],
            )
        console.print(table)
        return

    # ── --resume ──────────────────────────────────────────────────────────
    existing_state = None
    if args.resume:
        from tools.checkpoint_tools import load_latest_checkpoint
        existing_state = load_latest_checkpoint(args.resume)
        if existing_state is None:
            console.print(f"[red]❌ No checkpoint found for run-id: {args.resume}[/red]")
            sys.exit(1)

    # ── --task is required for new runs ───────────────────────────────────
    if not existing_state and not args.task:
        console.print("[red]❌ --task is required for new runs.[/red]")
        console.print("Use --help for usage.")
        sys.exit(1)

    # ── Run pipeline ──────────────────────────────────────────────────────
    from orchestrator import run
    state = run(
        task_prompt=args.task or (existing_state.task_prompt if existing_state else ""),
        project_root=os.path.abspath(args.project_root),
        rules_file=args.rules,
        existing_state=existing_state,
    )

    # Exit with non-zero code on failure
    if state.status in ("FAILED", "ABORTED"):
        sys.exit(1)


if __name__ == "__main__":
    main()
