"""
config.py — Central configuration for BE Multi-Agent Workflow
"""

import os
from pathlib import Path

# ─── LLM ──────────────────────────────────────────────────────────────────────
# Choose your LLM provider by setting LLM_PROVIDER:
#   gemini       → Google Gemini   (needs GEMINI_API_KEY)
#   openai       → OpenAI GPT      (needs OPENAI_API_KEY)
#   anthropic    → Anthropic Claude (needs ANTHROPIC_API_KEY)
#   ollama       → Ollama local     (needs Ollama running on OLLAMA_BASE_URL)
#   openai_compat→ Any OpenAI-compatible API (needs LLM_BASE_URL + optional OPENAI_API_KEY)

LLM_PROVIDER   = os.getenv("LLM_PROVIDER",   "gemini")
LLM_MODEL      = os.getenv("LLM_MODEL",      "gemini-2.0-flash")
LLM_BASE_URL   = os.getenv("LLM_BASE_URL",   "")   # for openai_compat / ollama override

# Legacy aliases (still read by GeminiProvider if you use provider=gemini)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = LLM_MODEL   # kept for backward compat

# ─── Retry limits ─────────────────────────────────────────────────────────────
MAX_DEBUG_RETRIES  = int(os.getenv("MAX_DEBUG_RETRIES", "3"))   # Debugger→Coder→Tester
MAX_REVIEW_RETRIES = int(os.getenv("MAX_REVIEW_RETRIES", "1"))  # Reviewer→Coder

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR           = Path(__file__).parent
RULES_DIR          = BASE_DIR / "rules"
DEFAULT_RULES_FILE = RULES_DIR / "RULES.md"
PROMPTS_DIR        = BASE_DIR / "prompts"
WORKFLOW_DIR       = BASE_DIR / ".workflow"       # checkpoint storage (gitignored)
MCP_DIR            = BASE_DIR / "mcp"
MCP_CONFIG_FILE    = MCP_DIR / "agent_mcp_config.json"

# ─── LLM generation settings ──────────────────────────────────────────────────
GENERATION_CONFIG = {
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
    "top_p": 0.95,
    "max_output_tokens": int(os.getenv("LLM_MAX_TOKENS", "8192")),
}

# ─── Pipeline status enum values ──────────────────────────────────────────────
class Status:
    INIT        = "INIT"
    ARCHITECT   = "ARCHITECT"
    PLAN_REVIEW = "PLAN_REVIEW"
    CODING      = "CODING"
    REVIEWING   = "REVIEWING"
    TESTING     = "TESTING"
    DEBUGGING   = "DEBUGGING"
    WRITING     = "WRITING"
    DONE        = "DONE"
    FAILED      = "FAILED"
    ABORTED     = "ABORTED"
