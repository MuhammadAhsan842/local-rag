"""How far an application can be taken on each kind of site.

Measured from the live Mercor run and from the board feeds: most "platforms"
do not host the application. They hand you a URL. Submission is a separate
gate and is never implied by a successful fetch.
"""
from __future__ import annotations

# step: (name, who)  who is "app" or "you"
_MERCOR = [
    ("open listing", "app"),
    ("fill name, email, LinkedIn", "app"),
    ("click Start application", "app"),
    ("click Continue to this role", "app"),
    ("sign in", "you"),
    ("upload resume", "you"),
    ("AI interview", "you"),
    ("work authorization", "you"),
    ("submit", "you"),
]
_ATS = [
    ("open company form", "app"),
    ("fill contact and attach resume", "app"),
    ("leave unknown questions blank", "app"),
    ("you review the form", "you"),
    ("submit", "you"),
]
_LINK = [
    ("save the listing", "app"),
    ("open the employer's apply link", "app"),
    ("if that link is Greenhouse, Lever, or Ashby, use the ATS steps", "app"),
    ("otherwise stop — the form belongs to the employer", "you"),
    ("submit", "you"),
]

_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "boards.greenhouse.io")
_MERCOR_HOSTS = ("mercor.com", "work.mercor.com")


def kind_of(source: str, url: str) -> str:
    host = (url or "").lower()
    src = (source or "").split(":")[0].lower()
    if src == "mercor" or any(h in host for h in _MERCOR_HOSTS):
        return "mercor"
    if any(h in host for h in _ATS_HOSTS) or src in {"greenhouse", "lever", "ashby"}:
        return "ats"
    return "link"


def steps_for(source: str, url: str) -> list[tuple[str, str]]:
    kind = kind_of(source, url)
    if kind == "mercor":
        return list(_MERCOR)
    if kind == "ats":
        return list(_ATS)
    return list(_LINK)


def app_can_finish(source: str, url: str) -> str:
    """Last step the app is allowed to complete. Submit is never that step."""
    kind = kind_of(source, url)
    if kind == "mercor":
        return "click Continue to this role"
    if kind == "ats":
        return "leave unknown questions blank"
    return "open the employer's apply link"


def stops_before_submit(source: str, url: str) -> bool:
    return steps_for(source, url)[-1] == ("submit", "you")


def cv_accepted(page_text: str) -> bool:
    """True only when the site says the application or resume was received."""
    low = (page_text or "").lower()
    phrases = (
        "application submitted",
        "application received",
        "thanks for applying",
        "thank you for applying",
        "we received your application",
        "resume uploaded",
        "cv uploaded",
    )
    return any(p in low for p in phrases)
