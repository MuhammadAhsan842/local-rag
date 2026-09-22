"""
Job source fetchers. Every fetcher returns a list[Job].

Design choices that matter:
- ATS boards (Greenhouse / Lever / Ashby) are public JSON, keyless, and are the
  jobs straight from the company. This is the highest-signal source for eng roles.
- Remotive is keyless but rate-limited (~4 calls/day) and asks you to credit it.
- Adzuna needs a free app_id/app_key from https://developer.adzuna.com (optional).
- LinkedIn is deliberately NOT here: no open API + scraping risks your account ban.
"""
from __future__ import annotations

import time
import requests

from .models import Job

TIMEOUT = 20
HEADERS = {"User-Agent": "personal-jobhunt-agent/1.0 (individual job seeker)"}

# Per-source health, so a *silent* failure (200 OK but empty, or a fetch error)
# is visible instead of being mistaken for "this company has no open roles" —
# the classic keyless-ATS trap. Read by the agent/report after a run.
SOURCE_HEALTH: dict[str, dict] = {}


def _note(label: str, ok: bool, njobs: int, detail: str = "") -> None:
    SOURCE_HEALTH[label] = {"ok": ok, "jobs": njobs, "detail": detail}


def _get(url: str, params: dict | None = None) -> dict | list | None:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 - one source failing must not kill the run
        print(f"  ! source error: {url} -> {e}")
        return None


# --------------------------------------------------------------------------- #
# Company ATS boards — keyless, direct from the employer
# --------------------------------------------------------------------------- #
def greenhouse(company: str) -> list[Job]:
    label = f"greenhouse:{company}"
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
    data = _get(url)
    out: list[Job] = []
    if data is None:
        _note(label, False, 0, "fetch failed (network/blocked/bad slug)")
        return out
    for j in data.get("jobs", []):
        out.append(
            Job(
                source=f"greenhouse:{company}",
                title=j.get("title", ""),
                company=company,
                url=j.get("absolute_url", ""),
                location=(j.get("location") or {}).get("name", ""),
                description=j.get("content", ""),
                published_at=j.get("updated_at", ""),
            )
        )
    _note(label, True, len(out), "empty board" if not out else "")
    return out


def lever(company: str) -> list[Job]:
    label = f"lever:{company}"
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    data = _get(url)
    out: list[Job] = []
    if data is None:
        _note(label, False, 0, "fetch failed (network/blocked/bad slug)")
        return out
    for j in data:
        cats = j.get("categories", {}) or {}
        out.append(
            Job(
                source=f"lever:{company}",
                title=j.get("text", ""),
                company=company,
                url=j.get("hostedUrl", ""),
                location=cats.get("location", ""),
                tags=[cats.get("team", ""), cats.get("commitment", "")],
                description=j.get("descriptionPlain", "") or j.get("description", ""),
                published_at=str(j.get("createdAt", "")),
            )
        )
    _note(label, True, len(out), "empty board" if not out else "")
    return out


def ashby(company: str) -> list[Job]:
    label = f"ashby:{company}"
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company}?includeCompensation=true"
    data = _get(url)
    out: list[Job] = []
    if data is None:
        _note(label, False, 0, "fetch failed (network/blocked/bad slug)")
        return out
    for j in data.get("jobs", []):
        out.append(
            Job(
                source=f"ashby:{company}",
                title=j.get("title", ""),
                company=company,
                url=j.get("jobUrl", ""),
                location=j.get("location", ""),
                salary=j.get("compensationTierSummary", "") or "",
                tags=[j.get("department", ""), j.get("employmentType", "")],
                description=j.get("descriptionPlain", ""),
                published_at=j.get("publishedAt", ""),
            )
        )
    _note(label, True, len(out), "empty board" if not out else "")
    return out


