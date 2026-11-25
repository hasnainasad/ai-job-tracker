import csv
from pathlib import Path

from job_tracker.extract import parse_job
from job_tracker.models import Job
from job_tracker.storage import save_to_csv
from job_tracker.urls import extract_job_id, parse_urls

SAMPLE = Path(__file__).parent / "sample_job.html"


def test_extract_job_id_shapes():
    assert extract_job_id("https://www.linkedin.com/jobs/view/4012345678/") == "4012345678"
    assert extract_job_id("https://uk.linkedin.com/jobs/view/ml-engineer-at-acme-4012345678?trk=x") == "4012345678"
    assert extract_job_id("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&k=ml") == "4012345678"
    assert extract_job_id("https://example.com/jobs/view/4012345678") is None


def test_parse_urls_dedupes_and_skips():
    targets, skipped = parse_urls([
        "https://www.linkedin.com/jobs/view/4012345678/",
        "https://www.linkedin.com/jobs/search/?currentJobId=4012345678",
        "https://example.com/nope",
    ])
    assert targets == [("https://www.linkedin.com/jobs/view/4012345678/", "4012345678")]
    assert skipped == ["https://example.com/nope"]


def test_parse_job():
    job = parse_job(SAMPLE.read_text(encoding="utf-8"), "1", "u")
    assert job.title == "Machine Learning Engineer"
    assert job.company == "Acme AI"
    assert job.location.startswith("London")
    assert job.seniority == "Mid-Senior level"
    assert job.employment_type == "Full-time"
    assert job.applicants == "Over 200 applicants"
    assert "RAG and fine-tuning" in job.description


def test_csv_keeps_your_notes(tmp_path):
    path = tmp_path / "jobs.csv"
    save_to_csv([Job(job_id="1", url="u", title="Old")], str(path))
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0]["notes"] = "applied!"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    save_to_csv([Job(job_id="1", url="u", title="New")], str(path))
    with open(path, newline="") as f:
        row = next(csv.DictReader(f))
    assert row["title"] == "New" and row["notes"] == "applied!"


def test_pipeline_end_to_end_with_fake_fetcher():
    from job_tracker import run

    class FakeFetcher:
        def fetch(self, job_id):
            if job_id == "999999999":
                return "<html>login wall</html>"
            return SAMPLE.read_text(encoding="utf-8")

    result = run(
        ["https://www.linkedin.com/jobs/view/4012345678/",
         "https://www.linkedin.com/jobs/view/999999999/",
         "not a url"],
        fetcher=FakeFetcher(),
        on_progress=lambda _: None,
    )
    assert [j.job_id for j in result.jobs] == ["4012345678"]
    assert len(result.failed) == 2


def test_old_csv_gains_new_columns_and_keeps_everything(tmp_path):
    """A CSV from before the new columns existed, with a user-added column, upgrades cleanly."""
    path = tmp_path / "jobs.csv"
    path.write_text(
        "job_id,title,status,notes,my_column,description\n"
        "1,Old title,Applied,great team,keep me,old desc\n",
        encoding="utf-8",
    )
    save_to_csv([Job(job_id="1", url="u", title="New title", description="new desc")], str(path))
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        row = next(reader)
    for col in ["date_applied", "status_note", "key_strengths", "key_gaps", "resume_status"]:
        assert col in header
    assert header[-1] == "my_column"
    assert row["title"] == "New title" and row["description"] == "new desc"
    assert row["status"] == "Applied" and row["notes"] == "great team"
    assert row["my_column"] == "keep me"


def test_merge_user_columns_win_scraped_columns_refresh():
    from job_tracker.storage import COLUMNS, merge

    existing = {"1": {"job_id": "1", "title": "Old", "key_gaps": "Kubernetes", "date_applied": "2026-11-20"}}
    rows = merge(existing, [Job(job_id="1", url="u", title="New"), Job(job_id="2", url="u2")], COLUMNS)
    by_id = {r["job_id"]: r for r in rows}
    assert by_id["1"]["title"] == "New"
    assert by_id["1"]["key_gaps"] == "Kubernetes" and by_id["1"]["date_applied"] == "2026-11-20"
    assert by_id["1"]["status"] == "To review"   # empty before, so the default fills it
    assert by_id["2"]["status"] == "To review"


def _insights(**kw):
    from job_tracker.models import JobInsights

    base = {
        "salary_text": "", "salary_min": None, "salary_max": None, "salary_currency": "",
        "remote_policy": "Hybrid", "office_days_per_week": 3, "years_experience_min": 5,
        "must_have_skills": ["React", "TypeScript"], "nice_to_have_skills": ["AG Grid"],
        "visa_sponsorship": "Not stated", "summary": "Front-end role on a trading team.",
    }
    base.update(kw)
    return JobInsights(**base)


