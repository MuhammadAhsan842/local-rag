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

from jobhunt import agent, apply as apply_mod, report, eval as eval_mod, autopilot


def load_cfg() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


def load_profile() -> tuple[str, list[dict]]:
    """profile.md = free-text resume; profile.yaml = structured facts for tailoring."""
    prof_text = Path("profile.md").read_text() if Path("profile.md").exists() else ""
    facts = []
    if Path("profile.yaml").exists():
        facts = yaml.safe_load(Path("profile.yaml").read_text()).get("facts", [])
    return prof_text, facts


def run_eval(path: str) -> None:
    """Score the faithfulness metric against a labeled adversarial set."""
    import json
    import sys

    data = json.loads(Path(path).read_text())
    facts = data["facts"]
    cases = data["cases"]
    results = [{"bullets": c["bullets"]} for c in cases]
    agg = eval_mod.evaluate_run(results, facts)

    print(f"\nFaithfulness eval — {path}")
    print(f"  mean faithfulness : {agg['mean_faithfulness']}")
    print(f"  cases             : {agg['cases']}")
    print(f"  total violations  : {agg['total_violations']}\n")

    # per-case, checked against the human 'expect_supported' label
    label_ok = True
    for c, s in zip(cases, agg["per_case"]):
        got_clean = s["total"] > 0 and not s["violations"]
        expect = c.get("expect_supported")
        mark = "?"
        if expect is not None:
            hit = (got_clean == expect)
            label_ok = label_ok and hit
            mark = "✓" if hit else "✗ MISLABELED BY METRIC"
        print(f"  [{s['score']:.2f}] {c['name']:22} expect={expect}  {mark}")
        for v in s["violations"]:
            print(f"        · caught: {v['reason']}")

    print(f"\n  metric matches human labels: {label_ok}")
    # exit non-zero so CI can gate on it
    sys.exit(0 if label_ok else 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-rank all jobs, not just new")
    ap.add_argument("--apply", metavar="URL", help="apply-assist on a job URL")
    ap.add_argument("--submit", action="store_true", help="allow submit (ATS sites only)")
    ap.add_argument("--eval", metavar="FILE", nargs="?", const="eval/faithfulness_seed.json",
                    help="run the faithfulness eval on a labeled set and exit")
    ap.add_argument("--auto-apply", action="store_true",
                    help="batch auto-apply on ATS roles above the fit floor (dry-run unless --go)")
    ap.add_argument("--go", action="store_true",
                    help="with --auto-apply: actually submit (ATS only). Omit for a dry-run plan.")
    args = ap.parse_args()

    if args.eval:
        run_eval(args.eval)
        return

    cfg = load_cfg()
    prof_text, facts = load_profile()

    if args.apply:
        contact = {}
        if Path("profile.yaml").exists():
            contact = yaml.safe_load(Path("profile.yaml").read_text()).get("contact", {})
        resume_path = cfg.get("resume_file", "resume.pdf")
        # Browser Use drives a real browser with a local Ollama model.
        res = asyncio.run(apply_mod.apply(args.apply, contact, resume_path,
                                          model_name=cfg["model"]["chat"],
                                          allow_submit=args.submit))
        print(res)
        return

    if not prof_text:
        print("! No profile.md found. Copy profile.example.md -> profile.md and edit it.")
        return

    if args.auto_apply:
        summary = autopilot.run(cfg, prof_text, facts, dry_run=not args.go)
        print(f"\n{'SUBMITTED' if args.go else 'DRY-RUN'}: "
              f"{summary['submitted']}/{summary['eligible']} eligible ATS roles")
        if not args.go:
            print("  (this was a plan; re-run with --auto-apply --go to actually submit)")
        return

    result = agent.run_pipeline(cfg, prof_text, facts, only_new=not args.all)
    out = report.write_report(result, facts)
    print(f"\n✓ wrote {out}  ({len(result['ranked'])} roles)")
    for j in result["ranked"][:8]:
        print(f"  [{j.fit_score:>3}] {j.title[:44]:44}  {j.company[:22]}")


if __name__ == "__main__":
    main()
