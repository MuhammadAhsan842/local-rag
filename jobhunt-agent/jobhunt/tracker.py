"""
The closed learning loop — what turns a radar into something that improves.

A tiny SQLite store of every application and its outcome. Two jobs:
  1. Memory  — "did I already apply to this?" survives across runs/machines.
  2. Feedback — learn which companies/sources actually convert (reply →
     interview) and feed a prior back into ranking, so roles like the ones
     that answered you rise over time.

No ORM, no server — stdlib sqlite3 only, so it runs anywhere.

Outcome ladder (higher = better signal):
    seen(0) < applied(1) < replied(2) < interview(3) < offer(4) < rejected(-1)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_FILE = "jobhunt.db"

STAGES = {"seen": 0, "applied": 1, "replied": 2, "interview": 3, "offer": 4, "rejected": -1}


@contextmanager
def _db(path: str = DB_FILE):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS applications (
                   job_key    TEXT PRIMARY KEY,
                   company    TEXT,
                   source     TEXT,
                   title      TEXT,
                   url        TEXT,
                   stage      TEXT DEFAULT 'seen',
                   fit_score  INTEGER DEFAULT 0,
                   created_at TEXT DEFAULT (datetime('now')),
                   updated_at TEXT DEFAULT (datetime('now'))
               )"""
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def record(job, stage: str = "applied", path: str = DB_FILE) -> None:
    """Upsert a job at a given stage (idempotent on job_key)."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}'; expected one of {list(STAGES)}")
    with _db(path) as conn:
        conn.execute(
            """INSERT INTO applications (job_key, company, source, title, url, stage, fit_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_key) DO UPDATE SET
                   stage=excluded.stage, updated_at=datetime('now')""",
            (job.key, job.company, job.source.split(":")[0], job.title, job.url,
             stage, getattr(job, "fit_score", 0)),
        )


def stage_of(job_key: str, path: str = DB_FILE) -> str | None:
    with _db(path) as conn:
        row = conn.execute("SELECT stage FROM applications WHERE job_key=?", (job_key,)).fetchone()
        return row["stage"] if row else None


def already_applied(job_key: str, path: str = DB_FILE) -> bool:
    return STAGES.get(stage_of(job_key, path) or "seen", 0) >= STAGES["applied"]


def conversion_priors(path: str = DB_FILE) -> dict[str, float]:
    """
    Per-source reply-or-better rate, as a 0..1 prior. A source where your
    applications actually get answered earns a boost; a black hole doesn't.
    Only sources with >=3 applications are counted (avoid noise).
    """
    priors: dict[str, float] = {}
    with _db(path) as conn:
        rows = conn.execute(
            """SELECT source,
                      SUM(CASE WHEN stage IN ('applied','replied','interview','offer','rejected')
                               THEN 1 ELSE 0 END) AS applied,
                      SUM(CASE WHEN stage IN ('replied','interview','offer')
                               THEN 1 ELSE 0 END) AS converted
                 FROM applications GROUP BY source"""
        ).fetchall()
    for r in rows:
        applied = r["applied"] or 0
        if applied >= 3:
            priors[r["source"]] = round((r["converted"] or 0) / applied, 3)
    return priors


def conversion_boost(job, priors: dict[str, float], scale: int = 10) -> int:
    """Translate a source's conversion prior into a bounded ranking bonus."""
    src = job.source.split(":")[0]
    if src not in priors:
        return 0
    # centre on 0.2 (a decent reply rate); above lifts, below dips, bounded ±scale
    return max(-scale, min(scale, round((priors[src] - 0.2) * scale * 5)))


def summary(path: str = DB_FILE) -> dict:
    with _db(path) as conn:
        rows = conn.execute("SELECT stage, COUNT(*) n FROM applications GROUP BY stage").fetchall()
    return {r["stage"]: r["n"] for r in rows}
