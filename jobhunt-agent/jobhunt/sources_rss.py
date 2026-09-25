"""
Generic RSS/Atom job-feed source.

Many remote boards expose a feed even without a JSON API — Jobspresso,
We Work Remotely, Remote.co, NoDesk, Working Nomads, Himalayas, etc. This
ingests any of them from a config list, so adding a board is one line, not a
new fetcher. stdlib only (xml.etree); one bad feed never kills the run.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from .models import Job
from . import sources  # reuse _get + SOURCE_HEALTH

_TAG = re.compile(r"\{.*\}")  # strip XML namespaces


def _text(el, *names) -> str:
    for n in names:
        child = el.find(n)
        if child is not None and (child.text or "").strip():
            return child.text.strip()
    return ""


def _strip_ns(root) -> None:
    for el in root.iter():
        el.tag = _TAG.sub("", el.tag)


def parse_feed(xml_text: str, label: str) -> list[Job]:
    """Pure, testable: RSS <item> or Atom <entry> -> Jobs."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    _strip_ns(root)
    out: list[Job] = []
    items = root.findall(".//item") or root.findall(".//entry")
    for it in items:
        title = _text(it, "title")
        link = _text(it, "link")
        if not link:  # Atom puts the url in <link href="...">
            le = it.find("link")
            link = le.get("href") if le is not None else ""
        desc = _text(it, "description", "summary", "content")
        pub = _text(it, "pubDate", "published", "updated")
        # feeds often format title as "Company: Role" or "Role at Company"
        company = ""
        for sep in (": ", " at ", " - "):
            if sep in title:
                a, b = title.split(sep, 1)
                company, title = (a, b) if sep == ": " else (b, a)
                break
        if title and link:
            out.append(Job(source=f"rss:{label}", title=title.strip(),
                           company=company.strip(), url=link, description=desc,
                           published_at=pub))
    return out


def fetch(feeds: list[dict]) -> list[Job]:
    """
    feeds: [{name, url}]. Returns all jobs, recording per-feed health.
    """
    out: list[Job] = []
    for feed in feeds:
        name, url = feed.get("name", "feed"), feed.get("url", "")
        if not url:
            continue
        try:
            import requests
            r = requests.get(url, headers=sources.HEADERS, timeout=sources.TIMEOUT)
            r.raise_for_status()
            jobs = parse_feed(r.text, name)
            sources._note(f"rss:{name}", True, len(jobs), "empty feed" if not jobs else "")
            out += jobs
        except Exception as e:  # noqa: BLE001
            sources._note(f"rss:{name}", False, 0, f"fetch failed: {e}")
    return out
