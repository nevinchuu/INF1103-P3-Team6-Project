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


def test_a_word_resume_is_tailored_worded_downloaded_and_a_copy_kept(tmp_path):
    io, parts, search_id = tailoring(tmp_path)
    task_id = io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE})
    progress = io.search_progress(task_id)
    assert progress["status"] == "done" and progress["kind"] == "tailor" and progress["job_url"] == job_link(0)
    page, docx_bytes = io.tailored_resume(task_id)
    assert 'Reworded "' in page["changes"][0] and "leads with SQL" in page["changes"][0]
    assert page["search_id"] == task_id and page["resume_name"] == SAMPLE and page["had_description"] is True
    assert page["job"]["title_text"] == "Data Analyst" and "docx_bytes" not in page
    assert page["download_name"] == "sample_resume_fintech_Data_Analyst.docx"
    assert [match["same_name"] for match in page["matched"]] == [True, True]
    assert any("Wrote SQL reports" in paragraph.text for paragraph in Document(BytesIO(docx_bytes)).paragraphs)
    assert page["saved_as"] == os.path.join(parts.folder, "tailored", "sample_resume_fintech_Data_Analyst.docx")
    assert os.path.exists(page["saved_as"])


def test_tailoring_another_resume_words_what_the_safety_check_removed(tmp_path):
    io, parts, search_id = tailoring(tmp_path)
    with open(os.path.join(parts.folder, "alex_tan.pdf"), "wb") as file:
        file.write(tiny_pdf(PDF_RESUME_LINES))
    task_id = io.start_tailoring(search_id, job_link(0), {"resume_choice": "alex_tan.pdf"})
    task, record = ai_requests[-1]
    assert task == "tailored_resume" and record["job"]["matched_skills"] == []
    page, _ = io.tailored_resume(task_id)
    assert page["matched_with"] == SAMPLE and "Aimed the resume at data work" in page["changes"]
    assert any("the new wording added SQL, which isn't in your resume" in note for note in page["notes"])


def test_tailoring_without_a_chosen_resume_uses_the_one_that_found_the_job(tmp_path):
    io, _, search_id = tailoring(tmp_path)
    task_id = io.start_tailoring("", job_link(1))  # a job opened from Saved jobs
    task, record = ai_requests[-1]
    assert task == "docx_edits" and [match["job_skill"] for match in record["job"]["matched_skills"]] == ["SQL", "Python"]
    assert io.search_progress(task_id)["status"] == "done"


def test_a_second_click_follows_the_first_tailoring_run(tmp_path):
    held: list = []
    waiting, _ = make_io(tmp_path, responder=fake_ai, listings=[listing(0)],
                         spawn=lambda work, *args, **kwargs: held.append((work, args, kwargs)))
    search = waiting.start_search(FORM)
    work, args, kwargs = held.pop()
    work(*args, **kwargs)  # the search finishes
    first = waiting.start_tailoring(search, job_link(0), {"resume_choice": SAMPLE})
    second = waiting.start_tailoring(search, job_link(0), {"resume_choice": SAMPLE})
    assert first == second and len(held) == 1 and waiting.search_progress(first)["status"] == "running"


def test_the_ai_failing_to_tailor_is_a_message_not_a_crash(tmp_path):
    io, parts, search_id = tailoring(tmp_path)
    parts.ai._responder = lambda record, task: None  # the AI stops answering
    task_id = io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE})
    progress = io.search_progress(task_id)
    assert progress["status"] == "error" and "The AI couldn't tailor your resume" in progress["error"]
    assert io.tailored_resume(task_id) == ({}, b"")


def test_tailoring_with_a_missing_key_says_what_to_fix(tmp_path, monkeypatch):
    io, parts, search_id = tailoring(tmp_path)
    parts.ai._responder = lambda record, task: None
    no_key(monkeypatch)
    task_id = io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE})
    assert io.search_progress(task_id)["error"].startswith("The AI isn't set up: GEMINI_API_KEY is not set.")