# --------------------------------------------------------------------------- #
# Remotive — keyless, remote roles. Respect their rate limit (max ~4/day).
# --------------------------------------------------------------------------- #
def remotive(search: str = "", category: str = "software-dev", limit: int = 200) -> list[Job]:
    url = "https://remotive.com/api/remote-jobs"
    params = {"category": category, "limit": limit}
    if search:
        params["search"] = search
    data = _get(url, params)
    out: list[Job] = []
    if data is None:
        _note("remotive", False, 0, "fetch failed (network/blocked/rate-limited)")
        return out
    for j in data.get("jobs", []):
        out.append(
            Job(
                source="remotive",
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                url=j.get("url", ""),
                location=j.get("candidate_required_location", ""),
                salary=j.get("salary", ""),
                tags=j.get("tags", []) or [],
                description=j.get("description", ""),
                published_at=j.get("publication_date", ""),
            )
        )
    _note("remotive", True, len(out), "empty result" if not out else "")
    return out


# --------------------------------------------------------------------------- #
# Arbeitnow — keyless, ATS-sourced, has a NATIVE remote flag + visa_sponsorship.
# Paginated (~250/page). We loop until a page is empty / no `links.next`, which
# is the defense against the classic "200 OK but page 2 is empty" silent-
# truncation bug that makes naive fetchers under-report the board.
# --------------------------------------------------------------------------- #
def arbeitnow(remote_only: bool = True, visa_only: bool = False,
              max_pages: int = 5) -> list[Job]:
    base = "https://www.arbeitnow.com/api/job-board-api"
    out: list[Job] = []
    page, fetched_any = 1, False
    while page <= max_pages:
        data = _get(base, {"page": page})
        if data is None:
            if not fetched_any:
                _note("arbeitnow", False, 0, "fetch failed (network/blocked)")
                return out
            break  # partial success: keep what we already have
        fetched_any = True
        rows = data.get("data", []) or []
        if not rows:
            break  # genuine end of results
        for j in rows:
            if remote_only and not j.get("remote", False):
                continue
            if visa_only and not j.get("visa_sponsorship", False):
                continue
            tags = (j.get("tags", []) or []) + (j.get("job_types", []) or [])
            if j.get("visa_sponsorship"):
                tags.append("visa-sponsorship")
            out.append(
                Job(
                    source="arbeitnow",
                    title=j.get("title", ""),
                    company=j.get("company_name", ""),
                    url=j.get("url", ""),
                    location="Remote" if j.get("remote") else j.get("location", ""),
                    tags=[t for t in tags if t],
                    description=j.get("description", ""),
                    published_at=str(j.get("created_at", "")),
                )
            )
        # stop when the API says there is no next page
        if not (data.get("links", {}) or {}).get("next"):
            break
        page += 1
        time.sleep(0.3)  # be polite
    _note("arbeitnow", True, len(out), "no matching rows" if not out else "")
    return out


# --------------------------------------------------------------------------- #
# Adzuna — free key from https://developer.adzuna.com (optional)
# --------------------------------------------------------------------------- #
def adzuna(app_id: str, app_key: str, what: str, where: str = "", country: str = "gb",
           pages: int = 2, results_per_page: int = 50) -> list[Job]:
    out: list[Job] = []
    if not (app_id and app_key):
        return out
    for page in range(1, pages + 1):
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
        params = {
            "app_id": app_id, "app_key": app_key,
            "what": what, "results_per_page": results_per_page,
            "content-type": "application/json",
        }
        if where:
            params["where"] = where
        data = _get(url, params)
        if not data:
            break
        for j in data.get("results", []):
            sal = ""
            if j.get("salary_min"):
                sal = f"{int(j['salary_min'])}-{int(j.get('salary_max', j['salary_min']))}"
            out.append(
                Job(
                    source="adzuna",
                    title=j.get("title", ""),
                    company=(j.get("company") or {}).get("display_name", ""),
                    url=j.get("redirect_url", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    salary=sal,
                    tags=[(j.get("category") or {}).get("label", "")],
                    description=j.get("description", ""),
                    published_at=j.get("created", ""),
                )
            )
        time.sleep(0.4)  # be polite
    return out
