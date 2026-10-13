"""Tests for the input/output layer (src/io_manager.py): checking what the user types, reading
resume files, wording, and the job portal's replies.

Plain functions only (the project is fully procedural). Run from the project root:

    python tests/test_io_manager.py

Typed answers are fed in by replacing input(), and the job portal's replies by replacing
requests.post / requests.get, so no keyboard or internet is needed.
"""

import builtins
import contextlib
import io
import os
import sys
import tempfile
from types import SimpleNamespace

# The project's modules live in src/, so add it to the import path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

import io_manager  # noqa: E402
from docx import Document  # noqa: E402

GOOD_URL = "https://www.mycareersfuture.gov.sg/job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca"


def run_with_answers(function, answers: list[str]):
    """Calls function() with input() answering from answers in order. Returns (result, what was printed)."""
    remaining = iter(answers)
    real_input = builtins.input
    builtins.input = lambda prompt="": next(remaining)
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            result = function()
    finally:
        builtins.input = real_input
    return result, printed.getvalue()


def fake_reply(payload, status: int = 200) -> SimpleNamespace:
    """A stand-in for a requests reply: raise_for_status() and json() like the real one."""
    def raise_for_status():
        if status >= 400:
            raise io_manager.requests.HTTPError(f"HTTP {status}")
    return SimpleNamespace(raise_for_status=raise_for_status, json=lambda: payload)


def with_fake_portal(function, post=None, get=None):
    """Calls function() with the job portal's HTTP calls (and the waits between retries) replaced."""
    real_post, real_get, real_sleep = io_manager.requests.post, io_manager.requests.get, io_manager.time.sleep
    io_manager.requests.post = post or real_post
    io_manager.requests.get = get or real_get
    io_manager.time.sleep = lambda seconds: None
    try:
        return function()
    finally:
        io_manager.requests.post, io_manager.requests.get, io_manager.time.sleep = real_post, real_get, real_sleep


# 1. Numbers: letters, negatives, decimals and out-of-range answers are rejected and asked again
def test_prompt_number_rejects_bad_answers_and_asks_again():
    value, printed = run_with_answers(lambda: io_manager.prompt_number("Pick", 1, 10), ["abc", "-3", "5.5", "70", "7"])
    assert value == 7
    assert printed.count("[!]") == 4
    # Enter takes the default; "$5,000" is read as 5000
    assert run_with_answers(lambda: io_manager.prompt_number("Salary", 0, 9000, default=0), [""])[0] == 0
    assert run_with_answers(lambda: io_manager.prompt_number("Salary", 0, 9000), ["$5,000"])[0] == 5000


# 2. Yes/no and text: anything else, or a blank answer, is asked again
def test_prompt_yes_no_and_text_ask_again():
    assert run_with_answers(lambda: io_manager.prompt_yes_no("Sure?"), ["maybe", "", "Y"])[0] is True
    assert run_with_answers(lambda: io_manager.prompt_yes_no("Sure?"), ["no"])[0] is False
    text, printed = run_with_answers(lambda: io_manager.prompt_text("Name"), ["", "   ", "Ann"])
    assert text == "Ann" and printed.count("This cannot be left blank.") == 2
    assert run_with_answers(lambda: io_manager.prompt_text("Filter", allow_blank=True), [""])[0] == ""


# 3. Filters: a maximum salary below the minimum asks for both again; Enter accepts the defaults
def test_prompt_filters_rechecks_the_salary_range():
    answers = ["9000", "100", "3000", "", "", "2", "4"]  # min, max (too low), min, max, years, Full Time, Remote
    filters, printed = run_with_answers(io_manager.prompt_filters, answers)
    assert "Maximum salary cannot be lower than minimum salary" in printed
    assert filters == {"min_salary": 3000, "max_salary": io_manager.MAX_SALARY,
                       "max_years_experience": io_manager.MAX_EXPERIENCE_YEARS,
                       "job_type": "Full Time", "work_arrangement": "Remote"}


# 4. The web form's filters get the same checks, as a message instead of a new question
def test_check_filters_gives_a_message_for_bad_form_values():
    filters, problem = io_manager.check_filters({"min_salary": "$3,000", "job_type": "Contract", "work_arrangement": "Any"})
    assert problem == "" and filters["min_salary"] == 3000 and filters["max_salary"] == io_manager.MAX_SALARY
    assert io_manager.check_filters({"min_salary": "lots"})[1] == "Salary and experience must be whole numbers."
    assert "between 0 and" in io_manager.check_filters({"min_salary": "-5"})[1]
    assert "cannot be lower" in io_manager.check_filters({"min_salary": "5000", "max_salary": "100"})[1]
    assert "from the lists" in io_manager.check_filters({"job_type": "Astronaut"})[1]


# 5. Resume files: every problem is a message, never a crash; a real resume is read
def test_resume_file_problems_are_messages():
    with tempfile.TemporaryDirectory() as folder:
        assert "File not found" in io_manager.check_resume_file(os.path.join(folder, "missing.pdf"))
        assert "folder" in io_manager.check_resume_file(folder)
        text_file = os.path.join(folder, "resume.txt")
        with open(text_file, "w", encoding="utf-8") as file:
            file.write("hello")
        assert "not supported" in io_manager.check_resume_file(text_file)
        empty_pdf = os.path.join(folder, "empty.pdf")
        open(empty_pdf, "w").close()
        assert io_manager.check_resume_file(empty_pdf) == "That file is empty."

        short_docx = os.path.join(folder, "short.docx")
        document = Document()
        document.add_paragraph("Too short to be a resume.")
        document.save(short_docx)
        assert "Could not read enough text" in io_manager.load_resume(short_docx)[1]
        broken_docx = os.path.join(folder, "broken.docx")
        with open(broken_docx, "w", encoding="utf-8") as file:
            file.write("not really a Word file")
        with contextlib.redirect_stdout(io.StringIO()):
            assert io_manager.load_resume(broken_docx)[0] == ""

    sample = os.path.join(PROJECT_ROOT, "resumes", "sample_resume_fintech.docx")
    text, problem = io_manager.load_resume(sample)
    assert problem == "" and len(text) >= io_manager.MIN_RESUME_CHARS