def test_ai_extraction_fills_columns_and_salary_from_title(tmp_path):
    from job_tracker import run

    class FakeFetcher:
        def fetch(self, job_id):
            return SAMPLE.read_text(encoding="utf-8")

    class FakeLLM:
        def extract(self, job):
            return _insights(salary_text="£150k-£200k + bonus", salary_min=150000)

    result = run(["https://www.linkedin.com/jobs/view/4012345678/"],
                 fetcher=FakeFetcher(), extractor=FakeLLM(), on_progress=lambda _: None)
    job = result.jobs[0]
    assert job.salary == "£150k-£200k + bonus"          # filled from the AI because the page had none
    assert job.insights.must_have_skills == ["React", "TypeScript"]

    path = tmp_path / "jobs.csv"
    save_to_csv(result.jobs, str(path))
    with open(path, newline="") as f:
        row = next(csv.DictReader(f))
    assert row["must_have_skills"] == "React, TypeScript"
    assert row["remote_policy"] == "Hybrid" and row["office_days_per_week"] == "3"
    assert row["ai_summary"].startswith("Front-end")

    # A later run WITHOUT --ai must not wipe the AI columns.
    save_to_csv([Job(job_id="4012345678", url="u", title="Machine Learning Engineer")], str(path))
    with open(path, newline="") as f:
        row = next(csv.DictReader(f))
    assert row["must_have_skills"] == "React, TypeScript"


def test_ai_failure_keeps_the_job():
    from job_tracker import run

    class FakeFetcher:
        def fetch(self, job_id):
            return SAMPLE.read_text(encoding="utf-8")

    class BrokenLLM:
        def extract(self, job):
            raise RuntimeError("no credit")

    result = run(["https://www.linkedin.com/jobs/view/4012345678/"],
                 fetcher=FakeFetcher(), extractor=BrokenLLM(), on_progress=lambda _: None)
    assert len(result.jobs) == 1 and result.jobs[0].insights is None


def _fit(**kw):
    from job_tracker.models import FitAssessment

    base = {
        "skills_score": 85, "seniority_score": 70, "domain_score": 80, "fit_score": 78,
        "key_strengths": ["React/TypeScript at HSBC", "Micro frontends"], "key_gaps": ["AG Grid"],
        "best_template": "B", "template_reason": "Fintech product role.",
        "recommendation": "Apply", "reasoning": "Strong frontend match.",
        "tailoring_benefit": "Medium", "tailoring_tips": ["Lead with data-grid work", "Name WebSockets"],
    }
    base.update(kw)
    return FitAssessment(**base)


def test_scoring_fills_empty_user_columns_only(tmp_path):
    from job_tracker.pipeline import enrich

    class FakeScorer:
        def score(self, job):
            return _fit()

    jobs = enrich([Job(job_id="1", url="u", title="FE"), Job(job_id="2", url="u2", title="FE2")],
                  scorer=FakeScorer(), on_progress=lambda _: None)
    assert jobs[0].fit_score == "78" and jobs[0].key_gaps == "AG Grid"

    path = tmp_path / "jobs.csv"
    path.write_text("job_id,title,fit_score,key_gaps\n1,FE,55,my own gap note\n", encoding="utf-8")
    save_to_csv(jobs, str(path))
    with open(path, newline="") as f:
        rows = {r["job_id"]: r for r in csv.DictReader(f)}
    assert rows["1"]["fit_score"] == "55" and rows["1"]["key_gaps"] == "my own gap note"  # yours win
    assert rows["2"]["fit_score"] == "78" and rows["2"]["recommendation"] == "Apply"
    assert rows["2"]["best_template"] == "B"
    assert rows["2"]["tailoring_benefit"] == "Medium"
    assert rows["2"]["tailoring_tips"] == "Lead with data-grid work; Name WebSockets"


def test_load_profile_ignores_comment_only_preferences(tmp_path):
    from job_tracker.scoring import load_profile

    (tmp_path / "master_resume.md").write_text("Asad, Tech Lead", encoding="utf-8")
    (tmp_path / "preferences.md").write_text("# Examples:\n#   Minimum salary: £110k\n", encoding="utf-8")
    p = load_profile(str(tmp_path))
    assert p.preferences == "" and "(none given)" in p.as_prompt()
    (tmp_path / "preferences.md").write_text("# header\nHybrid only\n", encoding="utf-8")
    assert load_profile(str(tmp_path)).preferences == "Hybrid only"


def test_json_merge_keeps_old_jobs(tmp_path):
    from job_tracker.cli import load_json, save_json

    path = str(tmp_path / "jobs.json")
    save_json([Job(job_id="1", url="u", title="A")], path)
    total = save_json([Job(job_id="2", url="u", title="B"), Job(job_id="1", url="u", title="A2")], path)
    jobs = {j.job_id: j for j in load_json(path)}
    assert total == 2 and jobs["1"].title == "A2"


def test_load_json_drops_old_format_fit(tmp_path):
    import json

    from job_tracker.cli import load_json

    old_fit = {k: v for k, v in _fit().model_dump().items() if not k.startswith("tailoring")}
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps([{"job_id": "1", "url": "u", "title": "FE", "fit_score": "78", "fit": old_fit}]))
    job = load_json(str(path))[0]
    assert job.fit is None and job.fit_score == "78" and job.title == "FE"
