"""Keyless public job-board feeds.

Each function is one platform. A failure is recorded and skipped so one dead
board cannot stop the radar. LinkedIn is not here.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

from .models import Job
from .sources import HEADERS, TIMEOUT, _note

# 26 platforms the radar knows. ATS boards and Adzuna live in sources.py.
PLATFORM_NAMES = [
    "remoteok",
    "himalayas",
    "jobicy",
    "themuse",
    "workingnomads",
    "weworkremotely",
    "getonbrd",
    "fourdayweek",
    "jobspresso",
    "larajobs",
    "pythonorg",
    "cryptojobslist",
    "landingjobs",
    "remotive",
    "arbeitnow",
    "mercor",
    "greenhouse",
    "lever",
    "ashby",
    "adzuna",
    "remoteok_dev",
    "himalayas_llm",
    "jobicy_data",
    "wwr_programming",
    "wwr_devops",
    "getonbrd_programming",
]


def _job(source: str, title: str, company: str, url: str, location: str = "",
         description: str = "", tags: list | None = None, salary: str = "",
         published_at: str = "") -> Job | None:
    if not title or not url:
        return None
    loc = location or ""
    low = f"{loc} {title}".lower()
    if any(w in low for w in ("remote", "worldwide", "anywhere", "global")):
        loc = loc or "Remote"
    return Job(
        source=source, title=title, company=company or source, url=url,
        location=loc, description=(description or "")[:4000],
        tags=list(tags or []), salary=salary, published_at=published_at,
    )


def _get(url: str, params: dict | None = None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _rss(source: str, url: str) -> list[Job]:
    out: list[Job] = []
    try:
        root = ET.fromstring(_get(url).content)
    except Exception as e:  # noqa: BLE001
        _note(source, False, 0, str(e))
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = item.findtext("description") or ""
        loc = item.findtext("location") or ""
        job = _job(source, title, source, link, loc, desc)
        if job:
            out.append(job)
    _note(source, True, len(out), "empty feed" if not out else "")
    return out


def remoteok() -> list[Job]:
    return _remoteok("remoteok", "https://remoteok.com/api")


def remoteok_dev() -> list[Job]:
    return _remoteok("remoteok_dev", "https://remoteok.com/api", {"tag": "engineer"})


def _remoteok(name: str, url: str, params: dict | None = None) -> list[Job]:
    out: list[Job] = []
    try:
        rows = _get(url, params).json()
    except Exception as e:  # noqa: BLE001
        _note(name, False, 0, str(e))
        return out
    for row in rows:
        if not isinstance(row, dict) or "position" not in row:
            continue
        job = _job(name, row.get("position", ""), row.get("company", ""), row.get("url", ""),
                   row.get("location", ""), row.get("description", ""), row.get("tags") or [],
                   published_at=str(row.get("date") or ""))
        if job:
            out.append(job)
    _note(name, True, len(out))
    return out


def himalayas() -> list[Job]:
    return _himalayas("himalayas", "ai engineer")


def himalayas_llm() -> list[Job]:
    return _himalayas("himalayas_llm", "llm")


def _himalayas(name: str, query: str) -> list[Job]:
    out: list[Job] = []
    try:
        data = _get("https://himalayas.app/jobs/api/search", {"q": query}).json()
    except Exception as e:  # noqa: BLE001
        _note(name, False, 0, str(e))
        return out
    for row in data.get("jobs") or []:
        locs = row.get("locationRestrictions") or []
        loc = ", ".join(locs) if isinstance(locs, list) else str(locs)
        sal = ""
        if row.get("minSalary"):
            sal = f"{row.get('minSalary')}-{row.get('maxSalary') or ''} {row.get('currency') or ''}"
        job = _job(name, row.get("title", ""), row.get("companyName", ""),
                   row.get("applicationLink") or "", loc, row.get("description") or row.get("excerpt") or "",
                   row.get("categories") or [], sal, str(row.get("pubDate") or ""))
        if job:
            out.append(job)
    _note(name, True, len(out))
    return out


def jobicy() -> list[Job]:
    return _jobicy("jobicy", {"count": 50, "tag": "python"})


def jobicy_data() -> list[Job]:
    return _jobicy("jobicy_data", {"count": 50, "industry": "data-science"})


def _jobicy(name: str, params: dict) -> list[Job]:
    out: list[Job] = []
    try:
        data = _get("https://jobicy.com/api/v2/remote-jobs", params).json()
    except Exception as e:  # noqa: BLE001
        _note(name, False, 0, str(e))
        return out
    for row in data.get("jobs") or []:
        sal = ""
        if row.get("salaryMin"):
            sal = f"{row.get('salaryMin')}-{row.get('salaryMax') or ''} {row.get('salaryCurrency') or ''}"
        job = _job(name, row.get("jobTitle", ""), row.get("companyName", ""), row.get("url", ""),
                   row.get("jobGeo") or "Remote", row.get("jobDescription") or row.get("jobExcerpt") or "",
                   [row.get("jobIndustry") or ""], sal, str(row.get("pubDate") or ""))
        if job:
            out.append(job)
    _note(name, True, len(out))
    return out


def themuse() -> list[Job]:
    out: list[Job] = []
    try:
        data = _get("https://www.themuse.com/api/public/jobs",
                    {"page": 0, "category": "Software Engineer"}).json()
    except Exception as e:  # noqa: BLE001
        _note("themuse", False, 0, str(e))
        return out
    for row in data.get("results") or []:
        refs = row.get("refs") or {}
        url = refs.get("landing_page") or ""
        locs = ", ".join((l.get("name") or "") for l in (row.get("locations") or []))
        company = (row.get("company") or {}).get("name", "")
        contents = row.get("contents") or ""
        job = _job("themuse", row.get("name", ""), company, url, locs, contents,
                   published_at=str(row.get("publication_date") or ""))
        if job:
            out.append(job)
    _note("themuse", True, len(out))
    return out


def workingnomads() -> list[Job]:
    out: list[Job] = []
    try:
        rows = _get("https://www.workingnomads.com/api/exposed_jobs/").json()
    except Exception as e:  # noqa: BLE001
        _note("workingnomads", False, 0, str(e))
        return out
    for row in rows if isinstance(rows, list) else []:
        job = _job("workingnomads", row.get("title", ""), row.get("company_name", ""),
                   row.get("url", ""), row.get("location") or "Remote", row.get("description") or "",
                   row.get("tags") or [], published_at=str(row.get("pub_date") or ""))
        if job:
            out.append(job)
    _note("workingnomads", True, len(out))
    return out


def weworkremotely() -> list[Job]:
    return _rss("weworkremotely", "https://weworkremotely.com/remote-jobs.rss")


def wwr_programming() -> list[Job]:
    return _rss("wwr_programming", "https://weworkremotely.com/categories/remote-programming-jobs.rss")


def wwr_devops() -> list[Job]:
    return _rss("wwr_devops", "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss")


def getonbrd() -> list[Job]:
    return _getonbrd("getonbrd", {"query": "machine learning", "per_page": 20})


def getonbrd_programming() -> list[Job]:
    return _getonbrd("getonbrd_programming", {"query": "python", "per_page": 20})


def _getonbrd(name: str, params: dict) -> list[Job]:
    out: list[Job] = []
    try:
        data = _get("https://www.getonbrd.com/api/v0/search/jobs", params).json()
    except Exception as e:  # noqa: BLE001
        _note(name, False, 0, str(e))
        return out
    for row in data.get("data") or []:
        attr = row.get("attributes") or {}
        links = row.get("links") or {}
        url = links.get("public_url") or ""
        company = (attr.get("company") or {}).get("name") or "Get on Board"
        loc = "Remote" if attr.get("remote") else (attr.get("remote_zone") or "")
        job = _job(name, attr.get("title", ""), company, url, loc,
                   attr.get("description") or attr.get("description_headline") or "",
                   [attr.get("category_name") or ""], published_at=str(attr.get("published_at") or ""))
        if job:
            out.append(job)
    _note(name, True, len(out))
    return out


def fourdayweek() -> list[Job]:
    out: list[Job] = []
    try:
        data = _get("https://4dayweek.io/api/jobs").json()
    except Exception as e:  # noqa: BLE001
        _note("fourdayweek", False, 0, str(e))
        return out
    for row in data.get("jobs") or []:
        slug = row.get("slug") or ""
        url = f"https://4dayweek.io/job/{slug}" if slug else ""
        locs = row.get("locations") or []
        loc = ", ".join(locs) if isinstance(locs, list) else str(locs)
        if str(row.get("work_arrangement") or "").lower() == "remote":
            loc = loc or "Remote"
        job = _job("fourdayweek", row.get("title", ""), row.get("company_name", ""), url, loc,
                   " ".join(row.get("stack") or []), [row.get("category") or ""],
                   str(row.get("salary") or ""), str(row.get("posted") or ""))
        if job:
            out.append(job)
    _note("fourdayweek", True, len(out))
    return out


def jobspresso() -> list[Job]:
    return _rss("jobspresso", "https://jobspresso.co/feed/")


def larajobs() -> list[Job]:
    return _rss("larajobs", "https://larajobs.com/feed")


def pythonorg() -> list[Job]:
    return _rss("pythonorg", "https://www.python.org/jobs/feed/rss/")


def cryptojobslist() -> list[Job]:
    return _rss("cryptojobslist", "https://cryptojobslist.com/rss")


def landingjobs() -> list[Job]:
    out: list[Job] = []
    try:
        data = _get("https://www.landing.jobs/api/v1/jobs", {"limit": 50}).json()
    except Exception as e:  # noqa: BLE001
        _note("landingjobs", False, 0, str(e))
        return out
    rows = data if isinstance(data, list) else data.get("jobs") or data.get("data") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        company = row.get("company_name") or (row.get("company") or {}).get("name") or "Landing.jobs"
        url = row.get("url") or row.get("link") or ""
        if not url and row.get("id"):
            url = f"https://landing.jobs/at/{row['id']}"
        loc = row.get("city") or row.get("location") or ""
        if row.get("remote"):
            loc = loc or "Remote"
        job = _job("landingjobs", row.get("title") or row.get("name") or "", company, url, loc,
                   row.get("description") or row.get("role_description") or "")
        if job:
            out.append(job)
    _note("landingjobs", True, len(out))
    return out


_FETCHERS = {
    "remoteok": remoteok,
    "himalayas": himalayas,
    "jobicy": jobicy,
    "themuse": themuse,
    "workingnomads": workingnomads,
    "weworkremotely": weworkremotely,
    "getonbrd": getonbrd,
    "fourdayweek": fourdayweek,
    "jobspresso": jobspresso,
    "larajobs": larajobs,
    "pythonorg": pythonorg,
    "cryptojobslist": cryptojobslist,
    "landingjobs": landingjobs,
    "remoteok_dev": remoteok_dev,
    "himalayas_llm": himalayas_llm,
    "jobicy_data": jobicy_data,
    "wwr_programming": wwr_programming,
    "wwr_devops": wwr_devops,
    "getonbrd_programming": getonbrd_programming,
}


def fetch_enabled(names: list[str] | None = None) -> list[Job]:
    chosen = names or list(_FETCHERS)
    jobs: list[Job] = []
    for name in chosen:
        fn = _FETCHERS.get(name)
        if fn is None:
            _note(name, False, 0, "unknown platform")
            continue
        jobs += fn()
    return jobs
