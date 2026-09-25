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

## Coverage: 40+ EU/remote portals
```bash
python run.py --portals     # list every known portal and how it connects
```
Portals connect four ways (see `portals.yaml`):
- **auto** — keyless API/RSS, ingested with no browser: Remotive, Arbeitnow,
  RemoteOK, web3.career, Hacker News "Who's Hiring", + any RSS feed (Jobspresso,
  We Work Remotely, ai-jobs.net…).
- **browser** — pre-fill via Browser Use, you submit (StepStone, Otta, Landing.jobs,
  NoFluffJobs, JustJoin.it, Honeypot, WeAreDevelopers, Jobbatical, …).
- **browser_login / manual / email** — assisted only where a login or ToS makes
  automation unsafe (LinkedIn, XING, Wellfound, Indeed, Glassdoor), or via your
  job-alert emails / manual apply.

## Email job-alerts as a source (read-only)
Turn the "new jobs for you" emails you already get into ranked, tailored roles.
```bash
export JOBHUNT_EMAIL_PASS=your_gmail_app_password   # never stored in config
# then set sources.email.enabled: true in config.yaml
python run.py
```
- **Read-only IMAP** — only fetches and parses; never sends, deletes, or marks.
- The app password lives in an **env var**, not the repo. Only the env-var name
  is in `config.yaml`.
- Parses LinkedIn/Indeed/Otta/Wellfound alerts, extracts role + apply link, and
  feeds them through the same dedupe → score → tailor → track pipeline.
- You click apply yourself (safest — no portal-ToS risk).

## Batch auto-apply (high throughput, ban-safe)
Fully automated to submission — on the lane where automation is allowed.
```bash
python run.py --auto-apply         # dry-run: plan + tailor, submit nothing
python run.py --auto-apply --go    # actually submit on company ATS pages
```
- **Auto-submits ONLY on company ATS** (Greenhouse/Lever/Ashby/Workable). Never
  on LinkedIn/Indeed/aggregators — automating your own account there gets it
  banned. Those stay assisted (pre-fill, you click).
- **Targeted volume:** applies only to roles ≥ `autopilot.min_fit`, and never
  submits a CV whose faithfulness < `min_faithfulness` (no unverified bullet
  ever goes out). 80 tailored applications beat 800 sprayed ones.
- Hard `max_applies` cap, polite pacing, skips already-applied, everything
  tracked. Dry-run by default; `--go` to submit.

## Chrome extension (in-browser panel, like Cowork)
An MV3 side panel that reads the job you're viewing, scores it, tailors your CV
truthfully, and saves it to your funnel — talking to a **local** bridge.
```bash
python bridge.py     # local engine API on 127.0.0.1:8765
# then Chrome → chrome://extensions → Developer mode → Load unpacked → extension/
```
Works on Greenhouse/Lever/Ashby/LinkedIn/Indeed/Wellfound/Otta. Read-only on the
page; you always click apply yourself. See `extension/README.md`. The bridge
(`bridge.py`) also serves the same engine to any local tool over HTTP.

## Control panel + learning loop (the "product" layer)
```bash
pip install gradio && python dashboard.py     # local Cowork-style console
```
- **Ranked shortlist** with fit + live **faithfulness** per role.
- **Funnel tracking** (`jobhunt/tracker.py`, SQLite): advance a job
  seen → applied → replied → interview → offer. Survives across runs/machines,
  so "did I already apply?" is answered for you.
- **Closed learning loop**: sources that actually *reply* to you earn a ranking
  boost over time; black holes get penalized (`scoring.use_history`). The tool
  gets better at *your* hunt every week.
- **Apply-assist** launches from the panel (dry-run pre-fill by default).

## Self-correcting, schema-hardened tailoring
- `tailor.py` asks Ollama with a **JSON schema** (`format`), not free text —
  fewer parse failures. (Upgrade path in code: Instructor/Outlines.)
