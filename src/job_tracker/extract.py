"""Phase 2: turn job page HTML into a Job (-> JSON)."""
from bs4 import BeautifulSoup

from .models import Job


def _text(node, selector: str) -> str:
    el = node.select_one(selector)
    return el.get_text(" ", strip=True) if el else ""


def parse_job(html: str, job_id: str, url: str) -> Job:
    soup = BeautifulSoup(html, "html.parser")

    # The "criteria" box: Seniority level, Employment type, Job function, Industries
    criteria = {}
    for li in soup.select("li.description__job-criteria-item"):
        key = _text(li, ".description__job-criteria-subheader")
        if key:
            criteria[key.lower()] = _text(li, ".description__job-criteria-text")

    desc_el = soup.select_one(".show-more-less-html__markup") or soup.select_one(
        ".description__text"
    )

    return Job(
        job_id=job_id,
        url=url,
        title=_text(soup, ".top-card-layout__title") or _text(soup, "h2"),
        company=_text(soup, ".topcard__org-name-link")
        or _text(soup, ".top-card-layout__second-subline a"),
        location=_text(soup, ".topcard__flavor--bullet"),
        posted=_text(soup, ".posted-time-ago__text"),
        applicants=_text(soup, ".num-applicants__caption"),
        seniority=criteria.get("seniority level", ""),
        employment_type=criteria.get("employment type", ""),
        job_function=criteria.get("job function", ""),
        industries=criteria.get("industries", ""),
        salary=_text(soup, ".salary") or _text(soup, ".compensation__salary"),
        description=desc_el.get_text("\n", strip=True) if desc_el else "",
    )
