"""
Apply-assist layer using Browser Use.

Deliberate safety design (this is a feature, not a limitation):
- DRY-RUN by default: the agent navigates and *pre-fills* the application, then
  STOPS. A human reviews and clicks submit. Nothing is submitted autonomously.
- Submit only happens if you pass allow_submit=True AND the site is a company ATS
  (Greenhouse/Lever/Ashby). We never automate LinkedIn/Easy-Apply: ToS + ban risk.
- Every action is logged for an audit trail.

Browser Use + Playwright are heavy deps, so this module is import-guarded: the
rest of the tool runs fine without them installed.

Install to use this:  pip install browser-use && playwright install chromium
"""
from __future__ import annotations

ALLOWED_ATS = ("greenhouse.io", "lever.co", "ashbyhq.com", "boards.greenhouse.io")


def _is_allowed(url: str) -> bool:
    return any(dom in url for dom in ALLOWED_ATS)


def build_task(profile_contact: dict, resume_path: str, allow_submit: bool) -> str:
    """Natural-language task for the Browser Use agent."""
    stop_rule = (
        "After filling every field you can, STOP and do NOT submit. "
        "Report the filled fields and any you couldn't fill."
        if not allow_submit else
        "Fill all fields, then submit the application, then confirm submission."
    )
    return f"""Fill in this job application form using ONLY these details:
name: {profile_contact.get('name','')}
email: {profile_contact.get('email','')}
phone: {profile_contact.get('phone','')}
location: {profile_contact.get('location','')}
linkedin: {profile_contact.get('linkedin','')}
github: {profile_contact.get('github','')}
Attach the resume file at: {resume_path}
If a question asks something not in these details, leave it blank and note it.
Never invent answers to screening questions. {stop_rule}"""


async def apply(url: str, profile_contact: dict, resume_path: str,
                model_provider, allow_submit: bool = False) -> dict:
    """
    model_provider: a Browser Use chat model (e.g. ChatOllama(...) for local, or a
    stronger hosted model for reliability on the browser step — see the plan).
    Returns a result dict; raises if browser-use isn't installed.
    """
    if allow_submit and not _is_allowed(url):
        return {"ok": False, "reason": f"auto-submit blocked for non-ATS url: {url}"}

    try:
        from browser_use import Agent  # noqa: PLC0415
    except ImportError:
        return {"ok": False, "reason": "browser-use not installed. "
                "pip install browser-use && playwright install chromium"}

    task = build_task(profile_contact, resume_path, allow_submit and _is_allowed(url))
    agent = Agent(task=f"Go to {url}. {task}", llm=model_provider)
    history = await agent.run(max_steps=40)
    return {
        "ok": True,
        "url": url,
        "submitted": bool(allow_submit and _is_allowed(url)),
        "steps": len(getattr(history, "history", []) or []),
        "result": str(getattr(history, "final_result", lambda: "")() if callable(
            getattr(history, "final_result", None)) else ""),
    }
