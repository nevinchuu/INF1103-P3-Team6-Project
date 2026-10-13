"""Tests for the web app (src/web_app.py): its pages and actions, through all four layers.

Plain functions only (the project is fully procedural). Run from the project root:

    python tests/test_web_app.py

Flask's test client sends the requests. Background threads run straight away (so each search is
finished when its request returns), MyCareersFuture's and the AI's replies are faked, and saved jobs
and uploaded resumes go to a temporary folder, so no browser, internet, API key or real data is needed.
"""

import io
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

# The project's modules live in src/, so add it to the import path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

import ai_manager  # noqa: E402
import data_manager  # noqa: E402
import io_manager  # noqa: E402
import web_app  # noqa: E402
from docx import Document  # noqa: E402

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


ai_requests = []  # what the app sent the AI, newest last


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


def run_now(target, args=(), kwargs=None, daemon=None) -> SimpleNamespace:
    """In place of threading.Thread: start() runs the work straight away, so a request returns when it's done."""
    return SimpleNamespace(start=lambda: target(*args, **(kwargs or {})))


def with_web_app(test_body) -> None:
    """Runs test_body(client, folder) with the fakes in place and a fresh temporary folder, then undoes them."""
    real = (io_manager.requests.post, io_manager.requests.get, io_manager.time.sleep, ai_manager.run_task,
            web_app.threading, data_manager.DATABASE_PATH, io_manager.RESUME_FOLDER,
            data_manager.read_saved_jobs, data_manager.write_database)
    with tempfile.TemporaryDirectory() as folder:
        resume_folder = os.path.join(folder, "resumes")
        os.makedirs(resume_folder)
        shutil.copy(os.path.join(PROJECT_ROOT, "resumes", SAMPLE_RESUME), resume_folder)
        listings = [portal_listing(number, title) for number, title in enumerate(LISTING_TITLES)]
        io_manager.requests.post = lambda url, **options: fake_reply({"results": listings})
        io_manager.requests.get = lambda url, **options: fake_reply({"description": "<p>Use SQL</p>",
                                                                     "skills": [{"skill": "SQL"}]})
        io_manager.time.sleep = lambda seconds: None
        ai_manager.run_task = fake_ai
        # Only web_app's own threads run straight away; the AI layer's worker threads are untouched
        web_app.threading = SimpleNamespace(Thread=run_now)
        data_manager.DATABASE_PATH = os.path.join(folder, "job_listings.json")
        io_manager.RESUME_FOLDER = resume_folder
        try:
            test_body(web_app.app.test_client(), folder)
        finally:
            (io_manager.requests.post, io_manager.requests.get, io_manager.time.sleep, ai_manager.run_task,
             web_app.threading, data_manager.DATABASE_PATH, io_manager.RESUME_FOLDER,
             data_manager.read_saved_jobs, data_manager.write_database) = real


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
def test_a_search_goes_from_the_form_to_the_results_page():
    def body(client, folder):
        search_id = search(client)
        page = client.get(f"/results/{search_id}").get_data(as_text=True)
        assert all(title in page for title in LISTING_TITLES) and "Strong match" in page
        assert len(data_manager.read_saved_jobs()) == 3
    with_web_app(body)


# 2. Form problems come back as messages, not errors
def test_form_problems_are_messages():
    def body(client, folder):
        reply = client.post("/search", data={**DEFAULT_FORM, "min_salary": "lots"})
        assert "whole numbers" in reply.get_json()["error"]
        assert client.post("/search", data={**DEFAULT_FORM, "resume_choice": "../.env"}).status_code == 400
        reply = client.post("/search", data={"resume_file": (io.BytesIO(b"hello"), "notes.txt")},
                            content_type="multipart/form-data")
        assert reply.status_code == 400 and "PDF or Word" in reply.get_json()["error"]
    with_web_app(body)


# 3. An upload that fails the checks is not kept in the resumes folder
def test_a_rejected_upload_is_not_kept():
    def body(client, folder):
        upload = (small_word_file("Too short."), "short.docx")
        reply = client.post("/search", data={**DEFAULT_FORM, "resume_file": upload},
                            content_type="multipart/form-data")
        assert reply.status_code == 400 and "Could not read enough text" in reply.get_json()["error"]
        assert io_manager.list_resumes() == [SAMPLE_RESUME]
    with_web_app(body)


