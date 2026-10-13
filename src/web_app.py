"""Web app (Flask): the same four layers as main.py, in the browser.

Run from the project root with python src/web_app.py, then open http://127.0.0.1:5000. The routes show
the pages; run_search and run_tailor are the background threads, polled through GET /progress/<id>.
To add a page, add a route and a template in src/templates/ that extends base.html.
"""

import io
import logging
import os
import threading

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from flask.typing import ResponseReturnValue
from werkzeug.utils import secure_filename

import ai_manager
import data_manager
import io_manager
import logic_manager

TOP_N = 5  # jobs shown and saved per search, until the user picks another number
JOBS_PER_SEARCH = 10  # listings fetched per job title; more means more AI batches (slower, costs more)
SHOW_OPTIONS = [5, 10, 20]  # "Show" choices on the results page

# Network address the server listens on. 127.0.0.1 = this computer only. The Docker image sets
# WEB_HOST=0.0.0.0, because inside a container 127.0.0.1 can't be reached from outside it
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = 5000

app = Flask(__name__)  # templates/ and static/ are found next to this file
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # largest resume upload: 5 MB (app.js checks it too)

# io_manager's text helpers, so templates show jobs the same way as the console
app.jinja_env.globals.update(format_salary=io_manager.format_salary, join_list=io_manager.join_list)
app.add_template_filter(io_manager.tidy_title, "tidy_title")
app.add_template_filter(io_manager.short_location, "short_location")
app.add_template_filter(io_manager.experience_text, "experience_text")
app.add_template_test(io_manager.same_skill, "same_skill")  # {% if job_skill is same_skill(candidate_skill) %}


# ==========================================
# HELPERS FOR THE ROUTES
# ==========================================

def find_job(job_url: str) -> dict | None:
    """A job by its link: from the saved jobs, else from a recent search (also if the saved jobs can't be opened)."""
    try:
        job = data_manager.find_by_url(data_manager.read_saved_jobs(), job_url)
    except OSError as error:  # e.g. the file is locked; a job from a recent search can still be tailored
        logging.error(f"Could not open saved jobs ({type(error).__name__}: {error})")
        job = None
    return job or io_manager.find_recent_job(job_url)


def resume_from_form() -> tuple[str, str, str]:
    """1. Input layer: the uploaded or chosen resume from the form, as (path, text, problem)."""
    # A name with nothing usable left after secure_filename keeps no extension, so it is rejected
    upload = request.files.get("resume_file")
    uploaded_name = (secure_filename(upload.filename) or "unnamed") if upload and upload.filename else ""
    path, problem = io_manager.resume_path_from_form(uploaded_name, request.form.get("resume_choice", ""))
    if problem:
        return "", "", problem
    if uploaded_name:
        upload.save(path)  # a new file: resume_path_from_form never reuses an existing name
    resume_text, problem = io_manager.load_resume(path)
    if problem and uploaded_name:
        # Don't keep an upload that can't be used, or it would be offered in the resume list from then on
        try:
            os.remove(path)
        except OSError as error:
            logging.warning(f"Could not delete the rejected upload {path} ({type(error).__name__})")
    return path, resume_text, problem


# ==========================================
# PAGES
# ==========================================

@app.get("/")
def index() -> ResponseReturnValue:
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
def results(search_id: str) -> ResponseReturnValue:
    """The profile and top jobs from one finished search."""
    search = io_manager.get_task(search_id)
    if search is None:
        abort(404)
    if search["status"] == "running":
        return redirect(url_for("index", search=search_id))  # the search page picks up its progress
    if search["status"] != "done":
        return redirect(url_for("index"))
    return render_results(search_id, search["result"])


def render_results(search_id: str, result: dict, problem: str = "") -> str:
    """The results page for one finished search. problem: why the filters sent to
    "Update matches" weren't used (io_manager.check_filters), shown above the results."""
    return render_template(
        "results.html",
        search_id=search_id,
        profile=result["profile"],
        **result["ranked"],
        checked=len(result["all_jobs"]),
        step_message=result.get("step_message", ""),
        skill_gaps=logic_manager.find_skill_gaps(result["ranked"]["jobs"]),
        filters=result["filters"],
        filter_problem=problem,
        show=result.get("show", TOP_N),
        show_options=SHOW_OPTIONS,
        job_types=io_manager.JOB_TYPES,
        work_arrangements=io_manager.WORK_ARRANGEMENTS,
        max_salary=io_manager.MAX_SALARY,
        max_years=io_manager.MAX_EXPERIENCE_YEARS,
    )


