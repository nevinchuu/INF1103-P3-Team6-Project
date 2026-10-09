from collections import Counter
from urllib.parse import urlparse
import re

# Ordered lowest to highest, so a list index works as an education level number.
# Same values as ai_manager.EDUCATION_LEVELS (copied so this layer does not import another layer)
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]
SENIORITY_LEVELS = [
        "Fresh/entry level", "Non-executive", "Junior Executive", "Executive", "Senior Executive",
        "Professional", "Manager", "Middle Management", "Senior Management",
]
ANY = "Any"  # the filter value meaning "no preference" (from io_manager)
SKILL_GAP_LIMIT = 5  # most common missing skills returned by find_skill_gaps


# Checks the link format only, without going online, so it is instant and
# cannot fail because of the network
def is_valid_job_url(url):
    if not isinstance(url, str):
        return False
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host == "mycareersfuture.gov.sg" or host.endswith(".mycareersfuture.gov.sg")
    )


# Returns why a job is removed, e.g. "salary", or "" if it has a valid link and
# meets all of the user's filters (from io_manager.prompt_filters)
def filter_reason(job, filters):
    if not is_valid_job_url(job.get("job_url")):
        return "bad link"

    # Salary: drop jobs whose pay range is completely outside the user's range.
    # Jobs that do not state a salary (0) are kept.
    job_min = job.get("min_salary") or 0
    job_max = job.get("max_salary") or 0
    if job_max > 0 and job_max < filters["min_salary"]:
        return "salary"
    if job_min > 0 and job_min > filters["max_salary"]:
        return "salary"

    # Experience: drop jobs that need more years than the user allows
    if (job.get("min_years_experience") or 0) > filters["max_years_experience"]:
        return "experience"

    # Job type: must be one of the job's employment types, unless the user picked "Any".
    # "Permanent" jobs on MyCareersFuture are full-time, so they also count as "Full Time".
    job_types = job.get("employment_types", [])
    if "Permanent" in job_types:
        job_types = job_types + ["Full Time"]
    if filters["job_type"] != ANY and filters["job_type"] not in job_types:
        return "job type"

    # Work arrangement: must match, unless the user picked "Any" or the job does not say
    arrangement = job.get("work_arrangement", "Not specified")
    if filters["work_arrangement"] != ANY and arrangement not in (filters["work_arrangement"], "Not specified"):
        return "work arrangement"

    return ""


# Returns True if a job has a valid link and meets all of the user's filters
def passes_filters(job, filters):
    return filter_reason(job, filters) == ""


# Counts how many jobs each filter removed, e.g. {"salary": 14, "job type": 6}.
# A job failing several filters is counted once, under the first one it fails.
def count_removed(jobs, filters):
    counts = {}
    for job in jobs:
        reason = filter_reason(job, filters)
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts

# How many levels a job's lowest listed level is above the candidate's, e.g. 2 for a
# "Senior Executive" job and a "Junior Executive" candidate. 0 if the job is at or below
# their level, or if either level is missing or not a known label
def seniority_gap(job, candidate_seniority):
    if candidate_seniority not in SENIORITY_LEVELS:
        return 0
    job_levels = [SENIORITY_LEVELS.index(level) for level in job.get("position_levels", [])
                  if level in SENIORITY_LEVELS]
    if not job_levels:
        return 0
    return max(0, min(job_levels) - SENIORITY_LEVELS.index(candidate_seniority))


# Returns the top_n jobs that best suit the candidate (moved from ai_manager_test.py).
# Jobs whose experience or education requirement the candidate misses rank below
# those they meet; within each group, jobs are ordered by share of required skills
# matched, then by how far the job's level is above the candidate's, then by number of skills matched.
def select_top_jobs(profile, jobs, top_n):
    candidate_level = EDUCATION_LEVELS.index(profile["highest_qualification"])

    # sorted() compares the returned tuples item by item, smallest first:
    #   1. experience_gap   - 0 years short beats 2 years short
    #   2. not education_ok - False (meets it) sorts before True (doesn't)
    #   3. -match_ratio     - negative so a HIGHER ratio sorts FIRST
    #   4. level_gap        - tie-breaker: a job at your level beats one 2 levels above
    #   5. -matched         - tie-breaker: more matched skills first
    
    
    def sort_key(job):
        experience_gap = max(0, job["min_years_experience"] - profile["years_of_experience"])
        education_ok = (
            job["min_education"] == "Not specified"
            or EDUCATION_LEVELS.index(job["min_education"]) <= candidate_level
        )
        matched = len(job["matched_skills"])
        match_ratio = matched / len(job["required_skills"]) if job["required_skills"] else 0
        level_gap = seniority_gap(job, profile["seniority"])
        return (experience_gap, not education_ok, -match_ratio, level_gap, -matched)

    return sorted(jobs, key=sort_key)[:top_n]


# Logic layer entry point, called by main.py: removes jobs with a bad link or that
# fail the user's filters, then returns the top_n best matches
def filter_and_rank(profile, jobs, filters, top_n=5):
    kept = []
    for job in jobs:
        if passes_filters(job, filters):
            kept.append(job)
    return select_top_jobs(profile, kept, top_n)


# The skills missing from the most jobs, as [(skill, number of jobs)], most common first.
# Only skills missing from 2 or more jobs; "SQL" and "sql" count as one skill
def find_skill_gaps(jobs):
    counts = Counter()
    names = {}  # lowercase skill -> how it was first written
    for job in jobs:
        job_skills = set()  # each skill counted once per job
        for skill in job.get("missing_skills", []):
            key = skill.strip().lower()
            names.setdefault(key, skill.strip())
            job_skills.add(key)
        counts.update(job_skills)
    return [(names[key], count) for key, count in counts.most_common(SKILL_GAP_LIMIT) if count >= 2]


# The skills that appear word for word in text, ignoring case,
# e.g. "SQL" is found in "Wrote SQL reports" but "Java" is not found in "JavaScript"
def keywords_found(skills, text):
    text = text.lower()
    return [skill for skill in skills if re.search(rf"(?<!\w){re.escape(skill.lower())}(?!\w)", text)]


# All the words of a tailored resume (ai_manager.tailor_resume) as one text, for keywords_found
def resume_as_text(resume):
    parts = [resume["summary"], *resume["skills"]]
    for section in resume["sections"]:
        for entry in section["entries"]:
            parts += [entry["title"], entry["organisation"], *entry["bullets"]]
    return "\n".join(parts)