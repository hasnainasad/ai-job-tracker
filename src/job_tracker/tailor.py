"""Phase 6: tailor your resume to one job, then check it for invented facts.

Two LLM calls per job (the "generator + verifier" pattern):
  1. Generator (LLM_MODEL, default Claude Opus 5.5): rewrites the best template for this
     job, using only facts from the master resume. Every bullet must quote the master
     resume line it's based on.
  2. Verifier (VERIFIER_MODEL, default Claude Haiku 5.5, much cheaper): reads the result
     against the master resume and lists anything it can't support.
Plus two checks in plain Python, which cost nothing and never hallucinate:
  - each bullet's quoted source really appears in the master resume;
  - every number on the resume (20+, ~20%, 13 years...) appears in the master resume.

Nothing is ever sent anywhere: you get a PDF and a report to review.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .llm import LLMError, build_prompt
from .models import Job, TailoredResume, Verification
from .render import contact_from_master, render_pdf
from .scoring import Profile

TAILOR_RULES = """You tailor ONE candidate's resume to ONE job.

Hard rules (breaking any of these makes the resume unusable):
- Use ONLY facts in the master resume. Never invent or inflate skills, employers,
  titles, dates, numbers, team sizes or results. Rewording and reordering are fine;
  new facts are not.
- Every bullet's `source` must be an exact, word-for-word quote from the master resume
  (at least 6 words) that supports it.
- Keep every role from the master resume, with titles, companies, dates and locations
  exactly as written there.
- Only use the job's keywords for things the candidate has really done.
- British English, matching the master resume's tone.

Shape (it must fit on 2 A4 pages):
- headline: 4 role labels separated by " | ", aimed at this job.
- summary: at most 85 words.
- core_competencies: 14-18 items, most relevant to the job first.
- career_highlights: 4-5 bullets.
- experience: most recent role 5-6 bullets; the next 3-4; older roles 2-3 each.
- every bullet at most 32 words.
- technical_skills: at most 6 groups.
- education: as in the master resume.

