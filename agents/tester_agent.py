"""
agents/tester_agent.py — Tester Agent

Role: "QA Engineer"
Pipeline:
  Step 1 — Static Analysis (language-aware: Python=ast+pyflakes, Java=javac, TS=tsc, Go=go vet)
            If errors found → auto-fix simple pyflakes issues (Python only), then
            escalate remaining errors to Debugger. pytest/test-runner is skipped.
  Step 2 — Generate test files (language-aware prompt: pytest / JUnit / Jest / Go test)
  Step 3 — Flush source + test files to disk
  Step 4 — Run the appropriate test command (language-aware via run_tests())

Results stored in:
  state.static_analysis_output  (str | None)
  state.test_output              (dict: returncode, stdout, stderr)
  state.error_log                (combined error string for Debugger)
"""

from __future__ import annotations

import os

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState
from tools.file_tools import write_file
from tools.shell_tools import (
    auto_fix_pyflakes,
    detect_language,
    run_static_analysis,
    run_tests,
)


# Map language → test framework name (for prompts)
_LANG_TEST_FRAMEWORK: dict[str, str] = {
    "python":  "pytest",
    "java":    "JUnit 5 + Mockito",
    "kotlin":  "JUnit 5 + MockK",
    "nodejs":  "Jest (TypeScript / JavaScript)",
    "go":      "Go testing package (table-driven tests)",
    "rust":    "Rust built-in #[test] + cargo test",
    "csharp":  "xUnit + Moq",
    "ruby":    "RSpec",
    "php":     "PHPUnit",
    "unknown": "the most appropriate testing framework for this language",
}

# Map language → canonical test folder
_LANG_TEST_FOLDER: dict[str, str] = {
    "python":  "tests/",
    "java":    "src/test/java/",
    "kotlin":  "src/test/kotlin/",
    "nodejs":  "__tests__/",
    "go":      "",          # Go tests live beside source files (*_test.go)
    "rust":    "tests/",
    "csharp":  "Tests/",
    "ruby":    "spec/",
    "php":     "tests/",
    "unknown": "tests/",
}

# Map language → file extension for test output
_LANG_TEST_EXT: dict[str, str] = {
    "python":  ".py",
    "java":    ".java",
    "kotlin":  ".kt",
    "nodejs":  ".test.ts",
    "go":      "_test.go",
    "rust":    ".rs",
    "csharp":  ".cs",
    "ruby":    "_spec.rb",
    "php":     "Test.php",
    "unknown": ".py",
}


