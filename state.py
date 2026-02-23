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
    description: str            # what this file does / what change to make
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
    language: str = "auto"         # target BE language: python|java|nodejs|go|etc.

    # ── User Rules (§5 in architecture) ─────────────────────────────────────
    user_rules: str = ""           # loaded RULES.md content
    active_rules_file: str = ""    # e.g. "rules/spring-boot.md"

    # ── Architect fields ─────────────────────────────────────────────────────
    plan: list[PlanItem] = field(default_factory=list)
    plan_summary: str = ""         # human-readable plan for display
    task_checklist: str = ""       # numbered implementation checklist from Architect
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
    static_analysis_output: Optional[str] = None                   # populated by Tester with static errors

    # ── Debugger fields ──────────────────────────────────────────────────────
    error_log: Optional[str] = None
    fix_instructions: Optional[str] = None
    retry_count: int = 0

    # ── Integration Agent fields ──────────────────────────────────────────
    integration_test_output: Optional[str] = None  # per-endpoint result log
    integration_passed: Optional[bool] = None       # None = not yet run

    # ── Writer fields ────────────────────────────────────────────────────────
    docs_updated: bool = False

    # ── DevOps fields (opt-in via --devops flag) ─────────────────────────────
    devops_mode: Optional[str] = None                              # "docker" | "k8s" | "all" | None
    devops_files: dict[str, str] = field(default_factory=dict)    # path → content

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
        """Return True only when both static analysis and runtime tests pass."""
        has_static_errors = bool(self.static_analysis_output)
        has_runtime_errors = self.test_output.get("returncode", 1) != 0
        return not has_static_errors and not has_runtime_errors

    def to_dict(self) -> dict:
        """Serialize state to a JSON-compatible dict (for checkpoints)."""
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineState":
        """Restore state from a checkpoint dict. Handles older checkpoints gracefully."""
        plan = [PlanItem(**p) for p in data.pop("plan", [])]
        trail = [AuditEntry(**e) for e in data.pop("audit_trail", [])]
        test_files = data.pop("test_files", {})
        devops_files = data.pop("devops_files", {})
        # Pop fields added later so old checkpoints don't raise TypeError
        data.setdefault("static_analysis_output", None)
        data.setdefault("task_checklist", "")
        data.setdefault("devops_mode", None)
        data.setdefault("language", "auto")
        data.setdefault("integration_test_output", None)
        data.setdefault("integration_passed", None)
        state = cls(**data)
        state.plan = plan
        state.audit_trail = trail
        state.test_files = test_files
        state.devops_files = devops_files
        return state
