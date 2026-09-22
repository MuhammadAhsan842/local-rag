#!/usr/bin/env python3
"""
CLI entrypoint.

  python run.py                 # discover -> score -> tailor -> write shortlist.md
  python run.py --all           # ignore 'seen' state, re-rank everything
  python run.py --apply URL     # apply-assist (dry-run: pre-fills, never submits)
  python run.py --apply URL --submit   # submit (ONLY on Greenhouse/Lever/Ashby)
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import yaml

from jobhunt import agent, apply as apply_mod, report


def load_cfg() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


def load_profile() -> tuple[str, list[dict]]:
    """profile.md = free-text resume; profile.yaml = structured facts for tailoring."""
    prof_text = Path("profile.md").read_text() if Path("profile.md").exists() else ""
    facts = []
    if Path("profile.yaml").exists():
        facts = yaml.safe_load(Path("profile.yaml").read_text()).get("facts", [])
    return prof_text, facts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-rank all jobs, not just new")
    ap.add_argument("--apply", metavar="URL", help="apply-assist on a job URL")
    ap.add_argument("--submit", action="store_true", help="allow submit (ATS sites only)")
    args = ap.parse_args()

    cfg = load_cfg()
    prof_text, facts = load_profile()

    if args.apply:
        contact = {}
        if Path("profile.yaml").exists():
            contact = yaml.safe_load(Path("profile.yaml").read_text()).get("contact", {})
        resume_path = cfg.get("resume_file", "resume.pdf")
        # Local model for browser control; swap to a stronger model for reliability.
        try:
            from browser_use import ChatOllama
            model = ChatOllama(model=cfg["model"]["chat"])
        except Exception as e:  # noqa: BLE001
            print(f"browser-use/ChatOllama unavailable: {e}")
            return
        res = asyncio.run(apply_mod.apply(args.apply, contact, resume_path, model,
                                          allow_submit=args.submit))
        print(res)
        return

    if not prof_text:
        print("! No profile.md found. Copy profile.example.md -> profile.md and edit it.")
        return

    result = agent.run_pipeline(cfg, prof_text, facts, only_new=not args.all)
    out = report.write_report(result, facts)
    print(f"\n✓ wrote {out}  ({len(result['ranked'])} roles)")
    for j in result["ranked"][:8]:
        print(f"  [{j.fit_score:>3}] {j.title[:44]:44}  {j.company[:22]}")


if __name__ == "__main__":
    main()
