"""
agents/writer_agent.py — Writer Agent

Role: "Technical Writer"
- Only runs AFTER Tester reports PASS
- Generates/updates docstrings, README section, and CHANGELOG entry
- Optionally creates a git commit via git_tools
"""

from __future__ import annotations

import os

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState
from tools.file_tools import write_file, read_file, file_exists
from tools.git_tools import is_git_repo, git_stage_all, git_commit


class WriterAgent(BaseAgent):
    name = "Writer"
    system_role = (
        "You are a Technical Writer and Documentation Specialist for backend systems. "
        "You write clear, concise, and accurate documentation. "
        "For docstrings, follow Google-style format. "
        "For README sections, use clean Markdown. "
        "For CHANGELOG entries, follow Keep a Changelog format."
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.WRITING
        total_tokens = 0

        # ── 1. Add / update docstrings ────────────────────────────────────
        py_files = {
            k: v for k, v in state.generated_files.items() if k.endswith(".py")
        }
        if py_files:
            state, tokens = self._add_docstrings(state, py_files)
            total_tokens += tokens

        # ── 2. Update README ──────────────────────────────────────────────
        readme_tokens = self._update_readme(state)
        total_tokens += readme_tokens

        # ── 3. Append CHANGELOG ───────────────────────────────────────────
        changelog_tokens = self._update_changelog(state)
        total_tokens += changelog_tokens

        # ── 4. Optional git commit ────────────────────────────────────────
        if state.project_root and is_git_repo(state.project_root):
            git_stage_all(state.project_root)
            git_commit(
                state.project_root,
                f"feat: {state.task_prompt[:72]} [auto-generated]",
            )

        state.docs_updated = True
        state.status = Status.DONE
        state.log(self.name, tokens=total_tokens, notes="docs written, status=DONE")
        return state

    # ── Docstrings ────────────────────────────────────────────────────────

    def _add_docstrings(
        self, state: PipelineState, py_files: dict
    ) -> tuple[PipelineState, int]:
        files_block = "\n\n".join(
            f"# FILE: {path}\n```python\n{content}\n```"
            for path, content in py_files.items()
        )
        prompt = f"""
Add or improve Google-style docstrings to every public class, method, and function
in the following Python files. Do not change any logic — only add/update docstrings.

{files_block}

Output each updated file as:
# FILE: <path>
```python
<complete updated content>
```
"""
        response_text, tokens = self._call_llm(state, prompt)

        import re
        pattern = r"#\s*FILE:\s*(.+?)\n```python\n(.*?)```"
        for file_path, content in re.findall(pattern, response_text, re.DOTALL):
            state.generated_files[file_path.strip()] = content.strip()
            if state.project_root:
                write_file(
                    os.path.join(state.project_root, file_path.strip()),
                    content.strip(),
                )

        return state, tokens

    # ── README ────────────────────────────────────────────────────────────

    def _update_readme(self, state: PipelineState) -> int:
        readme_path = os.path.join(state.project_root, "README.md") if state.project_root else "README.md"
        existing = ""
        if file_exists(readme_path):
            existing = read_file(readme_path)

        prompt = f"""
Update (or create if empty) the README.md for the following feature:

TASK: {state.task_prompt}

WHAT WAS IMPLEMENTED:
{state.plan_summary}

EXISTING README (may be empty):
{existing or "(empty)"}

Add a new section documenting the feature. Keep it concise. Use clean Markdown.
Output ONLY the complete updated README content inside a ```markdown block.
"""
        response_text, tokens = self._call_llm(state, prompt)
        content = self._extract_code_block(response_text, "markdown") or response_text
        if state.project_root:
            write_file(readme_path, content)
        return tokens

    # ── CHANGELOG ─────────────────────────────────────────────────────────

    def _update_changelog(self, state: PipelineState) -> int:
        from datetime import date
        changelog_path = (
            os.path.join(state.project_root, "CHANGELOG.md")
            if state.project_root else "CHANGELOG.md"
        )
        existing = read_file(changelog_path) if file_exists(changelog_path) else ""

        prompt = f"""
Add a new Keep-a-Changelog entry for today ({date.today().isoformat()}) describing:

TASK: {state.task_prompt}

WHAT WAS DONE:
{state.plan_summary}

EXISTING CHANGELOG:
{existing or "(empty)"}

Output ONLY the complete updated CHANGELOG.md content inside a ```markdown block.
"""
        response_text, tokens = self._call_llm(state, prompt)
        content = self._extract_code_block(response_text, "markdown") or response_text
        if state.project_root:
            write_file(changelog_path, content)
        return tokens
