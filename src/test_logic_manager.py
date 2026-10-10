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

import io_manager     # noqa: E402  (all output goes through the input/output layer)
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


# 1. Links: only https links on MyCareersFuture are accepted, checked without going online
def test_job_links_must_be_https_on_mycareersfuture():
    assert logic_manager.is_valid_job_url(GOOD_URL)
    assert not logic_manager.is_valid_job_url(GOOD_URL.replace("https", "http"))
    assert not logic_manager.is_valid_job_url("https://mycareersfuture.gov.sg.evil.com/job/1")
    assert not logic_manager.is_valid_job_url("N/A")
    assert not logic_manager.is_valid_job_url(None)
    assert logic_manager.filter_reason(make_job(job_url="N/A"), NO_FILTERS) == "bad link"


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