def test_a_crash_while_tailoring_becomes_an_error_message(tmp_path):
    io, parts, search_id = tailoring(tmp_path)

    def broken(record, task):
        raise RuntimeError("boom")
    parts.ai._responder = broken
    progress = io.search_progress(io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE}))
    assert progress["status"] == "error" and progress["error"] == "Something went wrong while tailoring. Please try again."


def test_a_cancelled_tailoring_run_stops(tmp_path):
    held: list = []
    io, _ = make_io(tmp_path, responder=fake_ai, details=DETAILS, listings=[listing(0)],
                    spawn=lambda work, *args, **kwargs: held.append((work, args, kwargs)))
    held_search = io.start_search(FORM)
    work, args, kwargs = held.pop()
    work(*args, **kwargs)
    task_id = io.start_tailoring(held_search, job_link(0), {"resume_choice": SAMPLE})
    io.cancel_search(task_id)
    work, args, kwargs = held.pop()
    work(*args, **kwargs)
    assert io.search_progress(task_id)["status"] == "cancelled" and io.tailored_resume(task_id) == ({}, b"")


def test_a_copy_that_cannot_be_written_is_only_logged(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)
    monkeypatch.setattr("io_layer.resume_files.ResumeFiles.save_tailored",
                        lambda self, data, name: (_ for _ in ()).throw(OSError("full")))
    page, _ = io.tailored_resume(io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE}))
    assert page["saved_as"] == "" and page["changes"]


def test_tailoring_needs_a_known_job_and_a_usable_resume(tmp_path):
    io, _, search_id = tailoring(tmp_path)
    with pytest.raises(LookupError, match="no longer in your saved jobs or recent searches"):
        io.start_tailoring("", "https://x/none", {"resume_choice": SAMPLE})
    with pytest.raises(ValueError, match="Please choose a resume or upload one."):
        io.start_tailoring(search_id, job_link(0), {"resume_choice": "../.env"})
    with pytest.raises(ValueError, match="Please upload a PDF or Word"):
        io.start_tailoring(search_id, job_link(0), {"resume_choice": "", "resume_filename": "notes.txt"}, b"x")
    assert io.tailoring_form("https://x/none") is None


def test_a_job_is_found_in_saved_jobs_or_a_recent_search_even_when_the_database_is_down(tmp_path):
    io, parts, search_id = tailoring(tmp_path)
    form = io.tailoring_form(job_link(0))
    assert form["job"]["title_text"] == "Data Analyst" and form["resumes"] == [SAMPLE]
    assert form["chosen"] == SAMPLE and form["ai_name"] == "Fake AI"
    parts.storage.fail_reads = True  # saved jobs can't be opened, but the recent search still has the job
    assert io.tailoring_form(job_link(0))["job"]["title"] == "Data Analyst"
    assert io.search_progress(io.start_tailoring("", job_link(0), {"resume_choice": SAMPLE}))["status"] == "done"
    assert io.tailoring_form("https://x/none") is None


def test_the_job_comes_from_the_search_it_was_found_in_first(tmp_path):
    io, parts, search_id = tailoring(tmp_path)
    parts.data.update_saved_job(job_link(0), note="saved copy")
    page, _ = io.tailored_resume(io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE}))
    assert "note" not in page["job"]  # the search's own record, not the saved copy


def test_a_task_of_the_other_kind_is_not_a_tailored_resume_or_a_results_page(tmp_path):
    io, _, search_id = tailoring(tmp_path)
    task_id = io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE})
    assert io.tailored_resume(search_id) == ({}, b"") and io.tailored_resume("nope") == ({}, b"")
    assert io.search_results(task_id) == {} and io.what_if(task_id, {}) == {} and io.roadmap(task_id, {}) == {}


