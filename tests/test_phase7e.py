"""Phase 7e: chunking, the index, search by meaning and its eval. No network: a fake
embedder turns words into vectors (bag of words), so "react" matches "react", which is
enough to test our code. Real meaning-matching comes from the real model."""
import hashlib
import math
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from test_phase7a import FakeFetcher, FakeScorer, FakeTailor, _insights
from test_phase7c import call
from test_tracker import _fit

from job_tracker import db
from job_tracker.agent_team import resume_context
from job_tracker.api import create_app
from job_tracker.mcp_server import create_server
from job_tracker.models import Job
from job_tracker.rag import Index, requirement_evidence, resume_chunks, split_text
from job_tracker.runs import Services
from job_tracker.search_eval import evaluate

MASTER = """Jane Doe
Lead Engineer | Platform & Design Systems

PROFESSIONAL SUMMARY
Engineering lead with 15 years building fintech and design system platforms.

PROFESSIONAL EXPERIENCE

Technical Lead | Bank Co | 2023 – 2026 | London
 • Led adoption of a shared component library across six micro frontends,
   cutting UI effort by ~20%.
 • Hired and mentored four React developers on component standards.

Senior Developer | Shop Ltd | 2018 – 2023 | Leeds
 • Built payment reconciliation services in Node.js for card transactions.

TECHNICAL SKILLS
Frontend & Architecture: React.js, TypeScript, Micro Frontends, Module Federation
Testing & Security: Jest, Playwright, Cypress, OWASP secure coding
"""


