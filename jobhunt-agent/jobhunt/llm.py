"""
Local-model scoring via Ollama, with a deterministic keyword fallback so the
agent still works if Ollama isn't running.

Ollama REST API: POST http://localhost:11434/api/generate
No extra dependency needed — it's just HTTP.
"""
from __future__ import annotations

import json
import re
import requests

from .models import Job

OLLAMA_URL = "http://localhost:11434/api/generate"

SCORING_PROMPT = """You are screening a job for a specific candidate. Be blunt and realistic.

CANDIDATE PROFILE:
{profile}

JOB:
Title: {title}
Company: {company}
Location: {location}
Salary: {salary}
Description (truncated): {description}

Return ONLY a JSON object, no prose, no markdown fences, with these keys:
- "fit_score": integer 0-100, how well this candidate matches THIS role
- "reasons": one sentence on why it fits (or doesn't)
- "red_flags": one short sentence on any mismatch/risk, or "" if none
- "hook": one sentence the candidate could open an application with, tailored to this role

JSON:"""


def _extract_json(text: str) -> dict | None:
    """Local models often wrap JSON in fences or chatter. Pull out the object."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def ollama_available(model: str) -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        # accept exact or prefix match (e.g. "qwen2.5" vs "qwen2.5:7b")
        return any(n == model or n.startswith(model.split(":")[0]) for n in names)
    except Exception:  # noqa: BLE001
        return False


def score_with_ollama(job: Job, profile: str, model: str) -> dict | None:
    prompt = SCORING_PROMPT.format(
        profile=profile[:4000],
        title=job.title,
        company=job.company,
        location=job.location,
        salary=job.salary or "not listed",
        description=job.description[:2500],
    )
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,            # Qwen3 reasoning burns time and breaks JSON
                "format": "json",          # ask Ollama to constrain to JSON
                "options": {"temperature": 0.2},
            },
            timeout=180,
        )
        r.raise_for_status()
        raw = r.json().get("response", "")
        return _extract_json(raw)
    except Exception as e:  # noqa: BLE001
        print(f"  ! ollama error on '{job.title[:40]}': {e}")
        return None


# --------------------------------------------------------------------------- #
# Fallback: keyword-overlap scoring (no model needed)
# --------------------------------------------------------------------------- #
def keyword_score(job: Job, must: list[str], nice: list[str], exclude: list[str]) -> dict:
    text = job.searchable
    for bad in exclude:
        if bad.lower() in text:
            return {"fit_score": 0, "reasons": f"excluded term '{bad}'", "red_flags": bad, "hook": ""}

    must_hits = sum(1 for k in must if k.lower() in text)
    nice_hits = sum(1 for k in nice if k.lower() in text)

    if must and must_hits == 0:
        base = 10
    else:
        must_ratio = (must_hits / len(must)) if must else 0.5
        base = int(60 * must_ratio + min(nice_hits, 8) * 5)
    base = max(0, min(base, 100))

    matched = [k for k in (must + nice) if k.lower() in text][:6]
    return {
        "fit_score": base,
        "reasons": f"keyword match: {', '.join(matched) or 'weak'}",
        "red_flags": "" if must_hits else "missing core skills",
        "hook": "",
    }
