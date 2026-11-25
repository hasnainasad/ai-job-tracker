"""Phase 4: use an LLM to read the job description and fill in JobInsights.

Providers are interchangeable, exactly like the fetchers in fetchers.py:
anything with an .extract(job) -> JobInsights method works. Pick one in .env:

  LLM_PROVIDER=anthropic   (default; needs ANTHROPIC_API_KEY)
  LLM_PROVIDER=ollama      (coming next; free, runs on your Mac)
"""
import os

from .models import Job, JobInsights

SYSTEM_PROMPT = """You extract facts from job postings for a job seeker's tracker.

Rules:
- Use ONLY what the posting says (title, description, and the other fields given).
  Never infer or guess. If something isn't stated, use the field's "not stated"
  value (empty string, null, "Not stated", or an empty list).
- Salary often appears only in the job title, e.g. "Up to £185,000 P/A + Bonus":
  check the title as well as the description.
- Skills: short canonical names ("React", "TypeScript", "AWS"), no duplicates,
  most important first, at most 12 per list."""


def build_prompt(job: Job) -> str:
    return (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Seniority: {job.seniority}\n"
        f"Employment type: {job.employment_type}\n"
        f"Salary box on the page: {job.salary or '(none)'}\n\n"
        f"Description:\n{job.description}"
    )


class LLMError(Exception):
    pass


class AnthropicExtractor:
    """Claude via the official `anthropic` SDK, using structured outputs.

    `messages.parse(..., output_format=JobInsights)` makes Claude's answer match
    the Pydantic model's JSON schema, and returns it as a validated JobInsights.
    """

    def __init__(self, model: str | None = None, effort: str = "low"):
        import anthropic  # imported here so users who never use --ai don't need a key

        self.anthropic = anthropic
        # Reads ANTHROPIC_API_KEY from the environment (.env is loaded by the CLI).
        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("LLM_MODEL", "claude-opus-5-5")
        # Extraction is a simple task, so low effort keeps it fast and cheap.
        self.effort = effort

    def extract(self, job: Job) -> JobInsights:
        a = self.anthropic
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(job)}],
                output_config={"effort": self.effort},
                output_format=JobInsights,
            )
        except a.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY is missing or wrong (check .env)") from e
        except a.RateLimitError as e:
            raise LLMError("rate limited by the API; try again in a minute") from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError("could not reach the Anthropic API (network?)") from e

        if response.stop_reason == "refusal":
            raise LLMError("the model declined this posting")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMError("the model's answer was cut off or didn't match the schema")
        return response.parsed_output


def make_extractor(provider: str | None = None):
    provider = (provider or os.getenv("LLM_PROVIDER", "anthropic")).lower()
    if provider == "anthropic":
        return AnthropicExtractor()
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Supported now: 'anthropic'.")
