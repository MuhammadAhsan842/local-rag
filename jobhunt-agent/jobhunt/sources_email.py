"""
Email job-alert source — turn the "new jobs for you" emails you already get
(LinkedIn, Indeed, Otta/Welcome, Wellfound, ...) into ranked, tailored roles.

Design / safety:
- READ-ONLY IMAP. We only fetch and parse; we never send, delete, or mark.
- The app password is read from an ENV VAR, never stored in config.yaml.
  (Gmail: create an App Password, then `export JOBHUNT_EMAIL_PASS=...`.)
- stdlib only (imaplib + email + html.parser) — no new dependencies.
- Parsing is a pure function (`extract_jobs_from_html`) so it is unit-testable
  offline without a mailbox.

Extracted jobs flow into the SAME pipeline as every other source: dedupe →
filter → score → tailor → track. You still click apply yourself.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from email.header import decode_header
from html.parser import HTMLParser

from .models import Job

# Links that look like an actual job posting (not tracking/unsubscribe chrome).
_JOB_URL = re.compile(
    r"(greenhouse\.io|lever\.co|ashbyhq\.com|linkedin\.com/jobs|linkedin\.com/comm/jobs"
    r"|indeed\.com|wellfound\.com|angel\.co|otta\.com|welcometothejungle|remotive\.com"
    r"|/jobs?/|/careers?/|/vacanc)",
    re.I,
)
# Chrome we never want to treat as a job link.
_SKIP_URL = re.compile(r"(unsubscribe|/settings|/help|/privacy|/account|mailto:|utm_)", re.I)


class _LinkGrabber(HTMLParser):
    """Collect (href, anchor_text) pairs from an HTML email body."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href, self._text = None, []


def _clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" -–—•|\t")


def _split_company(title: str) -> tuple[str, str]:
    """Alert links often read 'Senior ML Engineer - Acme' or '... at Acme'."""
    for sep in (" at ", " - ", " – ", " — ", " | "):
        if sep in title:
            role, company = title.rsplit(sep, 1)
            if 1 < len(company) < 60:
                return _clean_title(role), _clean_title(company)
    return _clean_title(title), ""


def extract_jobs_from_html(html: str, sender: str) -> list[Job]:
    """Pure, testable: pull candidate jobs out of one alert email's HTML."""
    grabber = _LinkGrabber()
    try:
        grabber.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML must not kill the run
        return []
    domain = sender.split("@")[-1].strip(">").lower() or "email"
    out, seen = [], set()
    for href, text in grabber.links:
        if not href or not _JOB_URL.search(href) or _SKIP_URL.search(href):
            continue
        url = href.split("?")[0]
        if url in seen or len(text) < 3:
            continue
        seen.add(url)
        title, company = _split_company(text)
        if not title:
            continue
        out.append(Job(source=f"email:{domain}", title=title, company=company,
                       url=href, description=text))
    return out


def _decode(value: str) -> str:
    parts = decode_header(value or "")
    return "".join(
        (b.decode(enc or "utf-8", "ignore") if isinstance(b, bytes) else b)
        for b, enc in parts
    )


def _html_of(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "ignore")
        # fall back to plain text
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "ignore")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "ignore")


def fetch(cfg: dict) -> list[Job]:
    """
    Read job-alert emails over IMAP and return Jobs.
    cfg keys: enabled, host, port, user, password_env, senders[], since_days,
              max_emails, folder.
    """
    user = cfg.get("user", "")
    password = os.environ.get(cfg.get("password_env", "JOBHUNT_EMAIL_PASS"), "")
    if not (user and password):
        print("  ! email source: set user in config and the app password in "
              f"${cfg.get('password_env', 'JOBHUNT_EMAIL_PASS')} (skipping)")
        return []

    host = cfg.get("host", "imap.gmail.com")
    port = int(cfg.get("port", 993))
    senders = cfg.get("senders", []) or []
    since_days = int(cfg.get("since_days", 7))
    max_emails = int(cfg.get("max_emails", 100))
    folder = cfg.get("folder", "INBOX")

    out: list[Job] = []
    try:
        M = imaplib.IMAP4_SSL(host, port)
        M.login(user, password)
        M.select(folder, readonly=True)  # readonly: we never modify the mailbox
        from datetime import datetime, timedelta
        since = (datetime.utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        # OR across sender filters; if none given, take everything since date.
        if senders:
            crit = ["(OR " * (len(senders) - 1)]
            for i, s in enumerate(senders):
                crit.append(f'FROM "{s}"')
                if i:
                    crit.append(")")
            criteria = f'(SINCE {since} {" ".join(crit)})'
        else:
            criteria = f"(SINCE {since})"
        typ, data = M.search(None, criteria)
        ids = data[0].split()[-max_emails:] if data and data[0] else []
        for num in ids:
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            sender = _decode(msg.get("From", "email"))
            out += extract_jobs_from_html(_html_of(msg), sender)
        M.logout()
    except Exception as e:  # noqa: BLE001 - one source failing must not kill the run
        print(f"  ! email source error: {e}")
        return out
    print(f"  email: parsed {len(out)} job link(s) from alerts")
    return out
