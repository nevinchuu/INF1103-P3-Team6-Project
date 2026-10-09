"""Web interface (Flask): the same four layers as main.py, in the browser.

Run from the project root, then open http://127.0.0.1:5000

    python src/web_app.py

A search runs in a background thread so the page can show a progress bar:
the browser starts it with POST /search, then asks GET /progress/<search_id>
every second until it is done and opens /results/<search_id>.

To add a page: add a route below and a template in src/templates/ that
starts with {% extends "base.html" %}.
"""

import logging
import os
import threading
import uuid

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

import ai_manager
import database_functions
import io_manager
import logic_manager

TOP_N = 5  # jobs kept and saved per search

app = Flask(__name__)  # templates/ and static/ are found next to this file
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # largest resume upload: 5 MB

# io_manager's text helpers, so templates show salaries and lists the same way as the console
app.jinja_env.globals.update(format_salary=io_manager.format_salary, join_list=io_manager.join_list)

# Searches by id: {"status": "running" | "done" | "error", "percent", "message", "result", "error"}.
# Kept in memory only (lost on restart); the jobs themselves are saved by the data layer.
# Background threads update this, so changes go through the lock.
searches = {}
searches_lock = threading.Lock()


# ==========================================
# PAGES
# ==========================================

@app.get("/")
def index():
    """Search form: pick or upload a resume and set job filters."""
    return render_template(
        "index.html",
        resumes=io_manager.list_resumes(),
        job_types=io_manager.JOB_TYPES,
        work_arrangements=io_manager.WORK_ARRANGEMENTS,
        education_levels=io_manager.EDUCATION_LEVELS[1:],  # leave out "None"
        max_salary=io_manager.MAX_SALARY,
        max_years=io_manager.MAX_EXPERIENCE_YEARS,
    )


@app.get("/results/<search_id>")
def results(search_id):
    """The profile and top jobs from one finished search."""
    with searches_lock:
        search = searches.get(search_id)
    if search is None:
        abort(404)
    if search["status"] != "done":
        return redirect(url_for("index"))
    return render_template("results.html", **search["result"])


@app.get("/saved")
def saved():
    """Every job saved by the data layer."""
    jobs = database_functions.read_database()
    if not isinstance(jobs, list):
        jobs = []
    return render_template("saved.html", jobs=[job for job in jobs if isinstance(job, dict)])


# ==========================================
# SEARCH API (used by static/app.js)
# ==========================================

@app.post("/search")
def start_search():
    """Checks the form, then starts the search in a background thread. Returns {"search_id"}."""
    path, problem = save_or_find_resume()
    if problem:
        return jsonify(error=problem), 400

    # 1. Input layer: same checks as the console version
    problem = io_manager.check_resume_file(path)
    if problem:
        return jsonify(error=problem), 400
    resume_text = io_manager.read_resume(path)
    if len(resume_text) < io_manager.MIN_RESUME_CHARS:
        return jsonify(error="Could not read enough text from that file. It may be a scanned image, "
                             "password-protected or corrupted. Please try another file."), 400

    filters, problem = read_filters(request.form)
    if problem:
        return jsonify(error=problem), 400

    fallback = {
        "qualification": request.form.get("fallback_qualification", ""),
        "skills": [s.strip() for s in request.form.get("fallback_skills", "").split(",") if s.strip()],
    }

    search_id = uuid.uuid4().hex
    with searches_lock:
        searches[search_id] = {"status": "running", "percent": 0, "message": "Starting...",
                               "result": None, "error": None}
    threading.Thread(target=run_search, args=(search_id, resume_text, filters, fallback), daemon=True).start()
    return jsonify(search_id=search_id)


@app.get("/progress/<search_id>")
def progress(search_id):
    """Progress of one search, for the progress bar."""
    with searches_lock:
        search = searches.get(search_id)
        if search is None:
            return jsonify(error="Search not found."), 404
        return jsonify(status=search["status"], percent=search["percent"],
                       message=search["message"], error=search["error"])


# ==========================================
# HELPERS
# ==========================================

def save_or_find_resume() -> tuple[str, str]:
    """Returns (path, problem). Saves an uploaded resume into the resumes folder,
    or uses the one picked from the list."""
    upload = request.files.get("resume_file")
    if upload and upload.filename:
        filename = secure_filename(upload.filename)
        if os.path.splitext(filename)[1].lower() not in io_manager.ALLOWED_EXTENSIONS:
            return "", "Please upload a PDF or Word (.docx) file."
        os.makedirs(io_manager.RESUME_FOLDER, exist_ok=True)
        path = os.path.join(io_manager.RESUME_FOLDER, filename)
        upload.save(path)
        return path, ""

    name = request.form.get("resume_choice", "")
    if name not in io_manager.list_resumes():
        return "", "Please choose a resume or upload one."
    return os.path.join(io_manager.RESUME_FOLDER, name), ""


