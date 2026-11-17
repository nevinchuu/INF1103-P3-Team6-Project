"""Tests for IOManager (src/io_layer/io_manager.py): the facade the web pages call, through the real LogicManager and
DataManager with a scripted AI, an in-memory store and a fake portal. Background work runs at once."""

import os
import re
import shutil
from io import BytesIO

import pytest
from docx import Document
from fakes import FakePortal, FlakyStorage, InMemoryStorage, ScriptedAIManager

from data_layer.data_manager import DataManager, STATUSES
from io_layer.io_manager import IOManager
from logic_layer.logic_manager import LogicManager
from test_web_app import PDF_RESUME_LINES, ai_requests, fake_ai, tiny_pdf

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = "sample_resume_fintech.docx"


class Parts:
    """The pieces a test may want to look at or break."""

    def __init__(self, ai, data, storage, portal, folder) -> None:
        self.ai, self.data, self.storage, self.portal, self.folder = ai, data, storage, portal, folder


def make_io(tmp_path, responder=None, listings=(), spawn=None, resume_folder=None, details=None) -> tuple[IOManager, Parts]:
    """An IOManager wired to a scripted AI, an in-memory store and a fake portal, with a temporary resume folder
    holding the sample resume (or resume_folder, as it is). Background work runs straight away unless spawn says
    otherwise."""
    folder = resume_folder
    if folder is None:
        folder = str(tmp_path / "resumes")
        os.makedirs(folder)
        shutil.copy(os.path.join(PROJECT_ROOT, "resumes", SAMPLE), folder)
    ai = ScriptedAIManager(responder or (lambda record, task: None))
    storage = FlakyStorage(InMemoryStorage())
    portal = FakePortal(list(listings) if isinstance(listings, tuple) else listings, details=details)
    data = DataManager(storage, portal)
    io = IOManager(ai, LogicManager(), data, folder, spawn or (lambda work, *args, **kwargs: work(*args, **kwargs)))
    return io, Parts(ai, data, storage, portal, folder)


def make_job(number: int, **changes) -> dict:
    job = {"job_url": f"https://www.mycareersfuture.gov.sg/job/it/job-{number:032x}", "title": f"Data Analyst {number}",
           "company": "Acme", "location": "D19 Hougang, Sengkang", "min_salary": 4000, "max_salary": 6000,
           "salary_period": "monthly", "employment_types": ["Full Time"], "min_years_experience": 2,
           "required_skills": ["SQL", "Python"], "missing_skills": ["Python"],
           "matched_skills": [{"job_skill": "SQL", "candidate_skill": "sql"},
                              {"job_skill": "Reporting", "candidate_skill": "Dashboards"}],
           "match_outcome": "Strong match"}
    job.update(changes)
    return job


# ---------- the form choices and the resume list ----------

def test_the_forms_get_their_choices_and_the_resume_list(tmp_path):
    io, _ = make_io(tmp_path)
    assert io.resume_choices() == [SAMPLE]
    options = io.form_options()
    assert options["education_levels"][0] == "Secondary" and "None" not in options["education_levels"]
    assert options["ai_name"] == "Fake AI" and options["statuses"] == STATUSES and options["show_options"] == [5, 10, 20]
    assert {"job_types", "work_arrangements", "max_salary", "max_years", "max_upload", "max_steps"} <= set(options)


def test_start_up_says_how_many_jobs_are_saved_and_where_to_open_the_app(tmp_path, capsys):
    io, parts = make_io(tmp_path)
    parts.data.save_new_jobs([make_job(1)], SAMPLE)
    io.start_up("127.0.0.1:5000")
    printed = capsys.readouterr().out
    assert "1 saved job loaded." in printed and "http://127.0.0.1:5000" in printed


def test_start_up_reports_a_database_that_is_down_and_carries_on(tmp_path, capsys):
    io, parts = make_io(tmp_path)
    parts.storage.fail_reads = True
    io.start_up("127.0.0.1:5000")
    printed = capsys.readouterr().out
    assert "[!] Could not open your saved jobs file. New searches still work." in printed and "is running" in printed


# ---------- saved jobs ----------

