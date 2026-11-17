"""Tests for the web app (src/web_app.py): its pages and actions, through all four managers.

Flask's test client sends the requests. The background work runs straight away (WebApp takes the way to start it as
an argument), MyCareersFuture's replies come from a stub, the AI's from ScriptedAIManager, and saved jobs and
uploaded resumes go to a temporary folder, so no browser, internet, API key or real data is needed.
"""

import io
import json
import logging
import os
import re
import shutil
from types import SimpleNamespace

import pytest
from docx import Document
from fakes import FlakyStorage, InMemoryStorage, ScriptedAIManager, StubHttp

from io_layer import io_manager
from data_layer import postgres_storage
import web_app
from data_layer.data_manager import DataManager
from io_layer.io_manager import IOManager
from data_layer.job_portal import JobPortal
from logic_layer.logic_manager import LogicManager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE_RESUME = "sample_resume_fintech.docx"  # the one resume kept in git, so this also runs in CI
LISTING_TITLES = ["Data Analyst", "Business Analyst", "Bank Teller"]
DEFAULT_FORM = {"resume_choice": SAMPLE_RESUME, "min_salary": "", "max_salary": "", "max_years_experience": "",
                "job_type": "Any", "work_arrangement": "Any"}
PROFILE = {"candidate_name": "Sample Candidate", "highest_qualification": "Diploma",
           "qualification_detail": "Diploma in Financial Technology", "years_of_experience": 1,
           "internship_months": 6, "seniority_level": "Fresh/entry level", "core_skills": ["SQL", "Python"],
           "certifications": [], "search_keywords": ["Data Analyst", "Business Analyst"]}


def job_link(number: int) -> str:
    return f"https://www.mycareersfuture.gov.sg/job/it/job-{number:032x}"


def portal_listing(number: int, title: str) -> dict:
    """One job listing in the shape MyCareersFuture's search API returns."""
    return {"title": title, "hiringCompany": {"name": f"Company {number}"},
            "salary": {"minimum": 4000, "maximum": 6000, "type": {"salaryType": "Monthly"}},
            "address": {"districts": [{"location": "Jurong"}]},
            "employmentTypes": [{"employmentType": "Full Time"}], "positionLevels": [{"position": "Executive"}],
            "minimumYearsExperience": 0, "flexibleWorkArrangements": [], "skills": [{"skill": "SQL"}],
            "description": "<p>SQL and Python reporting</p>", "metadata": {"jobDetailsUrl": job_link(number)}}


def fake_reply(payload: object) -> SimpleNamespace:
    """A stand-in for a requests reply."""
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)


ai_requests: list = []  # what the app sent the AI, newest last


def fake_ai(record: dict, task: str) -> dict:
    """The AI's replies: a profile, each listing's requirements, a rewritten PDF resume, and new wording
    for one Word paragraph. The PDF reply tries to add SQL, which the PDF resume never mentions."""
    ai_requests.append((task, record))
    if task == "tailored_resume":
        return {"name": "Alex Tan", "contact": "", "changes": ["Aimed the resume at data work"], "skill_evidence": [],
                "sections": [
                    {"heading": "PROFILE", "kind": "text", "text": "Cashier who writes SQL every day.",
                     "skill_groups": [], "entries": []},
                    {"heading": "EXPERIENCE", "kind": "entries", "text": "", "skill_groups": [], "entries": [
                        {"title": "Cashier", "organisation": "Example Cafe", "subtitle": "", "dates": "2023 to 2024",
                         "bullets": ["Served customers at the counter and handled cash payments",
                                     "Built SQL dashboards of daily sales"]}]}]}
    if task == "candidate_profile":
        return json.loads(json.dumps(PROFILE))
    if task == "job_requirements":
        return {"jobs": [{"job_index": index, "required_skills": ["SQL", "Python", "Tableau"],
                          "min_education": "Not specified", "certifications_required": [], "work_arrangement": "Onsite",
                          "skill_matches": [{"job_skill": "SQL", "candidate_skill": "SQL"},
                                            {"job_skill": "Python", "candidate_skill": "Python"}],
                          "suitability_reason": "Matches SQL and Python."} for index in range(len(record["jobs"]))]}
    bullet = next(paragraph for paragraph in record["paragraphs"]
                  if paragraph["kind"] == "bullet" and paragraph["editable"])
    return {"edits": [{"paragraph": bullet["id"], "text": "Wrote SQL reports", "reason": "leads with SQL"}],
            "skill_evidence": [{"job_skill": "SQL", "evidence": "Wrote SQL reports"}]}


