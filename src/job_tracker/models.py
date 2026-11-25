"""The data shapes. Pydantic models validate data, convert to/from JSON, and are
exactly what FastAPI uses for request/response bodies in phase 7."""
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class JobInsights(BaseModel):
    """Phase 4: facts an LLM reads out of the free-text job description.

    Every field has a "not stated" value, so the model never has to guess.
    The field descriptions are sent to the LLM as part of the JSON schema, so they
    double as instructions.
    """
    salary_text: str = Field(
        description='Salary exactly as advertised, e.g. "£150k-£200k + bonus". Empty if not stated.'
    )
    salary_min: int | None = Field(description="Lower bound as a whole number, e.g. 150000. null if not stated.")
    salary_max: int | None = Field(description="Upper bound as a whole number. null if not stated.")
    salary_currency: str = Field(description='ISO code like "GBP", "USD". Empty if not stated.')
    remote_policy: str = Field(description='One of "Remote", "Hybrid", "On-site", "Not stated".')
    office_days_per_week: int | None = Field(description="Days per week in the office if stated, else null.")
    years_experience_min: int | None = Field(description="Minimum years of experience asked for, else null.")
    must_have_skills: list[str] = Field(description="Skills/technologies the posting says are required.")
    nice_to_have_skills: list[str] = Field(description="Skills listed as a bonus, plus, or nice to have.")
    visa_sponsorship: str = Field(description='One of "Yes", "No", "Not stated".')
    summary: str = Field(description="One plain sentence: what the role is and what the team does.")


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
    insights: JobInsights | None = None  # filled by phase 4 (--ai)
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
