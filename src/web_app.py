"""Web interface (Flask): the same four layers as main.py, in the browser.

Run from the project root, then open http://127.0.0.1:5000

    python src/web_app.py

A search runs in a background thread so the page can show a progress bar:
the browser starts it with POST /search, then asks GET /progress/<search_id>
every second until it is done and opens /results/<search_id>.
The results page can change the filters afterwards (?min_salary=...&show=10):
that only re-ranks the jobs the search already checked, with no new AI calls.

"Tailor my resume" (/tailor?job_url=...) works the same way: POST /tailor starts it,
the same /progress route reports on it, and /tailored/<id> shows the result.

To add a page: add a route below and a template in src/templates/ that
starts with {% extends "base.html" %}.
"""

import io
import logging
import os
import threading

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

import ai_manager
import database_functions
import io_manager
import logic_manager

TOP_N = 5  # jobs shown and saved per search, until the user picks another number
JOBS_PER_SEARCH = 10  # listings fetched per job title; more means more AI batches (slower, costs more)
SHOW_OPTIONS = [5, 10, 20]  # "Show" choices on the results page

app = Flask(__name__)  # templates/ and static/ are found next to this file
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # largest resume upload: 5 MB (app.js checks it too)

# io_manager's text helpers, so templates show jobs the same way as the console
app.jinja_env.globals.update(format_salary=io_manager.format_salary, join_list=io_manager.join_list)
app.add_template_filter(io_manager.tidy_title, "tidy_title")
app.add_template_filter(io_manager.short_location, "short_location")
app.add_template_filter(io_manager.experience_text, "experience_text")
app.add_template_test(io_manager.same_skill, "same_skill")  # {% if job_skill is same_skill(candidate_skill) %}


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
    """The profile and top jobs from one finished search. Filters in the address
    re-rank the jobs the search already checked (3. logic layer only, no AI)."""
    search = io_manager.get_task(search_id)
    if search is None:
        abort(404)
    if search["status"] == "running":
        return redirect(url_for("index", search=search_id))  # the search page picks up its progress
    if search["status"] != "done":
        return redirect(url_for("index"))

    result = search["result"]
    filters, show, problem = result["filters"], TOP_N, ""
    if request.args:
        new_filters, problem = io_manager.check_filters(request.args)
        if not problem:
            filters = new_filters
            show = request.args.get("show", TOP_N, type=int)
            show = show if show in SHOW_OPTIONS else TOP_N

    if filters == result["filters"] and show == TOP_N:
        ranked = result["ranked"]  # the search's own results, saved when it finished
    else:
        # 3. Logic layer, then 4. data layer: newly shown jobs are saved too (a failed save still shows them)
        ranked = {"jobs": logic_manager.filter_and_rank(result["profile"], result["all_jobs"], filters, show),
                  "removed": logic_manager.count_removed(result["all_jobs"], filters),
                  "save_failed": False, "newly_saved": 0}
        try:
            ranked["newly_saved"] = database_functions.save_new_jobs(ranked["jobs"], result["resume"])
        except Exception as error:
            logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
            ranked["save_failed"] = True

    return render_template(
        "results.html",
        search_id=search_id,
        profile=result["profile"],
        **ranked,
        checked=len(result["all_jobs"]),
        jobs_per_search=JOBS_PER_SEARCH,
        skill_gaps=logic_manager.find_skill_gaps(ranked["jobs"]),
        filters=filters,
        filter_problem=problem,
        show=show,
        show_options=SHOW_OPTIONS,
        job_types=io_manager.JOB_TYPES,
        work_arrangements=io_manager.WORK_ARRANGEMENTS,
        max_salary=io_manager.MAX_SALARY,
        max_years=io_manager.MAX_EXPERIENCE_YEARS,
    )


@app.get("/saved")
def saved():
    """Every job saved by the data layer, or those matching ?q= (saved.js also filters as you type)."""
    jobs = database_functions.read_saved_jobs()
    keyword = request.args.get("q", "").strip()
    return render_template("saved.html", jobs=database_functions.search_jobs(jobs, keyword) if keyword else jobs,
                           total=len(jobs), keyword=keyword)


