"""
Parse the user's real CV into profile facts (gap #1, ingest side).

Turns an existing resume (PDF / DOCX / TXT / MD) into:
  - free text  -> profile.md (what the matcher compares against)
  - atomic facts -> profile.yaml facts (what tailoring is grounded on)

PDF/DOCX extraction uses optional libs if present (pdfminer.six / PyPDF /
python-docx); otherwise it reads text files directly and tells you how to
export. Fact extraction is heuristic by default (bullet/line splitting) and can
be upgraded to an LLM pass for cleaner atomic facts.
"""
from __future__ import annotations

import re
from pathlib import Path


def extract_text(path: str) -> str:
    """Best-effort text extraction. Returns '' if the format needs a missing lib."""
    p = Path(path)
    if not p.exists():
        return ""
    ext = p.suffix.lower()
    if ext in (".txt", ".md"):
        return p.read_text(errors="ignore")
    if ext == ".pdf":
        # try pdfminer.six, then pypdf/PyPDF2
        try:
            from pdfminer.high_level import extract_text as pdf_text
            return pdf_text(str(p)) or ""
        except Exception:  # noqa: BLE001
            pass
        for mod in ("pypdf", "PyPDF2"):
            try:
                reader = __import__(mod).PdfReader(str(p))
                return "\n".join((pg.extract_text() or "") for pg in reader.pages)
            except Exception:  # noqa: BLE001
                continue
        print("  ! PDF parsing needs a lib: pip install pdfminer.six (or pypdf)")
        return ""
    if ext == ".docx":
        try:
            from docx import Document
            return "\n".join(par.text for par in Document(str(p)).paragraphs)
        except Exception:  # noqa: BLE001
            print("  ! DOCX parsing needs python-docx: pip install python-docx")
            return ""
    return ""


_BULLET = re.compile(r"^\s*[-•*▪◦·]\s+(.*)")


def heuristic_facts(text: str, max_facts: int = 25) -> list[dict]:
    """
    Cheap, deterministic: treat bullet lines and substantial sentences as
    candidate atomic facts. A reviewable starting point (the user edits before
    tailoring trusts them). Upgrade path: llm_facts() for cleaner extraction.
    """
    facts, seen = [], set()
    for raw in text.splitlines():
        line = raw.strip()
        m = _BULLET.match(raw)
        cand = m.group(1).strip() if m else (line if len(line.split()) >= 6 else "")
        # keep lines that look like achievements/experience, drop headers/contact
        if not cand or "@" in cand or cand.lower() in seen:
            continue
        if len(cand) < 25 or len(cand) > 300:
            continue
        seen.add(cand.lower())
        facts.append({"id": f"F{len(facts) + 1}",
                      "source": "CV (auto-extracted — review)",
                      "text": cand})
        if len(facts) >= max_facts:
            break
    return facts


def llm_facts(text: str, model: str) -> list[dict] | None:
    """Optional: ask local Ollama to rewrite the CV into atomic, true facts."""
    import json
    import requests
    prompt = (
        "Extract the candidate's real experience from this CV as a JSON list of "
        "atomic, factual bullets. Do NOT invent anything. Each item: "
        '{"source": "<employer/project>", "text": "<one concrete achievement>"}.\n\n'
        f"CV:\n{text[:8000]}\n\nReturn ONLY JSON: {{\"facts\": [...]}}"
    )
    try:
        r = requests.post("http://localhost:11434/api/generate",
                          json={"model": model, "prompt": prompt, "stream": False,
                                "format": "json", "options": {"temperature": 0.1}},
                          timeout=180)
        r.raise_for_status()
        raw = r.json().get("response", "")
        s, e = raw.find("{"), raw.rfind("}")
        items = json.loads(raw[s:e + 1]).get("facts", []) if s != -1 else []
    except Exception:  # noqa: BLE001
        return None
    out = []
    for i, it in enumerate(items, 1):
        txt = (it.get("text") or "").strip()
        if txt:
            out.append({"id": f"F{i}", "source": it.get("source", "CV"), "text": txt})
    return out or None


def parse_to_profile(cv_path: str, model: str | None = None, use_llm: bool = False,
                     md_out: str = "profile.md", yaml_out: str = "profile.yaml") -> dict:
    """
    Full pipeline: CV file -> profile.md + profile.yaml (facts).
    Returns {"text_chars": n, "facts": k, "md": path, "yaml": path} or an error.
    Never overwrites an existing profile.yaml's contact block silently — it
    writes a *review* file when one already exists.
    """
    import yaml as _yaml
    text = extract_text(cv_path)
    if not text.strip():
        return {"error": f"could not extract text from {cv_path}"}
    facts = (llm_facts(text, model) if (use_llm and model) else None) or heuristic_facts(text)

    Path(md_out).write_text(text.strip() + "\n")
    # preserve an existing contact block if present
    contact = {"name": "", "email": "", "phone": "", "location": "",
               "linkedin": "", "github": ""}
    if Path(yaml_out).exists():
        try:
            existing = _yaml.safe_load(Path(yaml_out).read_text()) or {}
            contact = existing.get("contact", contact)
            yaml_out = yaml_out.replace(".yaml", ".review.yaml")  # don't clobber
        except Exception:  # noqa: BLE001
            pass
    Path(yaml_out).write_text(_yaml.safe_dump({"contact": contact, "facts": facts},
                                              sort_keys=False, allow_unicode=True))
    return {"text_chars": len(text), "facts": len(facts), "md": md_out, "yaml": yaml_out}
