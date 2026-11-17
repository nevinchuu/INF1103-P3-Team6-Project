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
