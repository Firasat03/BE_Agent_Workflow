"""
agents/coder_agent.py — Coder Agent

Role: "Expert Backend Developer"
- Reads the approved plan from state
- On first run: generates all files from scratch per plan
- On retry (after Debugger): applies fix_instructions to the relevant files
- Writes generated content into state.generated_files (does NOT touch disk;
  the Orchestrator flushes to disk after approval)
"""

from __future__ import annotations

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState
from tools.mcp_client import get_client


class CoderAgent(BaseAgent):
    name = "Coder"
    system_role = (
        "You are an Expert Backend Developer. "
        "You write clean, production-quality code following best practices. "
        "You implement exactly what is specified in the plan — no more, no less. "
        "Always output COMPLETE file contents inside fenced code blocks tagged with "
        "the correct language (e.g. ```python, ```java). "
        "Never use placeholder comments like '# TODO' or '# rest of code here'."
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.CODING

        if state.fix_instructions:
            return self._apply_fix(state)
        return self._generate_from_plan(state)

    # ── Initial generation ────────────────────────────────────────────────

    def _generate_from_plan(self, state: PipelineState) -> PipelineState:
        """Generate all files defined in the approved plan."""
        total_tokens = 0

        for item in state.plan:
            if item.action == "DELETE":
                state.generated_files.pop(item.file, None)
                continue

            # Read existing content for MODIFY actions
            existing = ""
            if item.action == "MODIFY" and item.file in state.generated_files:
                existing = state.generated_files[item.file]
            elif item.action == "MODIFY" and state.project_root:
                try:
                    from tools.file_tools import read_file
                    import os
                    full_path = os.path.join(state.project_root, item.file)
                    existing = read_file(full_path)
                except Exception:
                    existing = ""

            prompt = self._build_prompt(item, existing, state)
            response_text, tokens = self._call_llm(state, prompt)
            total_tokens += tokens

            # Detect language from file extension for extraction
            lang = _ext_to_lang(item.file)
            code = self._extract_code_block(response_text, lang)
            state.generated_files[item.file] = code

        state.fix_instructions = None  # clear after use
        state.log(self.name, tokens=total_tokens, notes=f"{len(state.plan)} files generated")
        return state

    def _build_prompt(self, item, existing: str, state: PipelineState) -> str:
        existing_block = f"\nExisting content to modify:\n```\n{existing}\n```" if existing else ""
        return f"""
Implement the following backend file:

File: {item.file}
Action: {item.action}
Description: {item.description}
API Contract: {item.api_contract or 'N/A'}
Scope estimate: {item.scope_estimate or 'N/A'}
{existing_block}

Full task context: {state.task_prompt}

Output ONLY the complete file content inside a single fenced code block.
Do not add any explanation outside the code block.
"""

    # ── Fix / retry generation ────────────────────────────────────────────

    def _apply_fix(self, state: PipelineState) -> PipelineState:
        """Apply Debugger's fix instructions to the affected files."""
        prompt = f"""
The following test failure was detected:

TEST OUTPUT:
{state.test_output.get('stdout', '')}
{state.test_output.get('stderr', '')}

DEBUGGER FIX INSTRUCTIONS:
{state.fix_instructions}

CURRENT FILE CONTENTS:
{self._format_files(state.generated_files)}

Apply the fix instructions to the relevant files.
Output each fixed file as a separate fenced code block, preceded by a comment line:
# FILE: <relative/path/to/file>
```<lang>
<complete fixed content>
```
"""
        response_text, tokens = self._call_llm(state, prompt)

        # Parse multiple files from response
        import re
        pattern = r"#\s*FILE:\s*(.+?)\n```\w*\n(.*?)```"
        matches = re.findall(pattern, response_text, re.DOTALL)
        if matches:
            for file_path, content in matches:
                state.generated_files[file_path.strip()] = content.strip()
        else:
            # Fallback: treat whole response as single-file fix
            pass

        state.fix_instructions = None
        state.log(self.name, tokens=tokens, notes="fix applied")
        return state

    @staticmethod
    def _format_files(files: dict[str, str]) -> str:
        parts = []
        for path, content in files.items():
            parts.append(f"# FILE: {path}\n```\n{content}\n```")
        return "\n\n".join(parts)


def _ext_to_lang(filename: str) -> str:
    mapping = {
        ".py":    "python",
        ".java":  "java",
        ".ts":    "typescript",
        ".js":    "javascript",
        ".go":    "go",
        ".rs":    "rust",
        ".kt":    "kotlin",
        ".rb":    "ruby",
        ".cs":    "csharp",
        ".php":   "php",
        ".yaml":  "yaml",
        ".yml":   "yaml",
        ".json":  "json",
        ".sql":   "sql",
        ".sh":    "bash",
    }
    import os
    ext = os.path.splitext(filename)[-1].lower()
    return mapping.get(ext, "")