def test_the_saved_page_has_the_jobs_the_tracker_and_the_texts_the_page_shows(tmp_path):
    io, parts = make_io(tmp_path)
    parts.data.save_new_jobs([make_job(1), make_job(2, title="BANK TELLER", min_salary=0, max_salary=0)], SAMPLE)
    page = io.saved_jobs()
    assert page["total"] == 2 and page["statuses"] == STATUSES and page["load_failed"] is False
    assert page["tracker"]["by_status"]["Saved"] == 2 and page["keyword"] == ""
    first, second = page["jobs"]
    assert first["title_text"] == "Data Analyst 1" and first["location_text"] == "Hougang"
    assert first["salary_text"] == "$4,000 - $6,000 monthly" and first["job_types_text"] == "Full Time"
    assert first["experience_text"] == "2+ years"
    assert [skill["same_name"] for skill in first["matched_skills"]] == [True, False]
    assert second["title_text"] == "Bank Teller" and second["salary_text"] == "Not stated"


def test_the_saved_page_can_be_filtered_by_a_keyword(tmp_path):
    io, parts = make_io(tmp_path)
    parts.data.save_new_jobs([make_job(1), make_job(2, title="Bank Teller")], SAMPLE)
    page = io.saved_jobs(" teller ")
    assert page["keyword"] == "teller" and page["total"] == 2
    assert [job["title"] for job in page["jobs"]] == ["Bank Teller"]


@pytest.mark.parametrize("outcome, key, text", [
    ("updated", "done", "Saved your changes."), ("removed", "done", "Removed the job."),
    ("cleared", "done", "Removed all saved jobs."), ("gone", "problem", "That job is no longer saved."),
    ("status", "problem", "Status must be one of Saved, Applied"), ("note", "problem", "at most 500 characters")])
def test_the_saved_page_words_an_outcome_code_and_nothing_else(tmp_path, outcome, key, text):
    io, _ = make_io(tmp_path)
    page = io.saved_jobs("", outcome)
    assert text in page[key] and page["update_failed"] is False
    assert io.saved_jobs("", "Account locked, call 123")["problem"] == ""  # a crafted code says nothing


def test_a_failed_change_is_flagged_on_the_saved_page(tmp_path):
    io, _ = make_io(tmp_path)
    assert io.saved_jobs("", "failed")["update_failed"] is True


def test_a_saved_job_gets_a_status_and_a_note(tmp_path):
    io, parts = make_io(tmp_path)
    parts.data.save_new_jobs([make_job(1)], SAMPLE)
    url = make_job(1)["job_url"]
    assert io.update_saved({"job_url": url, "status": "Applied", "note": " Phone screen "}) == "updated"
    saved = parts.data.get_saved_job(url)
    assert saved["status"] == "Applied" and saved["note"] == "Phone screen"
    assert io.update_saved({"job_url": url}) == "updated"  # nothing to change keeps the job as it was
    assert parts.data.get_saved_job(url) == saved


def test_a_bad_status_a_long_note_and_an_unsaved_job_are_codes(tmp_path):
    io, parts = make_io(tmp_path)
    parts.data.save_new_jobs([make_job(1)], SAMPLE)
    url = make_job(1)["job_url"]
    assert io.update_saved({"job_url": url, "status": "Hired"}) == "status"
    assert io.update_saved({"job_url": url, "note": "x" * 501}) == "note"
    assert io.update_saved({"job_url": "https://x/none", "status": "Applied"}) == "gone"
    assert parts.data.get_saved_job(url).get("status") is None


def test_saved_jobs_are_removed_one_at_a_time_and_cleared(tmp_path):
    io, parts = make_io(tmp_path)
    parts.data.save_new_jobs([make_job(1), make_job(2)], SAMPLE)
    assert io.remove_saved(make_job(1)["job_url"]) == "removed"
    assert io.remove_saved(make_job(1)["job_url"]) == "gone"
    assert io.clear_saved() == "cleared" and parts.data.read_saved_jobs() == []