@app.get("/saved")
def saved() -> ResponseReturnValue:
    """Every job saved by the data layer, or those matching ?q= (saved.js also filters as you type)."""
    try:
        jobs = data_manager.read_saved_jobs()
    except OSError as error:  # e.g. the file is locked or there's no permission to read it
        logging.error(f"Could not open saved jobs ({type(error).__name__}: {error})")
        return render_template("saved.html", jobs=[], total=0, keyword="", load_failed=True)
    keyword = request.args.get("q", "").strip()
    return render_template("saved.html", jobs=data_manager.search_jobs(jobs, keyword) if keyword else jobs,
                           total=len(jobs), keyword=keyword, update_failed=request.args.get("update_failed") == "1")


@app.post("/saved/remove")
def remove_saved() -> ResponseReturnValue:
    """Removes one saved job, found by its link (IDs change after every removal)."""
    try:
        data_manager.remove_saved_job(request.form.get("job_url", ""))
    except Exception as error:
        logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
        return redirect(url_for("saved", update_failed=1))  # the Saved page says what went wrong
    return redirect(url_for("saved"))


@app.post("/saved/clear")
def clear_saved() -> ResponseReturnValue:
    """Removes every saved job."""
    try:
        data_manager.clear_saved_jobs()
    except Exception as error:
        logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
        return redirect(url_for("saved", update_failed=1))
    return redirect(url_for("saved"))


@app.get("/tailor")
def tailor() -> ResponseReturnValue:
    """Page to tailor a resume for one job (?job_url=...). The job comes from Saved jobs or a recent search."""
    job = find_job(request.args.get("job_url", ""))
    if job is None:
        abort(404)
    resumes = io_manager.list_resumes()
    chosen = job.get("resume") if job.get("resume") in resumes else (resumes[0] if resumes else "")
    return render_template("tailor.html", job=job, resumes=resumes, chosen=chosen, ai_name=ai_manager.provider_name())


