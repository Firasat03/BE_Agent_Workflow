"""
tools/checkpoint_tools.py — Save, load, and list pipeline checkpoints

Checkpoints are JSON files written after each agent completes, stored under
  .workflow/<run-id>/state_<step>_<agent>.json

This allows crash recovery via --resume <run-id>.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from config import WORKFLOW_DIR


def _run_dir(run_id: str) -> Path:
    d = WORKFLOW_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_checkpoint(state, agent_name: str, step: int) -> str:
    """
    Serialize PipelineState to disk.
    Returns the path of the written checkpoint file.
    """
    run_dir = _run_dir(state.run_id)
    filename = f"state_{step:02d}_{agent_name}.json"
    path = run_dir / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2, default=str)

    return str(path)


def load_latest_checkpoint(run_id: str):
    """
    Load the most recent checkpoint for a given run_id.
    Returns a PipelineState instance, or None if no checkpoints exist.
    """
    from state import PipelineState

    run_dir = WORKFLOW_DIR / run_id
    if not run_dir.exists():
        return None

    checkpoints = sorted(run_dir.glob("state_*.json"))
    if not checkpoints:
        return None

    latest = checkpoints[-1]
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)

    print(f"[Checkpoint] Resuming from: {latest.name}")
    return PipelineState.from_dict(data)


def list_runs() -> list[dict]:
    """
    List all past workflow runs with their run_id, last checkpoint, and status.
    """
    if not WORKFLOW_DIR.exists():
        return []

    runs = []
    for run_dir in sorted(WORKFLOW_DIR.iterdir()):
        if not run_dir.is_dir():
            continue
        checkpoints = sorted(run_dir.glob("state_*.json"))
        if checkpoints:
            latest = checkpoints[-1]
            try:
                with open(latest, encoding="utf-8") as f:
                    data = json.load(f)
                runs.append({
                    "run_id":          run_dir.name,
                    "last_checkpoint": latest.name,
                    "status":          data.get("status", "?"),
                    "task_prompt":     data.get("task_prompt", "")[:80],
                    "checkpoints":     len(checkpoints),
                })
            except Exception:
                pass
    return runs
