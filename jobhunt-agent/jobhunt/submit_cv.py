"""Attach the CV on a company application form and submit it.

This is the non-Mercor path. Board pages do not accept a file. The employer
form does, when it is Greenhouse or Lever. Unknown required questions and any
US work-authorization prompt stop the run. cv_submitted is set only when the
page confirms the application was received.
"""
from __future__ import annotations

from pathlib import Path

from .apply_plan import cv_accepted


def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def submit(url: str, contact: dict, resume_path: str) -> dict:
    resume = Path(resume_path)
    if not resume.is_file():
        return {"ok": False, "stage": "error", "reason": f"missing resume {resume_path}", "cv_submitted": False}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "stage": "error", "reason": "playwright is not installed", "cv_submitted": False}

    first, last = _split_name(contact.get("name") or "")
    email = contact.get("email") or ""
    phone = contact.get("phone") or ""
    location = contact.get("location") or ""

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            apply = page.get_by_role("link", name="Apply")
            if apply.count():
                apply.first.click()
                page.wait_for_timeout(2000)
            target = page
            if page.locator("iframe").count():
                target = page.frame_locator("iframe").first
            body = page.inner_text("body").lower()
            if "authorized to work in the united states" in body or "us work authorization" in body:
                return {"ok": False, "stage": "us_job", "url": page.url, "cv_submitted": False,
                        "reason": "form asks for US work authorization"}
            filled = _fill(target, first, last, email, phone, location, str(resume))
            if not filled:
                return {"ok": False, "stage": "no_form", "url": page.url, "cv_submitted": False,
                        "reason": "no Greenhouse or Lever resume field on this page"}
            submit_btn = target.locator("button[type=submit], input[type=submit], button:has-text('Submit')")
            if not submit_btn.count():
                return {"ok": False, "stage": "needs_human", "url": page.url, "cv_submitted": False,
                        "reason": "resume attached, submit button not found"}
            submit_btn.first.click()
            page.wait_for_timeout(4000)
            text = page.inner_text("body")
            accepted = cv_accepted(text)
            return {
                "ok": accepted,
                "stage": "cv_submitted" if accepted else "needs_human",
                "url": page.url,
                "cv_submitted": accepted,
                "reason": "" if accepted else "form did not confirm the CV was received",
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "stage": "error", "reason": str(e), "cv_submitted": False, "url": url}
        finally:
            browser.close()


def _fill(target, first: str, last: str, email: str, phone: str, location: str, resume: str) -> bool:
    """Fill a Greenhouse or Lever form. Returns False when there is no resume field."""
    file_input = target.locator("input[type=file]")
    if not file_input.count():
        return False
    pairs = [
        ("#first_name", first),
        ("input[name=first_name]", first),
        ("#last_name", last),
        ("input[name=last_name]", last),
        ("#email", email),
        ("input[name=email]", email),
        ("#phone", phone),
        ("input[name=phone]", phone),
        ("input[name=name]", f"{first} {last}".strip()),
        ("input[name=location]", location),
        ("#job_application_location", location),
    ]
    for sel, value in pairs:
        if not value:
            continue
        box = target.locator(sel)
        if box.count():
            box.first.fill(value)
    file_input.first.set_input_files(resume)
    return True
