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


def _get(url: str, params: dict | None = None, retries: int = 3) -> dict | list | None:
    """
    GET with exponential backoff (gap #5): transient failures (timeouts, 429,
    5xx) are retried; a 4xx (bad slug) fails fast. One source failing never
    kills the run.
    """
    delay = 1.0
    last = ""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last = f"HTTP {r.status_code}"
                raise requests.HTTPError(last)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            # 4xx other than 429 => permanent; don't waste retries
            code = getattr(e.response, "status_code", None)
            if code and code != 429 and code < 500:
                print(f"  ! source error: {url} -> HTTP {code}")
                return None
            last = str(e)
        except Exception as e:  # noqa: BLE001 - network/JSON error, retry
            last = str(e)
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= 2  # 1s, 2s, 4s
    print(f"  ! source error after {retries} tries: {url} -> {last}")
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
# RemoteOK — keyless JSON API. First element is a legal/metadata notice.
# --------------------------------------------------------------------------- #
def remoteok(search: str = "") -> list[Job]:
    data = _get("https://remoteok.com/api")
    out: list[Job] = []
    if data is None:
        _note("remoteok", False, 0, "fetch failed (network/blocked)")
        return out
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # skips the leading legal-notice element
        title = j.get("position", "")
        if search and search.lower() not in (title + " " + " ".join(j.get("tags", []))).lower():
            continue
        out.append(Job(
            source="remoteok",
            title=title,
            company=j.get("company", ""),
            url=j.get("url", "") or j.get("apply_url", ""),
            location=j.get("location", "") or "Remote",
            salary=(f"{j.get('salary_min')}-{j.get('salary_max')}" if j.get("salary_min") else ""),
            tags=j.get("tags", []) or [],
            description=j.get("description", ""),
            published_at=j.get("date", ""),
        ))
    _note("remoteok", True, len(out), "no matching rows" if not out else "")
    return out


# --------------------------------------------------------------------------- #
# Hacker News "Who is Hiring" — keyless Algolia API. Parse the latest monthly
# thread's comments for roles + apply links (many point straight to ATS pages).
# --------------------------------------------------------------------------- #
def hackernews_hiring(keywords: list[str] | None = None, remote_only: bool = True,
                      max_comments: int = 500) -> list[Job]:
    import re as _re
    kws = [k.lower() for k in (keywords or ["ai", "ml", "machine learning", "llm"])]
    # find the most recent "Ask HN: Who is hiring?" story
    meta = _get("https://hn.algolia.com/api/v1/search_by_date",
                {"query": "Ask HN: Who is hiring?", "tags": "story", "hitsPerPage": 1})
    out: list[Job] = []
    if not meta or not meta.get("hits"):
        _note("hackernews", False, 0, "could not find monthly thread")
        return out
    story_id = meta["hits"][0]["objectID"]
    data = _get(f"https://hn.algolia.com/api/v1/items/{story_id}")
    if not data:
        _note("hackernews", False, 0, "thread fetch failed")
        return out
    url_re = _re.compile(r"https?://[^\s\"'<>]+")
    for c in (data.get("children") or [])[:max_comments]:
        text = (c.get("text") or "")
        low = text.lower()
        if not any(k in low for k in kws):
            continue
        if remote_only and "remote" not in low:
            continue
        # first line ~ the headline (Company | Role | location)
        headline = _re.sub(r"<[^>]+>", " ", text).strip()
        headline = _re.sub(r"\s+", " ", headline)[:140]
        links = url_re.findall(text)
        out.append(Job(source="hackernews", title=headline or "HN role",
                       company=(c.get("author") or ""), url=links[0] if links else "",
                       description=headline, published_at=str(c.get("created_at", ""))))
    _note("hackernews", True, len(out), "no matching comments" if not out else "")
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


_MERCOR_AI = {"ai", "ml", "llm", "rag", "genai", "agent", "agentic", "generative"}
_MERCOR_ROLE = {"engineer", "scientist", "developer", "network"}


def mercor(max_details: int = 8) -> list[Job]:
    """Remote-AI listings from Mercor's public explore page.

    The index only has URLs. Detail pages are fetched for the AI-shaped slugs,
    capped so a run cannot stall on hundreds of unrelated expert gigs.
    """
    import re

    out: list[Job] = []
    try:
        import requests
        page = requests.get(
            "https://work.mercor.com/explore", headers=HEADERS, timeout=TIMEOUT,
        )
        page.raise_for_status()
        html = page.text
    except Exception as e:  # noqa: BLE001
        _note("mercor", False, 0, f"explore fetch failed: {e}")
        return out

    seen, urls = set(), []
    for list_id, slug in re.findall(r"/jobs/(list_[A-Za-z0-9_-]+)/([a-z0-9-]+)", html):
        parts = set(slug.split("-"))
        ai_hit = bool(parts & _MERCOR_AI) or {"machine", "learning"} <= parts
        role_hit = bool(parts & _MERCOR_ROLE)
        if list_id in seen or not (ai_hit and role_hit):
            continue
        seen.add(list_id)
        urls.append((list_id, slug, f"https://work.mercor.com/jobs/{list_id}/{slug}"))
        if len(urls) >= max_details:
            break

    for _list_id, slug, url in urls:
        try:
            import requests
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            body = re.sub(r"<[^>]+>", " ", r.text)
            body = re.sub(r"\s+", " ", body)
        except Exception:  # noqa: BLE001
            continue
        title = slug.replace("-", " ").title()
        remote = "remote" in body.lower()
        out.append(Job(
            source="mercor",
            title=title,
            company="Mercor",
            url=url,
            location="Remote" if remote else "",
            tags=["remote"] if remote else [],
            description=body[:4000],
        ))
    _note("mercor", True, len(out), "no AI-shaped listings" if not out else "")
    return out
