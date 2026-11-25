"""Phase 5: score how well each job fits your resume.

Your profile lives in the profile/ folder (kept out of git):
  profile/master_resume.md   the full resume, as text
  profile/templates.md       the 5 resume angles (A-E) to choose between
  profile/preferences.md     optional; plain-English preferences

The profile is the same for every job, so it goes in the SYSTEM prompt with a cache
marker: the API stores it after the first call, and later calls read it from cache
(cheaper and faster). Only the job itself changes per call.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .llm import LLMError, build_prompt
from .models import FitAssessment, Job

RUBRIC = """You are an experienced tech recruiter scoring how well a job fits ONE candidate.

Score with this rubric:
- skills_score: share of the job's must-have skills the resume clearly shows.
  Nice-to-haves count a little; skills not on the resume count as missing.
- seniority_score: does the candidate's level match? Over-qualification lowers the
  score less than under-qualification, but a big mismatch (e.g. a mid-level role for
  a 20-year architect) should score below 60.
- domain_score: relevance of the candidate's industries and products to this job.
- fit_score: overall 0-100. Weigh skills highest, then seniority, then domain.
  If preferences are given and the job breaks one, cap fit_score at 40.
  90+ exceptional, 75-89 strong, 60-74 possible, below 60 weak.
- recommendation: "Apply" if fit_score >= 75, "Maybe" if 60-74, else "Skip".

Rules:
- Use only evidence in the resume and the job posting. Never invent experience.
- key_strengths and key_gaps: short, specific, at most 3 each; cite the evidence
  (e.g. "Led MUI adoption across 6 micro-frontends at HSBC").
- best_template: choose the template (A-E) whose angle best matches what this job
  emphasises.
- tailoring_benefit: "High" if the resume has strong evidence for this job that the
  best template doesn't put up front (so reordering/rewording would clearly help),
  "Medium" if a few tweaks would help, "Low" if the template already fits well.
- tailoring_tips: concrete edits to that template, e.g. which experience to move up,
  which of the job's keywords to use for things the candidate has really done.
  Never suggest claiming skills or experience the resume doesn't show."""


@dataclass
class Profile:
    resume: str
    templates: str
    preferences: str

    def as_prompt(self) -> str:
        prefs = self.preferences.strip() or "(none given)"
        return (
            f"<resume>\n{self.resume}\n</resume>\n\n"
            f"<resume_templates>\n{self.templates}\n</resume_templates>\n\n"
            f"<preferences>\n{prefs}\n</preferences>"
        )


def load_profile(folder: str = "profile") -> Profile:
    base = Path(folder)
    resume = base / "master_resume.md"
    if not resume.exists():
        raise FileNotFoundError(f"{resume} not found. Put your resume text there (see README).")

    def read(name: str) -> str:
        path = base / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    # Lines starting with # are comments/instructions, not preferences.
    prefs = "\n".join(
        line for line in read("preferences.md").splitlines() if not line.lstrip().startswith("#")
    ).strip()
    return Profile(resume=read("master_resume.md"), templates=read("templates.md"), preferences=prefs)


class AnthropicScorer:
    def __init__(self, profile: Profile, model: str | None = None, effort: str = "medium"):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("LLM_MODEL", "claude-opus-5-5")
        self.effort = effort  # judging fit takes more thought than extraction
        self.system = [
            {"type": "text", "text": RUBRIC},
            # The cache marker goes on the LAST stable block: everything up to here is cached.
            {"type": "text", "text": profile.as_prompt(), "cache_control": {"type": "ephemeral"}},
        ]
        self.cache_reads = 0  # tokens served from cache, for the end-of-run report

    def score(self, job: Job) -> FitAssessment:
        a = self.anthropic
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                messages=[{"role": "user", "content": "Score this job:\n\n" + build_prompt(job)}],
                output_config={"effort": self.effort},
                output_format=FitAssessment,
            )
        except ValidationError as e:  # answer stopped mid-JSON (ran out of tokens)
            raise LLMError("the answer was cut off before it finished") from e
        except a.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY is missing or wrong (check .env)") from e
        except a.RateLimitError as e:
            raise LLMError("rate limited by the API; try again in a minute") from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError("could not reach the Anthropic API (network?)") from e

        if response.stop_reason == "refusal":
            raise LLMError("the model declined this job")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMError("the model's answer was cut off or didn't match the schema")
        self.cache_reads += response.usage.cache_read_input_tokens or 0
        return response.parsed_output


def make_scorer(profile_dir: str = "profile", provider: str | None = None):
    provider = (provider or os.getenv("LLM_PROVIDER", "anthropic")).lower()
    if provider == "anthropic":
        return AnthropicScorer(load_profile(profile_dir))
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Supported now: 'anthropic'.")