def test_a_tailored_resume_is_worded_in_sentences(tmp_path, monkeypatch):
    """Every kind of change and note the tailors report, as the sentence the page shows."""
    io, _, search_id = tailoring(tmp_path)
    notes = [{"kind": "section_number_not_in_resume", "heading": "Awards"},
             {"kind": "entry_not_in_resume", "title": "Invented Corp"},
             {"kind": "section_adds_missing_skills", "heading": "Skills", "skills": ["Scala", "Java"]},
             {"kind": "bullet_adds_missing_skills", "title": "Cashier", "skills": ["Go"]},
             {"kind": "bullets_number_not_in_resume", "title": "Cashier", "count": 2},
             {"kind": "edit_number_not_in_resume", "start": "Built dashboards"},
             {"kind": "edit_adds_missing_skills", "start": "Built dashboards", "skills": ["Scala", "Java"]},
             {"kind": "bullet_group_kept", "heading": "Cashier"},
             {"kind": "edit_too_long", "start": "Built dashboards"},
             {"kind": "edit_adds_qualification", "start": "Diploma in Financial", "qualifications": ["Degree"]},
             {"kind": "section_adds_qualification", "heading": "Profile", "qualifications": ["Degree", "Bachelor"]},
             {"kind": "bullet_adds_qualification", "title": "Cashier", "qualifications": ["MBA"]},
             {"kind": "sentences_checked", "where": "Profile", "is_point": False, "kept_original": 2, "removed": 1,
              "skills": ["Tuition"], "qualifications": ["Degree"], "number": True},
             {"kind": "sentences_checked", "where": "Cashier", "is_point": True, "kept_original": 1, "removed": 0,
              "skills": ["Go"], "qualifications": [], "number": False},
             {"kind": "edit_recombines", "start": "Built Power BI dashboards", "tools": ["Tableau"], "words": []},
             {"kind": "edit_recombines", "start": "Wrote SQL queries", "tools": [], "words": ["complex", "strong"]},
             {"kind": "sentences_checked", "where": "Teacher", "is_point": True, "kept_original": 1, "removed": 0,
              "skills": [], "qualifications": [], "number": False, "tools": ["SQL", "Git"], "words": ["mastery"]}]
    changes = [{"kind": "reworded", "start": "Built dashboards", "reason": "leads with SQL"},
               {"kind": "removed", "start": "x" * 50 + "...", "reason": ""},
               {"kind": "layout_durations"}, {"kind": "layout_dated", "count": 2},
               "Aimed the summary at data work"]  # a PDF resume's changes are already sentences

    def canned(self, job, resume_path, resume_text, on_progress=None):
        return {"job": job, "resume_name": SAMPLE, "matched_with": None, "resume": {}, "paragraphs": [],
                "docx_bytes": b"docx", "changes": changes, "not_added": [], "notes": notes, "had_description": True,
                "is_open": True, "required": [], "matched": [], "shown": {}}
    monkeypatch.setattr("io_layer.tailoring_service.TailoringService.tailor", canned)
    page, _ = io.tailored_resume(io.start_tailoring(search_id, job_link(0), {"resume_choice": SAMPLE}))
    assert page["changes"] == [
        'Reworded "Built dashboards": leads with SQL', f'Removed "{"x" * 50}..."',
        'Tidied the layout: removed the length of time after the dates, e.g. "(6 months)".',
        "Tidied the layout: put the year of 2 certification(s) or award(s) on the right, like your other entries.",
        "Aimed the summary at data work"]
    assert page["notes"] == [
        'The "Awards" section was left out because it had a number that isn\'t in your resume.',
        'Left out "Invented Corp": it isn\'t in your resume.',
        'The "Skills" section was left out because it added Scala, Java, which isn\'t in your resume.',
        'Left out a point under "Cashier" that added Go, which isn\'t in your resume.',
        'Left out 2 point(s) under "Cashier" with numbers that aren\'t in your resume.',
        'Kept "Built dashboards..." as it was: the new wording had a number that isn\'t in your resume.',
        'Kept "Built dashboards..." as it was: the new wording added Scala, Java, which isn\'t in your resume.',
        'Kept every point under "Cashier": removing them all would leave it empty.',
        'Kept "Built dashboards..." as it was: the new wording was longer and could push your resume onto another page.',
        'Kept "Diploma in Financial..." as it was: the new wording mentioned "Degree", a qualification your resume '
        "doesn't list.",
        'The "Profile" text was left out because it mentioned "Degree", "Bachelor", a qualification your resume '
        "doesn't list.",
        'Left out a point under "Cashier" that mentioned "MBA", a qualification your resume doesn\'t list.',
        'In "Profile", kept your own wording for 2 sentences and left out 1 sentence: the new wording added Tuition, '
        'which isn\'t in your resume; mentioned "Degree", a qualification your resume doesn\'t list; had a number '
        "that isn't in your resume.",
        'In "Cashier", kept your own wording for 1 point: the new wording added Go, which isn\'t in your resume.',
        'Kept "Built Power BI dashboards..." as it was: the new wording put Tableau into work your resume doesn\'t '
        "use it for.",
        'Kept "Wrote SQL queries..." as it was: the new wording made it sound stronger (“complex”, “strong”).',
        'In "Teacher", kept your own wording for 1 point: the new wording put SQL, Git into work your resume '
        "doesn't use them for; made it sound stronger (“mastery”)."]