def test_a_database_that_is_down_becomes_a_message_not_a_crash(tmp_path):
    io, parts = make_io(tmp_path)
    parts.data.save_new_jobs([make_job(1)], SAMPLE)
    parts.storage.fail_reads = True
    assert io.saved_jobs() == {"jobs": [], "total": 0, "keyword": "", "load_failed": True}
    assert io.saved_jobs("analyst")["load_failed"] is True
    parts.storage.fail_reads, parts.storage.fail_writes = False, True
    assert io.update_saved({"job_url": make_job(1)["job_url"], "status": "Applied"}) == "failed"
    assert io.remove_saved(make_job(1)["job_url"]) == "failed"
    assert io.clear_saved() == "failed"


# ---------- the search ----------

PROFILE = {"candidate_name": "Sample Candidate", "highest_qualification": "Diploma",
           "qualification_detail": "Diploma in Financial Technology", "years_of_experience": 1,
           "internship_months": 6, "seniority_level": "Fresh/entry level", "core_skills": ["SQL", "Python"],
           "certifications": [], "search_keywords": ["Data Analyst", "Business Analyst"]}
FORM = {"resume_choice": SAMPLE, "min_salary": "", "max_salary": "", "max_years_experience": "",
        "job_type": "Any", "work_arrangement": "Any"}


def listing(number: int, **changes) -> dict:
    job = {"title": f"Job {number}", "company": "Acme", "location": "Jurong", "min_salary": 4000, "max_salary": 6000,
           "salary_period": "monthly", "employment_types": ["Full Time"], "position_levels": ["Executive"],
           "min_years_experience": 0, "flexible_work_arrangements": [], "listed_skills": ["SQL"],
           "description": "SQL reporting", "job_url": f"https://www.mycareersfuture.gov.sg/job/it/job-{number:032x}"}
    job.update(changes)
    return job


requests_made: list[str] = []  # the AI tasks asked for, newest last


def respond(record: dict, task: str) -> dict | None:
    """The AI's replies: a profile, and each listing's requirements."""
    requests_made.append(task)
    if task == "candidate_profile":
        return {**PROFILE, "core_skills": list(PROFILE["core_skills"])}
    return {"jobs": [{"job_index": index, "required_skills": ["SQL", "Python", "Tableau"],
                      "min_education": "Not specified", "certifications_required": [], "work_arrangement": "Onsite",
                      "skill_matches": [{"job_skill": "SQL", "candidate_skill": "SQL"},
                                        {"job_skill": "Python", "candidate_skill": "Python"}],
                      "suitability_reason": "Matches SQL and Python."} for index in range(len(record["jobs"]))]}


def searching(tmp_path, **options) -> tuple[IOManager, Parts]:
    requests_made.clear()
    return make_io(tmp_path, responder=options.pop("responder", respond),
                   listings=options.pop("listings", [listing(1), listing(2), listing(3)]), **options)


def test_a_search_goes_through_all_four_managers_to_the_results(tmp_path):
    io, parts = searching(tmp_path)
    task_id = io.start_search(FORM)
    progress = io.search_progress(task_id)
    assert progress["status"] == "done" and progress["percent"] == 100 and progress["kind"] == "search"
    page = io.search_results(task_id)
    assert page["search_id"] == task_id and page["profile"]["candidate_name"] == "Sample Candidate"
    assert sorted(job["title"] for job in page["jobs"]) == ["Job 1", "Job 2", "Job 3"]
    assert page["jobs"][0]["salary_text"] == "$4,000 - $6,000 monthly" and page["jobs"][0]["title_text"] == "Job 1"
    assert page["checked"] == 3 and page["save_failed"] is False and page["newly_saved"] == 3
    assert page["show"] == 5 and page["filters"]["job_type"] == "Any" and "show" not in page["filters"]
    assert page["skill_gaps"] and page["step_message"] == "" and page["partial_message"] == ""
    assert len(parts.data.read_saved_jobs()) == 3


# Searching, reading the postings and reading the requirements each have their own part of the progress bar, and
# the two searches per title (at the candidate's level, and without) report once: every percent is at least the one
# before
def test_the_progress_of_a_search_only_moves_forward(tmp_path):
    io, _ = searching(tmp_path, listings=[listing(number, description="") for number in range(1, 4)])
    tasks = io._IOManager__tasks
    reported: list[tuple[int, str]] = []
    real_update = tasks.update
    tasks.update = lambda task_id, percent, message: reported.append((percent, message)) or real_update(
        task_id, percent, message)
    io.start_search(FORM)
    percents = [percent for percent, _ in reported]
    assert percents == sorted(percents) and percents[-1] == 94
    messages = [message for _, message in reported]
    for stage in ("Searching MyCareersFuture...", "Reading the job postings...", "Reading job requirements..."):
        assert stage in messages, stage
    assert not any(re.search(r"\d+ of \d+", message) for message in messages)  # the bar shows progress, not counts


