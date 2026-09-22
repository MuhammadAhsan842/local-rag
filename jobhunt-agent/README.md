# jobhunt-agent

A local, agentic assistant for an AI-engineer job hunt. It discovers real
openings from keyless/legit sources, uses a **local LLM (Ollama)** to match them
to your resume, **truthfully tailors** resume bullets to each role, and can
**pre-fill applications with Browser Use** — with a human reviewing before submit.

## Why it's built this way
- **No LinkedIn scraping.** LinkedIn has no open jobs API and automating it risks
  banning the account you need. We pull from company ATS boards (Greenhouse /
  Lever / Ashby — direct from the employer), Remotive, and optionally Adzuna.
- **Tailor, never fabricate.** Every tailored bullet must trace to a real fact in
  your `profile.yaml`. A faithfulness gate drops anything the model makes up.
- **Human-in-the-loop apply.** The apply step pre-fills and stops; you review and
  submit. Auto-submit is restricted to company ATS pages and off by default.

## Setup
```bash
pip install -r requirements.txt
# local model:
#   install Ollama (ollama.com), then:
ollama pull qwen2.5            # or any chat model; put its name in config.yaml
ollama pull nomic-embed-text  # for semantic matching
cp profile.example.md  profile.md    # edit: your free-text resume
cp profile.example.yaml profile.yaml # edit: your atomic, TRUE facts + contact
# edit config.yaml -> add target companies + filters
```

## Run
```bash
python run.py            # new jobs -> ranked shortlist.md (+ tailored bullets)
python run.py --all      # re-rank everything, ignore 'seen' history
```
Schedule it as a daily radar (cron, 8am):
```
0 8 * * *  cd /path/to/jobhunt-agent && /usr/bin/python3 run.py
```

## Apply-assist (optional, needs Browser Use)
```bash
pip install browser-use && playwright install chromium
python run.py --apply "https://boards.greenhouse.io/acme/jobs/123"          # dry-run
python run.py --apply "https://boards.greenhouse.io/acme/jobs/123" --submit # ATS only
```

## Layout
- `jobhunt/sources.py`  — job fetchers (ATS, Remotive, Adzuna)
- `jobhunt/matcher.py`  — semantic similarity + skill-gap analysis
- `jobhunt/llm.py`      — Ollama scoring + keyword fallback
- `jobhunt/tailor.py`   — truthful tailoring + faithfulness gate
- `jobhunt/outreach.py` — truthful recruiter DM + cover note (fact-grounded)
- `jobhunt/apply.py`    — Browser Use apply-assist (human-in-the-loop)
- `jobhunt/agent.py`    — the orchestration loop
- `run.py`             — CLI

## Urgent-hunt tuning (config.yaml → `filters`)
- `remote_only: true`   — keep only roles detected as remote
- `max_age_days: 30`    — drop stale postings
- `recency_boost: 8`    — nudge fresh roles to the top of the shortlist

Each shortlisted role now shows **how old the posting is**, whether it's
**remote**, verified **tailored résumé bullets**, and a ready-to-send
**recruiter DM + cover note** — all grounded in your real `profile.yaml`
facts (nothing invented; ungrounded copy is flagged for review).

Works with **no Ollama** too (falls back to keyword scoring) and with **no
network to a given source** (that source is skipped, run continues).
