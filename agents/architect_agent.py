"""
agents/architect_agent.py — Architect Agent

Role: "Senior Backend Architect"
- Reads the task prompt and project file tree
- Queries the knowledge base (via MCP) for relevant patterns
- Produces a structured plan (list of PlanItem) + human-readable summary
- On re-plan: incorporates user feedback
"""

from __future__ import annotations

import json

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState, PlanItem
from tools.file_tools import file_tree
from tools.mcp_client import get_client


class ArchitectAgent(BaseAgent):
    name = "Architect"
    system_role = (
        "You are a Senior Backend Architect with 15+ years of experience. "
        "Your job is to analyse a task and produce a precise, minimal implementation plan. "
        "Think carefully about what files need to be created or modified, the API shape, "
        "any DB changes, and the scope of work. "
        "Always output a JSON plan followed by a human-readable summary."
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.ARCHITECT

        # ── Gather context ────────────────────────────────────────────────
        tree = ""
        if state.project_root:
            try:
                tree = file_tree(state.project_root)
            except Exception:
                tree = "(could not read project tree)"

        # ── MCP: optional knowledge base query ────────────────────────────
        kb_context = ""
        try:
            mcp = get_client("architect")
            if "knowledge-base" in mcp.list_allowed_servers():
                result = mcp.call("knowledge-base", "query", query=state.task_prompt)
                if result.get("results"):
                    kb_context = "\nRelevant past patterns from knowledge base:\n" + "\n".join(
                        str(r) for r in result["results"]
                    )
        except Exception:
            pass  # KB is optional; continue without it

        # ── Build feedback context (for re-plans) ─────────────────────────
        feedback_block = ""
        if state.user_feedback:
            feedback_block = f"\n\nUSER FEEDBACK ON PREVIOUS PLAN:\n{state.user_feedback}\nIncorporate this feedback."

        # ── Prompt ────────────────────────────────────────────────────────
        prompt = f"""
Task: {state.task_prompt}

Project file tree:
{tree or "(empty / new project)"}
{kb_context}
{feedback_block}

Produce your output in TWO parts EXACTLY:

PART 1 — JSON plan (inside a ```json block):
A JSON array of plan items, each with keys:
  - "file":           relative path (e.g. "src/auth/login.py")
  - "action":         "CREATE" | "MODIFY" | "DELETE"
  - "description":    what this file does / what change to make
  - "api_contract":   API signature if applicable, else ""
  - "scope_estimate": approximate lines of code, else ""

PART 2 — Human-readable summary (outside the json block):
A short, bullet-point plan that a developer could read and approve/reject.
Include: files list, API shape, DB changes (if any), dependencies (if any), estimated scope.
"""
        response_text, tokens = self._call_llm(state, prompt)

        # ── Parse plan ────────────────────────────────────────────────────
        try:
            raw_plan = self._extract_json(response_text)
            state.plan = [PlanItem(**item) for item in raw_plan]
        except Exception as e:
            # Fallback: store raw response so user can review
            state.plan = []
            state.plan_summary = f"[Plan parsing failed: {e}]\n\n{response_text}"
            state.log(self.name, notes=f"Plan parsing error: {e}", tokens=tokens)
            return state

        # ── Extract human summary (text after the closing ```) ────────────
        import re
        summary_match = re.search(r"```(?:json)?.*?```(.+)$", response_text, re.DOTALL)
        state.plan_summary = summary_match.group(1).strip() if summary_match else response_text

        state.replan_count += 1
        state.plan_approved = False  # reset — human must approve again
        state.log(self.name, tokens=tokens, notes=f"{len(state.plan)} plan items")
        return state