- **Agentic retry**: each bullet is verified by the faithfulness metric; if any
  bullet is ungrounded or invents a number, the tailor re-asks **once** with the
  exact rejections, then keeps only verified bullets. A real critic loop, not a
  single shot.

## Import your real CV → tailored ATS-clean CV
```bash
python run.py --import-cv path/to/your_cv.pdf   # -> profile.md + profile.yaml facts (review them)
```
- Parses PDF/DOCX/TXT into free text + atomic facts (heuristic, or LLM if Ollama up).
- At apply time the tool **renders a tailored, single-column, ATS-safe CV**
  (`.txt`/`.html`, `.docx` if python-docx present) from your verified bullets —
  so the tailoring actually reaches the recruiter, not a stale static PDF.
  Research: single-column parses ~97% vs ~71% for two-column/graphic PDFs.

## Eligibility-first gating
Hard constraints (seniority band, visa sponsorship, clearance, location) are
applied **before** ranking (`filters.eligibility`), so roles you can't take
never occupy a top slot. Ineligible roles are reported with the reason.

## Reliability + tests
- Every network fetch uses **exponential-backoff retry** (transient/429/5xx),
  fails fast on 4xx.
- Every run appends a structured **audit trail** to `runs.jsonl`
  (discover/score/tailor/apply, with the tailored-CV paths and submit result).
- **Test suite:** `pytest -q` (14 tests: scoring, eligibility, faithfulness,
  dedupe, email parsing, CV render, tracker) — all green.

## Accuracy backbone (what makes this defensibly world-class)
Most job-bots never measure their own output. This one does.

- **Weighted hybrid fit score** (`config.yaml → scoring.weights`): vector +
  LLM + keyword, default **30/60/10** (Resume-Matcher pattern). If a signal is
  unavailable (Ollama down), its weight is redistributed — score stays 0-100.
- **Faithfulness metric** (`jobhunt/eval.py`, DeepEval-inspired, dependency-free):
  every tailored bullet is checked for grounding, **numeric honesty** (a number
  in a bullet must appear in the source fact — catches fabricated metrics), and
  lexical support. Produces a 0-1 score.
- **CI-gateable eval**: `python run.py --eval` runs an adversarial labeled set
  (`eval/faithfulness_seed.json`) and exits non-zero if the metric disagrees
  with the human labels — so a prompt/model change can't silently regress honesty.

```bash
python run.py --eval          # score the built-in adversarial set
```

Reference repos studied: [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher)
(hybrid scoring), [confident-ai/deepeval](https://github.com/confident-ai/deepeval)
(faithfulness eval). Documented upgrade paths in code: Instructor/Outlines for
schema-guaranteed JSON, DeepEval + local Ollama judge for semantic entailment,
JobSpy for optional Indeed/Google sources.

## Layout
- `jobhunt/sources.py`  — job fetchers (ATS, Remotive, Arbeitnow, Adzuna) + per-source health
- `jobhunt/sources_email.py` — read job-alert emails over READ-ONLY IMAP (Gmail app password)
- `jobhunt/sources_rss.py` — generic RSS/Atom feed ingest (Jobspresso, WWR, ai-jobs.net, …)
- `jobhunt/portals.py` + `portals.yaml` — registry of 40+ EU/remote portals & how each connects
- `jobhunt/matcher.py`  — semantic similarity + skill-gap analysis
- `jobhunt/llm.py`      — Ollama scoring + keyword fallback
- `jobhunt/tailor.py`   — truthful tailoring + faithfulness gate
- `jobhunt/outreach.py` — truthful recruiter DM + cover note (fact-grounded)
- `jobhunt/eval.py`     — faithfulness metric (CI-gateable accuracy backbone)
- `jobhunt/tracker.py`  — SQLite funnel + learned per-source conversion priors
- `jobhunt/apply.py`    — Browser Use apply-assist (human-in-the-loop)
- `jobhunt/agent.py`    — the orchestration loop
- `run.py`             — CLI (`--eval`, `--apply`, `--all`)
- `dashboard.py`       — local Gradio control panel

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
