"""
tools/rules_loader.py — Load and validate user coding rules (RULES.md)

Rules are loaded once at startup by the Orchestrator and stored in
PipelineState.user_rules. They are then injected into every agent's
system prompt.
"""

from __future__ import annotations

from pathlib import Path
from config import DEFAULT_RULES_FILE


def load_rules(rules_file: str | Path | None = None) -> str:
    """
    Load a RULES.md file and return its content as a string.

    Args:
        rules_file: Path to the rules file. Falls back to DEFAULT_RULES_FILE.

    Returns:
        The raw markdown content of the rules file, or an empty string if
        no rules file is found.
    """
    path = Path(rules_file) if rules_file else DEFAULT_RULES_FILE

    if not path.exists():
        print(f"[Rules] No rules file found at {path}. Running without custom rules.")
        return ""

    content = path.read_text(encoding="utf-8").strip()
    print(f"[Rules] Loaded rules from: {path} ({len(content)} chars)")
    return content


def validate_rules(rules_content: str) -> list[str]:
    """
    Basic validation of rules content.
    Returns a list of warnings (empty list = all good).
    """
    warnings = []
    if not rules_content:
        warnings.append("Rules content is empty.")
        return warnings
    if len(rules_content) > 8000:
        warnings.append(
            f"Rules content is very long ({len(rules_content)} chars). "
            "Consider trimming to keep prompts efficient."
        )
    return warnings


def build_rules_block(user_rules: str) -> str:
    """
    Format the rules content into the system prompt block that is injected
    into every agent's prompt.
    """
    if not user_rules:
        return ""
    return (
        "\n\n[USER CODING RULES — YOU MUST FOLLOW THESE IN ALL OUTPUT]\n"
        "═══════════════════════════════════════════════════════════\n"
        + user_rules
        + "\n═══════════════════════════════════════════════════════════\n"
    )