@app.post("/saved/remove")
def remove_saved():
    """Removes one saved job, found by its link (IDs change after every removal)."""
    try:
        database_functions.remove_saved_job(request.form.get("job_url", ""))
    except Exception as error:
        logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
        abort(500)
    return redirect(url_for("saved"))


@app.post("/saved/clear")
def clear_saved():
    """Removes every saved job."""
    try:
        database_functions.clear_saved_jobs()
    except Exception as error:
        logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
        abort(500)
    return redirect(url_for("saved"))


@app.get("/tailor")
def tailor():
    """Page to tailor a resume for one job (?job_url=...). The job comes from Saved jobs or a recent search."""
    job_url = request.args.get("job_url", "")
    job = (database_functions.find_by_url(database_functions.read_saved_jobs(), job_url)
           or io_manager.find_recent_job(job_url))
    if job is None:
        abort(404)
    resumes = io_manager.list_resumes()
    chosen = job.get("resume") if job.get("resume") in resumes else (resumes[0] if resumes else "")
    return render_template("tailor.html", job=job, resumes=resumes, chosen=chosen, ai_name=ai_manager.provider_name())


@app.get("/tailored/<search_id>")
def tailored(search_id):
    """A finished tailored resume: what changed, what was left out, and a preview."""
    search = io_manager.get_task(search_id)
    if search is None:
        abort(404)
    if search["status"] == "running":
        return redirect(url_for("tailor", job_url=search["job_url"], search=search_id))
    if search["status"] != "done":
        return redirect(url_for("tailor", job_url=search["job_url"]))
    return render_template("tailored.html", search_id=search_id, **search["result"])