# 6. The web form only uses a saved resume from the list, so no other file can be chosen
def test_resume_path_from_form_only_allows_listed_resumes():
    assert io_manager.resume_path_from_form("", "../.env")[1] == "Please choose a resume or upload one."
    assert io_manager.resume_path_from_form("notes.txt", "")[1] == "Please upload a PDF or Word (.docx) file."
    path, problem = io_manager.resume_path_from_form("", "sample_resume_fintech.docx")
    assert problem == "" and path.endswith(os.path.join("resumes", "sample_resume_fintech.docx"))


# 7. Formatting records for the console and pages
def test_formatting_helpers():
    assert io_manager.format_salary({"min_salary": 4000, "max_salary": 6000}) == "$4,000 - $6,000 monthly"
    assert io_manager.format_salary({"min_salary": 0, "max_salary": 0}) == "Not stated"
    assert io_manager.tidy_title("IT SUPPORT ENGINEER") == "IT Support Engineer"
    assert io_manager.tidy_title("Data Analyst (SQL)") == "Data Analyst (SQL)"
    assert io_manager.tidy_title(None) == "N/A"
    assert io_manager.short_location("D19 Hougang, Sengkang, Punggol") == "Hougang"
    assert io_manager.experience_text(0) == "No experience needed" and io_manager.experience_text(2) == "2+ years"
    assert io_manager.join_list([{"job_skill": "SQL"}, "Python"]) == "SQL, Python"
    assert io_manager.join_list(None) == "None"
    assert io_manager.shorten("A very long company name", 10) == "A very ..."


# 8. The portal's filters for each search step
def test_portal_filters_for_each_search_step():
    preferences = {"job_type": "Full Time", "min_salary": 3000, "work_arrangement": "Remote"}
    assert io_manager.portal_filters_for(preferences, "all") == {
        "employmentTypes": ["Full Time", "Permanent"], "salary": 3000,
        "flexibleWorkArrangements": [io_manager.PORTAL_TELECOMMUTING]}
    assert io_manager.portal_filters_for(preferences, "job type") == {"employmentTypes": ["Full Time", "Permanent"]}
    assert io_manager.portal_filters_for(preferences, "general") == {}
    assert io_manager.portal_filters_for(None, "all") == {}


# 9. Job portal replies: a broken listing is skipped, a portal that keeps failing gives None,
#    and a malformed job-details reply is treated like no reply instead of crashing
def test_portal_replies_in_unexpected_formats_never_crash():
    good_listing = {"title": "Data Analyst", "metadata": {"jobDetailsUrl": GOOD_URL}}
    reply = {"results": [good_listing, {"title": "Broken", "employmentTypes": "not a list"}]}
    jobs = with_fake_portal(lambda: io_manager.fetch_jobs("Data Analyst"),
                            post=lambda url, **options: fake_reply(reply))
    assert [job["title"] for job in jobs] == ["Data Analyst"]
    assert with_fake_portal(lambda: io_manager.fetch_jobs("Data Analyst"),
                            post=lambda url, **options: fake_reply(None, status=500)) is None

    for payload in ({"description": "x", "skills": ["SQL"]}, [1, 2], {"description": "x", "skills": None}):
        details = with_fake_portal(lambda: io_manager.fetch_job_details(GOOD_URL),
                                   get=lambda url, **options: fake_reply(payload))
        assert details is None, payload
    details = with_fake_portal(lambda: io_manager.fetch_job_details(GOOD_URL),
                               get=lambda url, **options: fake_reply({"description": "<p>Use SQL</p>",
                                                                      "skills": [{"skill": "SQL"}]}))
    assert details == {"description": "Use SQL", "listed_skills": ["SQL"], "is_open": True}


# 10. The wording of the tailoring notes and changes
def test_tailoring_notes_and_changes_wording():
    notes = io_manager.describe_tailoring_notes([
        {"kind": "entry_not_in_resume", "title": "Invented Corp"},
        {"kind": "edit_adds_missing_skills", "start": "Built dashboards", "skills": ["Scala", "Java"]},
        {"kind": "bullet_group_kept", "heading": "Cashier"}])
    assert notes == ['Left out "Invented Corp": it isn\'t in your resume.',
                     'Kept "Built dashboards..." as it was: the new wording added Scala, Java, which isn\'t in your resume.',
                     'Kept every point under "Cashier": removing them all would leave it empty.']
    paragraphs = [{"id": 3, "text": "Built dashboards"}, {"id": 4, "text": "x" * 60}]
    changes = io_manager.describe_docx_changes(paragraphs, {3: "Built SQL dashboards", 4: ""}, {3: "leads with SQL"})
    assert changes == ['Reworded "Built dashboards": leads with SQL', f'Removed "{"x" * 50}..."']


# 11. A cancelled background task stops at its next progress update
def test_cancelled_task_stops_at_its_next_update():
    task_id = io_manager.start_task(job_url=GOOD_URL)
    io_manager.update_task(task_id, percent=10)
    io_manager.cancel_task(task_id)
    try:
        io_manager.update_task(task_id, percent=20)
        raise AssertionError("expected the cancelled task to stop")
    except InterruptedError:
        pass
    assert io_manager.get_task(task_id)["percent"] == 10 and io_manager.get_task("no such id") is None


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
