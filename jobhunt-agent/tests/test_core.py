"""
Unit tests for the engine (gap #8). Dependency-free (no network, no Ollama).
Run:  pytest -q      (or: python -m pytest)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobhunt.models import Job
from jobhunt import agent, eligibility, eval as ev, resume_render, tracker, cv_parse
from jobhunt.sources_email import extract_jobs_from_html


def J(title, company="Co", source="greenhouse:co", url="https://boards.greenhouse.io/co/1",
      location="Remote", desc="python pytorch llm rag", pub=""):
    return Job(source=source, title=title, company=company, url=url,
               location=location, description=desc, published_at=pub)


# ---------- weighted hybrid scoring ----------
def test_weighted_blend_all_present():
    w = {"vector": 0.3, "llm": 0.6, "keyword": 0.1}
    assert agent.weighted_blend({"vector": 80, "llm": 90, "keyword": 50}, w) == 83


def test_weighted_blend_renormalizes_when_llm_missing():
    w = {"vector": 0.3, "llm": 0.6, "keyword": 0.1}
    # (0.3*80 + 0.1*40) / 0.4 = 70
    assert agent.weighted_blend({"vector": 80, "llm": None, "keyword": 40}, w) == 70


def test_weighted_blend_nothing():
    assert agent.weighted_blend({"vector": None, "llm": None, "keyword": None},
                                {"vector": 1}) == 0


# ---------- eligibility gate ----------
def test_eligibility_seniority_floor():
    cfg = {"filters": {"eligibility": {"min_seniority": 2}}}
    ok, why = eligibility.check(J("Junior AI Engineer"), cfg)
    assert not ok and "seniority" in why


def test_eligibility_visa_required_blocks_no_sponsorship():
    cfg = {"filters": {"eligibility": {"require_visa_sponsorship": True}}}
    j = J("AI Engineer", desc="No visa sponsorship available for this role.")
    ok, why = eligibility.check(j, cfg)
    assert not ok and "visa" in why


def test_eligibility_passes_clean_role():
    cfg = {"filters": {"eligibility": {"min_seniority": 1, "require_visa_sponsorship": True}}}
    j = J("Senior AI Engineer", desc="Remote. Visa sponsorship available.")
    ok, why = eligibility.check(j, cfg)
    assert ok and why == ""


# ---------- faithfulness metric ----------
def test_faithfulness_flags_fabricated_number():
    facts = [{"id": "F1", "source": "x", "text": "Cut resolution time 30% with a Python RAG pipeline."}]
    tailored = {"bullets": [{"fact_id": "F1", "text": "Cut resolution time 90% with Python RAG."}]}
    r = ev.faithfulness(tailored, facts)
    assert r["score"] == 0.0 and r["violations"]


def test_faithfulness_accepts_grounded():
    facts = [{"id": "F1", "source": "x", "text": "Built a Python RAG pipeline; cut resolution time 30%."}]
    tailored = {"bullets": [{"fact_id": "F1", "text": "Built a Python RAG pipeline that cut resolution time 30%."}]}
    assert ev.faithfulness(tailored, facts)["score"] == 1.0


def test_faithfulness_flags_unknown_factid():
    facts = [{"id": "F1", "source": "x", "text": "anything"}]
    tailored = {"bullets": [{"fact_id": "F9", "text": "Led a team."}]}
    assert ev.faithfulness(tailored, facts)["score"] == 0.0


# ---------- dedupe + recency ----------
def test_dedupe_by_url_and_key():
    jobs = [J("AI Engineer", url="https://x/1"),
            J("AI Engineer", url="https://x/1?utm=a"),  # same url, different query
            J("ML Engineer", url="https://x/2")]
    assert len(agent.dedupe(jobs)) == 2


# ---------- email parsing ----------
def test_email_extract_splits_company_and_drops_chrome():
    html = ('<a href="https://boards.greenhouse.io/acme/jobs/1">AI Engineer at Acme</a>'
            '<a href="https://x.com/unsubscribe">Unsubscribe</a>')
    jobs = extract_jobs_from_html(html, "jobs@linkedin.com")
    assert len(jobs) == 1
    assert jobs[0].company == "Acme" and jobs[0].title == "AI Engineer"


# ---------- resume render (ATS-clean) ----------
def test_render_text_contains_bullets_and_no_html(tmp_path):
    facts = [{"id": "F1", "source": "Acme", "text": "Built RAG."}]
    tailored = {"bullets": [{"fact_id": "F1", "text": "Built a Python RAG pipeline."}]}
    out = resume_render.render({"name": "Jane Doe", "email": "j@x.com"}, tailored, facts,
                               ["python", "rag"], job_title="AI Engineer", company="Beta",
                               out_base=str(tmp_path / "cv"))
    txt = Path(out["txt"]).read_text()
    assert "Jane Doe" in txt and "Built a Python RAG pipeline." in txt
    html = Path(out["html"]).read_text()
    # single-column, no tables (ATS-safe)
    assert "<table" not in html.lower()


# ---------- cv parse (heuristic facts) ----------
def test_cv_heuristic_facts_from_text():
    text = ("John Engineer\njohn@x.com\n"
            "- Built and shipped a RAG pipeline over 2M docs using Python and FastAPI\n"
            "- Fine-tuned a 7B model with LoRA and served it via vLLM on Kubernetes\n"
            "Skills: Python\n")
    facts = cv_parse.heuristic_facts(text)
    assert len(facts) >= 2
    assert all("@" not in f["text"] for f in facts)  # contact line excluded


# ---------- tracker conversion boost ----------
def test_conversion_boost_bounds(tmp_path):
    db = str(tmp_path / "t.db")
    # distinct titles => distinct job_keys (key is company|title)
    for i in range(4):
        tracker.record(J(f"role {i}", source="greenhouse:good", url=f"https://x/{i}"), "applied", db)
    tracker.record(J("role 0", source="greenhouse:good", url="https://x/0"), "replied", db)
    tracker.record(J("role 1", source="greenhouse:good", url="https://x/1"), "interview", db)
    priors = tracker.conversion_priors(db)
    assert priors.get("greenhouse", 0) > 0
    good = J("role 9", source="greenhouse:good")
    assert tracker.conversion_boost(good, priors) > 0
