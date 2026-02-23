"""
tools/git_tools.py — Git helpers for the Writer agent (optional)
"""

from __future__ import annotations

from tools.shell_tools import run_command


def git_diff(cwd: str) -> str:
    """Return the current git diff (staged + unstaged)."""
    result = run_command("git diff HEAD", cwd=cwd)
    return result["stdout"] or "(no diff)"


def git_stage_all(cwd: str) -> dict:
    """Stage all changes."""
    return run_command("git add -A", cwd=cwd)


def git_commit(cwd: str, message: str) -> dict:
    """Commit all staged changes with the given message."""
    return run_command(["git", "commit", "-m", message], cwd=cwd)


def git_status(cwd: str) -> str:
    """Return a short git status."""
    result = run_command("git status --short", cwd=cwd)
    return result["stdout"] or "(clean)"


def git_current_branch(cwd: str) -> str:
    """Return the current branch name."""
    result = run_command("git rev-parse --abbrev-ref HEAD", cwd=cwd)
    return result["stdout"].strip()


def is_git_repo(cwd: str) -> bool:
    """Check whether the directory is inside a git repo."""
    result = run_command("git rev-parse --is-inside-work-tree", cwd=cwd)
    return result["returncode"] == 0
