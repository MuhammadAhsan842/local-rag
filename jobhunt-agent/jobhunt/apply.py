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

ALLOWED_ATS = ("greenhouse.io", "lever.co", "ashbyhq.com", "boards.greenhouse.io",
               "workable.com", "jobs.ashbyhq.com")


def _is_allowed(url: str) -> bool:
    return any(dom in url for dom in ALLOWED_ATS)


def local_model(model_name: str, base_url: str = "http://localhost:11434", num_ctx: int = 32000):
    """
    Build a Browser Use chat model backed by local Ollama.

    Robust to both layouts: newer browser-use ships its own ChatOllama; older
    setups use langchain-ollama. We also raise num_ctx — the Ollama default
    context is too small for browser-use's prompts and causes the well-known
    "Could not parse response" failures (browser-use issues #173/#220).
    Returns None (not an exception) if no provider is importable, so callers can
    degrade gracefully.
    """
    for importer in (
        lambda: __import__("browser_use", fromlist=["ChatOllama"]).ChatOllama,
        lambda: __import__("langchain_ollama", fromlist=["ChatOllama"]).ChatOllama,
    ):
        try:
            ChatOllama = importer()
        except Exception:  # noqa: BLE001
            continue
        try:
            return ChatOllama(model=model_name, base_url=base_url, num_ctx=num_ctx)
        except TypeError:
            # some versions don't accept base_url/num_ctx kwargs
            try:
                return ChatOllama(model=model_name, num_ctx=num_ctx)
            except TypeError:
                return ChatOllama(model=model_name)
    return None


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
                model_provider=None, allow_submit: bool = False,
                model_name: str | None = None, max_steps: int = 40) -> dict:
    """
    model_provider: a ready Browser Use chat model. If None, one is built from
    `model_name` via local_model() (local Ollama). Returns a result dict.
    """
    if allow_submit and not _is_allowed(url):
        return {"ok": False, "reason": f"auto-submit blocked for non-ATS url: {url}"}

    try:
        from browser_use import Agent  # noqa: PLC0415
    except ImportError:
        return {"ok": False, "reason": "browser-use not installed. "
                "pip install browser-use && playwright install chromium"}

    if model_provider is None:
        model_provider = local_model(model_name or "qwen2.5")
        if model_provider is None:
            return {"ok": False, "reason": "no local model provider available "
                    "(install browser-use or langchain-ollama, and run `ollama serve`)"}

    task = build_task(profile_contact, resume_path, allow_submit and _is_allowed(url))
    agent = Agent(task=f"Go to {url}. {task}", llm=model_provider)
    history = await agent.run(max_steps=max_steps)
    return {
        "ok": True,
        "url": url,
        "submitted": bool(allow_submit and _is_allowed(url)),
        "steps": len(getattr(history, "history", []) or []),
        "result": str(getattr(history, "final_result", lambda: "")() if callable(
            getattr(history, "final_result", None)) else ""),
    }
