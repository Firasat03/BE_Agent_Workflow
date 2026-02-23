"""
agents/reviewer_agent.py — Reviewer Agent

Role: "Senior Code Reviewer"
- Checks generated files for: coding standards, security issues, style,
  DRY violations, API contract alignment, and user-defined coding rules
- Returns a PASS verdict (pipeline continues) or REJECT verdict (Coder retries)
"""

from __future__ import annotations

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState


class ReviewerAgent(BaseAgent):
    name = "Reviewer"
    system_role = (
        "You are a Senior Code Reviewer and Security Specialist with 15+ years of backend experience. "
        "You enforce production-grade standards. Your review MUST cover all of the following:\n\n"
        "1. Correctness — does the code implement the plan accurately? Any missing logic?\n"
        "2. API contract alignment — do all endpoints/signatures exactly match the plan?\n"
        "3. Security:\n"
        "   - SQL injection / NoSQL injection risks\n"
        "   - Unvalidated / unsanitised inputs\n"
        "   - Secret / credential leaks in code or logs\n"
        "   - Missing authentication / authorisation guards\n"
        "   - Insecure defaults (debug mode, wide CORS, open endpoints)\n"
        "4. Error handling:\n"
        "   - All exceptions caught and handled gracefully\n"
        "   - No bare 'except' clauses\n"
        "   - HTTP responses always include a meaningful error message\n"
        "5. Observability:\n"
        "   - Structured logging on all significant operations\n"
        "   - No PII / secrets logged\n"
        "6. Data integrity:\n"
        "   - DB operations use correct transaction boundaries\n"
        "   - No N+1 query risks\n"
        "7. Code quality:\n"
        "   - DRY — no unnecessary duplication\n"
        "   - Naming conventions consistent with the rest of the codebase\n"
        "   - Complex logic documented with inline comments\n"
        "8. User coding rules (RULES.md) — any violation is an automatic REJECT\n\n"
        "Be strict but constructive. Quote the specific line or function causing the issue. "
        "Always end with exactly one verdict line."
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.REVIEWING

        if not state.generated_files:
            state.review_notes = "No files to review."
            state.log(self.name, notes="skip — no files")
            return state

        files_block = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in state.generated_files.items()
        )

        prompt = f"""
Review the following generated backend code strictly.

ORIGINAL TASK: {state.task_prompt}

ARCHITECT'S PLAN SUMMARY:
{state.plan_summary}

GENERATED FILES:
{files_block}

Perform a thorough review covering:
1. Correctness — does the code implement the plan accurately?
2. API contract alignment — do endpoints/signatures match the plan?
3. Security — any SQL injection, unvalidated inputs, secret leaks, etc.?
4. Error handling — are exceptions handled properly?
5. Code style & naming conventions
6. DRY — any unnecessary duplication?
7. User coding rules compliance (check all rules in the [USER CODING RULES] block)

End your review with ONE of these exact verdict lines:
VERDICT: PASS
or
VERDICT: REJECT
REASON: <brief explanation of what must be fixed>
"""
        response_text, tokens = self._call_llm(state, prompt)
        state.review_notes = response_text

        verdict = _parse_verdict(response_text)
        if verdict == "REJECT":
            state.review_retry_count += 1
            state.log(self.name, tokens=tokens, notes="REJECTED")
            # Signal to Orchestrator to send back to Coder
            # Orchestrator checks review_retry_count vs MAX_REVIEW_RETRIES
        else:
            state.log(self.name, tokens=tokens, notes="PASSED")

        return state


def _parse_verdict(text: str) -> str:
    import re
    match = re.search(r"VERDICT:\s*(PASS|REJECT)", text, re.IGNORECASE)
    return match.group(1).upper() if match else "PASS"  # default to PASS if unclear
