"""Tests for the console app (src/main.py) from start to finish: all four layers working together.

Plain functions only (the project is fully procedural). Run from the project root:

    python tests/test_main.py

The real main.py, io_manager, logic_manager and data_manager run; only the outside world is
replaced: typed answers (input), MyCareersFuture's replies (requests.post) and the AI's replies
(ai_manager.run_task). Saved jobs and the log go to a temporary folder, so no keyboard, internet,
API key or real data is needed.
"""

import builtins
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
from types import SimpleNamespace

# The project's modules live in src/, so add it to the import path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

import ai_manager  # noqa: E402
import data_manager  # noqa: E402
import io_manager  # noqa: E402
import main  # noqa: E402

SAMPLE_RESUME = "sample_resume_fintech.docx"  # the one resume kept in git, so this also runs in CI
LISTING_TITLES = ["Data Analyst", "Business Analyst", "Bank Teller"]
PROFILE = {"candidate_name": "Sample Candidate", "highest_qualification": "Diploma",
           "qualification_detail": "Diploma in Financial Technology", "years_of_experience": 1,
           "internship_months": 6, "seniority_level": "Fresh/entry level", "core_skills": ["SQL", "Python"],
           "certifications": [], "search_keywords": ["Data Analyst", "Business Analyst"]}


def resume_answer() -> str:
    """The number to type to pick the sample resume from the console's list."""
    return str(io_manager.list_resumes().index(SAMPLE_RESUME) + 1)


def search_answers() -> list[str]:
    """Typed answers for menu 1: the sample resume, default salaries and experience, Any job type and arrangement."""
    return ["1", resume_answer(), "", "", "", "1", "1"]


def portal_listing(number: int, title: str) -> dict:
    """One job listing in the shape MyCareersFuture's search API returns."""
    return {"title": title, "hiringCompany": {"name": f"Company {number}"},
            "salary": {"minimum": 4000, "maximum": 6000, "type": {"salaryType": "Monthly"}},
            "address": {"districts": [{"location": "Jurong"}]},
            "employmentTypes": [{"employmentType": "Full Time"}], "positionLevels": [{"position": "Executive"}],
            "minimumYearsExperience": 0, "flexibleWorkArrangements": [], "skills": [{"skill": "SQL"}],
            "description": "<p>SQL and Python reporting</p>",
            "metadata": {"jobDetailsUrl": f"https://www.mycareersfuture.gov.sg/job/it/job-{number:032x}"}}


def run_console(answers: list[str], profile_failures: int = 0, portal_status: int = 200,
                saved_jobs_locked: bool = False) -> tuple[str, list]:
    """Runs main.main() with these typed answers ("CTRL+C" presses Ctrl+C; after the last answer the
    keyboard is closed). Returns (everything printed, the saved jobs file's contents)."""
    remaining_answers = iter(answers)
    remaining_profile_failures = [profile_failures]

    def typed_answer(prompt: str = "") -> str:
        answer = next(remaining_answers, None)
        if answer is None:
            raise EOFError  # no more answers: like closing the keyboard
        if answer == "CTRL+C":
            raise KeyboardInterrupt
        return answer

    def portal_reply(url: str, **options: object) -> SimpleNamespace:
        def raise_for_status() -> None:
            if portal_status >= 400:
                raise io_manager.requests.HTTPError(f"HTTP {portal_status}")
        listings = [portal_listing(number, title) for number, title in enumerate(LISTING_TITLES)]
        return SimpleNamespace(raise_for_status=raise_for_status, json=lambda: {"results": listings})

    def ai_reply(record: dict, task: str) -> dict | None:
        if task == "candidate_profile":
            if remaining_profile_failures[0] > 0:
                remaining_profile_failures[0] -= 1
                return None  # as if the AI service were busy
            return json.loads(json.dumps(PROFILE))
        return {"jobs": [{"job_index": index, "required_skills": ["SQL", "Python", "Tableau"],
                          "min_education": "Not specified", "certifications_required": [], "work_arrangement": "Onsite",
                          "skill_matches": [{"job_skill": "SQL", "candidate_skill": "SQL"},
                                            {"job_skill": "Python", "candidate_skill": "Python"}],
                          "suitability_reason": "Matches SQL and Python."} for index in range(len(record["jobs"]))]}

    def locked_file() -> list[dict]:
        raise PermissionError(13, "Permission denied")

    real = (builtins.input, io_manager.requests.post, io_manager.time.sleep, ai_manager.run_task,
            data_manager.DATABASE_PATH, data_manager.read_saved_jobs, main.LOG_PATH)
    handlers_before = list(logging.getLogger().handlers)
    printed = io.StringIO()
    with tempfile.TemporaryDirectory() as folder:
        builtins.input = typed_answer
        io_manager.requests.post = portal_reply
        io_manager.time.sleep = lambda seconds: None
        ai_manager.run_task = ai_reply
        data_manager.DATABASE_PATH = os.path.join(folder, "job_listings.json")
        main.LOG_PATH = os.path.join(folder, "job_matcher.log")
        if saved_jobs_locked:
            data_manager.read_saved_jobs = locked_file
        try:
            with contextlib.redirect_stdout(printed):
                main.main()
            saved = []
            if os.path.exists(data_manager.DATABASE_PATH):
                with open(data_manager.DATABASE_PATH, encoding="utf-8") as file:
                    saved = json.load(file)
        finally:
            (builtins.input, io_manager.requests.post, io_manager.time.sleep, ai_manager.run_task,
             data_manager.DATABASE_PATH, data_manager.read_saved_jobs, main.LOG_PATH) = real
            for handler in logging.getLogger().handlers[:]:
                if handler not in handlers_before:  # the log file and terminal handlers main.main() added
                    logging.getLogger().removeHandler(handler)
                    handler.close()
    return printed.getvalue(), saved