@app.get("/tailored/<search_id>")
def tailored(search_id: str) -> ResponseReturnValue:
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
def download_tailored(search_id: str) -> ResponseReturnValue:
    """The tailored resume as a Word file, e.g. nevin_chua_Data_Analyst.docx."""
    search = io_manager.get_task(search_id)
    if search is None or search["status"] != "done":
        abort(404)
    result = search["result"]
    stem = os.path.splitext(result["resume_name"])[0]
    filename = secure_filename(f"{stem}_{result['job']['title']}.docx") or "tailored_resume.docx"
    return send_file(io.BytesIO(result["docx_bytes"]), as_attachment=True,
                     download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.errorhandler(404)
def not_found(error: Exception) -> ResponseReturnValue:
    """Friendly page for unknown links, e.g. results from before the server restarted."""
    return render_template("not_found.html"), 404


@app.errorhandler(500)
def server_error(error: Exception) -> ResponseReturnValue:
    """Friendly page for any error a page didn't handle, instead of Flask's plain one (the details go to the log)."""
    logging.error(f"Unexpected error on {request.path} ({type(getattr(error, 'original_exception', error)).__name__})")
    return render_template("server_error.html"), 500


@app.errorhandler(413)
def too_large(error: Exception) -> ResponseReturnValue:
    """Uploads over MAX_CONTENT_LENGTH. Sent as JSON because app.js sends the form."""
    megabytes = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify(error=f"That file is too large. Please upload a resume under {megabytes} MB."), 413


# ==========================================
# SEARCH API (used by static/progress.js)
# ==========================================

@app.post("/search")
def start_search() -> ResponseReturnValue:
    """Checks the form, then starts the search in a background thread. Returns {"search_id"}."""
    # 1. Input layer: the uploaded resume (saved into the resumes folder) or the one picked from the list
    path, resume_text, problem = resume_from_form()
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


@app.post("/results/<search_id>/update")
def update_search(search_id: str) -> ResponseReturnValue:
    """"Update matches" on the results page: a new search with the new filters, reusing this search's
    profile so the resume isn't read again. Opens the search page, which shows the new search's progress."""
    search = io_manager.get_task(search_id)
    if search is None or search["status"] != "done":
        abort(404)
    filters, problem = io_manager.check_filters(request.form)
    if problem:
        return render_results(search_id, search["result"], problem)
    show = request.form.get("show", TOP_N, type=int)
    show = show if show in SHOW_OPTIONS else TOP_N

    result = search["result"]
    new_id = io_manager.start_task()
    threading.Thread(target=run_search, daemon=True,
                     args=(new_id, "", result["resume"], filters, "", ""),
                     kwargs={"profile": result["profile"], "show": show}).start()
    return redirect(url_for("index", search=new_id))


@app.post("/tailor")
def start_tailor() -> ResponseReturnValue:
    """Starts tailoring a resume for one job in a background thread. Returns {"search_id"};
    progress and cancelling use the same routes as a search."""
    job = find_job(request.form.get("job_url", ""))
    if job is None:
        return jsonify(error="That job is no longer in your saved jobs or recent searches."), 404

    path, resume_text, problem = resume_from_form()
    if problem:
        return jsonify(error=problem), 400

    search_id = io_manager.start_task(job_url=job["job_url"])
    threading.Thread(target=run_tailor, args=(search_id, resume_text, path, job), daemon=True).start()
    return jsonify(search_id=search_id)


@app.get("/progress/<search_id>")
def progress(search_id: str) -> ResponseReturnValue:
    """Progress of one search or tailoring run, for the progress bar."""
    search = io_manager.get_task(search_id)
    if search is None:
        return jsonify(error="This search is no longer available. Please start a new one."), 404
    return jsonify(status=search["status"], percent=search["percent"],
                   message=search["message"], error=search["error"])


@app.post("/search/<search_id>/cancel")
def cancel_search(search_id: str) -> ResponseReturnValue:
    """Stops a running search. The background thread stops at its next progress update."""
    io_manager.cancel_task(search_id)
    return jsonify(ok=True)


# ==========================================
# BACKGROUND TASKS (the work each thread does, like main.py's search)
# ==========================================

def run_search(search_id: str, resume_text: str, resume_name: str, filters: dict, fallback_qualification: str,
               fallback_skills: str, profile: dict | None = None, show: int = TOP_N) -> None:
    """Runs the AI, logic and data layers in a background thread, reporting progress as it goes."""
    try:
        if profile is None:  # "Update matches" passes the earlier search's profile, so the resume isn't read again
            profile = read_profile_for_search(search_id, resume_text, fallback_qualification, fallback_skills)
            if profile is None:
                return
        jobs, summary = find_jobs_for_search(search_id, profile, filters)
        problem = io_manager.search_problem_message(summary["listings_found"], len(jobs))
        if problem:
            io_manager.update_task(search_id, status="error", error=problem)
            return

        io_manager.update_task(search_id, percent=94, message="Ranking and saving your matches...")
        ranked = rank_and_save(profile, jobs, filters, summary["search_step"], resume_name, show)
        # all_jobs lets the tailor page find a job from this search (io_manager.find_recent_job);
        # filters stays as the user typed it, for the results page's "Update matches" form
        result = {"profile": profile, "all_jobs": jobs, "filters": filters, "resume": resume_name, "ranked": ranked,
                  "step_message": io_manager.search_step_message(summary["search_step"], filters), "show": show}
        io_manager.update_task(search_id, status="done", percent=100, message="Done", result=result)

    except InterruptedError:  # cancelled (io_manager.update_task)
        return  # the browser has already gone back to the form

    except Exception as error:
        logging.error(f"Search failed ({type(error).__name__}: {error})")
        io_manager.update_task(search_id, status="error",
                               error=f"Something went wrong during the search ({type(error).__name__}). "
                                     "Please try again.")


def read_profile_for_search(search_id: str, resume_text: str, fallback_qualification: str,
                            fallback_skills: str) -> dict | None:
    """2. AI layer: the resume's profile, with what it missed filled in from the form; None if the AI failed."""
    io_manager.update_task(search_id, percent=5, message="Reading your resume with AI...")
    profile = ai_manager.extract_candidate_profile(resume_text)
    if profile is None:
        io_manager.update_task(search_id, status="error", error=io_manager.ai_unavailable_message())
        return None
    # 1. Input layer: details the AI missed, from the form's optional fields
    return io_manager.fill_missing_data(profile, fallback_qualification, fallback_skills)


def find_jobs_for_search(search_id: str, profile: dict, filters: dict) -> tuple[list[dict], dict]:
    """1. Input layer: job listings, then 2. AI layer: their requirements, with progress. Returns (jobs, summary)."""
    # on_progress is how io_manager and ai_manager report each finished step
    def on_progress(stage: str, done: int, total: int) -> None:
        """Shows the search's progress on its task: stage is "search" or "analyse", done of total finished."""
        if stage == "search":
            io_manager.update_task(search_id, percent=25 + 15 * done // total,
                                   message=f"Searching the job portal ({done} of {total} searches)...")
        else:
            io_manager.update_task(search_id, percent=40 + 50 * done // total,
                                   message=f"Analysing job listings ({done} of {total} batches)...")

    io_manager.update_task(search_id, percent=25, message="Searching the job portal...")
    summary = {}
    # The portal is searched with the preferences it can filter, and only listings that meet all of
    # them are analysed; with none, it falls back to the job type only, then to no preferences
    # (logic_manager.search_steps, summary["search_step"])
    listings = io_manager.fetch_listings(
        profile["search_keywords"], logic_manager.search_steps(filters), filters,
        limit_per_search=JOBS_PER_SEARCH, on_progress=on_progress, summary=summary,
        fits=lambda job, step: logic_manager.passes_filters(job, logic_manager.filters_for_step(filters, step)))
    answers = ai_manager.extract_requirements_for_listings(listings, profile, on_progress=on_progress, summary=summary)
    # 3. Logic layer: each listing with its answer, and its matched and missing skills
    jobs = logic_manager.add_requirements(listings, answers, profile)
    logging.info(f"event=web_search stage=jobs summary={summary}")
    return jobs, summary


def rank_and_save(profile: dict, jobs: list[dict], filters: dict, search_step: str, resume_name: str,
                  show: int) -> dict:
    """3. Logic layer, then 4. data layer: the best jobs, saved (a failed save still shows them).
    Returns {"jobs", "removed", "save_failed", "newly_saved"} for the results page."""
    # Only the preferences the search kept are applied: one it had to drop would empty the results again
    applied_filters = logic_manager.filters_for_step(filters, search_step)
    ranked = {"jobs": logic_manager.filter_and_rank(profile, jobs, applied_filters, show),
              "removed": logic_manager.count_removed(jobs, applied_filters),
              "save_failed": False, "newly_saved": 0}
    try:
        ranked["newly_saved"] = data_manager.save_new_jobs(ranked["jobs"], resume_name)
    except Exception as error:
        logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
        ranked["save_failed"] = True
    return ranked


def run_tailor(search_id: str, resume_text: str, resume_path: str, job: dict) -> None:
    """Tailors a resume for one job in a background thread: a Word file is reworded in place, a PDF rewritten."""
    try:
        io_manager.update_task(search_id, percent=5, message="Getting the full job description...")
        details = io_manager.fetch_job_details(job["job_url"])
        # Without the description the AI still has the job's title and skills
        full_job = {**job, **details} if details else job

        # A job's skill card was worked out for the resume that found it, so for another resume it doesn't
        # apply: the AI is not told those skills, and none of them count as shown by this resume
        resume_name = os.path.basename(resume_path)
        # 3. Logic layer: the card's skills only apply to the resume that found the job
        job_for_resume, same_resume = logic_manager.resume_view(full_job, resume_name)
        # 3. Logic layer: which of the job's words the AI may use (the ones this resume shows)
        use_keywords, avoid_keywords = logic_manager.job_keywords(job_for_resume, resume_text)

        io_manager.update_task(search_id, percent=25,
                               message="Tailoring your resume with AI (about 20-40 seconds)...")
        if resume_path.lower().endswith(".docx"):
            tailored = tailor_word_resume(search_id, resume_path, resume_text, job_for_resume,
                                          use_keywords, avoid_keywords)
        else:
            tailored = tailor_pdf_resume(search_id, resume_text, job_for_resume, avoid_keywords)
        if tailored is None:
            io_manager.update_task(search_id, status="error",
                                   error="The AI could not tailor your resume. Check your internet connection "
                                         "and API key, or wait a minute if the AI service is busy.")
            return

        result = tailoring_result(job, resume_name, same_resume, avoid_keywords, tailored, details)
        io_manager.update_task(search_id, status="done", percent=100, message="Done", result=result)

    except InterruptedError:  # cancelled (io_manager.update_task)
        return

    except Exception as error:
        logging.error(f"Tailoring failed ({type(error).__name__}: {error})")
        io_manager.update_task(search_id, status="error",
                               error=f"Something went wrong while tailoring ({type(error).__name__}). "
                                     "Please try again.")


def tailoring_result(job: dict, resume_name: str, same_resume: bool, avoid_keywords: list[str], tailored: dict,
                     details: dict | None) -> dict:
    """What the tailored page shows: the new resume, the changes and notes, and which of the job's skills it shows."""
    # 3. Logic layer: which of the card's skills apply to this resume, and which the job wants but it doesn't show
    skills = logic_manager.tailoring_skills(job, same_resume, avoid_keywords, tailored["removed_skills"])
    matched = skills["matched"]
    return {
        "job": job,
        "resume_name": resume_name,
        "matched_with": None if same_resume else job.get("resume"),
        "resume": tailored["resume"],
        "paragraphs": tailored["paragraphs"],
        "docx_bytes": tailored["docx_bytes"],
        "changes": tailored["changes"],
        "not_added": skills["not_added"],
        "notes": io_manager.describe_tailoring_notes(tailored["notes"]),
        "had_description": details is not None,
        "is_open": details["is_open"] if details else True,
        "required": job.get("required_skills", []),
        "matched": matched,
        # 3. Logic layer: which of the job card's matched skills the tailored resume still shows
        "shown": logic_manager.skills_shown(matched, tailored["skill_evidence"], tailored["parts"]),
    }


def tailor_word_resume(search_id: str, resume_path: str, resume_text: str, job: dict,
                       use_keywords: list[str], avoid_keywords: list[str]) -> dict | None:
    """A Word resume reworded in place (AI layer), with the logic layer's rules applied; None if the AI failed."""
    paragraphs = io_manager.read_docx_paragraphs(resume_path)
    tailored = ai_manager.tailor_docx(paragraphs, job, use_keywords, avoid_keywords)
    if tailored is None:
        return None
    io_manager.update_task(search_id, percent=92, message="Checking the result...")
    # 3. Logic layer: the AI's edits may not add anything the resume doesn't show, never leave a heading
    # with no points, and keep the number of pages
    sent = tailored["paragraphs"]
    edits, reasons, removed_skills, check_notes = logic_manager.ground_docx_edits(
        tailored["edits"], sent, resume_text, avoid_keywords)
    edits, group_notes = logic_manager.keep_bullet_groups(edits, sent)
    edits, page_notes = logic_manager.fit_edits_to_page_count(edits, sent)
    # Small layout tidy-ups (e.g. a certification's year moved to the right), shown in the preview too
    fixes, fix_notes = io_manager.find_layout_fixes(paragraphs, edits)
    preview = io_manager.word_preview(paragraphs, edits, fixes)
    return {
        "resume": None,
        "paragraphs": preview,
        "docx_bytes": io_manager.write_edited_docx(resume_path, edits, fixes),
        "changes": io_manager.describe_docx_changes(sent, edits, reasons) + fix_notes,
        "notes": check_notes + group_notes + page_notes,
        "removed_skills": removed_skills,
        "skill_evidence": tailored["skill_evidence"],
        "parts": [paragraph["text"] for paragraph in preview if not paragraph["removed"]],
    }


def tailor_pdf_resume(search_id: str, resume_text: str, job: dict, avoid_keywords: list[str]) -> dict | None:
    """A PDF resume rewritten for the job (AI layer) as a new Word file; None if the AI failed."""
    tailored = ai_manager.tailor_resume(resume_text, job)
    if tailored is None:
        return None
    io_manager.update_task(search_id, percent=92, message="Checking the result...")
    skill_evidence = tailored.pop("skill_evidence")
    # 3. Logic layer: remove anything the original resume doesn't contain
    resume, removed_skills, notes = logic_manager.ground_tailored_resume(tailored, resume_text, avoid_keywords)
    resume = io_manager.tidy_resume_dates(resume)
    changes = resume.pop("changes")
    return {
        "resume": resume,
        "paragraphs": None,
        "docx_bytes": io_manager.write_resume_docx(resume),
        "changes": changes,
        "notes": notes,
        "removed_skills": removed_skills,
        "skill_evidence": skill_evidence,
        "parts": logic_manager.resume_parts(resume),
    }


if __name__ == "__main__":
    # Show only warnings and errors in the terminal
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # hide a log line for every progress check
    # 4. Data layer: load every saved job on startup (a missing or corrupt file gives an empty list)
    try:
        saved_count = len(data_manager.read_saved_jobs())
        io_manager.display_message(f"{saved_count} saved job{'s' if saved_count != 1 else ''} loaded.")
    except OSError as error:
        logging.error(f"Could not open saved jobs ({type(error).__name__}: {error})")
        io_manager.display_error("Could not open your saved jobs file. New searches still work.")
    io_manager.display_message(f"Resume Job Matcher is running. Open http://127.0.0.1:{WEB_PORT} in your browser "
                               "(Ctrl+C to stop).")
    app.run(host=WEB_HOST, port=WEB_PORT)
