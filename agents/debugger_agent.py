"""
agents/debugger_agent.py — Debugger Agent

Role: "Debugging Specialist"
- Receives BOTH static analysis errors AND runtime test failures from state
- Identifies root cause with clear categorisation (static vs runtime)
- Emits precise, file-level fix_instructions → Coder re-applies them
- Prioritises static errors (they block runtime from starting)
- If confidence is low (< 3/5), sets status=FAILED for human escalation
"""

from __future__ import annotations

import re

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState


_LOW_CONFIDENCE_THRESHOLD = 3   # confidence < 3 → escalate to human


class DebuggerAgent(BaseAgent):
    name = "Debugger"
    system_role = (
        "You are an expert Debugging Specialist for production backend systems. "
        "You receive two categories of errors and must handle them in priority order:\n\n"
        "  PRIORITY 1 — STATIC ANALYSIS ERRORS (syntax / semantic issues)\n"
        "    These prevent the code from running at all. Fix these FIRST.\n"
        "    Common causes: syntax errors, misspelled names, missing imports, wrong indentation.\n\n"
        "  PRIORITY 2 — RUNTIME TEST ERRORS (failures during test execution)\n"
        "    These occur when code runs but produces wrong results or throws exceptions.\n"
        "    Common causes: wrong logic, missing null checks, incorrect API response shape,\n"
        "    mock not configured correctly, off-by-one errors.\n\n"
        "For each identified issue:\n"
        "  - Reference the exact file, function, and line number\n"
        "  - Explain why the error occurs (not just what the error says)\n"
        "  - Provide the precise fix the Coder must apply\n\n"
        "Always end your analysis with:\n"
        "CONFIDENCE: <1-5>  (1=guessing, 5=certain)\n"
        "FIX INSTRUCTIONS:\n<precise, file-by-file fix instructions for the Coder>"
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.DEBUGGING

        files_block = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in state.generated_files.items()
        )

        # Separate static vs runtime error sections
        static_section = (
            f"STATIC ANALYSIS ERRORS (fix these FIRST — they prevent code from running):\n"
            f"{state.static_analysis_output}"
            if state.static_analysis_output
            else "STATIC ANALYSIS ERRORS: None — code passed static checks."
        )

        runtime_section = (
            f"RUNTIME TEST ERRORS (pytest failures):\n"
            f"{state.error_log}"
            if state.error_log and not state.static_analysis_output
            else (
                "RUNTIME TEST ERRORS: Skipped — static errors must be fixed first."
                if state.static_analysis_output
                else "RUNTIME TEST ERRORS: None."
            )
        )

        prompt = f"""
A test stage has failed. Perform a root-cause analysis and provide fix instructions.

ORIGINAL TASK: {state.task_prompt}

ARCHITECT'S PLAN:
{state.plan_summary}

─── ERROR REPORT ────────────────────────────────────────────────────────
{static_section}

{runtime_section}
─────────────────────────────────────────────────────────────────────────

CURRENT SOURCE FILES:
{files_block}

Instructions:
1. If static errors exist, address them FIRST — they are blocking all other progress.
2. For each error, identify the exact root cause (not just a paraphrase of the error message).
3. Provide precise, file-level fix instructions referencing function names and line numbers.
4. Do NOT change behaviour that is already correct — minimal targeted fixes only.
5. If the fix requires adding an import, specify the exact import statement.

Format your response as:
ERROR CATEGORY: STATIC | RUNTIME | BOTH
ROOT CAUSE: <one clear sentence>
AFFECTED FILES: <comma-separated list>
ANALYSIS:
<detailed explanation — for each error: what it is, why it occurred, how to fix it>
CONFIDENCE: <1-5>
FIX INSTRUCTIONS:
<precise, step-by-step instructions — one section per affected file>
"""
        response_text, tokens = self._call_llm(state, prompt)

        # Parse confidence score
        confidence_match = re.search(r"CONFIDENCE:\s*(\d)", response_text)
        confidence = int(confidence_match.group(1)) if confidence_match else 3

        # Parse fix instructions
        fix_match = re.search(r"FIX INSTRUCTIONS:\s*(.+)$", response_text, re.DOTALL)
        fix_instructions = fix_match.group(1).strip() if fix_match else response_text

        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            # Low confidence — escalate to human
            state.fix_instructions = None
            state.status = Status.FAILED
            state.log(
                self.name,
                tokens=tokens,
                notes=f"LOW CONFIDENCE ({confidence}/5) — escalating to human",
            )
        else:
            state.fix_instructions = fix_instructions
            # Clear static errors so they re-evaluate after fix
            state.static_analysis_output = None
            state.retry_count += 1
            state.log(
                self.name,
                tokens=tokens,
                notes=f"Fix ready (confidence {confidence}/5, retry #{state.retry_count})",
            )

        return state
