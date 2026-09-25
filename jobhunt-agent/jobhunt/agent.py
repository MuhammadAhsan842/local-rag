"""
The agent loop. Deterministic orchestration of tool steps — this is the
'Cowork-style' controller: each step is a tool, state flows between them, and
the expensive/creative steps (score, tailor) run only on candidates that pass
the cheap filters first.
"""
from __future__ import annotations

from . import (
    sources, sources_email, sources_rss, boards, llm, matcher, tailor,
    outreach, tracker, eligibility, audit,
)
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
    an = s.get("arbeitnow", {})
    if an.get("enabled"):
        jobs += sources.arbeitnow(remote_only=an.get("remote_only", True),
                                  visa_only=an.get("visa_only", False),
                                  max_pages=an.get("max_pages", 5))
    ro = s.get("remoteok", {})
    if ro.get("enabled"):
        jobs += sources.remoteok(search=ro.get("search", ""))
    hn = s.get("hackernews", {})
    if hn.get("enabled"):
        jobs += sources.hackernews_hiring(keywords=hn.get("keywords"),
                                          remote_only=hn.get("remote_only", True))
    rss = s.get("rss", {})
    if rss.get("enabled") and rss.get("feeds"):
        jobs += sources_rss.fetch(rss["feeds"])
    mc = s.get("mercor", {})
    if mc.get("enabled"):
        jobs += sources.mercor(max_details=mc.get("max_details", 8))
    b = s.get("boards", {})
    if b.get("enabled", True):
        jobs += boards.fetch_enabled(b.get("names"))
    if s.get("email", {}).get("enabled"):
        jobs += sources_email.fetch(s["email"])
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
        title = j.title.lower().replace("-", " ")
        if must and not any(m in title for m in must):
            continue
        if remote_only and not j.is_remote:
            continue
        if eligibility.block_reason(j.searchable, j.is_remote or not remote_only):
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


def weighted_blend(components: dict[str, float | None], weights: dict[str, float]) -> int:
    """
    Weighted hybrid of 0-100 signals, renormalized over whatever is available.
    Pattern from srbhr/Resume-Matcher-style hybrids: vector + LLM + keyword,
    default 30/60/10. If a component is None (e.g. Ollama down), its weight is
    redistributed across the rest so the score stays on the same 0-100 scale.
    """
    live = {k: v for k, v in components.items() if v is not None and weights.get(k, 0) > 0}
    total_w = sum(weights[k] for k in live)
    if not live or total_w <= 0:
        return 0
    return round(sum(components[k] * weights[k] for k in live) / total_w)


def score(jobs: list[Job], profile: str, cfg: dict) -> list[Job]:
    m = cfg["model"]
    use_llm = llm.ollama_available(m["chat"])
    vocab = cfg.get("skill_vocab", [])
    f = cfg.get("filters", {})
    weights = cfg.get("scoring", {}).get("weights", {"vector": 0.3, "llm": 0.6, "keyword": 0.1})
    for j in jobs:
        mr = matcher.match_report(profile, j, vocab, m["embed"])
        vector = round(100 * mr["similarity"])          # semantic component (0-100)
        kw = llm.keyword_score(j, f.get("skills_must", []), f.get("skills_nice", []),
                               f.get("exclude_any", []))
        keyword = kw["fit_score"]
        llm_score = None
        res = None
        if use_llm:
            res = llm.score_with_ollama(j, profile, m["chat"])
            if res:
                llm_score = int(res.get("fit_score", vector))
        # hard-exclusion short-circuit stays authoritative
        if keyword == 0 and kw["red_flags"] and "excluded term" in kw["reasons"]:
            j.fit_score = 0
            j.reasons = kw["reasons"]
            j.red_flags = kw["red_flags"]
            continue
        j.fit_score = weighted_blend(
            {"vector": vector, "llm": llm_score, "keyword": keyword}, weights)
        if res:
            j.reasons = res.get("reasons", "") or f"skills: {', '.join(mr['matched_skills'][:5])}"
            j.red_flags = res.get("red_flags", "")
            j.hook = res.get("hook", "")
        else:
            j.reasons = kw["reasons"]
            j.red_flags = kw["red_flags"]
        j.reasons += (f" | vec {vector}/llm {llm_score if llm_score is not None else '—'}/kw {keyword}"
                      f" | missing: {', '.join(mr['missing_skills'][:4]) or 'none'}")
        j.tags = list(dict.fromkeys(j.tags + [f"fit:{j.fit_score}"]))
    # freshness + learned-conversion nudges (bounded to 100). The conversion
    # prior is the closed loop: sources that actually reply to you rise over time.
    priors = tracker.conversion_priors() if cfg.get("scoring", {}).get("use_history", True) else {}
    for j in jobs:
        j.fit_score = min(100, max(0, j.fit_score
                                   + _recency_boost(j, cfg)
                                   + tracker.conversion_boost(j, priors)))
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
    # surface silent failures: a board that errored vs one that's genuinely empty
    failed = [k for k, v in sources.SOURCE_HEALTH.items() if not v["ok"]]
    empty = [k for k, v in sources.SOURCE_HEALTH.items() if v["ok"] and v["jobs"] == 0]
    if failed:
        print(f"  ⚠️  {len(failed)} source(s) FAILED to fetch (check slug/network): {', '.join(failed)}")
    if empty:
        print(f"  · {len(empty)} source(s) returned 0 roles (genuinely empty): {', '.join(empty)}")
    jobs = dedupe(jobs)
    jobs = hard_filter(jobs, cfg)
    # eligibility-first: drop roles you can't take BEFORE ranking (gap #2)
    jobs, rejected = eligibility.filter_jobs(jobs, cfg)
    if rejected:
        print(f"  {len(rejected)} dropped as ineligible "
              f"(e.g. {rejected[0][1]})")
    print(f"  {len(jobs)} after filter + eligibility")
    audit.log("discover", found=len(jobs), ineligible=len(rejected),
              sources={k: v["jobs"] for k, v in sources.SOURCE_HEALTH.items()})

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
    # persist the shortlist so the dashboard + history have it (idempotent;
    # never downgrades a job you've already advanced past 'seen')
    for j in ranked[: cfg.get("report_top", 25)]:
        if tracker.stage_of(j.key) is None:
            tracker.record(j, stage="seen")
    return {"ranked": ranked, "tailored": tailored, "outreach": outreach_copy}
