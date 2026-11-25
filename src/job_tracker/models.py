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
    # Tracker columns you own (step 3 will fill fit_score later):
    status: str = "To review"
    fit_score: str = ""
    notes: str = ""


class FailedJob(BaseModel):
    url: str
    error: str


class RunResult(BaseModel):
    jobs: list[Job] = []
    failed: list[FailedJob] = []
