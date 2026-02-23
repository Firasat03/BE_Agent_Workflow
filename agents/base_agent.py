"""
agents/base_agent.py — Abstract base class for all agents

All agents inherit from BaseAgent and implement the run() method.
The Orchestrator calls agent.run(state) and receives the mutated state back.

LLM is fully pluggable — controlled by LLM_PROVIDER in config.py:
    LLM_PROVIDER=gemini      → Google Gemini (default)
    LLM_PROVIDER=openai      → OpenAI GPT
    LLM_PROVIDER=anthropic   → Anthropic Claude
    LLM_PROVIDER=ollama      → Ollama (local)
    LLM_PROVIDER=openai_compat → Any OpenAI-compatible URL
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from config import LLM_PROVIDER, LLM_MODEL, GENERATION_CONFIG
from tools.llm_provider import get_provider
from tools.rules_loader import build_rules_block

if TYPE_CHECKING:
    from state import PipelineState

# Build the provider once at import time (shared across all agent instances)
_provider = get_provider(LLM_PROVIDER, LLM_MODEL, GENERATION_CONFIG)


class BaseAgent(ABC):
    """
    Abstract base for all pipeline agents.

    Subclasses must define:
        - name: str                  — display name used in logs
        - system_role: str           — LLM persona (e.g. "Senior Backend Developer")
        - run(state) -> PipelineState
    """

    name: str = "BaseAgent"
    system_role: str = "You are a helpful AI assistant."

    # ── LLM call ────────────────────────────────────────────────────────────

    def _call_llm(self, state: "PipelineState", user_prompt: str) -> tuple[str, int]:
        """
        Build the full system prompt (role + user rules), call the configured
        LLM provider, and return (response_text, token_count).

        The provider is determined by LLM_PROVIDER in config.py — no code
        changes are needed to switch between Gemini, OpenAI, Anthropic, etc.
        """
        rules_block = build_rules_block(state.user_rules)
        system_prompt = f"[ROLE]\n{self.system_role}{rules_block}"
        return _provider.generate(system_prompt, user_prompt)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @abstractmethod
    def run(self, state: "PipelineState") -> "PipelineState":
        """Execute this agent's task and return the updated state."""
        ...

    def _timed_run(self, state: "PipelineState") -> "PipelineState":
        """Wrapper that times the run and logs to the audit trail."""
        start = time.time()
        result = self.run(state)
        elapsed_ms = int((time.time() - start) * 1000)
        result.log(agent=self.name, duration_ms=elapsed_ms)
        return result

    # ── Utilities ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_code_block(text: str, lang: str = "") -> str:
        """
        Extract the first fenced code block from an LLM response.
        Falls back to the full text if no fence is found.
        """
        import re
        pattern = rf"```{lang}\s*(.*?)```" if lang else r"```(?:\w+)?\s*(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else text.strip()

    @staticmethod
    def _extract_json(text: str) -> dict | list:
        """Extract and parse a JSON block from LLM response."""
        import json, re
        match = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
        raw = match.group(1).strip() if match else text.strip()
        return json.loads(raw)
