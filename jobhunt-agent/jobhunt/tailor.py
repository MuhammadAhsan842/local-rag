"""
Truthful tailoring engine.

The rule that makes this world-class instead of a liability:
    Every bullet in a tailored resume MUST trace back to a real fact you gave us.
The engine can REPHRASE, REORDER, and EMPHASIZE your real experience toward a JD.
It must NOT invent skills, employers, metrics, or titles.

How the guardrail works:
1. Your profile is a list of atomic, ID'd facts (see profile.example.yaml).
2. The model is asked to produce tailored bullets, each tagged with the fact_id
   it derives from.
3. A faithfulness gate drops any bullet whose fact_id is invalid — i.e. anything
   the model tried to make up gets stripped before it ever reaches your resume.

If a JD needs a skill you genuinely don't have, the engine reports it as a real
gap to go *learn/build*, not a blank to fill with fiction.
"""
from __future__ import annotations

import json
import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

TAILOR_PROMPT = """You tailor a candidate's REAL experience to a specific job.
You may rephrase, reorder, and emphasize. You must NOT invent anything.

You are given FACTS, each with an id. Produce resume bullets that highlight the
facts most relevant to the JOB. Each bullet MUST be grounded in exactly one fact
and MUST include that fact's id. Do not state anything not supported by its fact.

FACTS:
{facts}

JOB TITLE: {title}
JOB NEEDS: {needs}

Return ONLY JSON: {{"bullets": [{{"fact_id": "F3", "text": "..."}}, ...]}}
Order bullets by relevance to the job. Max 8 bullets."""


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


# JSON schema handed to Ollama so the model is *constrained* to valid output
# instead of us parse-and-praying. (Upgrade path: Instructor/Outlines for full
# Pydantic validation — this schema is the same contract, dependency-free.)
_BULLETS_SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["fact_id", "text"],
            },
        }
    },
    "required": ["bullets"],
}


def _call(prompt: str, model: str) -> dict | None:
    """One schema-constrained Ollama call. Returns parsed dict or None on failure."""
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False,
                  "think": False, "format": _BULLETS_SCHEMA,
                  "options": {"temperature": 0.3}},
            timeout=180,
        )
        r.raise_for_status()
        return _extract_json(r.json().get("response", "")) or {}
    except Exception:  # noqa: BLE001
        return None


def tailor(facts: list[dict], job_title: str, needs: list[str], model: str) -> dict:
    """
    Returns {"bullets": [...verified...], "dropped": N, "used_fact_ids": [...]}
    'dropped' counts bullets the faithfulness gate rejected (attempted fabrication
    or malformed grounding) — a signal worth watching.
    """
    from . import eval as eval_mod  # local import avoids a cycle

    fact_map = {f["id"]: f for f in facts}
    base_prompt = TAILOR_PROMPT.format(
        facts=_facts_block(facts),
        title=job_title,
        needs=", ".join(needs) or "general AI/ML engineering",
    )

    kept, dropped, rejected_notes = [], 0, []
    prompt = base_prompt
    # Agentic self-correction: generate, verify each bullet with the faithfulness
    # metric (grounding + numeric honesty + support), and if any were rejected,
    # re-ask ONCE telling the model exactly what to fix. This is the retry/critic
    # loop that separates an agent from a single-shot call.
    for attempt in range(2):
        parsed = _call(prompt, model)
        if parsed is None:
            if attempt == 0:
                continue
            return {"bullets": [], "dropped": 0, "used_fact_ids": [],
                    "error": "model call failed"}
        kept, dropped, rejected_notes = [], 0, []
        for b in parsed.get("bullets", []):
            cand = {"fact_id": b.get("fact_id"), "text": (b.get("text") or "").strip()}
            verdict = eval_mod.evaluate_bullet(cand, fact_map)
            if verdict["supported"] and cand["text"]:
                kept.append(cand)
            else:
                dropped += 1  # faithfulness gate: ungrounded/fabricated => discarded
                rejected_notes.append(f"- \"{cand['text'][:80]}\" ({verdict['reason']})")
        if dropped == 0 or attempt == 1:
            break
        # ask again, showing what failed
        prompt = base_prompt + (
            "\n\nYour previous attempt had bullets REJECTED for not tracing to a "
            "fact (no invented numbers, skills, or employers):\n"
            + "\n".join(rejected_notes)
            + "\nRewrite so every bullet is fully supported by its fact."
        )

    return {
        "bullets": kept,
        "dropped": dropped,
        "used_fact_ids": [b["fact_id"] for b in kept],
    }


def render_resume(header: str, tailored: dict, facts: list[dict]) -> str:
    """Assemble a plain-text tailored resume section from verified bullets."""
    fact_map = {f["id"]: f for f in facts}
    lines = [header, "", "RELEVANT EXPERIENCE (tailored to this role)", ""]
    for b in tailored["bullets"]:
        src = fact_map.get(b["fact_id"], {})
        tag = f"  [{src.get('source', b['fact_id'])}]"
        lines.append(f"• {b['text']}{tag}")
    if tailored.get("dropped"):
        lines.append("")
        lines.append(f"(note: {tailored['dropped']} generated bullet(s) dropped as unverifiable)")
    return "\n".join(lines)