def test_an_uploaded_resume_is_saved_and_used(tmp_path):
    io, _ = searching(tmp_path)
    with open(os.path.join(PROJECT_ROOT, "resumes", SAMPLE), "rb") as file:
        data = file.read()
    task_id = io.start_search({**FORM, "resume_choice": "", "resume_filename": "My CV.docx"}, data)
    assert io.search_progress(task_id)["status"] == "done"
    assert "My_CV.docx" in io.resume_choices()


@pytest.mark.parametrize("changes, message", [
    ({"min_salary": "lots"}, "Minimum salary must be a whole number"),
    ({"resume_choice": "../.env"}, "Please choose a resume or upload one."),
    ({"resume_choice": ""}, "Please choose a resume or upload one."),
    ({"resume_choice": "", "resume_filename": "notes.txt"}, "Please upload a PDF or Word (.docx) file."),
    ({"resume_choice": "", "resume_filename": "empty.pdf"}, "That file is empty."),
])
def test_bad_input_is_a_value_error_with_the_message_and_no_task(tmp_path, changes, message):
    io, _ = searching(tmp_path)
    with pytest.raises(ValueError, match=re.escape(message)):
        io.start_search({**FORM, **changes}, b"")
    assert requests_made == []


def test_a_rejected_upload_is_not_left_in_the_folder(tmp_path):
    io, _ = searching(tmp_path)
    short = Document()
    short.add_paragraph("Too short.")
    path = tmp_path / "short.docx"
    short.save(str(path))
    with pytest.raises(ValueError, match="Could not read enough text"):
        io.start_search({**FORM, "resume_choice": "", "resume_filename": "short.docx"}, path.read_bytes())
    assert io.resume_choices() == [SAMPLE]