# 1. A whole search: resume -> AI profile -> portal listings -> AI requirements -> ranking -> saved and shown
def test_a_search_goes_through_all_four_layers():
    printed, saved = run_console(search_answers() + ["4"])
    assert "MATCHING JOBS (3)" in printed and "Strong match" in printed
    assert "Checked 3 listings: up to 10 from each of 2 MyCareersFuture searches" in printed
    assert sorted(job["title"] for job in saved) == sorted(LISTING_TITLES)
    assert all(job["resume"] == SAMPLE_RESUME and job["match_outcome"] == "Strong match" for job in saved)
    assert printed.rstrip().endswith("Goodbye!")


# 2. The AI failing: "Try again?" yes repeats only that step; no goes back to the menu, saving nothing
def test_an_ai_failure_can_be_retried_or_left():
    printed, saved = run_console(search_answers() + ["y", "4"], profile_failures=1)
    assert io_manager.ai_unavailable_message() in printed and len(saved) == 3
    printed, saved = run_console(search_answers() + ["n", "4"], profile_failures=1)
    assert "MATCHING JOBS" not in printed and saved == []


# 3. MyCareersFuture not answering is reported, never a crash
def test_the_job_portal_being_down_is_reported():
    printed, saved = run_console(search_answers() + ["n", "4"], portal_status=500)
    assert io_manager.search_problem_message(0, 0) in printed and saved == []


# 4. Ctrl+C inside a menu option cancels just that option; Ctrl+C at the menu exits
def test_ctrl_c_cancels_an_option_but_exits_at_the_menu():
    printed, _ = run_console(["1", "CTRL+C", "4"])
    assert "Cancelled." in printed and "Main menu" in printed.split("Cancelled.")[-1]
    printed, _ = run_console(search_answers()[:4] + ["CTRL+C", "4"])  # while typing the filters
    assert "Cancelled." in printed and "Main menu" in printed.split("Cancelled.")[-1]
    printed, _ = run_console(["CTRL+C"])
    assert "Cancelled." not in printed and printed.rstrip().endswith("Goodbye!")


# 5. Saved jobs (the data layer's query): listed, narrowed by a keyword, and one viewed in detail
def test_saved_jobs_can_be_listed_filtered_and_viewed():
    printed, _ = run_console(search_answers() + ["2", "analyst", "3", "", "1", "4"])
    assert "2. Show my saved jobs" in printed
    assert "SAVED JOBS (2)" in printed  # "analyst" matches 2 of the 3 saved titles
    assert "Match           : Strong match." in printed  # the detail view of job 1


# 6. A saved jobs file that can't be opened gives one clear message, not a wrong "No saved jobs yet"
def test_a_locked_saved_jobs_file_gives_one_clear_message():
    printed, _ = run_console(["2", "4"], saved_jobs_locked=True)
    assert "no results from this session to show yet" in printed
    assert "No saved jobs yet" not in printed


# 7. No keyboard (e.g. Docker started without -it): a hint, then a clean exit
def test_no_keyboard_gives_the_docker_hint():
    printed, _ = run_console([])
    assert "No keyboard input" in printed and printed.rstrip().endswith("Goodbye!")


def run_all_tests() -> int:
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
