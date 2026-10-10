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

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

import ai_manager
import database_functions
import io_manager
import logic_manager

TOP_N = 5  # jobs kept and saved per search

app = Flask(__name__)  # templates/ and static/ are found next to this file
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # largest resume upload: 5 MB (app.js checks it too)

# io_manager's text helpers, so templates show salaries and lists the same way as the console
app.jinja_env.globals.update(format_salary=io_manager.format_salary, join_list=io_manager.join_list)


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
        max_upload=app.config["MAX_CONTENT_LENGTH"],
        ai_name=ai_manager.provider_name(),
    )


@app.get("/results/<search_id>")
def results(search_id):
    """The profile and top jobs from one finished search."""
    search = io_manager.get_task(search_id)
    if search is None:
        abort(404)
    if search["status"] == "running":
        return redirect(url_for("index", search=search_id))  # the search page picks up its progress
    if search["status"] != "done":
        return redirect(url_for("index"))
    return render_template("results.html", **search["result"])


@app.get("/saved")
def saved():
    """Every job saved by the data layer."""
    return render_template("saved.html", jobs=database_functions.read_saved_jobs())


@app.errorhandler(413)
def too_large(error):
    """Uploads over MAX_CONTENT_LENGTH. Sent as JSON because app.js sends the form."""
    megabytes = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify(error=f"That file is too large. Please upload a resume under {megabytes} MB."), 413


# ==========================================
# SEARCH API (used by static/progress.js)
# ==========================================

@app.post("/search")
def start_search():
    """Checks the form, then starts the search in a background thread. Returns {"search_id"}."""
    # 1. Input layer: the uploaded resume (saved into the resumes folder) or the one picked from the list.
    #    A name with nothing usable left after secure_filename keeps no extension, so it is rejected.
    upload = request.files.get("resume_file")
    uploaded_name = (secure_filename(upload.filename) or "unnamed") if upload and upload.filename else ""
    path, problem = io_manager.resume_path_from_form(uploaded_name, request.form.get("resume_choice", ""))
    if problem:
        return jsonify(error=problem), 400
    if uploaded_name:
        upload.save(path)
    resume_text, problem = io_manager.load_resume(path)
    if problem:
        return jsonify(error=problem), 400

    filters, problem = io_manager.check_filters(request.form)
    if problem:
        return jsonify(error=problem), 400

    search_id = io_manager.start_task()
    threading.Thread(target=run_search, daemon=True,
                     args=(search_id, resume_text, os.path.basename(path), filters,
                           request.form.get("fallback_qualification", ""),
                           request.form.get("fallback_skills", ""))).start()
    return jsonify(search_id=search_id)


@app.get("/progress/<search_id>")
def progress(search_id):
    """Progress of one search, for the progress bar."""
    search = io_manager.get_task(search_id)
    if search is None:
        return jsonify(error="This search is no longer available. Please start a new one."), 404
    return jsonify(status=search["status"], percent=search["percent"],
                   message=search["message"], error=search["error"])


@app.post("/search/<search_id>/cancel")
def cancel_search(search_id):
    """Stops a running search. The background thread stops at its next progress update."""
    io_manager.cancel_task(search_id)
    return jsonify(ok=True)


# ==========================================
# BACKGROUND TASKS (the work each thread does, like main.py's search)
# ==========================================

def run_search(search_id, resume_text, resume_name, filters, fallback_qualification, fallback_skills):
    """Runs the AI, logic and data layers in a background thread, reporting progress as it goes.

    Progress bar: 0-25% reading the resume, 25-40% portal searches, 40-90% AI batches,
    then ranking and saving.
    """
    try:
        # 2. AI layer: profile
        io_manager.update_task(search_id, percent=5, message="Reading your resume with AI...")
        profile = ai_manager.extract_candidate_profile(resume_text)
        if profile is None:
            io_manager.update_task(search_id, status="error",
                                   error="The AI could not analyse your resume. Check your internet connection "
                                         "and API key, or wait a minute if the AI service is busy.")
            return

        # 1. Input layer: details the AI missed, from the form's optional fields
        profile = io_manager.fill_missing_data(profile, fallback_qualification, fallback_skills)

        # 2. AI layer: job search and requirements
        def on_progress(stage, done, total):
            if stage == "search":
                io_manager.update_task(search_id, percent=25 + 15 * done // total,
                                       message=f"Searching the job portal ({done} of {total} searches)...")
            else:
                io_manager.update_task(search_id, percent=40 + 50 * done // total,
                                       message=f"Analysing job listings ({done} of {total} batches)...")

        io_manager.update_task(search_id, percent=25, message="Searching the job portal...")
        jobs = ai_manager.search_and_extract_jobs(profile, on_progress=on_progress)

        # 3. Logic layer
        io_manager.update_task(search_id, percent=92, message="Ranking jobs...")
        top_jobs = logic_manager.filter_and_rank(profile, jobs, filters, TOP_N)
        removed = logic_manager.count_removed(jobs, filters)

        # 4. Data layer (a failed save still shows the results)
        io_manager.update_task(search_id, percent=96, message="Saving results...")
        save_failed = False
        try:
            database_functions.save_new_jobs(top_jobs, resume_name)
        except Exception as error:
            logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
            save_failed = True

        result = {"profile": profile, "jobs": top_jobs, "checked": len(jobs),
                  "removed": removed, "save_failed": save_failed}
        io_manager.update_task(search_id, status="done", percent=100, message="Done", result=result)

    except InterruptedError:  # cancelled (io_manager.update_task)
        return  # the browser has already gone back to the form

    except Exception as error:
        logging.error(f"Search failed ({type(error).__name__}: {error})")
        io_manager.update_task(search_id, status="error",
                               error=f"Something went wrong during the search ({type(error).__name__}). "
                                     "Please try again.")


if __name__ == "__main__":
    # ai_manager turns on INFO logging when imported; show only warnings and errors
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # hide a log line for every progress check
    io_manager.display_message("Resume Job Matcher is running. Open http://127.0.0.1:5000 in your browser "
                               "(Ctrl+C to stop).")
    app.run(host="127.0.0.1", port=5000)