# ---------- what if I learned... ----------

def whatif_form(**changes) -> dict:
    return {"skills": ["Tableau"], "steps": "3", "top_n": "2", **changes}


def test_the_whatif_form_lists_the_skills_to_learn(tmp_path):
    io, _, search_id = tailoring(tmp_path)
    page = io.what_if(search_id, {})
    assert page["skills_available"] == ["Tableau"] and page["job_count"] == 3 and page["mode"] == "test"
    assert page["report"] is None and page["roadmap"] is None and page["problem"] == "" and page["steps"] == 3
    assert page["top_n"] == 5 and page["max_steps"] == 10 and page["chosen"] == []
    assert page["verdict"] is None and page["roadmap_steps"] == []
    assert page["planners"] == [("greedy", "Rank higher"), ("salary", "Earn more")]
    assert page["you_have"] and page["comparison"] is None
    # What the answer is based on: each searched title with how many of the jobs it found
    assert page["based_on"] is None or sum(count for _, count in page["based_on"]["titles"]) == page["job_count"]
    # Each skill comes with its own effect, worked out before any click: more strong matches, and jobs asking for it
    assert [(item["skill"], set(item)) for item in page["skill_impact"]] == [
        ("Tableau", {"skill", "order", "jobs", "strong_gain"})]
    assert page["skill_impact"][0]["jobs"] >= 1


def test_testing_a_skill_gives_a_report_and_its_verdict(tmp_path):
    io, _, search_id = tailoring(tmp_path)
    page = io.what_if(search_id, whatif_form())
    assert page["problem"] == "" and page["chosen"] == ["Tableau"] and page["top_n"] == 2
    assert page["report"]["skills"] == ["Tableau"] and page["roadmap"] is None
    assert page["verdict"]["tier"] in ("high", "some", "none") and page["verdict"]["effect"]
    assert page["roadmap_steps"] == []


def test_extra_typed_skills_are_used_too(tmp_path):
    io, _, search_id = tailoring(tmp_path)
    page = io.what_if(search_id, {"skills": [], "extra_skills": "tableau", "top_n": "5"})
    assert page["report"]["skills"] == ["Tableau"] and page["extra_skills"] == "tableau"


def test_a_roadmap_gives_its_steps_and_the_result_of_all_of_them(tmp_path):
    io, _, search_id = tailoring(tmp_path)
    page = io.roadmap(search_id, {"mode": "greedy", "steps": "1", "top_n": "5"})
    assert page["mode"] == "greedy" and page["roadmap"]["planner_label"] == "Rank higher"
    assert page["report"]["skills"] == ["Tableau"] and [step["skill"] for step in page["roadmap_steps"]] == ["Tableau"]
    assert page["verdict"] is not None


@pytest.mark.parametrize("form, message", [
    ({"skills": [], "top_n": "5"}, "at least one skill"),
    ({"skills": ["Cobol"], "top_n": "5"}, "None of these jobs asks for"),
    ({"skills": ["Tableau"], "extra_skills": "tableau", "top_n": "5"}, "more than once"),
    ({"skills": ["Tableau"], "top_n": ""}, "whole number"),
    ({"skills": ["Tableau"], "top_n": "0"}, "at least 1 job"),
])
def test_bad_whatif_forms_get_a_message_not_an_error(tmp_path, form, message):
    io, _, search_id = tailoring(tmp_path)
    page = io.what_if(search_id, form)
    assert message in page["problem"] and page["report"] is None


