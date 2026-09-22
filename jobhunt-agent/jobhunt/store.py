"""Tracks which jobs we've already surfaced, so runs act as a 'new jobs' radar."""
from __future__ import annotations

import json
from pathlib import Path

STATE_FILE = Path("seen_jobs.json")


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except json.JSONDecodeError:
            return set()
    return set()


def save_seen(keys: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(keys), indent=0))
