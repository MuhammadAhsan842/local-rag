"""
Batch auto-apply — throughput without the ban risk.

This is the "fully automated to submission" lane, done safely:
- It only auto-submits on company ATS pages (Greenhouse/Lever/Ashby/Workable),
  where automation is allowed. Everything else is left for assisted pre-fill.
- It applies only to roles above a fit threshold, so volume stays *targeted* —
  80 tailored ATS applications beat 800 sprayed ones that recruiters filter out.
- Hard caps (max_applies), polite pacing, skip-already-applied, and a DRY-RUN
  default keep it controllable and auditable.
- Every application is tailored (truthful, faithfulness-checked) and tracked.

Usage (see run.py --auto-apply):
    autopilot.run(cfg, profile, facts, dry_run=True)   # plan only
    autopilot.run(cfg, profile, facts, dry_run=False)  # actually submit on ATS
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import yaml

from . import agent, matcher, llm, tailor as tailor_mod, eval as eval_mod, tracker
from . import apply as apply_mod, resume_render, audit


def _contact() -> dict:
    if Path("profile.yaml").exists():
        return yaml.safe_load(Path("profile.yaml").read_text()).get("contact", {})
    return {}


def eligible(jobs, cfg) -> list:
    """ATS-only, above the fit floor, not already applied, deduped."""
    ap = cfg.get("autopilot", {})
    floor = ap.get("min_fit", 70)
    out = []
    for j in jobs:
        if j.fit_score < floor:
            continue
        if not apply_mod._is_allowed(j.url):      # ATS lane only — safe to auto-submit
            continue
        if tracker.already_applied(j.key):
            continue
        out.append(j)
    return out


def run(cfg: dict, profile: str, facts: list[dict], dry_run: bool = True) -> dict:
    """
    Discover → score → pick eligible ATS roles → tailor → (auto-submit) → track.
    Returns a summary dict. dry_run=True plans and tailors but never submits.
    """
    ap = cfg.get("autopilot", {})
    max_applies = ap.get("max_applies", 15)
    min_faith = ap.get("min_faithfulness", 1.0)
    pace_s = ap.get("pace_seconds", 20)
    model_chat = cfg.get("model", {}).get("chat", "qwen2.5")
    contact = _contact()
    resume = cfg.get("resume_file", "resume.pdf")

    result = agent.run_pipeline(cfg, profile, facts, only_new=True)
    ranked = result["ranked"]
    picks = eligible(ranked, cfg)[:max_applies]

    print(f"\n auto-apply: {len(picks)} eligible ATS role(s) "
          f"(fit ≥ {ap.get('min_fit', 70)}), dry_run={dry_run}")

    vocab = cfg.get("skill_vocab", [])
    use_llm = llm.ollama_available(model_chat)
    log = []
    for j in picks:
        # 1) tailor truthfully and gate on faithfulness before we ever submit
        needs = matcher.extract_required_skills(j, vocab)
        tailored = tailor_mod.tailor(facts, j.title, needs, model_chat) if use_llm else {"bullets": []}
        faith = eval_mod.faithfulness(tailored, facts)
        entry = {"title": j.title, "company": j.company, "url": j.url,
                 "fit": j.fit_score, "faithfulness": faith["score"]}
        if faith["score"] < min_faith:
            entry["action"] = f"SKIPPED — faithfulness {faith['score']} < {min_faith}"
            tracker.record(j, stage="seen")
            audit.log("autopilot_skip", **entry)
            log.append(entry)
            print(f"  · skip  [{j.fit_score}] {j.title[:40]} — low faithfulness")
            continue

        # render an ATS-clean tailored CV so the SUBMITTED document carries the
        # tailoring (gap #1) — not a static resume.pdf
        skills = matcher.match_report(profile, j, vocab, cfg.get("model", {}).get("embed", "")).get("matched_skills", [])
        rendered = resume_render.render(contact, tailored, facts, skills,
                                        job_title=j.title, company=j.company,
                                        out_base=f"out/cv_{j.key}")
        entry["cv"] = rendered

        if dry_run:
            entry["action"] = "DRY-RUN — would submit"
            tracker.record(j, stage="seen")
            audit.log("autopilot_plan", **entry)
            print(f"  · plan  [{j.fit_score}] {j.title[:40]} @ {j.company[:20]}  (cv: {rendered.get('html')})")
        else:
            # attach the tailored PDF/DOCX if we produced one, else the configured resume
            resume_file = rendered.get("docx") or rendered.get("html") or resume
            res = asyncio.run(apply_mod.apply(j.url, contact, resume_file,
                                              model_name=model_chat, allow_submit=True))
            ok = bool(res.get("ok") and res.get("submitted"))
            entry["action"] = "SUBMITTED" if ok else f"FAILED — {res.get('reason', res)}"
            entry["result"] = res
            tracker.record(j, stage="applied" if ok else "seen")
            audit.log("autopilot_apply", submitted=ok, **entry)
            print(f"  · {'sent' if ok else 'fail'}  [{j.fit_score}] {j.title[:40]} @ {j.company[:20]}")
            time.sleep(pace_s)  # be polite; don't hammer a careers site
        log.append(entry)

    submitted = sum(1 for e in log if e.get("action") == "SUBMITTED")
    return {"eligible": len(picks), "processed": len(log),
            "submitted": submitted, "dry_run": dry_run, "log": log,
            "funnel": tracker.summary()}
