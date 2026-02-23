"""
tools/file_tools.py — File system helpers for agents
"""

from __future__ import annotations

import os
from pathlib import Path


def read_file(path: str) -> str:
    """Read and return the content of a file."""
    return Path(path).read_text(encoding="utf-8")


def write_file(path: str, content: str) -> None:
    """Write content to a file, creating parent directories if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def file_exists(path: str) -> bool:
    return Path(path).exists()


def list_files(root: str, extensions: list[str] | None = None) -> list[str]:
    """
    Recursively list all files under root.
    Optionally filter by extensions e.g. ['.py', '.java']
    Skips hidden directories (.git, .workflow, __pycache__, node_modules).
    """
    skip_dirs = {".git", ".workflow", "__pycache__", "node_modules", ".venv", "venv"}
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune hidden / build dirs in-place
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            if extensions is None or any(fname.endswith(ext) for ext in extensions):
                results.append(os.path.join(dirpath, fname))
    return sorted(results)


def file_tree(root: str, max_depth: int = 4) -> str:
    """
    Return a compact tree string of the project structure, suitable for
    injecting into an LLM prompt.
    """
    lines: list[str] = []

    def _walk(path: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        skip = {".git", ".workflow", "__pycache__", "node_modules", ".venv", "venv"}
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        for i, entry in enumerate(entries):
            if entry.name in skip:
                continue
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                _walk(entry, prefix + extension, depth + 1)

    lines.append(Path(root).name + "/")
    _walk(Path(root), "", 1)
    return "\n".join(lines)


def delete_file(path: str) -> None:
    """Delete a file if it exists."""
    p = Path(path)
    if p.exists():
        p.unlink()