class Web:
    """A WebApp wired to test doubles, with the pieces tests want to look at or break. Background work runs at once,
    unless holding is set: then it waits in `held` until a test runs it (a search or tailoring run still in progress)."""

    def __init__(self, folder: str, portal_status: int = 200, holding: bool = False) -> None:
        self.folder = folder
        resume_folder = os.path.join(folder, "resumes")
        os.makedirs(resume_folder)
        shutil.copy(os.path.join(PROJECT_ROOT, "resumes", SAMPLE_RESUME), resume_folder)
        listings = [portal_listing(number, title) for number, title in enumerate(LISTING_TITLES)]
        http = StubHttp(post=[({"results": listings}, portal_status)],
                        get=[{"description": "<p>Use SQL</p>", "skills": [{"skill": "SQL"}]}])
        portal = JobPortal(http=http, sleep=lambda seconds: None)
        self.ai = ScriptedAIManager(fake_ai)
        self.logic = LogicManager()
        self.storage = FlakyStorage(InMemoryStorage())
        self.data = DataManager(self.storage, portal)
        self.held: list = []
        self.holding = holding
        self.io = IOManager(self.ai, self.logic, self.data, resume_folder, spawn=self.__spawn)
        self.web = web_app.WebApp(self.io)
        self.client = self.web.app.test_client()

    def __spawn(self, work, *args, **kwargs) -> None:
        if self.holding:
            self.held.append((work, args, kwargs))
        else:
            work(*args, **kwargs)  # run now, not in a thread

    def run_held_work(self) -> None:
        """Runs the background work that was held back."""
        while self.held:
            work, args, kwargs = self.held.pop(0)
            work(*args, **kwargs)


@pytest.fixture
def web(tmp_path):
    ai_requests.clear()
    return Web(str(tmp_path))


def search(client, **form_changes: str) -> str:
    """Runs a search from the form; returns its id once it is done."""
    reply = client.post("/search", data={**DEFAULT_FORM, **form_changes})
    assert reply.status_code == 200, reply.get_json()
    search_id = reply.get_json()["search_id"]
    assert client.get(f"/progress/{search_id}").get_json()["status"] == "done"
    return search_id


