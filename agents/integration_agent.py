"""
integration_agent.py — Live Integration Testing Agent

Runs AFTER the Tester (unit tests) passes.
  1. Calls integration_tools.run_integration_tests() to:
       build the project → start the server → curl every API endpoint → stop server
  2. Logs per-endpoint results to the console with rich formatting.
  3. If any endpoint fails, populates state.error_log with a detailed report
     so the Debugger + Coder loop can self-heal.
"""

from __future__ import annotations

import time
from rich.console import Console
from rich.table import Table

from config import Status
from state import PipelineState
from tools.integration_tools import run_integration_tests

console = Console()


class IntegrationAgent:
    """Runs the built application and validates every declared API endpoint."""

    name = "IntegrationAgent"

    # ── public ──────────────────────────────────────────────────────────────

    def run(self, state: PipelineState) -> PipelineState:
        old_status = state.status
        state.status = Status.INTEGRATION
        console.rule("[bold cyan]🔗 Integration Tests — Build → Start → Curl[/bold cyan]")

        lang = (state.language or "auto").lower().strip()
        if lang in ("auto", "unknown"):
            from tools.shell_tools import detect_language
            lang = detect_language(state.generated_files)

        # -- Run -----------------------------------------------------------
        t0 = time.time()
        result = run_integration_tests(
            project_root=state.project_root,
            language=lang,
            plan_items=state.plan,
            generated_files=state.generated_files,
        )
        elapsed = int((time.time() - t0) * 1000)

        # -- Render build summary ------------------------------------------
        if result.get("build_output"):
            console.print(f"[dim]Build output:[/dim]\n{result['build_output'][:800]}")

        # -- Table of per-endpoint results ---------------------------------
        if result.get("results"):
            self._print_results_table(result["results"])

        # -- Verdict -------------------------------------------------------
        if result.get("error"):
            console.print(f"[red]Integration error: {result['error']}[/red]")

        if result["passed"]:
            console.print("[green bold]✅ All integration tests passed![/green bold]")
            state.integration_test_output = self._format_results(result)
            state.integration_passed = True
            state.log(self.name, notes="All integration tests passed", duration_ms=elapsed)
        else:
            console.print("[red bold]❌ Integration tests FAILED[/red bold]")
            report = self._build_failure_report(result)
            state.integration_test_output = report
            state.integration_passed = False
            state.error_log = f"INTEGRATION TEST FAILURES:\n{report}"
            state.log(self.name, notes="Integration tests failed", duration_ms=elapsed)

        return state

    # ── private ─────────────────────────────────────────────────────────────

    def _print_results_table(self, results: list[dict]) -> None:
        table = Table(title="Integration Test Results", show_lines=True)
        table.add_column("Method",   style="cyan",  width=7)
        table.add_column("Path",     style="white")
        table.add_column("Expected", style="dim",   width=8)
        table.add_column("Actual",   style="white", width=8)
        table.add_column("Status",   style="white", width=6)
        table.add_column("Response (preview)", style="dim")

        for r in results:
            ok = r.get("passed", False)
            status_icon = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
            actual_col  = (
                f"[green]{r['actual_status']}[/green]" if ok
                else f"[red]{r['actual_status']}[/red]"
            )
            table.add_row(
                r["method"],
                r["path"],
                str(r["expected_status"]),
                actual_col,
                status_icon,
                (r.get("response_body") or "")[:80],
            )
        console.print(table)

    def _format_results(self, result: dict) -> str:
        lines = []
        for r in result.get("results", []):
            flag = "PASS" if r["passed"] else "FAIL"
            lines.append(
                f"[{flag}] {r['method']} {r['path']} "
                f"→ expected={r['expected_status']} got={r['actual_status']}"
            )
        return "\n".join(lines)

    def _build_failure_report(self, result: dict) -> str:
        lines = []
        if result.get("error"):
            lines.append(f"ERROR: {result['error']}")
        if result.get("build_output"):
            lines.append("BUILD OUTPUT:")
            lines.append(result["build_output"][:2000])
        failed = [r for r in result.get("results", []) if not r["passed"]]
        if failed:
            lines.append("FAILED ENDPOINTS:")
            for r in failed:
                lines.append(
                    f"  {r['method']} {r['path']} — "
                    f"expected HTTP {r['expected_status']}, "
                    f"got HTTP {r['actual_status']}\n"
                    f"  Response: {(r.get('response_body') or '')[:300]}"
                )
        return "\n".join(lines)