class TesterAgent(BaseAgent):
    name = "Tester"

    @property
    def system_role(self) -> str:  # type: ignore[override]
        return (
            "You are a meticulous QA Engineer specialising in production-grade backend testing. "
            "You write comprehensive, idiomatic test suites using the test framework native "
            "to the project's language. Your tests cover:\n"
            "  - Happy paths for every public function / endpoint\n"
            "  - Edge cases: empty inputs, boundary values, null/None, empty collections\n"
            "  - Error conditions: invalid data, unauthorized, not-found, server errors\n"
            "  - Idempotency: verify repeated calls produce the same result\n"
            "  - Contract tests: response shape matches the API contract in the plan\n\n"
            "Rules:\n"
            "  - Mock ALL external dependencies (DB, HTTP, filesystem, time, env vars)\n"
            "  - Never depend on a live database, network, or real filesystem\n"
            "  - Each test must document the scenario it covers (docstring or comment)\n"
            "  - Use the idiomatic setup/teardown mechanism for the language\n"
            "Output each file inside a fenced code block preceded by: # FILE: <relative/path>"
        )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.TESTING

        # Resolve effective language (detect from files if state says 'auto')
        language = self._resolve_language(state)

        # ── Step 1: Static Analysis ───────────────────────────────────────
        state = self._run_static_analysis(state, language)

        # Python-only: auto-fix simple pyflakes errors before escalating
        if state.static_analysis_output and language == "python":
            state = self._try_auto_fix_pyflakes(state)

        # If static errors remain → skip test runner; Debugger will fix first
        if state.static_analysis_output:
            state.log(
                self.name,
                notes=f"STATIC ERRORS found — test run skipped (attempt {state.retry_count + 1})",
            )
            return state

        # ── Step 2: Generate test files ───────────────────────────────────
        if not state.test_files or state.retry_count > 0:
            state = self._generate_tests(state, language)

        # ── Step 3: Flush generated source + test files to disk ──────────
        if state.project_root:
            self._flush_to_disk(state, language)

        # ── Step 4: Run language-appropriate tests ────────────────────────
        result = run_tests(
            project_root=state.project_root or ".",
            language=language,
        )
        state.test_output = result

        if result.get("returncode", 1) != 0:
            state.error_log = (
                f"STDOUT:\n{result.get('stdout', '')}\n\nSTDERR:\n{result.get('stderr', '')}"
            )
            state.log(
                self.name,
                notes=f"RUNTIME FAIL (attempt {state.retry_count + 1})",
            )
        else:
            state.error_log = None
            state.log(self.name, notes="PASS -- all tests green")

        return state

    # ── Language resolution ────────────────────────────────────────────────

    def _resolve_language(self, state: PipelineState) -> str:
        """Return the effective language: user-set or auto-detected from files."""
        if state.language and state.language != "auto":
            return state.language.lower().strip()
        detected = detect_language(state.generated_files)
        # Persist so other agents see the same value
        state.language = detected
        return detected

    # ── Static analysis ───────────────────────────────────────────────────

    def _run_static_analysis(self, state: PipelineState, language: str) -> PipelineState:
        from rich.console import Console
        console = Console()
        console.rule("[bold yellow]🔬 Static Analysis[/bold yellow]")

        result = run_static_analysis(state.generated_files, language=language)

        if result["has_errors"]:
            error_block = "\n".join(result["errors"])
            state.static_analysis_output = error_block
            state.error_log = f"STATIC ANALYSIS ERRORS:\n{error_block}"
            console.print(
                f"[red]❌ Static analysis found {len(result['errors'])} error(s):[/red]\n"
                + error_block
            )
        else:
            state.static_analysis_output = None
            console.print("[green]✅ Static analysis passed — no issues found[/green]")

        return state

    # ── Python pyflakes auto-fix ───────────────────────────────────────────

    def _try_auto_fix_pyflakes(self, state: PipelineState) -> PipelineState:
        """
        For Python only: deterministically remove unused-import lines that pyflakes
        flagged, without a slow LLM roundtrip.  Remaining (complex) errors are left
        for the Debugger.
        """
        from rich.console import Console
        console = Console()

        errors = state.static_analysis_output.splitlines() if state.static_analysis_output else []
        patched, remaining = auto_fix_pyflakes(state.generated_files, errors)

        fixed_count = len(errors) - len(remaining)
        if fixed_count > 0:
            state.generated_files = patched
            console.print(
                f"[cyan]🔧 Auto-fixed {fixed_count} pyflakes issue(s) "
                f"({len(remaining)} remain)[/cyan]"
            )
            if remaining:
                state.static_analysis_output = "\n".join(remaining)
                state.error_log = f"STATIC ANALYSIS ERRORS:\n{state.static_analysis_output}"
            else:
                state.static_analysis_output = None
                state.error_log = None

        return state

    # ── Test generation ───────────────────────────────────────────────────

    def _generate_tests(self, state: PipelineState, language: str) -> PipelineState:
        framework  = _LANG_TEST_FRAMEWORK.get(language, _LANG_TEST_FRAMEWORK["unknown"])
        test_folder = _LANG_TEST_FOLDER.get(language, "tests/")
        test_ext    = _LANG_TEST_EXT.get(language, ".py")

        files_block = "\n\n".join(
            f"# FILE: {path}\n```\n{content}\n```"
            for path, content in state.generated_files.items()
        )

        prompt = f"""
Write a comprehensive, production-grade test suite for the following backend code.

LANGUAGE: {language}
TEST FRAMEWORK: {framework}
TEST FOLDER: {test_folder}

TASK CONTEXT:
{state.task_prompt}

ARCHITECT'S PLAN SUMMARY:
{state.plan_summary}

SOURCE FILES:
{files_block}

Requirements:
- Write idiomatic {language} tests using {framework}
- Test every public function, method, and HTTP endpoint documented in the plan
- Cover: happy paths, edge cases (empty input, boundary values, null/None),
  ALL documented error conditions (4xx, 5xx, validation failures)
- Mock ALL external dependencies: database, HTTP calls, file I/O, clock, env vars
- Use the idiomatic setup/teardown mechanism ({framework})
- Each test has a clear descriptive name and a comment or docstring explaining the scenario
- Naming convention: test_<scenario>_<expected_outcome> (or language-idiomatic equivalent)
- Place all test files under "{test_folder}"

Output each file as:
# FILE: {test_folder}<filename>{test_ext}
```
<complete file content>
```

Do not output any explanation outside the code blocks.
"""
        response_text, tokens = self._call_llm(state, prompt)

        import re
        # Match: # FILE: <path> followed by a fenced code block
        pattern = r"#\s*FILE:\s*([^\n]+)\n```[^\n]*\n(.*?)```"
        matches = re.findall(pattern, response_text, re.DOTALL)

        if matches:
            for file_path, content in matches:
                state.test_files[file_path.strip()] = content.strip()
        else:
            # Fallback: store full response as single test file
            ext_map = {"python": ".py", "java": ".java", "nodejs": ".test.ts",
                       "go": "_test.go", "kotlin": ".kt"}
            ext = ext_map.get(language, ".py")
            fallback_path = f"{test_folder}test_generated{ext}"
            state.test_files[fallback_path] = response_text.strip()

        state.log(
            self.name,
            tokens=tokens,
            notes=f"{len(state.test_files)} test file(s) generated",
        )
        return state

    # ── File flush ────────────────────────────────────────────────────────

    def _flush_to_disk(self, state: PipelineState, language: str) -> None:
        """Write generated source files and test files to disk."""
        root = state.project_root
        for rel_path, content in state.generated_files.items():
            write_file(os.path.join(root, rel_path), content)
        for rel_path, content in state.test_files.items():
            write_file(os.path.join(root, rel_path), content)

        # Python-specific: ensure tests/__init__.py exists
        if language == "python":
            init_path = os.path.join(root, "tests", "__init__.py")
            if not os.path.exists(init_path):
                write_file(init_path, "")
