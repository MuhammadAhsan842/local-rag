"""
Faithfulness evaluation — the accuracy backbone.

Almost no hobby job-bot measures whether its tailored output is *grounded*.
This module does, turning the anti-fabrication gate in tailor.py into a
CI-gateable number. It is inspired by DeepEval's Faithfulness metric but is
deterministic and dependency-free, so it runs anywhere with no model call.

For each tailored bullet we check three things against its source fact:
  1. GROUNDING   — the bullet's fact_id must reference a real fact.
  2. NUMERIC      — every number in the bullet must appear in the source fact
                    (this is what catches fabricated metrics like "cut cost 40%").
  3. LEXICAL      — enough content-word overlap with the source to count as
                    "supported" rather than a loosely-related claim.

faithfulness = supported_bullets / total_bullets   (1.0 = perfect, 0 = all made up)

Upgrade path (documented, not required): swap `faithfulness()` for DeepEval's
FaithfulnessMetric with a local Ollama judge for semantic (not just lexical)
entailment. Calibrate any LLM judge against a human-labeled set first — judge
models have measurable, published biases.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d+(?:[.,]\d+)?%?")
_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "as", "is", "was", "were", "be", "been", "built", "using",
    "used", "via", "over", "into", "that", "this", "it", "i", "my", "our",
}


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP and len(w) > 2}


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,").replace(",", "") for n in _NUM.findall(text)}


def evaluate_bullet(bullet: dict, fact_map: dict[str, dict],
                    lexical_threshold: float = 0.3) -> dict:
    """Judge one tailored bullet. Returns a verdict dict with a reason."""
    fid = bullet.get("fact_id")
    text = (bullet.get("text") or "").strip()
    if fid not in fact_map:
        return {"supported": False, "reason": f"ungrounded: fact_id '{fid}' unknown"}
    source = fact_map[fid]["text"]

    # 2. numeric consistency — fabricated metrics are the cardinal sin
    fabricated = _numbers(text) - _numbers(source)
    if fabricated:
        return {"supported": False,
                "reason": f"fabricated number(s) not in source: {sorted(fabricated)}"}

    # 3. lexical support — share of the bullet's content that traces to the fact
    bw = _content_words(text)
    if not bw:
        return {"supported": False, "reason": "empty/no content words"}
    overlap = len(bw & _content_words(source)) / len(bw)
    if overlap < lexical_threshold:
        return {"supported": False,
                "reason": f"weak support: only {overlap:.0%} of content traces to source"}
    return {"supported": True, "reason": f"grounded ({overlap:.0%} overlap)"}


def faithfulness(tailored: dict, facts: list[dict], lexical_threshold: float = 0.3) -> dict:
    """
    Score a tailor.tailor() result. Returns:
      {"score": 0..1, "supported": n, "total": n, "violations": [...]}
    score == 1.0 means every surfaced bullet is grounded, numerically honest,
    and lexically supported by a real fact.
    """
    fact_map = {f["id"]: f for f in facts}
    bullets = tailored.get("bullets", [])
    if not bullets:
        return {"score": 1.0, "supported": 0, "total": 0, "violations": [],
                "note": "no bullets produced"}
    violations, supported = [], 0
    for b in bullets:
        v = evaluate_bullet(b, fact_map, lexical_threshold)
        if v["supported"]:
            supported += 1
        else:
            violations.append({"text": b.get("text", ""), "reason": v["reason"]})
    return {
        "score": round(supported / len(bullets), 3),
        "supported": supported,
        "total": len(bullets),
        "violations": violations,
    }


def evaluate_run(results: list[dict], facts: list[dict],
                 min_score: float = 1.0) -> dict:
    """
    Aggregate faithfulness across many tailored outputs (an eval set).
    'passed' is False if the mean score drops below min_score — wire this into
    CI to stop a prompt/model change from regressing honesty.
    """
    scored = [faithfulness(r, facts) for r in results]
    with_bullets = [s for s in scored if s["total"] > 0]
    mean = round(sum(s["score"] for s in with_bullets) / len(with_bullets), 3) if with_bullets else 1.0
    total_v = sum(len(s["violations"]) for s in scored)
    return {
        "mean_faithfulness": mean,
        "cases": len(scored),
        "total_violations": total_v,
        "passed": mean >= min_score and total_v == 0,
        "per_case": scored,
    }
