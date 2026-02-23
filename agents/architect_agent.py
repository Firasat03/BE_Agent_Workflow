"""
agents/architect_agent.py — Architect Agent

Role: "Senior Backend Architect"
- Reads the task prompt and project file tree
- Queries the knowledge base (via MCP) for relevant patterns
- Produces a structured plan (list of PlanItem) + a numbered Task Checklist
  + a human-readable summary
- On re-plan: incorporates user feedback
"""

from __future__ import annotations

import json
import re

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState, PlanItem
from tools.file_tools import file_tree
from tools.mcp_client import get_client


class ArchitectAgent(BaseAgent):
    name = "Architect"
    system_role = (
        "You are a Senior Backend Architect with 15+ years of experience designing "
        "production-grade, cloud-native backend systems in Python, Java, Node.js, Go, "
        "Kotlin, Rust, C#, Ruby, and PHP. "
        "Your job is to analyse a task and produce:\n"
        "  1. A precise, minimal implementation plan (JSON)\n"
        "  2. A numbered Task Checklist that a developer will follow step-by-step\n"
        "  3. A concise human-readable summary\n\n"
        "Think carefully about: files to create/modify, API contracts, DB schema changes, "
        "auth/security considerations, error handling strategy, and scope of work. "
        "Use idiomatic patterns for the requested language — not Python patterns for a Java project. "
        "Never skip error handling or validation — production quality is mandatory. "
        "Always output all three parts in the exact format requested."
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
            feedback_block = (
                f"\n\nUSER FEEDBACK ON PREVIOUS PLAN:\n{state.user_feedback}\n"
                "You MUST incorporate all feedback points. Do not re-produce any item the user rejected."
            )

        # ── Prompt ────────────────────────────────────────────────────────
        lang_hint = (
            f"\nTARGET LANGUAGE: {state.language}  "
            "(Use idiomatic conventions, file layout, and dependency management for this language.)"
            if state.language and state.language != "auto"
            else ""
        )

        prompt = f"""
Task: {state.task_prompt}
{lang_hint}
Project file tree:
{tree or "(empty / new project)"}
{kb_context}
{feedback_block}

Produce your output in THREE parts EXACTLY (do not omit any part):

─────────────────────────────────────────────────
PART 1 — JSON plan (inside a ```json block):
─────────────────────────────────────────────────
A JSON array of plan items, each with keys:
  - "file":           relative path (e.g. "src/auth/login.py")
  - "action":         "CREATE" | "MODIFY" | "DELETE"
  - "description":    what this file does / what specific change to make (be detailed)
  - "api_contract":   full API signature if applicable, e.g. "POST /login → 200 {{token, user_id}} | 401 {{error}}", else ""
  - "scope_estimate": approximate lines of code, else ""

─────────────────────────────────────────────────
PART 2 — Task Checklist (between CHECKLIST_START and CHECKLIST_END markers):
─────────────────────────────────────────────────
CHECKLIST_START
1. <First concrete implementation step>
2. <Second step>
...
N. <Final step>
CHECKLIST_END

Each checklist item should be one actionable sentence a developer can execute independently.
Include: dependency installation, DB migrations, env var setup, implementation steps, testing.

─────────────────────────────────────────────────
PART 3 — Human-readable summary (after CHECKLIST_END):
─────────────────────────────────────────────────
A concise bullet-point plan a developer can read and approve/reject.
Include: files list, API shape, DB changes (if any), dependencies (if any),
security considerations, error handling strategy, estimated scope.
"""
        response_text, tokens = self._call_llm(state, prompt)

        # ── Parse JSON plan ───────────────────────────────────────────────
        try:
            raw_plan = self._extract_json(response_text)
            state.plan = [PlanItem(**item) for item in raw_plan]
        except Exception as e:
            # Fallback: store raw response so user can review
            state.plan = []
            state.plan_summary = f"[Plan parsing failed: {e}]\n\n{response_text}"
            state.task_checklist = ""
            state.log(self.name, notes=f"Plan parsing error: {e}", tokens=tokens)
            return state

        # ── Parse Task Checklist ──────────────────────────────────────────
        checklist_match = re.search(
            r"CHECKLIST_START\s*(.+?)\s*CHECKLIST_END",
            response_text,
            re.DOTALL,
        )
        state.task_checklist = checklist_match.group(1).strip() if checklist_match else ""

        # ── Extract human summary (text after CHECKLIST_END) ──────────────
        summary_match = re.search(r"CHECKLIST_END\s*(.+)$", response_text, re.DOTALL)
        if summary_match:
            state.plan_summary = summary_match.group(1).strip()
        else:
            # Fallback: everything after the closing json fence
            fallback = re.search(r"```(?:json)?.*?```(.+)$", response_text, re.DOTALL)
            state.plan_summary = fallback.group(1).strip() if fallback else response_text

        state.replan_count += 1
        state.plan_approved = False  # reset — human must approve again
        state.log(self.name, tokens=tokens, notes=f"{len(state.plan)} plan items")
        return state
