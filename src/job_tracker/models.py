"""The data shapes. Pydantic models validate data, convert to/from JSON, and are
exactly what FastAPI uses for request/response bodies in phase 7."""
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Job(BaseModel):
    job_id: str
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    posted: str = ""
    applicants: str = ""
    seniority: str = ""
    employment_type: str = ""
    job_function: str = ""
    industries: str = ""
    salary: str = ""
    description: str = ""
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Tracker columns YOU own. The tool never overwrites them once they have a value.
    # Fill them in the CSV / Google Sheet now, and from the React UI later.
    # (Phase 5 will pre-fill fit_score, key_strengths and key_gaps for you to edit.)
    status: str = "To review"     # e.g. To review, Applying, Applied, Interview, Offer, Rejected
    status_note: str = ""         # e.g. "Recruiter call booked for Fri"
    date_applied: str = ""        # YYYY-MM-DD, e.g. 2026-11-25
    resume_status: str = ""       # e.g. Not started, Tailored, Sent
    fit_score: str = ""           # 0-100
    key_strengths: str = ""       # where you match the job well
    key_gaps: str = ""            # what the job wants that you lack
    notes: str = ""


class FailedJob(BaseModel):
    url: str
    error: str


class RunResult(BaseModel):
    jobs: list[Job] = []
    failed: list[FailedJob] = []
