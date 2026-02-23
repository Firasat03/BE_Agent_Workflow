"""
agents/debugger_agent.py — Debugger Agent

Role: "Debugging Specialist"
- Receives failing test output + relevant source code
- Identifies the root cause
- Emits precise fix_instructions → Coder re-applies them
- If confidence is low, sets status to FAILED for human escalation
"""

from __future__ import annotations

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState


_LOW_CONFIDENCE_THRESHOLD = 3   # If debugger scores confidence < 3 → escalate

class DebuggerAgent(BaseAgent):
    name = "Debugger"
    system_role = (
        "You are a expert Debugging Specialist for backend systems. "
        "Given a test failure, you identify the root cause precisely and provide "
        "clear, line-level fix instructions for the developer to apply. "
        "Be specific: reference exact file names, function names, and line numbers "
        "where possible. "
        "Always end your analysis with:\n"
        "CONFIDENCE: <1-5>  (1=guessing, 5=certain)\n"
        "FIX INSTRUCTIONS:\n<precise instructions for the Coder to apply>"
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.DEBUGGING

        files_block = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in state.generated_files.items()
        )

        prompt = f"""
A test run has failed. Diagnose and provide fix instructions.

ORIGINAL TASK: {state.task_prompt}

ARCHITECT'S PLAN:
{state.plan_summary}

FAILING TEST OUTPUT:
{state.error_log or state.test_output}

CURRENT SOURCE FILES:
{files_block}

Identify the root cause and provide step-by-step fix instructions.

Format your response as:
ROOT CAUSE: <one sentence>
AFFECTED FILES: <comma-separated list>
ANALYSIS: <detailed explanation>
CONFIDENCE: <1-5>
FIX INSTRUCTIONS:
<precise instructions — reference file names, function names, and specific changes needed>
"""
        response_text, tokens = self._call_llm(state, prompt)

        # Parse confidence
        import re
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
            state.retry_count += 1
            state.log(
                self.name,
                tokens=tokens,
                notes=f"Fix ready (confidence {confidence}/5, retry #{state.retry_count})",
            )

        return state
