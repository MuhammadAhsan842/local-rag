"""Normalized job model shared across all sources."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    # strip HTML tags and collapse whitespace
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    salary: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    published_at: str = ""

    # filled in by the scoring step
    fit_score: int = 0
    reasons: str = ""
    red_flags: str = ""
    hook: str = ""

    def __post_init__(self):
        self.title = _clean(self.title)
        self.company = _clean(self.company)
        self.location = _clean(self.location)
        self.description = _clean(self.description)

    @property
    def key(self) -> str:
        """Stable identity for dedupe / 'have I seen this' tracking."""
        basis = f"{self.company.lower()}|{self.title.lower()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    @property
    def searchable(self) -> str:
        return " ".join(
            [self.title, self.company, self.location, " ".join(self.tags), self.description]
        ).lower()

    @property
    def is_remote(self) -> bool:
        """Best-effort remote detection from location/tags/title."""
        hay = f"{self.location} {' '.join(self.tags)} {self.title}".lower()
        if any(t in hay for t in ("remote", "anywhere", "work from home", "distributed")):
            return True
        # ATS often leaves location blank for remote-first companies
        return self.location.strip() == "" and self.source.split(":")[0] in {"remotive"}

    @property
    def age_days(self) -> Optional[float]:
        """Days since the posting was published/updated, if we can parse a date."""
        raw = (self.published_at or "").strip()
        if not raw:
            return None
        # epoch millis (Lever createdAt) or epoch seconds
        if raw.isdigit():
            ts = int(raw)
            if ts > 1e11:  # milliseconds
                ts /= 1000.0
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                return None
        else:
            iso = raw.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError:
                try:
                    dt = datetime.strptime(raw[:10], "%Y-%m-%d")
                except ValueError:
                    return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)

    def to_dict(self) -> dict:
        return asdict(self)
