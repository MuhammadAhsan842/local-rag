"""
The agent loop. Deterministic orchestration of tool steps — this is the
'Cowork-style' controller: each step is a tool, state flows between them, and
the expensive/creative steps (score, tailor) run only on candidates that pass
the cheap filters first.
"""
from __future__ import annotations

from . import sources, llm, matcher, tailor, outreach
from .models import Job
from .store import load_seen, save_seen


def discover(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    s = cfg["sources"]
    for c in s.get("greenhouse", []):
        jobs += sources.greenhouse(c)
    for c in s.get("lever", []):
        jobs += sources.lever(c)
    for c in s.get("ashby", []):
        jobs += sources.ashby(c)
    if s.get("remotive", {}).get("enabled"):
        jobs += sources.remotive(search=s["remotive"].get("search", ""),
                                 category=s["remotive"].get("category", "software-dev"))
    az = s.get("adzuna", {})
    if az.get("app_id") and az.get("app_key"):
        jobs += sources.adzuna(az["app_id"], az["app_key"], az.get("what", "AI engineer"),
                               az.get("where", ""), az.get("country", "gb"))
    return jobs


def dedupe(jobs: list[Job]) -> list[Job]:
    """Drop duplicates by identity (company|title) AND by apply URL."""
    seen_keys, seen_urls, out = set(), set(), []
    for j in jobs:
        url = (j.url or "").split("?")[0].rstrip("/").lower()
        if j.key in seen_keys or (url and url in seen_urls):
            continue
        seen_keys.add(j.key)
        if url:
            seen_urls.add(url)
        out.append(j)
    return out


def hard_filter(jobs: list[Job], cfg: dict) -> list[Job]:
    f = cfg.get("filters", {})
    must = [m.lower() for m in f.get("title_must_include_any", [])]
    excl = [e.lower() for e in f.get("exclude_any", [])]
    remote_only = f.get("remote_only", False)
    max_age = f.get("max_age_days")  # e.g. 30 -> drop stale posts; None -> keep all
    out = []
    for j in jobs:
        text = j.searchable
        if excl and any(e in text for e in excl):
            continue
        if must and not any(m in j.title.lower() for m in must):
            continue
        if remote_only and not j.is_remote:
            continue
        if max_age is not None:
            age = j.age_days
            if age is not None and age > max_age:
                continue
        out.append(j)
    return out


def _recency_boost(job: Job, cfg: dict) -> int:
    """Small additive bonus for fresh roles — an urgent hunt wants new posts first."""
    boost = cfg.get("filters", {}).get("recency_boost", 8)
    age = job.age_days
    if age is None or boost <= 0:
        return 0
    if age <= 3:
        return boost
    if age <= 7:
        return round(boost * 0.6)
    if age <= 14:
        return round(boost * 0.3)
    return 0


def score(jobs: list[Job], profile: str, cfg: dict) -> list[Job]:
    m = cfg["model"]
    use_llm = llm.ollama_available(m["chat"])
    vocab = cfg.get("skill_vocab", [])
    f = cfg.get("filters", {})
    for j in jobs:
        mr = matcher.match_report(profile, j, vocab, m["embed"])
        j.tags = list(dict.fromkeys(j.tags + [f"fit:{mr['fit']}"]))
        base = mr["fit"]
        if use_llm:
            res = llm.score_with_ollama(j, profile, m["chat"])
            if res:
                # blend model judgment with structural match
                j.fit_score = round(0.5 * base + 0.5 * int(res.get("fit_score", base)))
                j.reasons = res.get("reasons", mr and f"skills: {', '.join(mr['matched_skills'][:5])}")
                j.red_flags = res.get("red_flags", "")
                j.hook = res.get("hook", "")
                j.reasons += f" | missing: {', '.join(mr['missing_skills'][:4]) or 'none'}"
                continue
        kw = llm.keyword_score(j, f.get("skills_must", []), f.get("skills_nice", []),
                               f.get("exclude_any", []))
        j.fit_score = round(0.5 * base + 0.5 * kw["fit_score"])
        j.reasons = f"{kw['reasons']} | missing: {', '.join(mr['missing_skills'][:4]) or 'none'}"
        j.red_flags = kw["red_flags"]
    # freshness nudge (bounded to 100) so the newest good roles rise to the top
    for j in jobs:
        j.fit_score = min(100, j.fit_score + _recency_boost(j, cfg))
    return sorted(jobs, key=lambda x: x.fit_score, reverse=True)


def tailor_top(jobs: list[Job], facts: list[dict], cfg: dict, n: int) -> dict[str, dict]:
    """Generate a truthful tailored resume section for the top-n jobs."""
    if not facts or not llm.ollama_available(cfg["model"]["chat"]):
        return {}
    out = {}
    vocab = cfg.get("skill_vocab", [])
    for j in jobs[:n]:
        needs = matcher.extract_required_skills(j, vocab)
        out[j.key] = tailor.tailor(facts, j.title, needs, cfg["model"]["chat"])
    return out


def outreach_top(jobs: list[Job], facts: list[dict], cfg: dict, n: int) -> dict[str, dict]:
    """Truthful recruiter DM + cover note for the top-n jobs."""
    if not facts:
        return {}
    use_llm = llm.ollama_available(cfg["model"]["chat"])
    vocab = cfg.get("skill_vocab", [])
    out = {}
    for j in jobs[:n]:
        needs = matcher.extract_required_skills(j, vocab)
        out[j.key] = outreach.generate(facts, j.title, j.company, needs,
                                        cfg["model"]["chat"], use_llm=use_llm)
    return out


def run_pipeline(cfg: dict, profile: str, facts: list[dict], only_new: bool = True) -> dict:
    print("→ discovering jobs...")
    jobs = discover(cfg)
    print(f"  found {len(jobs)}")
    jobs = dedupe(jobs)
    jobs = hard_filter(jobs, cfg)
    print(f"  {len(jobs)} after filter")

    seen = load_seen()
    if only_new:
        fresh = [j for j in jobs if j.key not in seen]
        print(f"  {len(fresh)} new since last run")
    else:
        fresh = jobs

    print("→ scoring...")
    ranked = score(fresh, profile, cfg)

    top_n = cfg.get("tailor_top_n", 5)
    print(f"→ tailoring top {top_n}...")
    tailored = tailor_top(ranked, facts, cfg, top_n)

    print(f"→ drafting outreach for top {top_n}...")
    outreach_copy = outreach_top(ranked, facts, cfg, top_n)

    save_seen(seen | {j.key for j in jobs})
    return {"ranked": ranked, "tailored": tailored, "outreach": outreach_copy}
