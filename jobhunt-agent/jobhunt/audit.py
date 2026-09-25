"""
Observability / audit trail (gaps #3, #6).

Research names a persisted trace of tool calls, retries and outcomes the #1
production practice. This writes one JSON object per event to an append-only
log, so every score/tailor/apply is reconstructable after the fact — and so
"applied" can be backed by evidence, not just an agent's self-report.

stdlib only. Safe to call from anywhere; failures to log never break a run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

LOG_FILE = "runs.jsonl"


def log(event: str, **fields) -> None:
    """Append one structured event. Never raises."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    try:
        with open(LOG_FILE, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # noqa: BLE001 - logging must never break the pipeline
        pass


def tail(n: int = 20, path: str = LOG_FILE) -> list[dict]:
    """Read the last n events (for the dashboard / debugging)."""
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
