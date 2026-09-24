#!/usr/bin/env python3
"""
Local bridge — the tiny HTTP server the Chrome extension talks to.

It exposes the SAME engine the CLI uses (matcher, tailor, eval, outreach,
tracker) over localhost, so the in-browser panel can score the job you're
looking at, tailor your CV to it, and save it to your funnel — all locally,
nothing leaves your machine.

stdlib only (http.server) — no new dependency. Endpoints:
  GET  /health                      -> engine + model status
  POST /score   {title,company,url,jd}   -> fit, gap, reasons
  POST /tailor  {title,company,url,jd}   -> verified bullets + outreach + faithfulness
  POST /track   {title,company,url,source,stage} -> record in the funnel

Run:  python bridge.py        (defaults to 127.0.0.1:8765)
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml

from jobhunt import agent, matcher, llm, tailor as tailor_mod, outreach, eval as eval_mod, tracker
from jobhunt.models import Job

HOST, PORT = "127.0.0.1", 8765


def _cfg() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text()) if Path("config.yaml").exists() else {}


def _profile() -> tuple[str, list[dict]]:
    text = Path("profile.md").read_text() if Path("profile.md").exists() else ""
    facts = []
    if Path("profile.yaml").exists():
        facts = yaml.safe_load(Path("profile.yaml").read_text()).get("facts", [])
    return text, facts


def _job_from(payload: dict) -> Job:
    url = payload.get("url", "")
    domain = urlparse(url).netloc or "extension"
    return Job(source=f"extension:{domain}", title=payload.get("title", ""),
               company=payload.get("company", ""), url=url,
               description=payload.get("jd", ""))


def do_score(payload: dict) -> dict:
    cfg, (profile, _facts) = _cfg(), _profile()
    m = cfg.get("model", {"chat": "qwen2.5", "embed": "nomic-embed-text"})
    f = cfg.get("filters", {})
    vocab = cfg.get("skill_vocab", [])
    weights = cfg.get("scoring", {}).get("weights", {"vector": 0.3, "llm": 0.6, "keyword": 0.1})
    job = _job_from(payload)
    mr = matcher.match_report(profile, job, vocab, m["embed"])
    vector = round(100 * mr["similarity"])
    kw = llm.keyword_score(job, f.get("skills_must", []), f.get("skills_nice", []),
                           f.get("exclude_any", []))
    llm_score = None
    reasons = kw["reasons"]
    if llm.ollama_available(m["chat"]):
        res = llm.score_with_ollama(job, profile, m["chat"])
        if res:
            llm_score = int(res.get("fit_score", vector))
            reasons = res.get("reasons", reasons)
    fit = agent.weighted_blend({"vector": vector, "llm": llm_score, "keyword": kw["fit_score"]}, weights)
    return {
        "fit": fit,
        "components": {"vector": vector, "llm": llm_score, "keyword": kw["fit_score"]},
        "reasons": reasons,
        "matched_skills": mr["matched_skills"],
        "gap": mr["missing_skills"],       # the honest "what you're missing"
        "coverage": mr["coverage"],
    }


def do_tailor(payload: dict) -> dict:
    cfg, (_profile_text, facts) = _cfg(), _profile()
    if not facts:
        return {"error": "no profile.yaml facts found; cannot tailor truthfully"}
    m = cfg.get("model", {"chat": "qwen2.5"})
    vocab = cfg.get("skill_vocab", [])
    job = _job_from(payload)
    needs = matcher.extract_required_skills(job, vocab)
    use_llm = llm.ollama_available(m["chat"])
    tailored = tailor_mod.tailor(facts, job.title, needs, m["chat"]) if use_llm else {"bullets": [], "dropped": 0}
    faith = eval_mod.faithfulness(tailored, facts)
    oc = outreach.generate(facts, job.title, job.company, needs, m["chat"], use_llm=use_llm)
    return {
        "bullets": tailored.get("bullets", []),
        "dropped": tailored.get("dropped", 0),
        "faithfulness": faith["score"],
        "faithfulness_detail": f"{faith['supported']}/{faith['total']} verified",
        "outreach": {"dm": oc.get("dm", ""), "cover": oc.get("cover", ""),
                     "grounded": oc.get("grounded", True)},
    }


def do_track(payload: dict) -> dict:
    job = _job_from(payload)
    if payload.get("source"):
        job.source = payload["source"]
    stage = payload.get("stage", "seen")
    tracker.record(job, stage=stage)
    return {"ok": True, "stage": stage, "summary": tracker.summary()}


ROUTES = {"/score": do_score, "/tailor": do_tailor, "/track": do_track}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # the extension runs from a chrome-extension:// origin
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # CORS preflight
        self._send(204, {})

    def do_GET(self):
        if self.path == "/health":
            cfg = _cfg()
            chat = cfg.get("model", {}).get("chat", "qwen2.5")
            text, facts = _profile()
            self._send(200, {"ok": True, "model": chat,
                             "ollama": llm.ollama_available(chat),
                             "profile_loaded": bool(text), "facts": len(facts)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        fn = ROUTES.get(self.path)
        if not fn:
            return self._send(404, {"error": f"unknown route {self.path}"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, fn(payload))
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, *args):  # quieter console
        pass


def main():
    print(f"jobhunt bridge on http://{HOST}:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
