"""
Matching engine: how well does a resume fit a job, and what's missing?

Two layers:
1. Semantic similarity  -> resume vs JD via local embeddings (nomic-embed-text),
   falling back to keyword Jaccard if Ollama embeddings aren't available.
2. Gap analysis         -> which JD-required skills the resume does NOT evidence.

The gap list is what drives tailoring: we only ever *surface real, relevant*
experience to close a gap — never invent one. (See tailor.py.)
"""
from __future__ import annotations

import math
import re
import requests

from .models import Job

EMBED_URL = "http://localhost:11434/api/embeddings"
_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text) if len(t) > 1}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed(text: str, model: str) -> list[float] | None:
    try:
        r = requests.post(EMBED_URL, json={"model": model, "prompt": text[:6000]}, timeout=60)
        r.raise_for_status()
        return r.json().get("embedding")
    except Exception:  # noqa: BLE001
        return None


def semantic_similarity(resume_text: str, job: Job, embed_model: str) -> float:
    """0..1 similarity. Uses embeddings if available, else keyword Jaccard."""
    jd = f"{job.title}\n{' '.join(job.tags)}\n{job.description}"
    ev, jv = _embed(resume_text, embed_model), _embed(jd, embed_model)
    if ev and jv:
        # cosine is -1..1; clamp to 0..1 for a friendly score
        return max(0.0, _cosine(ev, jv))
    # fallback: Jaccard over tokens
    a, b = _tokens(resume_text), _tokens(jd)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def gap_analysis(resume_text: str, required_skills: list[str]) -> dict:
    """Which required skills are (not) evidenced in the resume text."""
    rt = resume_text.lower()
    present = [s for s in required_skills if s.lower() in rt]
    missing = [s for s in required_skills if s.lower() not in rt]
    coverage = len(present) / len(required_skills) if required_skills else 1.0
    return {"present": present, "missing": missing, "coverage": round(coverage, 2)}


def extract_required_skills(job: Job, skill_vocab: list[str]) -> list[str]:
    """
    Cheap, deterministic skill extraction: match a known AI/ML skill vocabulary
    against the JD. (Upgrade path: replace with an LLM extraction call — see plan.)
    """
    text = job.searchable
    return [s for s in skill_vocab if s.lower() in text]


def match_report(resume_text: str, job: Job, skill_vocab: list[str], embed_model: str) -> dict:
    required = extract_required_skills(job, skill_vocab)
    gaps = gap_analysis(resume_text, required)
    sim = semantic_similarity(resume_text, job, embed_model)
    # blended fit: semantic understanding + concrete skill coverage
    fit = round(100 * (0.5 * sim + 0.5 * gaps["coverage"]))
    return {
        "fit": fit,
        "similarity": round(sim, 3),
        "required_skills": required,
        "matched_skills": gaps["present"],
        "missing_skills": gaps["missing"],
        "coverage": gaps["coverage"],
    }
