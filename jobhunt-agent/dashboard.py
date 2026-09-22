#!/usr/bin/env python3
"""
Local control panel — the "feels like a product" layer.

A single-file Gradio app over the same engine the CLI uses. Nothing new in the
pipeline: it reads the SQLite tracker + config, runs the radar on demand, shows
the ranked shortlist with fit + faithfulness, lets you advance a job through the
funnel (seen → applied → replied → interview → offer), and launches apply-assist
on one URL. All local; no data leaves your machine.

Run:  pip install gradio && python dashboard.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from jobhunt import agent, apply as apply_mod, tracker, tailor as tailor_mod, eval as eval_mod

try:
    import gradio as gr
except ImportError:  # pragma: no cover
    raise SystemExit("Dashboard needs Gradio:  pip install gradio")

STAGES = ["seen", "applied", "replied", "interview", "offer", "rejected"]


def _cfg() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


def _profile() -> tuple[str, list[dict]]:
    text = Path("profile.md").read_text() if Path("profile.md").exists() else ""
    facts = []
    if Path("profile.yaml").exists():
        facts = yaml.safe_load(Path("profile.yaml").read_text()).get("facts", [])
    return text, facts


# in-memory handle to the most recent run, so the UI can act on Job objects
_STATE: dict = {"ranked": [], "tailored": {}, "outreach": {}}


def run_radar(only_new: bool):
    cfg = _cfg()
    text, facts = _profile()
    if not text:
        return [["—", "copy profile.example.md → profile.md first", "", "", ""]], "No profile.md"
    result = agent.run_pipeline(cfg, text, facts, only_new=only_new)
    _STATE.update(result)
    rows = _rows(result["ranked"], result["tailored"], facts)
    return rows, _stats()


def _rows(ranked, tailored, facts):
    out = []
    for j in ranked[:25]:
        faith = "—"
        if j.key in tailored:
            f = eval_mod.faithfulness(tailored[j.key], facts)
            faith = f"{f['score']:.2f} ({f['supported']}/{f['total']})" if f["total"] else "n/a"
        out.append([j.fit_score, j.title[:48], j.company[:22],
                    tracker.stage_of(j.key) or "seen", faith, j.url])
    return out


def _stats() -> str:
    s = tracker.summary()
    if not s:
        return "No history yet — run the radar."
    order = " · ".join(f"**{k}**: {s.get(k, 0)}" for k in STAGES if k in s)
    priors = tracker.conversion_priors()
    pr = ", ".join(f"{k} {v:.0%}" for k, v in priors.items()) or "learning… (need ≥3 apps/source)"
    return f"{order}\n\nLearned reply-rate by source: {pr}"


def _find(url: str):
    for j in _STATE["ranked"]:
        if j.url == url:
            return j
    return None


def set_stage(url: str, stage: str):
    j = _find(url)
    if not j:
        return "Job not in the current run — run the radar first."
    tracker.record(j, stage=stage)
    return f"Marked **{j.title[:40]}** → {stage}\n\n{_stats()}"


def show_detail(url: str):
    j = _find(url)
    if not j:
        return "Paste a URL from the table above."
    _, facts = _profile()
    parts = [f"### {j.title} — {j.company}", f"**Fit {j.fit_score}** · {j.location or 'n/a'}",
             f"_{j.reasons}_", f"[Apply]({j.url})", ""]
    oc = _STATE["outreach"].get(j.key)
    if oc:
        parts += ["**Recruiter DM:**", f"> {oc.get('dm','')}", "", "**Cover note:**", f"> {oc.get('cover','')}", ""]
    t = _STATE["tailored"].get(j.key)
    if t and t.get("bullets"):
        parts.append("**Tailored bullets (verified):**")
        parts += [f"- {b['text']}" for b in t["bullets"]]
    return "\n".join(parts)


def launch_apply(url: str, allow_submit: bool):
    j = _find(url)
    if not j:
        return "Paste a URL from the table first."
    cfg = _cfg()
    contact = {}
    if Path("profile.yaml").exists():
        contact = yaml.safe_load(Path("profile.yaml").read_text()).get("contact", {})
    try:
        from browser_use import ChatOllama
        model = ChatOllama(model=cfg["model"]["chat"])
    except Exception as e:  # noqa: BLE001
        return f"browser-use/ChatOllama unavailable: {e}\n(pip install browser-use && playwright install chromium)"
    res = asyncio.run(apply_mod.apply(url, contact, cfg.get("resume_file", "resume.pdf"),
                                      model, allow_submit=allow_submit))
    if res.get("ok"):
        tracker.record(j, stage="applied")
    return f"{res}\n\n{_stats()}"


def build():
    with gr.Blocks(title="jobhunt-agent") as ui:
        gr.Markdown("# jobhunt-agent — local control panel\nEvidence-first, human-in-the-loop. All local.")
        with gr.Row():
            only_new = gr.Checkbox(label="Only new since last run", value=True)
            run_btn = gr.Button("↻ Run radar", variant="primary")
        stats = gr.Markdown(_stats())
        table = gr.Dataframe(
            headers=["fit", "title", "company", "stage", "faithfulness", "url"],
            datatype=["number", "str", "str", "str", "str", "str"],
            interactive=False, wrap=True, label="Ranked shortlist",
        )
        with gr.Row():
            url_in = gr.Textbox(label="Job URL (paste from table)", scale=3)
            stage_in = gr.Dropdown(STAGES, value="applied", label="Stage", scale=1)
            mark_btn = gr.Button("Mark stage")
        detail_btn = gr.Button("Show detail / outreach")
        detail = gr.Markdown()
        with gr.Accordion("Apply-assist (human-in-the-loop)", open=False):
            submit_chk = gr.Checkbox(label="Allow submit (ATS only, off = dry-run/pre-fill)", value=False)
            apply_btn = gr.Button("Launch apply-assist", variant="stop")
            apply_out = gr.Markdown()

        run_btn.click(run_radar, [only_new], [table, stats])
        mark_btn.click(set_stage, [url_in, stage_in], [stats])
        detail_btn.click(show_detail, [url_in], [detail])
        apply_btn.click(launch_apply, [url_in, submit_chk], [apply_out])
    return ui


if __name__ == "__main__":
    build().launch()