def tiny_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with these lines of text (plain letters only), for a PDF resume in the tests."""
    stream = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               "/Resources << /Font << /F1 5 0 R >> >> >>",
               f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n{body}\nendobj\n"
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    pdf += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n"
    return pdf.encode("latin-1")


PDF_RESUME_LINES = ["Alex Tan", "Cashier, Example Cafe, 2023 to 2024",
                    "Served customers at the counter and handled cash payments every day",
                    "Kept the cafe clean and restocked the shelves before opening",
                    "Skills: Customer Service, Teamwork"]


def small_word_file(text: str) -> io.BytesIO:
    """A Word file holding text, to upload."""
    output = io.BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(output)
    output.seek(0)
    return output


# 1. A search from the form to the results page, through all four layers
def test_a_search_goes_from_the_form_to_the_results_page(web):
    client = web.client
    search_id = search(client)
    page = client.get(f"/results/{search_id}").get_data(as_text=True)
    assert all(title in page for title in LISTING_TITLES) and "Strong match" in page
    assert len(web.data.read_saved_jobs()) == 3


# 2. Form problems come back as messages, not errors
def test_form_problems_are_messages(web):
    client = web.client
    reply = client.post("/search", data={**DEFAULT_FORM, "min_salary": "lots"})
    assert "Minimum salary must be a whole number" in reply.get_json()["error"]
    assert client.post("/search", data={**DEFAULT_FORM, "resume_choice": "../.env"}).status_code == 400
    reply = client.post("/search", data={"resume_file": (io.BytesIO(b"hello"), "notes.txt")},
                        content_type="multipart/form-data")
    assert reply.status_code == 400 and "PDF or Word" in reply.get_json()["error"]


# 3. An upload that fails the checks is not kept in the resumes folder
def test_a_rejected_upload_is_not_kept(web):
    client = web.client
    upload = (small_word_file("Too short."), "short.docx")
    reply = client.post("/search", data={**DEFAULT_FORM, "resume_file": upload},
                        content_type="multipart/form-data")
    assert reply.status_code == 400 and "Could not read enough text" in reply.get_json()["error"]
    assert web.io.resume_choices() == [SAMPLE_RESUME]


# 4. Saved jobs: listed, one removed, all cleared; a failed save says so on the page
def test_saved_jobs_can_be_removed_and_cleared(web):
    client = web.client
    search(client)
    assert "Bank Teller" in client.get("/saved").get_data(as_text=True)
    client.post("/saved/remove", data={"job_url": job_link(2)})
    assert sorted(job["title"] for job in web.data.read_saved_jobs()) == ["Business Analyst", "Data Analyst"]

    web.storage.fail_writes = True  # as if the disk were full
    page = client.post("/saved/clear", follow_redirects=True)
    assert page.status_code == 200 and "Could not update your saved jobs file" in page.get_data(as_text=True)
    web.storage.fail_writes = False
    client.post("/saved/clear")
    assert web.data.read_saved_jobs() == []


# 5. Tailoring a Word resume for a saved job, then downloading it
def test_a_word_resume_is_tailored_and_downloaded(web):
    client = web.client
    search(client)
    reply = client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME})
    tailor_id = reply.get_json()["search_id"]
    assert client.get(f"/progress/{tailor_id}").get_json()["status"] == "done"
    page = client.get(f"/tailored/{tailor_id}").get_data(as_text=True)
    assert "Wrote SQL reports" in page and "leads with SQL" in page
    download = client.get(f"/tailored/{tailor_id}/download")
    assert download.status_code == 200 and download.mimetype.endswith("wordprocessingml.document")
    paragraphs = [paragraph.text for paragraph in Document(io.BytesIO(download.data)).paragraphs]
    assert any("Wrote SQL reports" in text for text in paragraphs)


# 6. A saved jobs file that can't be opened: the Saved page says so, and a job from a recent
#    search can still be tailored
def test_a_locked_saved_jobs_file_is_handled_on_every_page(web):
    client = web.client
    search(client)
    web.storage.fail_reads = True  # as if the file were locked
    assert "Could not open your saved jobs file" in client.get("/saved").get_data(as_text=True)
    assert client.get(f"/tailor?job_url={job_link(0)}").status_code == 200
    assert client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME}).status_code == 200
    assert client.get("/tailor?job_url=https://www.mycareersfuture.gov.sg/job/unknown").status_code == 404


# 7. After a search loosens the preferences, "Update matches" still shows what the user typed
def test_the_results_form_keeps_the_filters_the_user_typed(web):
    client = web.client
    search_id = search(client, min_salary="40000")  # no fake listing pays that much, so the search loosens it
    page = client.get(f"/results/{search_id}").get_data(as_text=True)
    assert "No jobs matched your preferences" in page and 'value="40000"' in page


# The active filters show beside "Top matches" in words, and a problem with the filters sent opens the panel
def test_the_results_page_names_its_filters_and_opens_them_on_a_problem(web):
    search_id = search(web.client, job_type="Full Time", min_salary="3000")
    page = web.client.get(f"/results/{search_id}").get_data(as_text=True)
    assert "From $3,000 a month · Full Time" in page
    problem = web.client.post(f"/results/{search_id}/update", data={**DEFAULT_FORM, "min_salary": "lots"})
    assert '<details class="filters-fold" open>' in problem.get_data(as_text=True)


# The results can be sorted like the saved jobs (in the browser, results.js); each card carries what it sorts by
def test_the_results_can_be_sorted_like_the_saved_jobs(web):
    page = web.client.get(f"/results/{search(web.client)}").get_data(as_text=True)
    assert '<select id="job-sort">' in page and "results.js" in page
    for option in ("Recommended", "Newest first", "Oldest first", "Highest salary", "Best skill match"):
        assert f">{option}</option>" in page
    assert page.count("data-posted=") == page.count('class="job-card"') > 1


# Close matches come after the jobs that fit every filter, under their own heading, each saying what it misses
def test_close_matches_are_shown_apart_with_the_filter_they_miss(web, monkeypatch):
    original = JobPortal.search

    def part_time_except_first(self, query, limit=10, filters=None):
        jobs = original(self, query, limit=limit, filters=filters)
        return [job if index == 0 else {**job, "employment_types": ["Part Time"]} for index, job in enumerate(jobs or [])]
    monkeypatch.setattr(JobPortal, "search", part_time_except_first)
    search_id = search(web.client, job_type="Full Time")
    page = web.client.get(f"/results/{search_id}").get_data(as_text=True)
    assert "Close matches" in page and "Only 1 job fits all your filters" in page
    assert page.count('class="close-badge">Not Full Time<') == 2


# 8. Unknown pages, unexpected errors and uploads that are too large all get a friendly answer
def test_error_pages_are_friendly(web, monkeypatch):
    client = web.client
    page = client.get("/no-such-page")
    assert page.status_code == 404 and "This page isn't available" in page.get_data(as_text=True)

    monkeypatch.setattr(web.io, "resume_choices", lambda: 1 / 0)  # any unexpected error
    page = client.get("/")
    monkeypatch.undo()
    assert page.status_code == 500 and "Something went wrong" in page.get_data(as_text=True)

    too_large = io.BytesIO(b"x" * (web.web.app.config["MAX_CONTENT_LENGTH"] + 1))
    reply = client.post("/search", data={"resume_file": (too_large, "big.pdf")}, content_type="multipart/form-data")
    assert reply.status_code == 413 and "too large" in reply.get_json()["error"]


# 9. Tailoring a different resume from the one that found the job: the AI isn't given that job card's
#    skills, the page says the card doesn't apply, and the PDF rewrite can't add skills the resume lacks
def test_tailoring_another_resume_never_adds_skills_it_lacks(web):
    client, folder = web.client, web.folder
    search(client)  # the job is found with the sample resume, which shows SQL and Python
    with open(os.path.join(folder, "resumes", "alex_tan.pdf"), "wb") as file:
        file.write(tiny_pdf(PDF_RESUME_LINES))
    reply = client.post("/tailor", data={"job_url": job_link(0), "resume_choice": "alex_tan.pdf"})
    tailor_id = reply.get_json()["search_id"]
    assert client.get(f"/progress/{tailor_id}").get_json()["status"] == "done"
    task, record = ai_requests[-1]
    assert task == "tailored_resume" and record["job"]["matched_skills"] == []
    page = client.get(f"/tailored/{tailor_id}").get_data(as_text=True)
    assert "was matched using sample_resume_fintech.docx" in page
    # Checked a sentence at a time: what added SQL went back to the resume's own wording or was left out
    assert "the new wording added SQL, which isn&#39;t in your resume" in page
    download = client.get(f"/tailored/{tailor_id}/download")
    tailored_text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(download.data)).paragraphs)
    assert "Served customers at the counter" in tailored_text and "SQL" not in tailored_text


# 10. Tailoring the resume that found the job still uses the job card's skills
def test_tailoring_the_same_resume_uses_the_job_card(web):
    client = web.client
    search(client)
    reply = client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME})
    assert client.get(f"/progress/{reply.get_json()['search_id']}").get_json()["status"] == "done"
    task, record = ai_requests[-1]
    assert task == "docx_edits"
    assert [match["job_skill"] for match in record["job"]["matched_skills"]] == ["SQL", "Python"]
    page = client.get(f"/tailored/{reply.get_json()['search_id']}").get_data(as_text=True)
    assert "was matched using" not in page and "2/3" in page


# ---------- more: the search's failure paths, updating, cancelling and the pages around them ----------

def test_the_form_page_lists_resumes_and_the_ai_in_use(web):
    page = web.client.get("/").get_data(as_text=True)
    assert SAMPLE_RESUME in page and "Fake AI" in page


def test_the_job_portal_being_down_is_reported(tmp_path):
    ai_requests.clear()
    down = Web(str(tmp_path), portal_status=500)
    search_id = down.client.post("/search", data=DEFAULT_FORM).get_json()["search_id"]
    progress = down.client.get(f"/progress/{search_id}").get_json()
    assert progress["status"] == "error" and "Could not get any job listings from MyCareersFuture" in progress["error"]
    assert down.data.read_saved_jobs() == []


def test_logging_is_set_up_without_a_writable_log_file(monkeypatch, tmp_path, web):
    web_io = web.io
    monkeypatch.setattr(io_manager, "LOG_PATH", str(tmp_path / "file" / "x.log"))
    (tmp_path / "file").write_text("a file where the log folder should be")
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    try:
        web_io.start_up("127.0.0.1:5000")  # must not raise
    finally:
        for handler in root.handlers[:]:
            if handler not in handlers_before:
                root.removeHandler(handler)


def test_logging_writes_info_to_the_log_file_and_opens_it_once(monkeypatch, tmp_path, web):
    """A web search leaves a record of how many listings it found and how many the AI analysed."""
    log_path = tmp_path / "logs" / "job_matcher.log"
    monkeypatch.setattr(io_manager, "LOG_PATH", str(log_path))
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    try:
        web.io.start_up("127.0.0.1:5000")
        web.io.start_up("127.0.0.1:5000")  # what WebApp.run calls; must not open a second handler
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)
                         and h.baseFilename == os.path.abspath(str(log_path))]
        assert len(file_handlers) == 1, f"{len(file_handlers)} handlers on the same file"
        logging.info("event=search stage=jobs listings_found=20 listings_analysed=20")
        file_handlers[0].flush()
        assert "listings_found=20 listings_analysed=20" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in root.handlers[:]:
            if handler not in handlers_before:
                handler.close()
                root.removeHandler(handler)


def test_a_failed_save_still_shows_the_results_with_a_warning(web):
    web.storage.fail_writes = True
    search_id = search(web.client)
    page = web.client.get(f"/results/{search_id}").get_data(as_text=True)
    assert all(title in page for title in LISTING_TITLES) and "could not be saved" in page.lower()


def test_update_matches_reuses_the_profile_and_applies_new_filters(web):
    search_id = search(web.client)
    reads_before = sum(task == "candidate_profile" for task, _ in ai_requests)
    reply = web.client.post(f"/results/{search_id}/update", data={**DEFAULT_FORM, "show": "10"})
    assert reply.status_code == 302 and "search=" in reply.headers["Location"]
    assert sum(task == "candidate_profile" for task, _ in ai_requests) == reads_before  # the resume was not read again
    bad = web.client.post(f"/results/{search_id}/update", data={**DEFAULT_FORM, "min_salary": "lots"})
    assert bad.status_code == 200 and "must be a whole number" in bad.get_data(as_text=True)


def test_update_matches_needs_a_finished_search(tmp_path):
    web = Web(str(tmp_path), holding=True)
    assert web.client.post("/results/nope/update", data=DEFAULT_FORM).status_code == 404
    running = web.client.post("/search", data=DEFAULT_FORM).get_json()["search_id"]
    assert web.client.post(f"/results/{running}/update", data=DEFAULT_FORM).status_code == 404


def test_pages_for_a_running_or_unknown_search_redirect_or_404(tmp_path):
    web = Web(str(tmp_path))
    search(web.client)
    web.holding = True
    running_search = web.client.post("/search", data=DEFAULT_FORM).get_json()["search_id"]
    running_tailor = web.client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME}).get_json()["search_id"]
    assert web.client.get(f"/results/{running_search}").status_code == 302
    assert web.client.get(f"/whatif/{running_search}").status_code == 302
    reply = web.client.get(f"/tailored/{running_tailor}")
    assert reply.status_code == 302 and f"job_url={job_link(0)}&search={running_tailor}" in reply.headers["Location"]
    assert web.client.get(f"/tailored/{running_tailor}/download").status_code == 404
    assert web.client.get("/results/nope").status_code == 404
    assert web.client.get("/tailored/nope").status_code == 404
    assert web.client.get("/tailored/nope/download").status_code == 404
    assert web.client.get("/progress/nope").status_code == 404


def test_a_cancelled_search_stops_and_keeps_nothing(tmp_path):
    web = Web(str(tmp_path), holding=True)
    task_id = web.client.post("/search", data=DEFAULT_FORM).get_json()["search_id"]
    assert web.client.post(f"/search/{task_id}/cancel").get_json() == {"ok": True}
    web.run_held_work()
    assert web.client.get(f"/progress/{task_id}").get_json()["status"] == "cancelled"
    assert web.data.read_saved_jobs() == [] and web.io.search_results(task_id) == {}


def test_tailor_needs_a_known_job_and_a_usable_resume(web):
    assert web.client.post("/tailor", data={"job_url": "https://x/none", "resume_choice": SAMPLE_RESUME}).status_code == 404
    search(web.client)
    reply = web.client.post("/tailor", data={"job_url": job_link(0), "resume_choice": "../.env"})
    assert reply.status_code == 400


def test_saved_jobs_can_be_searched_with_a_keyword(web):
    search(web.client)
    page = web.client.get("/saved?q=teller").get_data(as_text=True)
    assert "Bank Teller" in page and "Data Analyst" not in page


def test_removing_a_job_that_is_not_saved_changes_nothing(web):
    search(web.client)
    assert web.client.post("/saved/remove", data={"job_url": "https://x/none"}).status_code == 302
    assert len(web.data.read_saved_jobs()) == 3


def test_the_app_is_built_from_the_environment_and_starts_with_the_database_down(monkeypatch):
    monkeypatch.setattr(postgres_storage, "POOL_TIMEOUT_SECONDS", 0.2)  # conftest points DATABASE_URL at nothing
    flask_app = web_app.WebApp.create_app()
    page = flask_app.test_client().get("/saved")
    assert page.status_code == 200 and "Could not open your saved jobs file" in page.get_data(as_text=True)


# ---------- the what-if page ----------

def test_the_whatif_page_lists_the_skills_to_learn(web):
    search_id = search(web.client)
    page = web.client.get(f"/whatif/{search_id}").get_data(as_text=True)
    assert "What if I learned" in page and 'value="Tableau"' in page and 'value="SQL"' not in page
    # Two tabs (shown by whatif.js), and each skill with its own effect and how many jobs ask for it
    assert 'role="tablist"' in page and "Try a skill" in page and "Plan what to learn" in page
    assert re.search(r'class="impact( zero)?">\+\d+<', page) and re.search(r'asked for in \d+ jobs?"', page)
    assert "Get a learning plan" in page and ">Rank higher<" in page and ">Earn more<" in page
    assert 'id="result"' not in page  # nothing asked yet, so no answer


def test_the_results_page_links_to_the_whatif_page(web):
    search_id = search(web.client)
    assert f"/whatif/{search_id}" in web.client.get(f"/results/{search_id}").get_data(as_text=True)


def test_testing_a_skill_shows_how_the_jobs_move(web):
    search_id = search(web.client)
    page = web.client.post(f"/whatif/{search_id}", data={"mode": "test", "skill": ["Tableau"], "steps": "3", "top_n": "2"})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Learning <b>Tableau</b>" in text and "Newly covered" in text
    assert re.search(r'class="verdict v-(high|some|none)">\s*<span class="verdict-badge">(Big boost|Small boost|No change)<', text)
    assert 'id="result"' in text and 'class="whatif-tiles"' in text and "Your top 2" in text
    assert re.search(r'class="r">1st<', text) and "Best match" in text  # the top list, in words, best first
    assert "checked" in text  # the skill stays ticked for the next try


def test_a_roadmap_shows_its_steps_and_the_result(web):
    search_id = search(web.client)
    text = web.client.post(f"/whatif/{search_id}", data={"mode": "greedy", "steps": "1", "top_n": "5"}).get_data(as_text=True)
    assert 'class="stairs"' in text and "How your jobs move" in text
    assert text.count('class="stair"') == 2 and "Learning <b>" in text  # "Now", then the one step
    assert 'aria-selected="true">Plan what to learn' in text  # the plan's tab opens


@pytest.mark.parametrize("form, message", [
    ({"mode": "test", "steps": "3", "top_n": "5"}, "at least one skill"),
    ({"mode": "test", "skill": ["Cobol"], "steps": "3", "top_n": "5"}, "None of these jobs asks for"),
    ({"mode": "test", "skill": ["Tableau"], "extra_skills": "tableau", "steps": "3", "top_n": "5"}, "more than once"),
    ({"mode": "greedy", "skill": ["Tableau"], "steps": "many", "top_n": "5"}, "whole numbers"),
    ({"mode": "test", "skill": ["Tableau"], "steps": "3", "top_n": ""}, "whole number"),
    ({"mode": "greedy", "steps": "99", "top_n": "5"}, "1 to 10 skills"),
    ({"mode": "nonsense", "steps": "3", "top_n": "5"}, "Unknown planner"),
    ({"mode": "test", "skill": ["Tableau"], "steps": "3", "top_n": "0"}, "at least 1 job"),
])
def test_bad_whatif_forms_get_a_message_not_an_error(web, form, message):
    search_id = search(web.client)
    reply = web.client.post(f"/whatif/{search_id}", data=form)
    assert reply.status_code == 200 and message in reply.get_data(as_text=True)


def test_the_whatif_page_needs_a_finished_search(tmp_path):
    web = Web(str(tmp_path), holding=True)
    assert web.client.get("/whatif/nope").status_code == 404
    assert web.client.post("/whatif/nope", data={"mode": "test"}).status_code == 404
    assert web.client.post("/whatif/nope", data={"mode": "greedy"}).status_code == 404  # a roadmap too
    running = web.client.post("/search", data=DEFAULT_FORM).get_json()["search_id"]
    assert web.client.get(f"/whatif/{running}").status_code == 302  # the search page follows its progress
    web.client.post(f"/search/{running}/cancel")
    assert web.client.get(f"/whatif/{running}").status_code == 404  # a cancelled search has nothing to simulate


def test_the_whatif_page_says_so_when_there_is_nothing_to_learn(web, monkeypatch):
    def has_every_skill(record: dict, task: str) -> dict:
        answer = fake_ai(record, task)
        if task == "candidate_profile":
            answer["core_skills"].append("Tableau")
        if task == "job_requirements":
            for job in answer["jobs"]:
                job["skill_matches"] = [{"job_skill": skill, "candidate_skill": skill} for skill in job["required_skills"]]
        return answer
    monkeypatch.setattr(web.ai, "_responder", has_every_skill)
    search_id = search(web.client)
    page = web.client.get(f"/whatif/{search_id}").get_data(as_text=True)
    assert "Nothing to learn" in page


# ---------- a status and note on a saved job ----------

def update(web, **form):
    return web.client.post("/saved/update", data={"job_url": job_link(0), **form})


def test_a_saved_job_gets_a_status_and_a_note_that_the_page_shows(web):
    search(web.client)
    assert update(web, status="Applied", note="Phone screen Friday").status_code == 302
    page = web.client.get("/saved").get_data(as_text=True)
    assert "Phone screen Friday" in page and 'class="status-badge">Applied' in page
    job = web.data.get_saved_job(job_link(0))
    assert job["status"] == "Applied" and job["note"] == "Phone screen Friday"


def test_a_bad_status_a_long_note_and_an_unsaved_job_are_messages(web):
    search(web.client)
    assert "Status must be one of" in web.client.post("/saved/update", data={"job_url": job_link(0), "status": "Hired"},
                                                      follow_redirects=True).get_data(as_text=True)
    long_note = web.client.post("/saved/update", data={"job_url": job_link(0), "note": "x" * 501}, follow_redirects=True)
    assert "at most 500 characters" in long_note.get_data(as_text=True)
    gone = web.client.post("/saved/update", data={"job_url": "https://x/none", "status": "Applied"}, follow_redirects=True)
    assert "no longer saved" in gone.get_data(as_text=True)
    removed_twice = web.client.post("/saved/remove", data={"job_url": "https://x/none"}, follow_redirects=True)
    assert "no longer saved" in removed_twice.get_data(as_text=True)


# The Saved page says what changed, keeps the filter and reopens the changed job; it shows no text from its address
def test_saved_page_feedback_keeps_the_filter(web):
    search(web.client)
    reply = web.client.post("/saved/update", data={"job_url": job_link(0), "status": "Applied", "q": "analyst"})
    assert "q=analyst" in reply.headers["Location"] and "outcome=updated" in reply.headers["Location"]
    page = web.client.get(reply.headers["Location"]).get_data(as_text=True)
    assert "Saved your changes." in page and 'open id="changed-job"' in page
    assert "Removed the job." in web.client.post("/saved/remove", data={"job_url": job_link(1)},
                                                 follow_redirects=True).get_data(as_text=True)


def test_an_update_that_cannot_be_written_says_so(web):
    search(web.client)
    web.storage.fail_writes = True
    page = web.client.post("/saved/update", data={"job_url": job_link(0), "status": "Applied"}, follow_redirects=True)
    assert "Could not update your saved jobs file" in page.get_data(as_text=True)


def test_updating_with_no_changes_keeps_the_job_as_it_was(web):
    search(web.client)
    before = web.data.read_saved_jobs()
    assert web.client.post("/saved/update", data={"job_url": job_link(0)}).status_code == 302
    assert web.data.read_saved_jobs() == before


# A search where the AI skipped some listings still shows the rest, and says some matches may be missing
def test_a_partly_failed_search_says_what_it_left_out(web, monkeypatch):
    def first_listing_only(record: dict, task: str) -> dict:
        answer = fake_ai(record, task)
        if task == "job_requirements":
            answer["jobs"] = answer["jobs"][:1]
        return answer
    monkeypatch.setattr(web.ai, "_responder", first_listing_only)
    search_id = search(web.client)
    page = web.client.get(f"/results/{search_id}").get_data(as_text=True)
    assert "2 of 3 listings could not be analysed" in page and "Data Analyst" in page


# A search's id can't open a tailoring page and a tailoring run's id can't open a results page: 404, not a crash
def test_a_task_id_of_the_other_kind_is_not_found(web):
    search_id = search(web.client)
    tailor_id = web.client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME}).get_json()["search_id"]
    assert web.client.get(f"/results/{search_id}").status_code == 200
    for path in [f"/tailored/{search_id}", f"/tailored/{search_id}/download", f"/results/{tailor_id}",
                 f"/whatif/{tailor_id}"]:
        assert web.client.get(path).status_code == 404, path
    assert web.client.post(f"/results/{tailor_id}/update", data=DEFAULT_FORM).status_code == 404


# The Saved page still opens when a saved job's skill lists are the wrong type
def test_the_saved_page_survives_a_badly_typed_saved_job(web):
    web.storage.insert({"job_url": job_link(5), "title": "Odd Job", "required_skills": None, "matched_skills": "abc"})
    page = web.client.get("/saved")
    assert page.status_code == 200 and "Odd Job" in page.get_data(as_text=True)


# DNS rebinding: a website that points its own name at 127.0.0.1 sends its name as the Host header; only the app's
# own names are answered, so that website can't read the pages
def test_only_the_apps_own_host_names_are_answered(web):
    assert web.client.get("/saved", base_url="http://attacker.example").status_code == 400
    assert web.client.get("/saved", base_url="http://attacker.example:5000").status_code == 400
    for own in ("http://127.0.0.1:5000", "http://localhost:5000", "http://localhost"):
        assert web.client.get("/saved", base_url=own).status_code == 200, own


# Cross-site request forgery: a POST another website makes the browser send is refused and changes nothing; the
# app's own pages (same origin), and clients that aren't browsers, still work
@pytest.mark.parametrize("headers", [{"Origin": "https://attacker.example"},
                                     {"Sec-Fetch-Site": "cross-site"},
                                     {"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:3000"},
                                     {"Origin": "null"}])
def test_a_post_from_another_website_is_refused(web, headers):
    search(web.client)
    assert web.client.post("/saved/clear", headers=headers).status_code == 403
    assert web.client.post("/search", data=DEFAULT_FORM, headers=headers).status_code == 403
    assert len(web.data.read_saved_jobs()) == 3


def test_a_post_from_the_apps_own_page_still_works(web):
    search(web.client)
    own_page = {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}
    assert web.client.post("/saved/clear", headers=own_page).status_code == 302
    assert web.data.read_saved_jobs() == []
    assert web.client.get("/saved", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200  # reading is fine


# The Saved page shows the application tracker: saved jobs by stage, and strong matches not applied to yet
def test_the_saved_page_shows_the_application_tracker(web):
    search(web.client)
    web.client.post("/saved/update", data={"job_url": job_link(0), "status": "Applied"})
    page = web.client.get("/saved").get_data(as_text=True)
    assert 'aria-label="Application tracker"' in page
    counts = dict(re.findall(r'tracker-count">(\d+)</span><span class="tracker-label">(\w+)<', page)[i][::-1]
                  for i in range(5))
    assert counts == {"Saved": "2", "Applied": "1", "Interviewing": "0", "Offer": "0", "Rejected": "0"}
    assert "2 strong matches are saved but not applied to yet" in page


# Each tracker stage is a filter button for saved.js: one per status, off at first, and a stage with no jobs can't be
# picked. Every saved job card says its status, so the script can show one stage's jobs.
def test_the_tracker_stages_filter_the_saved_jobs_by_status(web):
    search(web.client)
    web.client.post("/saved/update", data={"job_url": job_link(0), "status": "Applied"})
    page = web.client.get("/saved").get_data(as_text=True)
    buttons = re.findall(r'<button class="tracker-button" type="button" data-status="(\w+)" aria-pressed="false"'
                         r'[^>]*?( disabled)?>', page)
    assert buttons == [("Saved", ""), ("Applied", ""), ("Interviewing", " disabled"), ("Offer", " disabled"),
                       ("Rejected", " disabled")]
    assert sorted(re.findall(r'data-status="(\w+)"\s+data-text', page)) == ["Applied", "Saved", "Saved"]
    assert 'id="status-note"' in page and 'id="status-show-all"' in page
    results = web.client.get(f"/results/{search(web.client)}").get_data(as_text=True)
    assert "data-status" not in results  # the results page has no tracker to filter by

# The Tailor link on the results page names the search the job came from, the Tailor page sends it back, and the
# job is found in that search; the Saved page's link has none
def test_the_tailor_link_carries_the_search_the_job_came_from(web):
    search_id = search(web.client)
    results = web.client.get(f"/results/{search_id}").get_data(as_text=True)
    assert f"from_search={search_id}" in results and "from_search=" not in web.client.get("/saved").get_data(as_text=True)
    page = web.client.get(f"/tailor?job_url={job_link(0)}&from_search={search_id}").get_data(as_text=True)
    assert f'name="from_search" value="{search_id}"' in page and "Data Analyst" in page
    web.storage.fail_reads = True  # the saved jobs can't be opened, but the search still has the job
    reply = web.client.post("/tailor", data={"job_url": job_link(1), "resume_choice": SAMPLE_RESUME,
                                             "from_search": search_id})
    assert web.client.get(f"/progress/{reply.get_json()['search_id']}").get_json()["status"] == "done"


def test_a_tailored_resume_is_downloaded_under_the_resume_and_job_names(web):
    search(web.client)
    tailor_id = web.client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME}).get_json()["search_id"]
    download = web.client.get(f"/tailored/{tailor_id}/download")
    assert "sample_resume_fintech_Data_Analyst.docx" in download.headers["Content-Disposition"]
    assert os.path.exists(os.path.join(web.folder, "resumes", "tailored", "sample_resume_fintech_Data_Analyst.docx"))


def test_a_cancelled_or_failed_tailoring_run_goes_back_to_the_tailor_page(tmp_path):
    web = Web(str(tmp_path))
    search(web.client)
    web.holding = True
    tailor_id = web.client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME}).get_json()["search_id"]
    web.client.post(f"/search/{tailor_id}/cancel")
    reply = web.client.get(f"/tailored/{tailor_id}")
    assert reply.status_code == 302 and "search=" not in reply.headers["Location"]
