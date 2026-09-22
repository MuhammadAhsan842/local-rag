"""Writes a ranked shortlist to Markdown, with tailored bullets inline."""
from __future__ import annotations

from datetime import datetime

from .models import Job
from .tailor import render_resume


def write_report(result: dict, facts: list[dict], path: str = "shortlist.md", top: int = 25) -> str:
    ranked: list[Job] = result["ranked"][:top]
    tailored: dict = result["tailored"]
    outreach: dict = result.get("outreach", {})
    lines = [
        f"# AI Engineer job shortlist — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{len(ranked)} roles, ranked by fit. Source: your configured boards + Remotive.",
        "_Remotive listings are delayed 24h and credited to remotive.com per their API terms._",
        "",
    ]
    for i, j in enumerate(ranked, 1):
        age = j.age_days
        freshness = f"{int(age)}d ago" if age is not None else "date n/a"
        remote = "remote" if j.is_remote else (j.location or "n/a")
        lines += [
            f"## {i}. {j.title} — {j.company}  ·  fit {j.fit_score}",
            f"- **Where:** {remote}  ·  **Posted:** {freshness}  ·  **Salary:** {j.salary or 'n/a'}  ·  **Source:** {j.source}",
            f"- **Why:** {j.reasons}",
        ]
        if j.red_flags:
            lines.append(f"- **Watch:** {j.red_flags}")
        if j.hook:
            lines.append(f"- **Opening line:** {j.hook}")
        lines.append(f"- **Apply:** {j.url}")
        if j.key in tailored and tailored[j.key].get("bullets"):
            lines += ["", "<details><summary>Tailored resume bullets (verified)</summary>", "", "```"]
            lines.append(render_resume(f"{j.title} @ {j.company}", tailored[j.key], facts))
            lines += ["```", "</details>"]
        oc = outreach.get(j.key)
        if oc and (oc.get("dm") or oc.get("cover")):
            flag = "" if oc.get("grounded", True) else " ⚠️ review — model cited an unknown fact"
            lines += ["", f"<details><summary>Outreach draft (recruiter DM + cover note){flag}</summary>", ""]
            if oc.get("dm"):
                lines += ["**DM / short message:**", "", f"> {oc['dm']}", ""]
            if oc.get("cover"):
                lines += ["**Cover note:**", "", f"> {oc['cover']}", ""]
            lines.append("</details>")
        lines.append("")
    text = "\n".join(lines)
    with open(path, "w") as fh:
        fh.write(text)
    return path