@pytest.mark.parametrize("form, message", [
    ({"mode": "greedy", "skills": ["Tableau"], "steps": "many", "top_n": "5"}, "whole numbers"),
    ({"mode": "greedy", "steps": "99", "top_n": "5"}, "1 to 10 skills"),
    ({"mode": "nonsense", "steps": "3", "top_n": "5"}, "Unknown planner"),
])
def test_bad_roadmap_forms_get_a_message_not_an_error(tmp_path, form, message):
    io, _, search_id = tailoring(tmp_path)
    assert message in io.roadmap(search_id, form)["problem"]


def test_the_whatif_ranks_only_the_jobs_the_searchs_filters_kept(tmp_path):
    io, _ = make_io(tmp_path, responder=fake_ai, listings=[listing(0), listing(1, employment_types=["Part Time"],
                                                                              title="Part Time Clerk")])
    search_id = io.start_search({**FORM, "job_type": "Full Time"})
    page = io.what_if(search_id, whatif_form(top_n="5"))
    assert page["job_count"] == 1 and "Part Time Clerk" not in str(page["report"])


def test_a_whatif_never_changes_the_saved_jobs(tmp_path):
    io, parts, search_id = tailoring(tmp_path)
    before = parts.data.read_saved_jobs()
    io.what_if(search_id, whatif_form())
    io.roadmap(search_id, {"mode": "greedy", "steps": "2", "top_n": "5"})
    assert parts.data.read_saved_jobs() == before


def has_every_skill(record: dict, task: str) -> dict | None:
    """The candidate has every skill the jobs ask for."""
    answer = respond(record, task)
    if task == "candidate_profile":
        answer["core_skills"].append("Tableau")
    if task == "job_requirements":
        for job in answer["jobs"]:
            job["skill_matches"] = [{"job_skill": skill, "candidate_skill": skill} for skill in job["required_skills"]]
    return answer


def test_the_whatif_says_so_when_there_is_nothing_to_learn(tmp_path):
    io, _ = make_io(tmp_path, responder=has_every_skill, listings=[listing(1)])
    search_id = io.start_search(FORM)
    page = io.what_if(search_id, {})
    assert page["skills_available"] == [] and page["job_count"] == 1 and page["skill_impact"] == []
    plan = io.roadmap(search_id, {"mode": "greedy", "steps": "3", "top_n": "5"})
    assert plan["roadmap"]["is_empty"] and plan["roadmap_steps"] == []


def whatif_move(**changes) -> dict:
    return {"title": "SENIOR DATA ANALYST", "company": "A", "rank_before": 2, "rank_after": 1, "listed_before": True,
            "listed_after": True, "places_gained": 1, "moved_up": True, "outcome_before": "Fair match",
            "outcome_after": "Strong match", "outcome_upgraded": True, "skills_gained": ["Tableau"], "salary": 5000,
            **changes}


def whatif_report(**changes) -> dict:
    movement = whatif_move()
    return {"skills": ["Tableau"], "top_n": 2, "jobs_moved_up": 1, "places_gained": 1, "outcome_upgrades": 1,
            "movements": [movement], "entered_top": [movement], "left_top": [{**movement, "title": "BANK TELLER"}],
            **changes}


# A job that only moved down because others passed it, and one that stayed where it was
PUSHED = whatif_move(title="BANK TELLER", rank_before=1, rank_after=3, places_gained=-2, moved_up=False,
                     outcome_before="Stretch", outcome_after="Stretch", outcome_upgraded=False, skills_gained=[])
STAYED = {**PUSHED, "title": "CASHIER", "rank_before": 4, "rank_after": 4, "places_gained": 0}