def read_filters(form) -> tuple[dict, str]:
    """Returns (filters, problem), with filters in the same shape as io_manager.prompt_filters."""
    numbers = {}
    limits = {
        "min_salary": (0, io_manager.MAX_SALARY),
        "max_salary": (io_manager.MAX_SALARY, io_manager.MAX_SALARY),
        "max_years_experience": (io_manager.MAX_EXPERIENCE_YEARS, io_manager.MAX_EXPERIENCE_YEARS),
    }
    for name, (default, maximum) in limits.items():
        text = form.get(name, "").strip().replace(",", "").replace("$", "")
        try:
            numbers[name] = int(text) if text else default
        except ValueError:
            return {}, "Salary and experience must be whole numbers."
        if not 0 <= numbers[name] <= maximum:
            return {}, f"Please keep {name.replace('_', ' ')} between 0 and {maximum}."
    if numbers["max_salary"] < numbers["min_salary"]:
        return {}, "Maximum salary cannot be lower than minimum salary."

    job_type = form.get("job_type", io_manager.ANY)
    work_arrangement = form.get("work_arrangement", io_manager.ANY)
    if job_type not in io_manager.JOB_TYPES or work_arrangement not in io_manager.WORK_ARRANGEMENTS:
        return {}, "Please pick a job type and work arrangement from the lists."
    return {**numbers, "job_type": job_type, "work_arrangement": work_arrangement}, ""


def update_search(search_id: str, **changes) -> None:
    with searches_lock:
        searches[search_id].update(changes)


def run_search(search_id: str, resume_text: str, filters: dict, fallback: dict) -> None:
    """Runs the AI, logic and data layers in a background thread, reporting progress as it goes.

    Progress bar: 0-25% reading the resume, 25-40% portal searches, 40-90% AI batches,
    then ranking and saving.
    """
    try:
        # 2. AI layer: profile
        update_search(search_id, percent=5, message="Reading your resume with AI...")
        profile = ai_manager.extract_candidate_profile(resume_text)
        if profile is None:
            update_search(search_id, status="error",
                          error="The AI could not analyse your resume. Check your internet connection "
                                "and API key, or wait a minute if the AI service is busy.")
            return

        # Web version of io_manager.prompt_missing_data: use what the user typed in the form
        if profile["highest_qualification"] == "None" and fallback["qualification"] in io_manager.EDUCATION_LEVELS:
            profile["highest_qualification"] = fallback["qualification"]
            profile["qualification_detail"] = profile["qualification_detail"] or fallback["qualification"]
        if not profile["core_skills"] and fallback["skills"]:
            profile["core_skills"] = fallback["skills"]

        # 2. AI layer: job search and requirements
        def on_progress(stage, done, total):
            if stage == "search":
                update_search(search_id, percent=25 + 15 * done // total,
                              message=f"Searching the job portal ({done} of {total} searches)...")
            else:
                update_search(search_id, percent=40 + 50 * done // total,
                              message=f"Analysing job listings ({done} of {total} batches)...")

        update_search(search_id, percent=25, message="Searching the job portal...")
        jobs = ai_manager.search_and_extract_jobs(profile, on_progress=on_progress)

        # 3. Logic layer
        update_search(search_id, percent=92, message="Ranking jobs...")
        top_jobs = logic_manager.filter_and_rank(profile, jobs, filters, TOP_N)
        removed = logic_manager.count_removed(jobs, filters)

        # 4. Data layer (a failed save still shows the results)
        update_search(search_id, percent=96, message="Saving results...")
        save_failed = False
        if top_jobs:
            try:
                database = database_functions.read_database()
                database = database_functions.insert_no_duplicates(top_jobs, database)
                database_functions.write_database(database)
            except Exception as error:
                logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
                save_failed = True

        result = {"profile": profile, "jobs": top_jobs, "checked": len(jobs),
                  "removed": removed, "save_failed": save_failed}
        update_search(search_id, status="done", percent=100, message="Done", result=result)

    except Exception as error:
        logging.error(f"Search failed ({type(error).__name__}: {error})")
        update_search(search_id, status="error",
                      error=f"Something went wrong during the search ({type(error).__name__}). Please try again.")


if __name__ == "__main__":
    # ai_manager turns on INFO logging when imported; show only warnings and errors
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # hide a log line for every progress check
    print("Resume Job Matcher is running. Open http://127.0.0.1:5000 in your browser (Ctrl+C to stop).")
    app.run(host="127.0.0.1", port=5000)