Start from the given template (its angle and ordering), apply the tailoring tips,
then push the evidence that matters most for this job to the top."""

VERIFY_RULES = """You are a strict fact-checker for resumes.
Compare the TAILORED resume against the MASTER resume. List every claim in the
tailored resume (skills, employers, titles, dates, numbers, scope, results) that the
master resume does not support. Rewording is fine; new or inflated facts are not.
Return an empty list if everything is supported."""


def _norm(text: str) -> str:
    """Make text comparable: lower case, one space between words, no hyphen line-breaks."""
    text = text.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"-\s+", "-", text).strip()


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text))


def check_against_master(resume: TailoredResume, master: str) -> list[str]:
    """Plain-Python guardrails. Returns a list of warnings (empty = all good)."""
    warnings = []
    master_n = _norm(master)
    master_numbers = _numbers(master)
    bullets = list(resume.career_highlights) + [b for r in resume.experience for b in r.bullets]
    for b in bullets:
        if _norm(b.source) not in master_n:
            warnings.append(f'Source quote not found in master resume for: "{b.text}"')
    all_text = " ".join(
        [resume.headline, resume.summary, *resume.core_competencies, *resume.education]
        + [b.text for b in bullets]
        + [f"{r.title} {r.company} {r.dates} {r.location}" for r in resume.experience]
        + [", ".join(g.items) for g in resume.technical_skills]
    )
    for num in sorted(_numbers(all_text) - master_numbers):
        warnings.append(f'Number "{num}" does not appear in the master resume')
    return warnings


def resume_as_text(resume: TailoredResume) -> str:
    parts = [resume.headline, "", resume.summary, "", "Competencies: " + ", ".join(resume.core_competencies), ""]
    parts += ["- " + b.text for b in resume.career_highlights]
    for r in resume.experience:
        parts += ["", f"{r.title} | {r.company} | {r.dates} | {r.location}"] + ["- " + b.text for b in r.bullets]
    parts += ["", *resume.education, ""] + [f"{g.category}: {', '.join(g.items)}" for g in resume.technical_skills]
    return "\n".join(parts)


class AnswerCutOff(LLMError):
    pass


@dataclass
class TailorResult:
    pdf_path: str
    report_path: str
    pages: int
    warnings: list[str] = field(default_factory=list)
    template: str = ""


class AnthropicTailor:
    def __init__(self, profile: Profile, profile_dir: str = "profile",
                 model: str | None = None, verifier_model: str | None = None):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.profile = profile
        self.profile_dir = Path(profile_dir)
        self.model = model or os.getenv("LLM_MODEL", "claude-opus-5-5")
        self.verifier_model = verifier_model or os.getenv("VERIFIER_MODEL", "claude-haiku-5-5")

    def _parse(self, **kwargs):
        a = self.anthropic
        try:
            response = self.client.messages.parse(max_tokens=16000, **kwargs)
        except ValidationError as e:
            # The answer stopped mid-JSON. With thinking on, the model's thinking and its
            # answer share the same max_tokens budget, so long thinking leaves too little room.
            raise AnswerCutOff("the answer was cut off before it finished") from e
        except a.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY is missing or wrong (check .env)") from e
        except a.RateLimitError as e:
            raise LLMError("rate limited by the API; try again in a minute") from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError("could not reach the Anthropic API (network?)") from e
        if response.stop_reason == "refusal":
            raise LLMError("the model declined this request")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise AnswerCutOff("the answer was cut off before it finished")
        return response.parsed_output

    def template_text(self, letter: str) -> str:
        path = self.profile_dir / "templates" / f"{letter}.md"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found (full text of template {letter})")
        return path.read_text(encoding="utf-8")

    def generate(self, job: Job, template: str, feedback: str = "") -> TailoredResume:
        tips = ""
        if job.fit:
            tips = (
                f"Fit score: {job.fit.fit_score}. Strengths: {'; '.join(job.fit.key_strengths)}. "
                f"Gaps: {'; '.join(job.fit.key_gaps)}. Tailoring tips: {'; '.join(job.fit.tailoring_tips)}"
            )
        user = (
            f"<starting_template letter='{template}'>\n{self.template_text(template)}\n</starting_template>\n\n"
            f"<job>\n{build_prompt(job)}\n</job>\n\n"
            f"<fit_assessment>\n{tips or '(not scored)'}\n</fit_assessment>\n\n"
        )
        if feedback:
            # A revision (phase 7d): a reviewer's notes. Still bound by the hard rules above.
            user += (f"<reviewer_feedback>\n{feedback}\n</reviewer_feedback>\n\n"
                     "Apply this feedback where the master resume supports it. ")
        user += "Write the tailored resume."
        system = [
            {"type": "text", "text": TAILOR_RULES},
            {"type": "text", "text": f"<master_resume>\n{self.profile.resume}\n</master_resume>",
             "cache_control": {"type": "ephemeral"}},
        ]
        # Thinking and the (long) resume share one token budget. Medium effort leaves
        # room for the answer; if it still runs out, retry once with less thinking.
        for effort in ("medium", "low"):
            try:
                return self._parse(
                    model=self.model,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_config={"effort": effort},
                    output_format=TailoredResume,
                )
            except AnswerCutOff:
                if effort == "low":
                    raise
                print("  answer was cut off; retrying with less thinking...")

    def verify(self, resume: TailoredResume) -> Verification:
        return self._parse(
            model=self.verifier_model,
            system=VERIFY_RULES,
            messages=[{"role": "user", "content": (
                f"<master_resume>\n{self.profile.resume}\n</master_resume>\n\n"
                f"<tailored_resume>\n{resume_as_text(resume)}\n</tailored_resume>"
            )}],
            output_config={"effort": "low"},
            output_format=Verification,
        )


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40] or "job"


def tailor_job(job: Job, tailor, out_dir: str = "output/resumes", template: str | None = None,
               on_progress=print, feedback: str | None = None) -> TailorResult:
    """feedback: a reviewer's notes for a revision (phase 7d); saved as a separate _rev file."""
    letter = (template or (job.fit.best_template if job.fit else "") or "A").strip().upper()[:1]
    on_progress(f"  {'revising' if feedback else 'writing'} resume from template {letter}...")
    resume = tailor.generate(job, letter, feedback=feedback) if feedback else tailor.generate(job, letter)

    warnings = check_against_master(resume, tailor.profile.resume)
    on_progress("  fact-checking...")
    try:
        for c in tailor.verify(resume).unsupported_claims:
            warnings.append(f'Checker: "{c.claim}" ({c.reason})')
    except LLMError as e:
        warnings.append(f"Checker call failed ({e}); review carefully by hand")

    base = Path(out_dir) / f"{safe_name(job.company)}_{job.job_id}_{letter}{'_rev' if feedback else ''}"
    pdf_path, report_path = f"{base}.pdf", f"{base}.md"
    pages = render_pdf(resume, contact_from_master(tailor.profile.resume), pdf_path)
    if pages > 2:
        warnings.append(f"PDF is {pages} pages even at the smallest text size; trim some bullets")

    lines = [
        f"# Tailored resume: {job.title} @ {job.company}", "",
        f"- Job: {job.url}", f"- Starting template: {letter}", f"- PDF: {pdf_path} ({pages} pages)",
        *([f"- Revised with reviewer feedback: {feedback}"] if feedback else []), "",
        "## Check before sending", "",
        *([f"- [ ] {w}" for w in warnings] or ["- No problems found by the automatic checks. Still read it once."]),
        "", "## What changed from the template", "", *[f"- {c}" for c in resume.change_log],
        "", "## Full text", "", resume_as_text(resume),
    ]
    Path(report_path).write_text("\n".join(lines), encoding="utf-8")

    job.resume_file = pdf_path
    job.resume_status = "Tailored"  # only lands in your sheet if that cell is empty
    on_progress(f"  saved {pdf_path} ({pages} pages, {len(warnings)} thing(s) to check)")
    return TailorResult(pdf_path, report_path, pages, warnings, letter)


def make_tailor(profile_dir: str = "profile", provider: str | None = None):
    from .scoring import load_profile

    provider = (provider or os.getenv("LLM_PROVIDER", "anthropic")).lower()
    if provider == "anthropic":
        return AnthropicTailor(load_profile(profile_dir), profile_dir)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Supported now: 'anthropic'.")