# 4. Saved jobs: listed, one removed, all cleared; a failed save says so on the page
def test_saved_jobs_can_be_removed_and_cleared():
    def body(client, folder):
        search(client)
        assert "Bank Teller" in client.get("/saved").get_data(as_text=True)
        client.post("/saved/remove", data={"job_url": job_link(2)})
        assert sorted(job["title"] for job in data_manager.read_saved_jobs()) == ["Business Analyst", "Data Analyst"]

        def failing_write(jobs):
            raise OSError("disk full")
        real_write = data_manager.write_database
        data_manager.write_database = failing_write
        page = client.post("/saved/clear", follow_redirects=True)
        assert page.status_code == 200 and "Could not update your saved jobs file" in page.get_data(as_text=True)
        data_manager.write_database = real_write
        client.post("/saved/clear")
        assert data_manager.read_saved_jobs() == []
    with_web_app(body)


# 5. Tailoring a Word resume for a saved job, then downloading it
def test_a_word_resume_is_tailored_and_downloaded():
    def body(client, folder):
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
    with_web_app(body)


# 6. A saved jobs file that can't be opened: the Saved page says so, and a job from a recent
#    search can still be tailored
def test_a_locked_saved_jobs_file_is_handled_on_every_page():
    def body(client, folder):
        search(client)

        def locked():
            raise PermissionError(13, "Permission denied")
        data_manager.read_saved_jobs = locked
        assert "Could not open your saved jobs file" in client.get("/saved").get_data(as_text=True)
        assert client.get(f"/tailor?job_url={job_link(0)}").status_code == 200
        assert client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME}).status_code == 200
        assert client.get("/tailor?job_url=https://www.mycareersfuture.gov.sg/job/unknown").status_code == 404
    with_web_app(body)


# 7. After a search loosens the preferences, "Update matches" still shows what the user typed
def test_the_results_form_keeps_the_filters_the_user_typed():
    def body(client, folder):
        search_id = search(client, max_salary="1000")  # every fake listing pays more, so the search loosens it
        page = client.get(f"/results/{search_id}").get_data(as_text=True)
        assert "No jobs matched your preferences" in page and 'value="1000"' in page
    with_web_app(body)


# 8. Unknown pages, unexpected errors and uploads that are too large all get a friendly answer
def test_error_pages_are_friendly():
    def body(client, folder):
        page = client.get("/no-such-page")
        assert page.status_code == 404 and "This page isn't available" in page.get_data(as_text=True)

        real_list = io_manager.list_resumes
        io_manager.list_resumes = lambda: 1 / 0  # any unexpected error
        page = client.get("/")
        io_manager.list_resumes = real_list
        assert page.status_code == 500 and "Something went wrong" in page.get_data(as_text=True)

        too_large = io.BytesIO(b"x" * (web_app.app.config["MAX_CONTENT_LENGTH"] + 1))
        reply = client.post("/search", data={"resume_file": (too_large, "big.pdf")}, content_type="multipart/form-data")
        assert reply.status_code == 413 and "too large" in reply.get_json()["error"]
    with_web_app(body)


# 9. Tailoring a different resume from the one that found the job: the AI isn't given that job card's
#    skills, the page says the card doesn't apply, and the PDF rewrite can't add skills the resume lacks
def test_tailoring_another_resume_never_adds_skills_it_lacks():
    def body(client, folder):
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
        assert "that added SQL" in page and "section was left out because it added SQL" in page
        download = client.get(f"/tailored/{tailor_id}/download")
        tailored_text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(download.data)).paragraphs)
        assert "Served customers at the counter" in tailored_text and "SQL" not in tailored_text
    with_web_app(body)


# 10. Tailoring the resume that found the job still uses the job card's skills
def test_tailoring_the_same_resume_uses_the_job_card():
    def body(client, folder):
        search(client)
        reply = client.post("/tailor", data={"job_url": job_link(0), "resume_choice": SAMPLE_RESUME})
        assert client.get(f"/progress/{reply.get_json()['search_id']}").get_json()["status"] == "done"
        task, record = ai_requests[-1]
        assert task == "docx_edits"
        assert [match["job_skill"] for match in record["job"]["matched_skills"]] == ["SQL", "Python"]
        page = client.get(f"/tailored/{reply.get_json()['search_id']}").get_data(as_text=True)
        assert "was matched using" not in page and "2/3" in page
    with_web_app(body)


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