class FakeEmbedder:
    name = "fake-bow"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * 128
            for w in re.findall(r"[a-z]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 128] += 1
            norm = math.sqrt(sum(x * x for x in v)) or 1
            out.append([x / norm for x in v])
        return out


def _ins(skills, summary):
    return _insights(must_have_skills=skills, summary=summary)


JOBS = [
    Job(job_id="1", url="u", title="Design Systems Lead", company="Fin", location="London",
        description="Own our component library and design system across micro frontends. " * 30,
        insights=_ins(["component library", "React"], "Lead the design system team."), fit=_fit()),
    Job(job_id="2", url="u", title="Payments Engineer", company="Pay", location="Leeds",
        description="Build payment reconciliation services for card transactions in Node.js."),
    Job(job_id="3", url="u", title="Data Scientist", company="Stats", location="Remote",
        description="Train forecasting models in Python and SQL."),
]


@pytest.fixture
def engine(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with Session(engine) as s:
        db.upsert_jobs(s, JOBS)
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "master_resume.md").write_text(MASTER, encoding="utf-8")
    return engine


@pytest.fixture
def index(tmp_path):
    return Index(str(tmp_path / "chroma"), FakeEmbedder())


def test_split_text_overlaps_and_covers_everything():
    text = "".join(f"Sentence number {i} is here. " for i in range(200))
    parts = split_text(text, size=500, overlap=100)
    assert len(parts) > 5 and all(len(p) <= 500 for p in parts)
    assert parts[0][-50:] in text and "Sentence number 199" in parts[-1]
    assert split_text("") == [] and split_text("short") == ["short"]


def test_resume_chunks_one_per_bullet_with_its_role():
    chunks = resume_chunks(MASTER)
    texts = [c.text for c in chunks]
    assert "Led adoption of a shared component library across six micro frontends, cutting UI effort by ~20%." in texts
    roles = {c.text[:20]: c.meta["role"] for c in chunks}
    assert roles["Built payment reconc"] == "Senior Developer @ Shop Ltd"
    assert roles["Hired and mentored f"] == "Technical Lead @ Bank Co"
    assert any(c.meta["role"] == "Professional Summary" for c in chunks)
    skills = [c.text for c in chunks if c.meta["role"] == "Technical Skills"]
    assert len(skills) == 2 and skills[1].startswith("Testing & Security:")  # one chunk per line


def test_index_skips_unchanged_jobs_and_finds_by_content(engine, index, tmp_path):
    with Session(engine) as s:
        assert index.refresh(s, str(tmp_path / "profile")) == 3
        calls = index.embedder.calls
        assert index.refresh(s, str(tmp_path / "profile")) == 0 and index.embedder.calls == calls  # nothing re-embedded
        db.update_user_fields(s, "2", {"notes": "user columns aren't indexed"})
        assert index.refresh(s, str(tmp_path / "profile")) == 0
        db.upsert_jobs(s, [Job(job_id="2", url="u", title="Payments Lead", company="Pay",
                               description="Lead payment reconciliation for card transactions.")])
        assert index.refresh(s, str(tmp_path / "profile")) == 1

    hits = index.find_jobs("component library design system", limit=3)
    assert hits[0].job_id == "1" and hits[0].score > hits[-1].score
    assert len({h.job_id for h in hits}) == len(hits)  # one result per job, not per chunk
    assert index.find_jobs("card payment reconciliation")[0].job_id == "2"
    assert "1" not in [h.job_id for h in index.similar_jobs("1")]


def test_requirement_evidence_finds_matching_bullets(engine, index, tmp_path):
    with Session(engine) as s:
        index.refresh(s, str(tmp_path / "profile"))
        job = db.get_job(s, "1")
    pairs = requirement_evidence(index, job, per_item=2)
    assert [r for r, _ in pairs] == ["component library", "React"]
    assert "component library" in pairs[0][1][0].text and pairs[0][1][0].role == "Technical Lead @ Bank Co"


def test_eval_scores_hits_and_rank(engine, index, tmp_path):
    with Session(engine) as s:
        index.refresh(s, str(tmp_path / "profile"))
    report = evaluate(index, [{"query": "design system component library", "expect": ["1"]},
                              {"query": "forecasting models python", "expect": ["3"]},
                              {"query": "nothing matches zebra", "expect": ["999"]}], k=2)
    assert report["hit_rate"] == pytest.approx(2 / 3) and report["cases"][0]["rr"] == 1.0
    assert report["cases"][2]["hit"] == 0


def _services(tmp_path, index):
    return Services(fetcher=FakeFetcher, scorer=lambda _p: FakeScorer(), tailor=lambda _p: FakeTailor(),
                    extractor=lambda: None, resumes_dir=str(tmp_path / "resumes"),
                    profile_dir=str(tmp_path / "profile"), index=lambda: index)


def test_mcp_find_similar_and_evidence(engine, index, tmp_path):
    server = create_server(engine, _services(tmp_path, index), wait_seconds=10)
    err, hits = call(server, "find_jobs", {"query": "payment reconciliation", "limit": 2})
    assert not err and hits[0]["job_id"] == "2" and "match" in hits[0] and "fit_score" in hits[0]
    err, hits = call(server, "similar_jobs", {"job_id": "1"})
    assert not err and "1" not in [h["job_id"] for h in hits]
    err, ev = call(server, "resume_evidence", {"job_id": "1"})
    assert not err and ev[0]["requirement"] == "component library" and ev[0]["evidence"][0]["score"] > 0

    def broken():
        raise RuntimeError("search by meaning needs OPENAI_API_KEY in .env")
    server = create_server(engine, Services(index=broken), wait_seconds=10)
    err, text = call(server, "find_jobs", {"query": "x"})
    assert err and "OPENAI_API_KEY" in text


def test_api_find_and_similar(engine, index, tmp_path):
    with TestClient(create_app(engine, _services(tmp_path, index))) as c:
        r = c.get("/jobs/find", params={"q": "forecasting python", "limit": 2})
        assert r.status_code == 200 and r.json()[0]["job_id"] == "3"
        assert c.get("/jobs/1/similar").status_code == 200
        assert c.get("/jobs/nope/similar").status_code == 404
        assert c.get("/jobs/1").json()["title"] == "Design Systems Lead"  # /jobs/find didn't shadow it


def test_reviewer_context_includes_evidence(engine, index, tmp_path):
    from job_tracker.tailor import tailor_job

    with Session(engine) as s:
        job = db.get_job(s, "1")
        db.add_resume(s, "1", tailor_job(job, FakeTailor(), out_dir=str(tmp_path / "resumes"),
                                         on_progress=lambda _: None))
    text = resume_context("1", engine, str(tmp_path / "profile"), index)
    block = text.split("<requirement_evidence", 1)[1].split("</requirement_evidence>")[0]
    assert "- component library" in block and "(Technical Lead @ Bank Co)" in block
