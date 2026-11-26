"""Phase 7a: database, Google Sheet sync and the API. No network: everything outside
(LinkedIn, Claude, Google) is replaced by small fakes."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from test_tracker import MASTER, SAMPLE, _fit, _tailored

from job_tracker import db
from job_tracker.api import create_app
from job_tracker.models import Job, JobInsights
from job_tracker.runs import Services
from job_tracker.sheet_sync import plan_sheet_sync, sync_sheet
from job_tracker.storage import COLUMNS


@pytest.fixture
def engine(tmp_path):
    return db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")


def _insights(**kw):
    base = {"salary_text": "", "salary_min": None, "salary_max": None, "salary_currency": "",
            "remote_policy": "Hybrid", "office_days_per_week": 2, "years_experience_min": None,
            "must_have_skills": ["React"], "nice_to_have_skills": [], "visa_sponsorship": "Not stated",
            "summary": "Front-end role."}
    base.update(kw)
    return JobInsights(**base)


# ---------------------------------------------------------------- database

def test_job_table_mirrors_job_model():
    assert set(db.JOB_FIELDS) <= set(db.JobRow.model_fields)


def test_upsert_keeps_your_columns_and_old_ai_output(engine):
    with Session(engine) as s:
        db.upsert_jobs(s, [Job(job_id="1", url="u", title="A", insights=_insights(), fit=_fit())])
        db.update_user_fields(s, "1", {"status": "Applied", "title": "ignored: not your column"})
        # A re-fetch without --ai/--score: fresh title, empty AI output, default status.
        added, updated = db.upsert_jobs(s, [Job(job_id="1", url="u", title="A2")])
        job = db.get_job(s, "1")
    assert (added, updated) == (0, 1)
    assert job.title == "A2" and job.status == "Applied"
    assert job.insights.remote_policy == "Hybrid" and job.fit.fit_score == 78


def test_import_json_and_stale_fit(engine, tmp_path):
    path = tmp_path / "jobs.json"
    old = Job(job_id="7", url="u", title="Old", fit=_fit()).model_dump(mode="json")
    del old["fit"]["tailoring_benefit"]  # written by an older version of the tool
    path.write_text(json.dumps([old, Job(job_id="8", url="u").model_dump(mode="json")]))
    with Session(engine) as s:
        assert db.import_json(s, str(path)) == 2
        assert db.known_ids(s) == {"7", "8"}
        assert db.get_job(s, "7").fit is None


# ---------------------------------------------------------------- sheet sync

def _grid(rows, header=None):
    header = header or ["job_id", "title", "my_column", "status", "notes"]
    return [header] + [[r.get(c, "") for c in header] for r in rows]


def test_first_sync_pulls_sheet_edits_keeps_order_and_your_columns():
    job = Job(job_id="1", url="u", title="Lead", notes="")
    grid = _grid([{"job_id": "1", "title": "Lead", "my_column": "mine", "status": "Applied", "notes": "call Fri"},
                  {"job_id": "999", "title": "Only in the Sheet"}])
    plan = plan_sheet_sync(grid, [job, Job(job_id="2", url="u2", title="New")], {"1": None})

    assert plan.pulled == {"1": {"status": "Applied", "notes": "call Fri"}}
    assert plan.header[:5] == grid[0] and set(plan.header) == set(grid[0]) | set(COLUMNS)
    touched = {(r, c) for r, c, _ in plan.cell_updates}
    assert (2, 3) not in touched and not any(r == 3 for r, _ in touched)  # your column + unknown row untouched
    assert (2, 4) not in touched  # status already matches after the pull
    assert len(plan.new_rows) == 1 and plan.new_rows[0][0] == "2"
    assert plan.snapshots["1"]["notes"] == "call Fri"


def test_later_sync_sheet_edit_wins_else_database_wins():
    job = Job(job_id="1", url="u", title="Lead", status="Interview", notes="from API")
    snap = {"status": "Applied", "notes": "old note"}
    # status unchanged in the Sheet since last sync -> the database's newer value is pushed;
    # notes edited in the Sheet -> pulled.
    grid = _grid([{"job_id": "1", "title": "Lead", "status": "Applied", "notes": "typed in Sheet"}])
    plan = plan_sheet_sync(grid, [job], {"1": snap})
    assert plan.pulled == {"1": {"notes": "typed in Sheet"}}
    assert (2, 4, "Interview") in plan.cell_updates


class FakeWorksheet:
    def __init__(self, grid):
        self.grid = [list(r) for r in grid]
        self.col_count = max((len(r) for r in grid), default=0)
        self.frozen = False

    def get_all_values(self):
        return [list(r) for r in self.grid]

    def add_cols(self, n):
        self.col_count += n

    def batch_update(self, data, value_input_option=None):
        from gspread.utils import a1_to_rowcol

        for item in data:
            r, c = a1_to_rowcol(item["range"])
            while len(self.grid) < r:
                self.grid.append([])
            row = self.grid[r - 1]
            row.extend([""] * (c - len(row)))
            row[c - 1] = item["values"][0][0]

    def append_rows(self, rows, value_input_option=None, table_range=None):
        self.grid.extend(list(r) for r in rows)

    def freeze(self, rows=None):
        self.frozen = True


def test_sync_sheet_end_to_end(engine):
    ws = FakeWorksheet([])
    with Session(engine) as s:
        db.upsert_jobs(s, [Job(job_id="1", url="u", title="Lead")])
        sync_sheet(s, ws, on_progress=lambda _: None)
        header = ws.grid[0]
        assert header == COLUMNS and ws.frozen and ws.grid[1][0] == "1"

        ws.grid[1][header.index("status")] = "Applied"  # you edit the Sheet
        sync_sheet(s, ws, on_progress=lambda _: None)
        assert db.get_job(s, "1").status == "Applied"

        db.update_user_fields(s, "1", {"notes": "set via API"})
        sync_sheet(s, ws, on_progress=lambda _: None)
        assert ws.grid[1][header.index("notes")] == "set via API"
        assert len(ws.grid) == 2


# ---------------------------------------------------------------- API

class FakeFetcher:
    def fetch(self, job_id):
        return SAMPLE.read_text(encoding="utf-8")


class FakeScorer:
    cache_reads = 0

    def score(self, job):
        return _fit()


class FakeTailor:
    def __init__(self):
        from job_tracker.scoring import Profile

        self.profile = Profile(resume=MASTER, templates="", preferences="")

    feedback = None

    def generate(self, job, template, feedback=""):
        self.feedback = feedback
        return _tailored()

    def verify(self, resume):
        from job_tracker.models import Verification

        return Verification(unsupported_claims=[])


@pytest.fixture
def client(engine, tmp_path):
    services = Services(fetcher=FakeFetcher, scorer=lambda _p: FakeScorer(), tailor=lambda _p: FakeTailor(),
                        extractor=lambda: None, resumes_dir=str(tmp_path / "resumes"))
    with TestClient(create_app(engine, services)) as c:
        yield c


def test_api_add_list_edit_score_tailor(client):
    url = "https://www.linkedin.com/jobs/view/4012345678/"
    r = client.post("/jobs", json={"urls": [url, "not a url"], "score": True})
    assert r.status_code == 202
    run = client.get(f"/runs/{r.json()['id']}").json()  # TestClient runs background tasks before returning
    assert run["status"] == "done" and run["job_ids"] == ["4012345678"]
    assert any("failed: not a url" in line for line in run["log"])

    jobs = client.get("/jobs", params={"recommendation": "apply"}).json()
    assert [j["job_id"] for j in jobs] == ["4012345678"] and jobs[0]["fit_score"] == "78"

    r = client.patch("/jobs/4012345678", json={"status": "Applied", "date_applied": "2026-11-26"})
    assert r.json()["status"] == "Applied"
    assert client.get("/jobs", params={"status": "applied"}).json()[0]["date_applied"] == "2026-11-26"

    # Adding the same URL again is skipped (already saved).
    run = client.get(f"/runs/{client.post('/jobs', json={'urls': [url]}).json()['id']}").json()
    assert run["job_ids"] == [] and "already saved" in run["log"][0]

    run = client.get(f"/runs/{client.post('/jobs/4012345678/score').json()['id']}").json()
    assert run["status"] == "done"

    run = client.get(f"/runs/{client.post('/jobs/4012345678/tailor').json()['id']}").json()
    assert run["status"] == "done", run["error"]
    pdf = client.get("/jobs/4012345678/resume")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get("/jobs/4012345678").json()["resume_status"] == "Tailored"


def test_api_errors(client):
    assert client.get("/jobs/nope").status_code == 404
    assert client.patch("/jobs/nope", json={"status": "x"}).status_code == 404
    assert client.post("/jobs/nope/score").status_code == 404
    assert client.get("/jobs/nope/resume").status_code == 404
    assert client.post("/jobs", json={"urls": []}).status_code == 422  # validation from the Pydantic model


def test_failed_run_records_the_error(engine, tmp_path):
    def broken(_profile):
        raise RuntimeError("ANTHROPIC_API_KEY is missing")

    with Session(engine) as s:
        db.upsert_jobs(s, [Job(job_id="1", url="u", title="Lead")])
    services = Services(scorer=broken)
    with TestClient(create_app(engine, services)) as c:
        run = c.get(f"/runs/{c.post('/jobs/1/score').json()['id']}").json()
    assert run["status"] == "failed" and "ANTHROPIC_API_KEY" in run["error"]
