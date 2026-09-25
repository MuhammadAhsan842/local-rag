"""
Eligibility-first gating (gap #2).

Research (Synapse two-phase fit; smart-jobapply-agent): embeddings *conflate
semantically similar but incompatible roles* and *cannot enforce hard
constraints*. So hard eligibility must run BEFORE ranking — a role you can't
take should never occupy a top-10 slot, however similar it looks.

Deterministic, dependency-free. Each check returns a reason when it rejects,
so the shortlist can explain *why* something was dropped.
"""
from __future__ import annotations

import re

# Seniority ladder detected from the title. Checked high→low; first hit wins.
_SENIORITY_RANK = [
    (8, ("chief", "cto", "ceo")),
    (7, ("vp", "vice president")),
    (6, ("head of", "director")),
    (5, ("principal", "staff")),
    (4, ("lead", "manager")),
    (3, ("senior", "sr.", "sr ")),
    (1, ("junior", "graduate", "entry", "working student", "werkstudent")),
    (0, ("intern", "internship")),
]
# Strong denials of sponsorship / work authorization. If any is present, the
# role won't sponsor — block it (these take priority over any positive phrase,
# because "no visa sponsorship" contains the substring "visa sponsorship").
_VISA_NEG = ("no visa", "no sponsorship", "not sponsor", "cannot sponsor",
             "can not sponsor", "do not sponsor", "does not sponsor",
             "unable to sponsor", "without sponsorship", "no relocation",
             "must have the right to work", "citizens only", "citizen only",
             "must be authorized to work", "must be authorised to work",
             "no work visa")


def _title_seniority(title: str) -> int:
    """Detect seniority level from the title; default 2 (mid) if none named."""
    t = title.lower()
    for lvl, words in _SENIORITY_RANK:
        if any(w in t for w in words):
            return lvl
    return 2  # mid-level default


def check(job, cfg: dict) -> tuple[bool, str]:
    """
    Return (eligible, reason). Reason is '' when eligible, else why it's dropped.
    Config (filters.eligibility):
      min_seniority / max_seniority : ints on the ladder above (e.g. 1..4)
      require_visa_sponsorship : bool  (drop roles that explicitly won't sponsor)
      allowed_locations : [substrings]  (job.location must contain one; remote passes)
      exclude_clearance : bool
    """
    e = cfg.get("filters", {}).get("eligibility", {})
    hay = f"{job.title} {job.location} {job.description}".lower()

    # seniority band
    lvl = _title_seniority(job.title)
    if "min_seniority" in e and lvl < e["min_seniority"]:
        return False, f"below seniority floor (level {lvl} < {e['min_seniority']})"
    if "max_seniority" in e and lvl > e["max_seniority"]:
        return False, f"above seniority ceiling (level {lvl} > {e['max_seniority']})"

    # visa / work authorization — a strong denial blocks regardless of any
    # positive-sounding phrase it may also contain.
    if e.get("require_visa_sponsorship"):
        if any(neg in hay for neg in _VISA_NEG):
            return False, "no visa sponsorship / right-to-work required"

    # clearance is a hard blocker for most
    if e.get("exclude_clearance") and "clearance" in hay:
        return False, "requires security clearance"

    # location allow-list (remote always passes)
    allowed = [a.lower() for a in e.get("allowed_locations", [])]
    if allowed and not job.is_remote:
        if not any(a in job.location.lower() for a in allowed):
            return False, f"location '{job.location}' not in allow-list"

    return True, ""


def filter_jobs(jobs, cfg):
    """Split jobs into (eligible, [(job, reason)...] rejected)."""
    keep, dropped = [], []
    for j in jobs:
        ok, reason = check(j, cfg)
        (keep if ok else dropped).append(j if ok else (j, reason))
    return keep, dropped
