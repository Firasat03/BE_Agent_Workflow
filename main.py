"""
main.py -- CLI Entry Point for BE Multi-Agent Workflow

Usage:
  python main.py --task "Add POST /login endpoint"
                 --project-root ./my_project
                 [--language python|java|nodejs|go|kotlin|rust|csharp|ruby|php]
                 [--rules rules/spring-boot.md]
                 [--max-retries 3]
                 [--model gemini-2.0-flash]
                 [--devops docker|k8s|all]
                 [--resume <run-id>]
                 [--list-runs]
"""
import os
os.environ.setdefault("PYTHONUTF8", "1")

import argparse
import os
import sys

from rich.console import Console
from rich.table import Table

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="be-agent-workflow",
        description="Multi-Agent Backend Code Workflow powered by LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a new feature (Python, auto-detected)
  python main.py --task "Add POST /login endpoint" --project-root ./my_api

  # Java Spring Boot feature
  python main.py --task "Add POST /register" --project-root ./my_api --language java

  # Node.js / TypeScript REST endpoint
  python main.py --task "Add GET /products" --project-root ./shop-api --language nodejs

  # Go microservice endpoint
  python main.py --task "Add health check" --project-root ./svc --language go

  # Use a language-specific rules profile
  python main.py --task "Add payment service" --project-root ./billing --rules rules/spring-boot.md --language java

  # Generate code + Docker files
  python main.py --task "Add auth service" --project-root ./api --devops docker

  # Resume a crashed run
  python main.py --resume a3f2-20260223-0452

  # List all past runs
  python main.py --list-runs
        """,
    )

    parser.add_argument("--task",         type=str, help="Task description for the agents")
    parser.add_argument("--project-root", type=str, default=".", help="Path to the target project (default: current dir)")
    parser.add_argument("--language",     type=str, default="auto",
                        choices=["auto", "python", "java", "nodejs", "go",
                                 "kotlin", "rust", "csharp", "ruby", "php"],
                        help=("Target backend language (default: auto-detect from files). "
                              "Sets the test framework, static analyser, and code style."))
    parser.add_argument("--rules",        type=str, default=None, help="Path to RULES.md (default: rules/RULES.md)")
    parser.add_argument("--max-retries",  type=int, default=None, help="Max debug retry cycles (overrides config)")
    parser.add_argument("--model",        type=str, default=None, help="Gemini model name (overrides GEMINI_MODEL env)")
    parser.add_argument("--resume",       type=str, default=None, help="Resume a previous run by run-id")
    parser.add_argument("--list-runs",    action="store_true",    help="List all past workflow runs and exit")
    parser.add_argument(
        "--devops",
        nargs="?",          # optional value: --devops  or  --devops docker  etc.
        const="all",        # --devops with no value defaults to 'all'
        default=None,       # not passed = None = DevOps agent disabled
        choices=["docker", "k8s", "all"],
        metavar="MODE",
        help=(
            "Enable the DevOps agent. MODE = docker | k8s | all "
            "(default when flag is present with no MODE: all). "
            "docker = Dockerfile + docker-compose.yml; "
            "k8s = Kubernetes manifests; "
            "all = both."
        ),
    )
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

    # ── Inform user of active modes ────────────────────────────────────────────
    if args.devops:
        console.print(
            f"[cyan]🐳 DevOps agent enabled: mode=[bold]{args.devops}[/bold][/cyan]"
        )
    if args.language != "auto":
        console.print(f"[cyan]>> Language: [bold]{args.language}[/bold][/cyan]")

    # ── Run pipeline ──────────────────────────────────────────────────────
    from orchestrator import run
    state = run(
        task_prompt=args.task or (existing_state.task_prompt if existing_state else ""),
        project_root=os.path.abspath(args.project_root),
        rules_file=args.rules,
        existing_state=existing_state,
        devops_mode=args.devops,
        language=args.language,
    )

    # Exit with non-zero code on failure
    if state.status in ("FAILED", "ABORTED"):
        sys.exit(1)


if __name__ == "__main__":
    main()
