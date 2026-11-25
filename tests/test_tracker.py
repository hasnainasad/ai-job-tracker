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
