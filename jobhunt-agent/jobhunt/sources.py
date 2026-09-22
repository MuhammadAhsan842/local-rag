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
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
    data = _get(url)
    out: list[Job] = []
    if not data:
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
    return out


def lever(company: str) -> list[Job]:
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    data = _get(url)
    out: list[Job] = []
    if not data:
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
    return out


def ashby(company: str) -> list[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company}?includeCompensation=true"
    data = _get(url)
    out: list[Job] = []
    if not data:
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
    if not data:
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
