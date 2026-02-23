"""
agents/tester_agent.py — Tester Agent

Role: "QA Engineer"
- Generates test files (pytest by default) for the generated source code
- Writes test files to disk in the project root
- Runs tests via shell_tools and captures pass/fail output
- Stores result in state.test_output
"""

from __future__ import annotations

import os

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState
from tools.file_tools import write_file
from tools.shell_tools import run_pytest


class TesterAgent(BaseAgent):
    name = "Tester"
    system_role = (
        "You are a meticulous QA Engineer specialising in backend testing. "
        "You write comprehensive pytest test suites that cover: happy paths, "
        "edge cases, error conditions, and boundary values. "
        "Mock all external dependencies (DB, HTTP calls, file I/O) using pytest-mock or unittest.mock. "
        "Never write tests that depend on a live database or network. "
        "Output each test file inside a fenced ```python code block, "
        "preceded by a comment: # FILE: <relative/path>."
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.TESTING

        # ── Step 1: Generate test files ───────────────────────────────────
        if not state.test_files or state.retry_count > 0:
            state = self._generate_tests(state)

        # ── Step 2: Flush generated source + test files to disk ──────────
        if state.project_root:
            self._flush_to_disk(state)

        # ── Step 3: Run tests ─────────────────────────────────────────────
        result = run_pytest(
            project_root=state.project_root or ".",
            test_path="tests/",
        )
        state.test_output = result

        if result["returncode"] != 0:
            state.error_log = f"STDOUT:\n{result['stdout']}\n\nSTDERR:\n{result['stderr']}"
            state.log(self.name, notes=f"FAIL (attempt {state.retry_count + 1})")
        else:
            state.error_log = None
            state.log(self.name, notes="PASS")

        return state

    # ── Test generation ───────────────────────────────────────────────────

    def _generate_tests(self, state: PipelineState) -> PipelineState:
        files_block = "\n\n".join(
            f"# FILE: {path}\n```python\n{content}\n```"
            for path, content in state.generated_files.items()
        )

        prompt = f"""
Write a comprehensive pytest test suite for the following backend code.

TASK CONTEXT: {state.task_prompt}

SOURCE FILES:
{files_block}

Requirements:
- Cover all public functions/endpoints with unit tests
- Include edge cases and error conditions
- Mock all database calls, HTTP requests, and file I/O
- Use pytest fixtures where appropriate
- Follow the naming: test_<module_name>.py → place in tests/ folder

Output each test file as:
# FILE: tests/test_<name>.py
```python
<complete test content>
```
"""
        response_text, tokens = self._call_llm(state, prompt)

        # Parse test files from response
        import re
        pattern = r"#\s*FILE:\s*(tests/[^\n]+)\n```python\n(.*?)```"
        matches = re.findall(pattern, response_text, re.DOTALL)
        if matches:
            for file_path, content in matches:
                state.test_files[file_path.strip()] = content.strip()
        else:
            # Fallback: store all content as single test file
            state.test_files["tests/test_generated.py"] = self._extract_code_block(
                response_text, "python"
            )

        state.log(self.name, tokens=tokens, notes=f"{len(state.test_files)} test files generated")
        return state

    # ── File flush ────────────────────────────────────────────────────────

    def _flush_to_disk(self, state: PipelineState) -> None:
        """Write generated source files and test files to disk."""
        root = state.project_root
        for rel_path, content in state.generated_files.items():
            write_file(os.path.join(root, rel_path), content)
        for rel_path, content in state.test_files.items():
            write_file(os.path.join(root, rel_path), content)
        # Ensure tests/__init__.py exists
        init_path = os.path.join(root, "tests", "__init__.py")
        if not os.path.exists(init_path):
            write_file(init_path, "")
