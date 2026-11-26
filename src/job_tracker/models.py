"""The data shapes. Pydantic models validate data, convert to/from JSON, and are
exactly what FastAPI uses for request/response bodies in phase 7."""
from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError


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


class FitAssessment(BaseModel):
    """Phase 5: how well a job fits your resume, as judged by the LLM."""
    skills_score: int = Field(description="0-100: how well your skills cover the job's must-haves.")
    seniority_score: int = Field(description="0-100: how well your level and experience match the role's level.")
    domain_score: int = Field(description="0-100: how relevant your industry/domain background is.")
    fit_score: int = Field(description="0-100 overall, using the rubric. Not a plain average.")
    key_strengths: list[str] = Field(description="Top 3 reasons you're a strong match, each citing resume evidence.")
    key_gaps: list[str] = Field(description="Top 3 things the job asks for that the resume doesn't show. Empty if none.")
    best_template: str = Field(description='Letter of the resume template that fits best: "A", "B", "C", "D" or "E".')
    template_reason: str = Field(description="One sentence: why that template.")
    recommendation: str = Field(description='"Apply", "Maybe" or "Skip".')
    reasoning: str = Field(description="One or two sentences explaining the overall score.")
    tailoring_benefit: str = Field(
        description='"High", "Medium" or "Low": how much a resume tailored to this job would help '
        "compared with sending the best template as it is."
    )
    tailoring_tips: list[str] = Field(
        description="2-3 specific changes to make when tailoring, using only facts already in the resume. "
        "Empty if tailoring_benefit is Low."
    )


class SourcedBullet(BaseModel):
    text: str = Field(description="The bullet as it should appear on the tailored resume.")
    source: str = Field(
        description="An exact, word-for-word quote (at least 6 words) from the MASTER resume that "
        "supports this bullet."
    )


class RoleEntry(BaseModel):
    title: str
    company: str
    dates: str = Field(description="Exactly as in the master resume.")
    location: str
    bullets: list[SourcedBullet]


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class TailoredResume(BaseModel):
    """Phase 6: a resume tailored to one job. Name and contact details are NOT here:
    they're copied from your master resume by code, so the LLM can't get them wrong."""
    headline: str = Field(description='Role titles separated by " | ", like the templates.')
    summary: str
    core_competencies: list[str]
    career_highlights: list[SourcedBullet]
    experience: list[RoleEntry]
    education: list[str]
    technical_skills: list[SkillGroup]
    change_log: list[str] = Field(description="What you changed from the starting template, and why, one line each.")


class UnsupportedClaim(BaseModel):
    claim: str = Field(description="The words on the tailored resume.")
    reason: str = Field(description="Why the master resume doesn't support it.")


class Verification(BaseModel):
    unsupported_claims: list[UnsupportedClaim] = Field(
        description="Every claim not supported by the master resume. Empty list if all are supported."
    )


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
    fit: FitAssessment | None = None      # filled by phase 5 (--score)
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
    resume_file: str = ""  # phase 6: path of the last tailored resume PDF


class FailedJob(BaseModel):
    url: str
    error: str


class RunResult(BaseModel):
    jobs: list[Job] = []
    failed: list[FailedJob] = []
    known: list[str] = []  # job IDs skipped because they were already saved


class JobSummary(BaseModel):
    """The short form used in lists (no description, no full AI output)."""
    job_id: str
    title: str
    company: str
    location: str
    salary: str
    status: str
    fit_score: str
    recommendation: str = ""
    best_template: str = ""
    resume_status: str
    date_applied: str
    url: str

    @classmethod
    def of(cls, job: Job) -> "JobSummary":
        fit = job.fit
        return cls(**job.model_dump(include=set(cls.model_fields)),
                   recommendation=fit.recommendation if fit else "", best_template=fit.best_template if fit else "")


def load_job(data: dict) -> Job:
    """Load one saved job, coping with data written by an older version of the tool.

    When we add fields to the AI outputs (e.g. tailoring_benefit), older saved results
    no longer match the model. They're only cached AI output, so we drop the stale
    part and the next --ai / --score run regenerates it. (This is "schema migration".)
    """
    try:
        return Job.model_validate(data)
    except ValidationError:
        stale = [k for k in ("fit", "insights") if data.get(k) is not None]
        job = Job.model_validate({**data, **dict.fromkeys(stale)})
        print(f"  note: job {job.job_id} has old-format {' and '.join(stale)}; run --score/--ai to refresh")
        return job