def test_an_upload_that_cannot_be_saved_is_a_message(tmp_path, monkeypatch):
    io, parts = searching(tmp_path)
    monkeypatch.setattr("io_layer.resume_files.ResumeFiles.save_upload", lambda self, data, name: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(ValueError, match="Could not save your resume on the server"):
        io.start_search({**FORM, "resume_choice": "", "resume_filename": "cv.docx"}, b"x")


def test_an_ai_that_cannot_read_the_resume_ends_the_search_with_a_message(tmp_path):
    io, parts = searching(tmp_path, responder=lambda record, task: None)
    progress = io.search_progress(io.start_search(FORM))
    assert progress["status"] == "error" and progress["error"].startswith("The AI couldn't read your resume")
    assert "API key" not in progress["error"] and parts.data.read_saved_jobs() == []  # nothing is known to be wrong with the key, so it isn't blamed


def no_key(monkeypatch) -> None:
    """Plays an AI whose key is missing (AIManager.setup_problem, as GeminiProvider reports it)."""
    monkeypatch.setattr(ScriptedAIManager, "setup_problem", property(lambda self: "GEMINI_API_KEY is not set"))


def test_a_missing_key_says_what_to_fix_instead_of_blaming_the_connection(tmp_path, monkeypatch):
    io, _ = searching(tmp_path, responder=lambda record, task: None)
    no_key(monkeypatch)
    error = io.search_progress(io.start_search(FORM))["error"]
    assert error == ("The AI isn't set up: GEMINI_API_KEY is not set. Put a valid key in the .env file "
                     "(see .env.example), then restart the app.")


def test_a_form_sent_back_for_a_wrong_filter_leaves_no_copy_of_its_upload(tmp_path):
    io, parts = searching(tmp_path)
    with open(os.path.join(parts.folder, SAMPLE), "rb") as file:
        data = file.read()
    with pytest.raises(ValueError, match="Minimum salary must be a whole number"):
        io.start_search({**FORM, "min_salary": "lots", "resume_choice": "", "resume_filename": "My CV.docx"}, data)
    assert io.resume_choices() == [SAMPLE]
    assert parts.data.read_saved_jobs() == []


def test_a_crash_inside_the_search_becomes_an_error_message(tmp_path):
    def broken(record, task):
        raise RuntimeError("boom")
    io, _ = searching(tmp_path, responder=broken)
    progress = io.search_progress(io.start_search(FORM))
    # A plain message for the user; the error's type goes to the log only
    assert progress["status"] == "error" and progress["error"] == "Something went wrong during the search. Please try again."
    assert "RuntimeError" not in progress["error"]


def test_a_portal_that_is_down_and_one_that_lists_nothing_are_told_apart(tmp_path):
    down, _ = searching(tmp_path / "a", listings=None)
    assert "Could not get any job listings from MyCareersFuture" in down.search_progress(down.start_search(FORM))["error"]
    empty, _ = searching(tmp_path / "b", listings=[])
    assert "has no listings for your resume's job titles" in empty.search_progress(empty.start_search(FORM))["error"]


def test_listings_the_ai_could_not_analyse_end_the_search_with_a_message(tmp_path):
    io, _ = searching(tmp_path, responder=lambda record, task: respond(record, task) if task == "candidate_profile" else None)
    assert "We found 3 job listings but couldn't read them" in io.search_progress(io.start_search(FORM))["error"]


def test_a_search_that_had_to_drop_a_preference_says_so_and_keeps_what_was_typed(tmp_path):
    io, _ = searching(tmp_path)
    page = io.search_results(io.start_search({**FORM, "max_salary": "1000"}))  # every listing pays more
    assert "No jobs matched your preferences" in page["step_message"] and page["filters"]["max_salary"] == 1000


def test_a_failed_save_still_gives_the_results_with_a_flag(tmp_path):
    io, parts = searching(tmp_path)
    parts.storage.fail_writes = True
    page = io.search_results(io.start_search(FORM))
    assert page["save_failed"] is True and page["newly_saved"] == 0 and len(page["jobs"]) == 3


def test_show_decides_how_many_jobs_are_listed(tmp_path):
    io, _ = searching(tmp_path, listings=[listing(number) for number in range(1, 9)])
    assert len(io.search_results(io.start_search({**FORM, "show": "10"}))["jobs"]) == 8
    assert len(io.search_results(io.start_search({**FORM, "show": "7"}))["jobs"]) == 5  # not offered: the default


def test_update_matches_reuses_the_profile_and_the_resume(tmp_path):
    io, _ = searching(tmp_path)
    first = io.start_search(FORM)
    reads = requests_made.count("candidate_profile")
    second = io.start_search({"from_search": first, "job_type": "Full Time", "work_arrangement": "Any", "show": "10"})
    assert requests_made.count("candidate_profile") == reads  # the resume was not read again
    page = io.search_results(second)
    assert page["show"] == 10 and page["filters"]["job_type"] == "Full Time" and page["profile"]["candidate_name"]
    with pytest.raises(ValueError, match="must be a whole number"):
        io.start_search({"from_search": first, "min_salary": "lots"})


def test_a_search_starts_in_the_background_and_returns_its_id_at_once(tmp_path):
    started: list = []
    io, _ = searching(tmp_path, spawn=lambda work, *args, **kwargs: started.append((work, args, kwargs)))
    task_id = io.start_search(FORM)
    assert len(started) == 1 and io.search_progress(task_id)["status"] == "running"
    assert io.search_results(task_id) == {}  # not finished yet
    work, args, kwargs = started[0]
    work(*args, **kwargs)
    assert io.search_progress(task_id)["status"] == "done"


def test_an_unknown_task_has_a_message_and_no_results(tmp_path):
    io, _ = searching(tmp_path)
    assert io.search_progress("nope") == {
        "status": "missing", "percent": 0, "message": "",
        "error": "This search is no longer available. Please start a new one.", "kind": "", "job_url": ""}
    assert io.search_results("nope") == {}
    io.cancel_search("nope")  # nothing to cancel, and no error


# ---------- the resume files ----------

def write_docx(path: str, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


@pytest.mark.parametrize("name, content", [("empty.pdf", b""), ("broken.docx", b"not really a Word file"),
                                           ("scan.pdf", b"%PDF-1.4 nothing readable")])
def test_a_saved_resume_that_cannot_be_read_is_a_message_not_a_crash(tmp_path, name, content):
    io, parts = searching(tmp_path)
    with open(os.path.join(parts.folder, name), "wb") as file:
        file.write(content)
    with pytest.raises(ValueError, match="Could not read enough text from that file"):
        io.start_search({**FORM, "resume_choice": name})


def test_a_resume_with_too_little_text_is_rejected_and_a_real_one_is_read(tmp_path):
    io, parts = searching(tmp_path)
    write_docx(os.path.join(parts.folder, "short.docx"), "Too short to be a resume.")
    with pytest.raises(ValueError, match="Could not read enough text"):
        io.start_search({**FORM, "resume_choice": "short.docx"})
    assert io.search_progress(io.start_search({**FORM, "resume_choice": SAMPLE}))["status"] == "done"


def test_an_upload_never_replaces_a_saved_resume(tmp_path):
    io, parts = searching(tmp_path)
    with open(os.path.join(parts.folder, SAMPLE), "rb") as file:
        data = file.read()
    changed = Document(os.path.join(parts.folder, SAMPLE))
    changed.add_paragraph("A new line, so this upload differs from the saved file of the same name.")
    upload = BytesIO()
    changed.save(upload)
    io.start_search({**FORM, "resume_choice": "", "resume_filename": SAMPLE}, upload.getvalue())
    assert io.resume_choices() == [SAMPLE, "sample_resume_fintech_2.docx"]
    with open(os.path.join(parts.folder, SAMPLE), "rb") as file:
        assert file.read() == data


def test_uploading_the_same_file_again_reuses_it_instead_of_adding_a_copy(tmp_path):
    io, parts = searching(tmp_path)
    with open(os.path.join(parts.folder, SAMPLE), "rb") as file:
        data = file.read()
    for _ in range(2):
        task_id = io.start_search({**FORM, "resume_choice": "", "resume_filename": SAMPLE}, data)
        assert io.search_progress(task_id)["status"] == "done"
    assert io.resume_choices() == [SAMPLE]


def test_an_unreadable_upload_that_matches_a_saved_file_does_not_delete_that_file(tmp_path):
    io, parts = searching(tmp_path)
    short = Document()
    short.add_paragraph("Too short.")
    short.save(os.path.join(parts.folder, "short.docx"))
    with open(os.path.join(parts.folder, "short.docx"), "rb") as file:
        data = file.read()
    with pytest.raises(ValueError, match="Could not read enough text"):
        io.start_search({**FORM, "resume_choice": "", "resume_filename": "short.docx"}, data)
    assert "short.docx" in io.resume_choices()  # the user's own file stays; only a new upload is removed


@pytest.mark.parametrize("name", ["简历.docx", "a" * 300 + ".docx", "Résumé Tan.DOCX"])
def test_unusual_upload_names_keep_their_extension_and_are_shortened(tmp_path, name):
    io, parts = searching(tmp_path)
    with open(os.path.join(parts.folder, SAMPLE), "rb") as file:
        data = file.read()
    io.start_search({**FORM, "resume_choice": "", "resume_filename": name}, data)
    assert all(len(saved) < 100 for saved in io.resume_choices()) and len(io.resume_choices()) == 2
    assert all(saved.lower().endswith(".docx") for saved in io.resume_choices())


# ---------- tailoring a resume ----------

TITLES = ["Data Analyst", "Business Analyst", "Bank Teller"]
DETAILS = {"description": "Use SQL", "listed_skills": ["SQL"], "is_open": True}


def job_link(number: int) -> str:
    return listing(number)["job_url"]


def tailoring(tmp_path, **options) -> tuple[IOManager, Parts, str]:
    """An IOManager that has already run a search with the web test's AI, and the search's id."""
    ai_requests.clear()
    io, parts = make_io(tmp_path, responder=fake_ai, details=DETAILS,
                        listings=[listing(number, title=title) for number, title in enumerate(TITLES)], **options)
    return io, parts, io.start_search(FORM)
