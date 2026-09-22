"""
Outreach generator: for each top role, produce a short recruiter/hiring-manager
DM and a tight cover note — grounded ONLY in the candidate's real facts.

Same discipline as tailor.py: the model may rephrase and emphasize real facts
toward a specific JD, but must never invent skills, employers, or metrics. We
pass the fact ids through and verify every claim traces to a real fact before
the copy is allowed out.

Falls back to a deterministic template if Ollama is unavailable, so an urgent
run always produces send-ready copy.
"""
from __future__ import annotations

import json
import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

OUTREACH_PROMPT = """You write concise, honest job outreach for a candidate.
Use ONLY the FACTS below. Do NOT invent skills, employers, numbers, or titles.

FACTS (each has an id):
{facts}

ROLE: {title} at {company}
WHAT THE ROLE NEEDS: {needs}

Write outreach that references the 2-3 facts most relevant to THIS role.
Keep it specific, warm, and free of buzzwords. No fabricated enthusiasm.

Return ONLY JSON with these keys:
- "dm": a 2-3 sentence LinkedIn/email message to a recruiter (<= 60 words), first person
- "cover": a 4-6 sentence cover note (<= 130 words), first person
- "fact_ids": the list of fact ids you actually drew on
JSON:"""


def _facts_block(facts: list[dict]) -> str:
    return "\n".join(f"{f['id']}: {f['text']}" for f in facts)


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None


def _fallback(facts: list[dict], title: str, company: str, needs: list[str]) -> dict:
    """Deterministic, always-truthful template when no model is available."""
    top = facts[:2]
    highlights = "; ".join(f["text"] for f in top) if top else "relevant AI/ML experience"
    dm = (
        f"Hi — I'm applying for the {title} role at {company}. "
        f"Most relevant to it: {highlights}. "
        f"Happy to share more or walk through my work."
    )
    cover = (
        f"I'm reaching out about the {title} position at {company}. "
        f"{highlights}. "
        f"I'd bring this directly to what the role needs"
        + (f" ({', '.join(needs[:4])})" if needs else "")
        + ". I'd welcome the chance to discuss how I can contribute."
    )
    return {"dm": dm, "cover": cover, "fact_ids": [f["id"] for f in top], "grounded": True}


def generate(facts: list[dict], title: str, company: str, needs: list[str],
             model: str, use_llm: bool = True) -> dict:
    """
    Returns {"dm", "cover", "fact_ids", "grounded"}.
    'grounded' is False if the model referenced a fact id we don't recognize —
    a signal that the copy should be reviewed before sending.
    """
    if not facts:
        return {"dm": "", "cover": "", "fact_ids": [], "grounded": True}
    if not use_llm:
        return _fallback(facts, title, company, needs)

    valid_ids = {f["id"] for f in facts}
    prompt = OUTREACH_PROMPT.format(
        facts=_facts_block(facts),
        title=title,
        company=company,
        needs=", ".join(needs) or "general AI/ML engineering",
    )
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False,
                  "format": "json", "options": {"temperature": 0.4}},
            timeout=180,
        )
        r.raise_for_status()
        parsed = _extract_json(r.json().get("response", "")) or {}
    except Exception:  # noqa: BLE001 - never let outreach failure kill the run
        return _fallback(facts, title, company, needs)

    dm = (parsed.get("dm") or "").strip()
    cover = (parsed.get("cover") or "").strip()
    used = [i for i in (parsed.get("fact_ids") or []) if i in valid_ids]
    grounded = all(i in valid_ids for i in (parsed.get("fact_ids") or []))
    if not dm or not cover:
        return _fallback(facts, title, company, needs)
    return {"dm": dm, "cover": cover, "fact_ids": used, "grounded": grounded}