@app.get("/tailored/<search_id>/download")
def download_tailored(search_id):
    """The tailored resume as a Word file, e.g. nevin_chua_Data_Analyst.docx."""
    search = io_manager.get_task(search_id)
    if search is None or search["status"] != "done":
        abort(404)
    result = search["result"]
    stem = os.path.splitext(result["resume_name"])[0]
    filename = secure_filename(f"{stem}_{result['job']['title']}.docx") or "tailored_resume.docx"
    return send_file(io.BytesIO(io_manager.write_resume_docx(result["resume"])), as_attachment=True,
                     download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.errorhandler(404)
def not_found(error):
    """Friendly page for unknown links, e.g. results from before the server restarted."""
    return render_template("not_found.html"), 404


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


@app.post("/tailor")
def start_tailor():
    """Starts tailoring a resume for one job in a background thread. Returns {"search_id"};
    progress and cancelling use the same routes as a search."""
    job_url = request.form.get("job_url", "")
    job = (database_functions.find_by_url(database_functions.read_saved_jobs(), job_url)
           or io_manager.find_recent_job(job_url))
    if job is None:
        return jsonify(error="That job is no longer in your saved jobs or recent searches."), 404

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

    search_id = io_manager.start_task(job_url=job["job_url"])
    threading.Thread(target=run_tailor, args=(search_id, resume_text, os.path.basename(path), job),
                     daemon=True).start()
    return jsonify(search_id=search_id)


@app.get("/progress/<search_id>")
def progress(search_id):
    """Progress of one search or tailoring run, for the progress bar."""
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

        # 2. AI layer: job search and requirements. on_progress is how ai_manager reports each finished step
        def on_progress(stage, done, total):
            if stage == "search":
                io_manager.update_task(search_id, percent=25 + 15 * done // total,
                                       message=f"Searching the job portal ({done} of {total} searches)...")
            else:
                io_manager.update_task(search_id, percent=40 + 50 * done // total,
                                       message=f"Analysing job listings ({done} of {total} batches)...")

        io_manager.update_task(search_id, percent=25, message="Searching the job portal...")
        summary = {}
        jobs = ai_manager.search_and_extract_jobs(profile, limit_per_search=JOBS_PER_SEARCH, on_progress=on_progress,
                                                  summary=summary)
        logging.info(f"event=web_search stage=jobs summary={summary}")
        if summary["listings_found"] == 0:
            io_manager.update_task(search_id, status="error",
                                   error="Could not get any job listings from MyCareersFuture. Check your internet "
                                         "connection, or try again in a few minutes.")
            return
        if not jobs:
            io_manager.update_task(search_id, status="error",
                                   error=f"The AI could not analyse any of the {summary['listings_found']} listings "
                                         "found. Check your API key, or wait a minute if the AI service is busy.")
            return

        # 3. Logic layer and 4. data layer (a failed save still shows the results)
        io_manager.update_task(search_id, percent=94, message="Ranking and saving your matches...")
        ranked = {"jobs": logic_manager.filter_and_rank(profile, jobs, filters, TOP_N),
                  "removed": logic_manager.count_removed(jobs, filters),
                  "save_failed": False, "newly_saved": 0}
        try:
            ranked["newly_saved"] = database_functions.save_new_jobs(ranked["jobs"], resume_name)
        except Exception as error:
            logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
            ranked["save_failed"] = True

        # all_jobs and filters let the results page re-rank without searching again
        result = {"profile": profile, "all_jobs": jobs, "filters": filters,
                  "resume": resume_name, "ranked": ranked}
        io_manager.update_task(search_id, status="done", percent=100, message="Done", result=result)

    except InterruptedError:  # cancelled (io_manager.update_task)
        return  # the browser has already gone back to the form

    except Exception as error:
        logging.error(f"Search failed ({type(error).__name__}: {error})")
        io_manager.update_task(search_id, status="error",
                               error=f"Something went wrong during the search ({type(error).__name__}). "
                                     "Please try again.")


def run_tailor(search_id, resume_text, resume_name, job):
    """Tailors a resume for one job in a background thread (2. AI layer, 1 AI call).

    Progress bar: 0-25% fetching the job description, 25-90% the AI rewrite, then checks.
    """
    try:
        io_manager.update_task(search_id, percent=5, message="Getting the full job description...")
        details = ai_manager.fetch_job_details(job["job_url"])
        # Without the description the AI still has the job's title and skills
        full_job = {**job, **details} if details else job

        io_manager.update_task(search_id, percent=25,
                               message="Tailoring your resume with AI (about 20-40 seconds)...")
        tailored_resume = ai_manager.tailor_resume(resume_text, full_job)
        if tailored_resume is None:
            io_manager.update_task(search_id, status="error",
                                   error="The AI could not tailor your resume. Check your internet connection "
                                         "and API key, or wait a minute if the AI service is busy.")
            return

        # 3. Logic layer: which of the job's key skills the resume shows, before and after
        io_manager.update_task(search_id, percent=92, message="Checking the result...")
        resume = tailored_resume["resume"]
        required = job.get("required_skills", [])
        # Skills the job wants that the resume doesn't show: never added, so the user can decide
        not_added = {skill.lower(): skill
                     for skill in [*job.get("missing_skills", []), *tailored_resume["removed_skills"]]}

        result = {
            "job": job,
            "resume_name": resume_name,
            "resume": resume,
            "changes": resume.pop("changes"),
            "not_added": list(not_added.values()),
            "notes": tailored_resume["notes"],
            "had_description": details is not None,
            "is_open": details["is_open"] if details else True,
            "required": required,
            "covered_before": logic_manager.keywords_found(required, resume_text),
            "covered_after": logic_manager.keywords_found(required, logic_manager.resume_as_text(resume)),
        }
        io_manager.update_task(search_id, status="done", percent=100, message="Done", result=result)

    except InterruptedError:  # cancelled (io_manager.update_task)
        return

    except Exception as error:
        logging.error(f"Tailoring failed ({type(error).__name__}: {error})")
        io_manager.update_task(search_id, status="error",
                               error=f"Something went wrong while tailoring ({type(error).__name__}). "
                                     "Please try again.")


if __name__ == "__main__":
    # ai_manager turns on INFO logging when imported; show only warnings and errors
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # hide a log line for every progress check
    io_manager.display_message("Resume Job Matcher is running. Open http://127.0.0.1:5000 in your browser "
                               "(Ctrl+C to stop).")
    app.run(host="127.0.0.1", port=5000)
