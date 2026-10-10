"""Tests for the logic layer (src/logic_manager.py): filtering and ranking job records.

Plain functions only (the project is fully procedural). Run from the project root:

    python tests/test_logic_manager.py

Each test_* function checks one rule with assert; the runner at the bottom calls them all.
They also run under pytest, if it is installed (python -m pytest tests).
"""

import os
import sys

# The project's modules live in src/, so add it to the import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import io_manager  # noqa: E402  (all output goes through the input/output layer)
import logic_manager  # noqa: E402

GOOD_URL = "https://www.mycareersfuture.gov.sg/job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca"

# Filters that let everything through, in the shape io_manager.prompt_filters returns
NO_FILTERS = {
    "min_salary": 0,
    "max_salary": 50000,
    "max_years_experience": 60,
    "job_type": "Any",
    "work_arrangement": "Any",
}

PROFILE = {"highest_qualification": "Diploma", "years_of_experience": 1}


def make_job(**changes):
    """A job record like ai_manager returns, with only the given fields changed."""
    job = {
        "title": "Data Analyst",
        "job_url": GOOD_URL,
        "min_salary": 4000,
        "max_salary": 6000,
        "min_years_experience": 0,
        "employment_types": ["Full Time"],
        "work_arrangement": "Onsite",
        "min_education": "Not specified",
        "required_skills": ["Python", "SQL", "Excel", "Tableau"],
        "matched_skills": [],
    }
    job.update(changes)
    return job


def matched(*skills):
    """matched_skills entries for the given job skills."""
    return [{"job_skill": skill, "candidate_skill": skill} for skill in skills]


# 1. Links: only https links on MyCareersFuture are accepted, checked without going online
def test_job_links_must_be_https_on_mycareersfuture():
    assert logic_manager.is_valid_job_url(GOOD_URL)
    assert not logic_manager.is_valid_job_url(GOOD_URL.replace("https", "http"))
    assert not logic_manager.is_valid_job_url("https://mycareersfuture.gov.sg.evil.com/job/1")
    assert not logic_manager.is_valid_job_url("N/A")
    assert not logic_manager.is_valid_job_url(None)
    assert logic_manager.filter_reason(make_job(job_url="N/A"), NO_FILTERS) == "bad link"


# 2. Salary: a job is removed only if its whole pay range is outside the user's range
def test_salary_filter_keeps_overlapping_and_unstated_pay():
    filters = {**NO_FILTERS, "min_salary": 5000, "max_salary": 8000}
    assert logic_manager.filter_reason(make_job(min_salary=4000, max_salary=6000), filters) == ""
    assert logic_manager.filter_reason(make_job(min_salary=3000, max_salary=4500), filters) == "salary"
    assert logic_manager.filter_reason(make_job(min_salary=9000, max_salary=12000), filters) == "salary"
    # 0 means the job doesn't state a salary, so it is kept
    assert logic_manager.filter_reason(make_job(min_salary=0, max_salary=0), filters) == ""


# 3. Job type and work arrangement, including the special cases
def test_job_type_and_work_arrangement_filters():
    full_time = {**NO_FILTERS, "job_type": "Full Time"}
    # "Permanent" jobs on MyCareersFuture are full-time
    assert logic_manager.filter_reason(make_job(employment_types=["Permanent"]), full_time) == ""
    assert logic_manager.filter_reason(make_job(employment_types=["Contract"]), full_time) == "job type"

    remote = {**NO_FILTERS, "work_arrangement": "Remote"}
    assert logic_manager.filter_reason(make_job(work_arrangement="Remote"), remote) == ""
    assert logic_manager.filter_reason(make_job(work_arrangement="Onsite"), remote) == "work arrangement"
    # A job that doesn't say is kept rather than wrongly removed
    assert logic_manager.filter_reason(make_job(work_arrangement="Not specified"), remote) == ""


# 4. count_removed counts each removed job once, under the first filter it fails
def test_count_removed_groups_jobs_by_first_failed_filter():
    filters = {**NO_FILTERS, "min_salary": 5000, "max_years_experience": 2}
    jobs = [
        make_job(),  # kept
        make_job(job_url="N/A"),  # bad link
        make_job(max_salary=4000),  # salary
        make_job(min_years_experience=5),  # experience
        make_job(max_salary=4000, min_years_experience=5),  # fails both: counted under salary only
    ]
    assert logic_manager.count_removed(jobs, filters) == {"bad link": 1, "salary": 2, "experience": 1}
    assert logic_manager.count_removed([make_job()], filters) == {}


# 5. Ranking: jobs the candidate qualifies for come first, then by share of skills matched
def test_filter_and_rank_orders_best_matches_first():
    jobs = [
        make_job(title="Too senior", min_years_experience=5,
                 matched_skills=matched("Python", "SQL", "Excel", "Tableau")),
        make_job(title="Needs a degree", min_education="Bachelor's", matched_skills=matched("Python", "SQL", "Excel")),
        make_job(title="Half match", matched_skills=matched("Python", "SQL")),
        make_job(title="Best match", matched_skills=matched("Python", "SQL", "Excel")),
        make_job(title="Filtered out", employment_types=["Contract"],
                 matched_skills=matched("Python", "SQL", "Excel", "Tableau")),
    ]
    filters = {**NO_FILTERS, "job_type": "Full Time"}

    ranked = logic_manager.filter_and_rank(PROFILE, jobs, filters, top_n=4)
    assert [job["title"] for job in ranked] == ["Best match", "Half match", "Needs a degree", "Too senior"]

    # top_n limits how many are returned
    top_two = logic_manager.filter_and_rank(PROFILE, jobs, filters, top_n=2)
    assert [job["title"] for job in top_two] == ["Best match", "Half match"]


# 6. Seniority: with the same skill match, a job at the candidate's level beats one above it
def test_job_above_candidate_level_ranks_lower():
    profile = {**PROFILE, "seniority_level": "Fresh/entry level"}
    jobs = [
        make_job(title="Senior role", position_levels=["Senior Executive"], matched_skills=matched("Python", "SQL")),
        make_job(title="Entry role", position_levels=["Fresh/entry level", "Junior Executive"],
                 matched_skills=matched("Python", "SQL")),
        make_job(title="Better match, senior", position_levels=["Manager"],
                 matched_skills=matched("Python", "SQL", "Excel")),
    ]
    ranked = logic_manager.filter_and_rank(profile, jobs, NO_FILTERS, top_n=3)
    # Skill match still comes first; seniority only breaks the tie between the 2-of-4 jobs
    assert [job["title"] for job in ranked] == ["Better match, senior", "Entry role", "Senior role"]
    # Unknown or missing levels count as no gap
    assert logic_manager.seniority_gap(make_job(position_levels=["Unknown"]), "Executive") == 0
    assert logic_manager.seniority_gap(make_job(), "Executive") == 0


def run_all_tests():
    """Runs every test_* function in this file. Returns how many failed."""
    tests = [function for name, function in globals().items() if name.startswith("test_") and callable(function)]
    failed = 0
    for test in tests:
        try:
            test()
            io_manager.display_message(f"PASS  {test.__name__}")
        except AssertionError as error:
            failed += 1
            io_manager.display_error(f"FAIL  {test.__name__} {error}")
    io_manager.display_message(f"\n{len(tests) - failed} of {len(tests)} tests passed.")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_all_tests() else 0)  # a non-zero exit code tells CI that a test failed
