"""Pre-filter module - hard gate filtering before LLM evaluation."""

import re


INTERNSHIP_KEYWORDS = ("实习", "intern", "internship", "管培")


def quick_score(job: dict, config: dict) -> tuple[int, str]:
    """Return a hard prefilter result.

    Returns:
    - (0, reason): hard rejection, do not call the LLM.
    - (100, "预筛通过"): pass prefilter and enter LLM scoring.

    Product boundary: exclusion keywords intentionally match the job title only,
    not the JD, to avoid rejecting jobs whose JD says things like "非外包".
    """
    profile = config.get("profile", {})
    deal_breakers = profile.get("deal_breakers", [])
    salary_min = profile.get("salary_min", 0)
    allow_internship = profile.get("allow_internship", False)

    title = (job.get("title") or "").lower()
    salary_text = job.get("salary") or ""

    for breaker in deal_breakers:
        if breaker.lower() in title:
            return 0, f"触发排除词: {breaker}"

    if not allow_internship and any(keyword in title for keyword in INTERNSHIP_KEYWORDS):
        return 0, "实习/管培岗位"

    if salary_text and salary_min > 0:
        _, parsed_max = _parse_salary(salary_text)
        if parsed_max > 0 and parsed_max < salary_min:
            return 0, f"薪资低于硬性要求: {salary_text} < {salary_min}K"

    return 100, "预筛通过"


def _parse_salary(salary_text: str) -> tuple[int, int]:
    """Parse salary text like '15-25K' or '15-25K·14薪' into (min_k, max_k)."""
    match = re.search(r"(\d+)\s*[-~]\s*(\d+)\s*[kK]", salary_text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d+)\s*[kK]", salary_text)
    if match:
        val = int(match.group(1))
        return val, val
    return 0, 0
