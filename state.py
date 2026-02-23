"""
state.py — Shared Pipeline State for BE Multi-Agent Workflow

PipelineState is the single source of truth passed between the Orchestrator
and all agents. No direct agent-to-agent communication; everything goes via
this object.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from config import Status


@dataclass
class AuditEntry:
    """One log entry per agent run."""
    agent: str
    status: str
    tokens_used: int = 0
    duration_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    notes: str = ""


@dataclass
class PlanItem:
    """A single item in the Architect's plan."""
    file: str                   # relative path e.g. "src/auth/login.py"
    action: str                 # "CREATE" | "MODIFY" | "DELETE"
    description: str            # what this file should do
    api_contract: str = ""      # e.g. "POST /login → {token, user_id}"
    scope_estimate: str = ""    # e.g. "~50 lines"


@dataclass
class PipelineState:
    """
    The central state object for one pipeline run.
    The Orchestrator passes this into each agent, which mutates only its
    own designated fields and returns the updated state.
    """

    # ── Identity ────────────────────────────────────────────────────────────
    run_id: str = field(default_factory=lambda: f"{uuid.uuid4().hex[:8]}-{datetime.now().strftime('%Y%m%d-%H%M')}")
    status: str = Status.INIT

    # ── Input ────────────────────────────────────────────────────────────────
    task_prompt: str = ""          # original user request
    project_root: str = ""         # abs path to target project

    # ── User Rules (§5 in architecture) ─────────────────────────────────────
    user_rules: str = ""           # loaded RULES.md content
    active_rules_file: str = ""    # e.g. "rules/spring-boot.md"

    # ── Architect fields ─────────────────────────────────────────────────────
    plan: list[PlanItem] = field(default_factory=list)
    plan_summary: str = ""         # human-readable plan for display
    replan_count: int = 0

    # ── Human gate fields ────────────────────────────────────────────────────
    plan_approved: bool = False
    user_feedback: Optional[str] = None  # if user requests changes

    # ── Coder fields ─────────────────────────────────────────────────────────
    generated_files: dict[str, str] = field(default_factory=dict)  # path → content

    # ── Reviewer fields ──────────────────────────────────────────────────────
    review_notes: Optional[str] = None
    review_retry_count: int = 0

    # ── Tester fields ────────────────────────────────────────────────────────
    test_files: dict[str, str] = field(default_factory=dict)       # path → content
    test_output: dict[str, Any] = field(default_factory=dict)      # {returncode, stdout, stderr}

    # ── Debugger fields ──────────────────────────────────────────────────────
    error_log: Optional[str] = None
    fix_instructions: Optional[str] = None
    retry_count: int = 0

    # ── Writer fields ────────────────────────────────────────────────────────
    docs_updated: bool = False

    # ── Audit trail ──────────────────────────────────────────────────────────
    audit_trail: list[AuditEntry] = field(default_factory=list)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def log(self, agent: str, notes: str = "", tokens: int = 0, duration_ms: int = 0) -> None:
        """Append a timestamped entry to the audit trail."""
        self.audit_trail.append(AuditEntry(
            agent=agent,
            status=self.status,
            tokens_used=tokens,
            duration_ms=duration_ms,
            notes=notes,
        ))

    def test_passed(self) -> bool:
        return self.test_output.get("returncode", 1) == 0

    def to_dict(self) -> dict:
        """Serialize state to a JSON-compatible dict (for checkpoints)."""
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineState":
        """Restore state from a checkpoint dict."""
        plan = [PlanItem(**p) for p in data.pop("plan", [])]
        trail = [AuditEntry(**e) for e in data.pop("audit_trail", [])]
        test_files = data.pop("test_files", {})
        state = cls(**data)
        state.plan = plan
        state.audit_trail = trail
        state.test_files = test_files
        return state
