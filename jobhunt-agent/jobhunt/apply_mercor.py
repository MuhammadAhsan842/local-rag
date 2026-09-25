"""Start a Mercor application and stop at sign-in.

This is the procedure that worked in the browser: fill legal name, email, and
LinkedIn, click Start, click Continue to this role, then stop. Mercor will not
accept a resume or interview until the person signs in. Work-authorization
questions are never answered here.
"""
from __future__ import annotations


def start(url: str, contact: dict, resume_path: str = "") -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "stage": "error", "reason": "playwright is not installed", "url": url}

    name = contact.get("name") or ""
    email = contact.get("email") or ""
    linkedin = contact.get("linkedin") or ""
    if not (name and email and linkedin):
        return {"ok": False, "stage": "error", "reason": "profile.yaml contact needs name, email, linkedin", "url": url}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            cookies = page.get_by_role("button", name="Accept All")
            if cookies.count():
                cookies.first.click()
            page.get_by_label("Full legal name").fill(name)
            page.get_by_label("Email").fill(email)
            page.get_by_label("LinkedIn URL").fill(linkedin)
            page.locator("button", has_text="Start application").click()
            page.wait_for_timeout(1500)
            cont = page.locator("button", has_text="Continue to this role")
            if cont.count():
                cont.first.click()
                page.wait_for_timeout(5000)
            landed = page.url
            body = page.inner_text("body")
            from .apply_plan import cv_accepted
            accepted = cv_accepted(body)
            needs_signin = "login" in landed or "sign in" in body.lower()
            files = page.locator("input[type=file]")
            if resume_path and files.count() and not needs_signin:
                files.first.set_input_files(resume_path)
            return {
                "ok": True,
                "stage": "cv_submitted" if accepted else ("needs_signin" if needs_signin else "needs_human"),
                "url": url,
                "continue_url": landed,
                "submitted": accepted,
                "cv_submitted": accepted,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "stage": "error", "reason": str(e), "url": url}
        finally:
            browser.close()