def test_the_whatif_verdict_says_what_the_page_says(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.what_if", lambda self, *args, **kwargs: whatif_report())
    page = io.what_if(search_id, whatif_form())
    verdict = page["verdict"]
    assert (verdict["tier"], verdict["badge"]) == ("high", "Big boost")
    assert verdict["effect"] == "turns 1 more job into strong matches and puts 1 new job in your top 2"
    assert (verdict["strong_before"], verdict["strong_after"], verdict["more_strong"]) == (0, 1, 1)
    assert verdict["entered_top"] == ["Senior Data Analyst"] and verdict["left_top"] == ["Bank Teller"]  # tidied
    assert [move["title"] for move in verdict["top"]] == ["Senior Data Analyst"]
    assert verdict["below"] == verdict["others"] == [] and verdict["others_line"] == ""
    assert page["report"]["movements"][0]["title"] == "Senior Data Analyst"  # titles come tidied
    # The same report judged by the plan goals: places gained, and the top list's average pay
    assert (verdict["rank_tier"], verdict["rank_effect"]) == (
        "high", "moves 1 job up 1 place and puts 1 new job in your top 2")
    assert (verdict["pay_before"], verdict["pay_after"], verdict["best_pay_after"]) == (5000, 5000, 5000)
    assert (verdict["pay_tier"], verdict["pay_effect"]) == (
        "some", "doesn't raise the average pay of your top 2 ($5,000 a month)")


def test_a_pay_rise_is_the_pay_goals_verdict(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)
    risen = whatif_report(movements=[whatif_move(salary=8000), whatif_move(title="TELLER", rank_before=1, rank_after=3,
                                                                           places_gained=-2, moved_up=False,
                                                                           outcome_upgraded=False, salary=4000)])
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.what_if", lambda self, *args, **kwargs: risen)
    verdict = io.what_if(search_id, whatif_form())["verdict"]
    assert (verdict["pay_before"], verdict["pay_after"]) == (6000, 8000)
    assert (verdict["pay_tier"], verdict["pay_effect"]) == (
        "high", "raises the average pay of your top 2 from $6,000 to $8,000 a month and puts 1 new job in your top 2")


@pytest.mark.parametrize("report, tier, effect", [
    (whatif_report(jobs_moved_up=2, places_gained=3, outcome_upgrades=2, entered_top=[], left_top=[],
                   movements=[whatif_move()] * 2), "high", "turns 2 more jobs into strong matches"),
    (whatif_report(outcome_upgrades=0, entered_top=[], left_top=[],
                   movements=[whatif_move(outcome_before="Strong match", outcome_upgraded=False)]),
     "some", "moves 1 job up, but adds no strong matches"),
    (whatif_report(jobs_moved_up=0, places_gained=0, entered_top=[], left_top=[],
                   movements=[whatif_move(places_gained=0, moved_up=False, outcome_before="Stretch",
                                          outcome_after="Fair match")]),
     "some", "improves 1 match, but adds no strong matches"),
    (whatif_report(jobs_moved_up=0, places_gained=0, outcome_upgrades=0, entered_top=[], left_top=[],
                   movements=[STAYED]), "none", "wouldn't change your matches"),
])
def test_the_whatif_verdict_has_three_tiers_and_pluralises(tmp_path, monkeypatch, report, tier, effect):
    io, _, search_id = tailoring(tmp_path)
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.what_if", lambda self, *args, **kwargs: report)
    verdict = io.what_if(search_id, whatif_form())["verdict"]
    assert (verdict["tier"], verdict["effect"]) == (tier, effect)


# Only the jobs that got better are listed; the knock-on drops and the jobs that stayed are summed up in one sentence
def test_the_jobs_that_only_moved_down_or_stayed_are_one_sentence(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)
    report = whatif_report(movements=[whatif_move(), PUSHED, STAYED])
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.what_if", lambda self, *args, **kwargs: report)
    verdict = io.what_if(search_id, whatif_form())["verdict"]
    assert [move["title"] for move in verdict["top"]] == ["Senior Data Analyst"] and verdict["below"] == []
    assert [move["title"] for move in verdict["others"]] == ["Bank Teller", "Cashier"]  # best new rank first
    assert verdict["others_line"] == "1 other job slipped down, 1 unchanged."


# The whole top list shows, even a job that didn't move (it may be the new best match); below it, only jobs that
# got better
def test_the_top_list_always_shows_in_full(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)
    still_first = whatif_move(title="STILL FIRST", rank_before=1, rank_after=1, places_gained=0, moved_up=False,
                              outcome_before="Strong match", outcome_upgraded=False)
    climbed = whatif_move(title="CLIMBED", rank_before=5, rank_after=3, places_gained=2)
    report = whatif_report(movements=[still_first, climbed, STAYED])
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.what_if", lambda self, *args, **kwargs: report)
    verdict = io.what_if(search_id, whatif_form())["verdict"]
    assert [move["title"] for move in verdict["top"]] == ["Still First"]  # top_n is 2
    assert [move["title"] for move in verdict["below"]] == ["Climbed"]
    assert [move["title"] for move in verdict["others"]] == ["Cashier"] and verdict["others_line"] == "1 unchanged."


# Skills are offered most helpful first, by what each one would do on its own
def test_the_skills_are_ranked_by_their_own_effect(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.learnable_skills", lambda self, jobs: ["Excel", "Tableau", "Go"])
    effects = {"Excel": whatif_report(movements=[STAYED]), "Tableau": whatif_report(movements=[whatif_move()] * 2),
               "Go": whatif_report(movements=[whatif_move()])}
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.what_if",
                        lambda self, profile, jobs, skills, top_n: effects[skills[0]])
    impact = io.what_if(search_id, {})["skill_impact"]
    assert [(item["skill"], item["strong_gain"], item["jobs"]) for item in impact] == [
        ("Tableau", 2, 2), ("Go", 1, 1), ("Excel", 0, 0)]


def test_the_roadmap_steps_say_what_each_one_adds(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)

    def paying(amount: int, places: int) -> dict:
        return whatif_report(places_gained=places, movements=[whatif_move(salary=amount)])
    steps = [{"skill": "Tableau", "skills": ["Tableau"], "places_gained": 1, "report": paying(5000, 1)},
             {"skill": "Excel", "skills": ["Excel"], "places_gained": 2, "report": paying(5600, 3)},
             {"skill": "Go + Rust", "skills": ["Go", "Rust"], "places_gained": 0, "report": paying(5600, 3)}]
    # The whole plan: the $5,000 job was in the top 2 before; the $5,600 job is first after
    whole = whatif_report(places_gained=3, movements=[
        whatif_move(salary=5000, rank_before=2, rank_after=3, places_gained=-1, moved_up=False),
        whatif_move(title="B", salary=5600, rank_before=3, rank_after=1, places_gained=2)])
    roadmap = {"planner_label": "Earn more", "is_empty": False, "steps": steps, "report": whole,
               "skills": ["Tableau", "Excel", "Go", "Rust"]}
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.roadmap", lambda self, *args, **kwargs: roadmap)
    page = io.roadmap(search_id, {"mode": "salary", "steps": "4", "top_n": "2"})
    # Pay starts from the top list before any step ($5,000), and each step says what it adds
    assert page["roadmap_steps"] == [
        {"skill": "Tableau", "together": False, "places": 1, "places_added": 1, "pay": 5000, "pay_added": 0},
        {"skill": "Excel", "together": False, "places": 3, "places_added": 2, "pay": 5600, "pay_added": 600},
        {"skill": "Go + Rust", "together": True, "places": 3, "places_added": 0, "pay": 5600, "pay_added": 0}]
    # The other goal's plan, for the one-line comparison (here the same fake plan)
    assert page["comparison"]["key"] == "greedy" and page["comparison"]["label"] == "Rank higher"
    assert page["comparison"]["steps"] == ["Tableau", "Excel", "Go + Rust"] and page["comparison"]["places"] == 3
    empty = {**roadmap, "is_empty": True, "steps": []}
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.roadmap", lambda self, *args, **kwargs: empty)
    assert io.roadmap(search_id, {"mode": "greedy", "steps": "3", "top_n": "2"})["roadmap_steps"] == []


def test_a_skill_the_simulator_rejects_becomes_the_problem(tmp_path, monkeypatch):
    io, _, search_id = tailoring(tmp_path)

    def refuse(self, *args, **kwargs):
        raise ValueError("You already have that skill.")
    monkeypatch.setattr("logic_layer.logic_manager.LogicManager.what_if", refuse)
    page = io.what_if(search_id, whatif_form())
    assert page["problem"] == "You already have that skill." and page["report"] is None
