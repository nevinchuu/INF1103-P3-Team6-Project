"""IO Manager: every boundary between the program and the person using it, and the only place that prints.

The web pages call IOManager and nothing else. It holds the three managers (AI, logic, data), builds the parts that
do the work (the search and tailoring services, the resume files, the Word editor, the task registry), words every
message and hands each page the facts it shows.
"""

import datetime
import logging
import os
import re
import threading
from collections.abc import Callable

from werkzeug.utils import secure_filename

from ai_layer.ai_manager import AIManager
from data_layer.data_manager import DataManager, MAX_NOTE_CHARS, STATUSES
from data_layer.data_store_error import DataStoreError
from io_layer.docx_editor import DocxEditor
from io_layer.input_validator import InputValidator, MAX_STEPS, MAX_UPLOAD_BYTES, SHOW_OPTIONS
from logic_layer.logic_manager import (EDUCATION_LEVELS, JOB_TYPES, LogicManager, MAX_EXPERIENCE_YEARS, MAX_SALARY,
                                       OUTCOME_STRONG, WORK_ARRANGEMENTS)
from io_layer.resume_files import DEFAULT_RESUME_FOLDER, ResumeFiles
from io_layer.search_service import SearchService, TOP_N
from io_layer.tailoring_service import TailoringService
from io_layer.task_registry import TaskRegistry

# Short words kept in capitals when an all-caps title is tidied, e.g. "IT SUPPORT" -> "IT Support"
ACRONYMS = {"IT", "HR", "AI", "UX", "UI", "QA", "PR", "F&B", "SQL", "AWS", "SAP", "CRM", "ERP", "IOS", "PHP"}

# The log file the web app writes to: the terminal stays readable, while INFO and up (every search's
# listings_found vs listings_analysed, and every handled failure) is kept for looking at afterwards
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs",
                        "job_matcher.log")  # <project root>/logs/ (this file is in src/io_layer/)
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(module)s | %(message)s"


# What the Saved page says after a change, by outcome code (the codes travel in the page address, so a link
# someone crafted can't put its own words on the page)
SAVED_PROBLEMS = ("status", "note", "gone")
SAVED_DONE = ("updated", "removed", "cleared")


class IOManager:
    """The one class the web pages talk to: it holds the managers, builds the services and words the messages."""

    @staticmethod
    def __set_up_logging(terminal_level: int = logging.WARNING) -> None:
        """Keeps the terminal to warnings and errors, and writes everything (INFO and up) to the log file.
        The web app calls this at start-up. Called twice, it does
        not add the file handler again."""
        logging.basicConfig(level=logging.INFO)  # the terminal handler, turned down just below
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        # The log file keeps INFO, so only the other handlers (the terminal) are turned down. Turning them
        # all down would silence the log file on the second call, which is what a web run used to do.
        already_open = [handler for handler in root_logger.handlers
                        if isinstance(handler, logging.FileHandler)
                        and getattr(handler, "baseFilename", "") == os.path.abspath(LOG_PATH)]
        for terminal_handler in root_logger.handlers:
            if terminal_handler not in already_open:
                terminal_handler.setLevel(terminal_level)
        if already_open:
            return
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            log_file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            log_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            root_logger.addHandler(log_file_handler)
        except OSError as error:
            # No log file (e.g. a read-only folder): the program still runs, with terminal logging only
            logging.warning(f"Could not open the log file {LOG_PATH} ({type(error).__name__}); "
                            "logging to the terminal only")

    def __init__(self, ai: AIManager, logic: LogicManager, data: DataManager,
                 resume_folder: str = DEFAULT_RESUME_FOLDER, spawn: Callable[..., None] | None = None) -> None:
        # Association: the three managers are built outside and handed in; they outlive any one request, and tests
        # give them fakes. IOManager works with each through its public methods and owns none of them
        self.__ai = ai
        self.__logic = logic
        self.__data = data
        # `spawn(work, *args, **kwargs)` starts background work; tests run it at once, in the same thread
        self.__spawn = spawn if spawn is not None else IOManager.__start_thread
        # Composition: these parts are built here and belong to this IOManager alone; nothing else uses them, and
        # they go when it goes. The validator checks everything the user sends before any work starts (the brief
        # gives "collect and validate user input" to the IO manager). The Word editor comes before the tailoring
        # service, which is built with it
        self.__validator = InputValidator()
        self.__files = ResumeFiles(resume_folder)
        self.__docx = DocxEditor()
        self.__tasks = TaskRegistry()
        self.__search = SearchService(ai, logic, data)
        self.__tailoring = TailoringService(ai, logic, data, self.__docx)

    @staticmethod
    def __start_thread(work: Callable[..., None], *args: object, **kwargs: object) -> None:
        """Runs work in a background thread (how a search or a tailoring run starts in the real app)."""
        threading.Thread(target=work, args=args, kwargs=kwargs, daemon=True).start()

    # ---------- what the pages show: form choices, the resume list and the saved jobs ----------

    def resume_choices(self) -> list[str]:
        """The saved resumes the search and tailor forms offer."""
        return self.__files.list_resumes()

    def form_options(self) -> dict:
        """The choices and limits the search forms show (education levels without "None")."""
        return {"job_types": JOB_TYPES, "work_arrangements": WORK_ARRANGEMENTS,
                "education_levels": EDUCATION_LEVELS[1:], "max_salary": MAX_SALARY,
                "max_years": MAX_EXPERIENCE_YEARS, "max_upload": MAX_UPLOAD_BYTES, "statuses": STATUSES,
                "show_options": SHOW_OPTIONS, "max_steps": MAX_STEPS, "ai_name": self.__ai.provider_name}

    def start_up(self, address: str) -> None:
        """Sets up logging and tells the person how many jobs are saved and where to open the app.
        address: where the server listens, e.g. "127.0.0.1:5000". The only place the program prints."""
        # Warnings and errors in the terminal; everything (INFO and up) to logs/job_matcher.log, the same
        # record of what it found and analysed
        IOManager.__set_up_logging()
        logging.getLogger("werkzeug").setLevel(logging.WARNING)  # hide a log line for every progress check
        logging.info(f"event=start app=web provider={self.__ai.provider_name}")
        try:
            saved_count = len(self.__data.read_saved_jobs())
            print(f"{saved_count} saved job{'s' if saved_count != 1 else ''} loaded.")
        except DataStoreError as error:
            logging.error(f"Could not open saved jobs ({type(error).__name__}: {error})")
            print("[!] Could not open your saved jobs file. New searches still work.")
        print(f"Resume Job Matcher is running. Open http://{address} in your browser (Ctrl+C to stop).")

    def saved_jobs(self, keyword: str = "", outcome: str = "") -> dict:
        """The Saved page: the jobs (those matching keyword, if given), how many are saved, the application tracker
        and the message for the last change (outcome is the code update_saved, remove_saved or clear_saved gave)."""
        keyword = keyword.strip()
        try:
            jobs = self.__data.search_saved(keyword) if keyword else self.__data.read_saved_jobs()
            tracker = self.__data.tracker_summary()  # counted by the storage
        except DataStoreError as error:  # e.g. the database is down
            logging.error(f"Could not open saved jobs ({type(error).__name__}: {error})")
            return {"jobs": [], "total": 0, "keyword": "", "load_failed": True}
        return {"jobs": [self.__present_job(job) for job in jobs], "total": tracker["total"], "keyword": keyword,
                "statuses": STATUSES, "tracker": tracker, "load_failed": False, "update_failed": outcome == "failed",
                "problem": self.__problem_message(outcome) if outcome in SAVED_PROBLEMS else "",
                "done": self.__problem_message(outcome) if outcome in SAVED_DONE else ""}

    def update_saved(self, values: dict) -> str:
        """Sets the status and note of one saved job (values: job_url, status, note). Returns an outcome code:
        "updated", "status" or "note" (what was wrong), "gone" (no longer saved) or "failed" (store unreachable)."""
        status = values.get("status") or None
        note = values.get("note")
        if self.__validator.check_saved_update(status, note):
            return "status" if status not in (None, *STATUSES) else "note"
        try:
            found = self.__data.update_saved_job(values.get("job_url", ""), status=status, note=note)
        except DataStoreError as error:  # the saved jobs can't be reached
            logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
            return "failed"
        return "updated" if found else "gone"

    def remove_saved(self, job_url: str) -> str:
        """Removes one saved job, found by its link (IDs change after every removal). Returns "removed", "gone"
        (it wasn't saved) or "failed" (store unreachable)."""
        try:
            removed = self.__data.remove_saved_job(job_url)
        except DataStoreError as error:
            logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
            return "failed"
        return "removed" if removed else "gone"

    def clear_saved(self) -> str:
        """Removes every saved job. Returns "cleared", or "failed" (store unreachable)."""
        try:
            self.__data.clear_saved_jobs()
        except DataStoreError as error:
            logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
            return "failed"
        return "cleared"

    # ---------- the search: start it, follow it, cancel it, read its results ----------

    def start_search(self, values: dict, resume: bytes = b"") -> str:
        """Checks the search form, then starts the search in the background and returns its task id.
        values: min_salary, max_salary, max_years_experience, job_type, work_arrangement (as typed), resume_choice (a
        saved resume's name), resume_filename (the upload's name, "" if none), fallback_qualification,
        fallback_skills, show, and from_search (the id of a finished search to update: its profile and resume are
        reused and no file is read). resume: the uploaded file's bytes. Raises ValueError(message) for input that
        can't be used and LookupError if from_search is not a finished search."""
        resume_text, resume_name, profile = "", "", None
        # The filters are checked first, so a form sent back for a wrong filter leaves no copy of its upload behind
        filters, problem = self.__validator.check_filters(values)
        if problem:
            raise ValueError(problem)
        if values.get("from_search"):
            earlier = self.__finished_task(values["from_search"], "search")
            if earlier is None:
                raise LookupError("That search is not finished.")
            resume_name, profile = earlier["resume"], earlier["profile"]
        else:
            path, resume_text = self.__resume_from(values, resume)
            resume_name = os.path.basename(path)
        # The form's other answers are not filters: the filters stay the five the search is judged by
        fallback_qualification = filters.pop("fallback_qualification")
        fallback_skills = filters.pop("fallback_skills")
        show = filters.pop("show")
        task_id = self.__tasks.start(kind="search")
        self.__spawn(self.__run_search, task_id, resume_text, resume_name, filters, fallback_qualification,
                     fallback_skills, profile=profile, show=show)
        return task_id

    def search_progress(self, task_id: str) -> dict:
        """How a search or tailoring run is going: status ("running", "done", "error", "cancelled", or "missing" for an
        unknown id), percent, message, error, and its kind ("search" or "tailor") and job link (tailoring)."""
        task = self.__tasks.get(task_id)
        if task is None:
            return {"status": "missing", "percent": 0, "message": "",
                    "error": "This search is no longer available. Please start a new one.", "kind": "",
                    "job_url": ""}
        return {"status": task["status"], "percent": task["percent"], "message": task["message"],
                "error": task["error"], "kind": task.get("kind", ""), "job_url": task.get("job_url", "")}

    def cancel_search(self, task_id: str) -> None:
        """Stops a running search or tailoring run: its background thread stops at its next progress update."""
        self.__tasks.cancel(task_id)

    def search_results(self, task_id: str) -> dict:
        """What the results page shows for a finished search, or {} if the task is not one."""
        result = self.__finished_task(task_id, "search")
        if result is None:
            return {}
        ranked = result["ranked"]
        # A close match (SearchService.rank_and_save) says which of the user's filters it misses
        filters = result["filters"]
        misses = {"salary": "Pay below your minimum", "experience": "Asks for more experience",
                  "job type": f"Not {filters['job_type']}", "work arrangement": f"Not {filters['work_arrangement']}"}
        jobs = [{**self.__present_job(job), "close_reason_text": misses.get(job.get("close_reason", ""), "Outside your filters")}
                if job.get("close_match") else self.__present_job(job) for job in ranked["jobs"]]
        return {"search_id": task_id, "profile": result["profile"],
                "jobs": jobs, "removed": ranked["removed"],
                "save_failed": ranked["save_failed"], "newly_saved": ranked["newly_saved"],
                "checked": len(result["all_jobs"]), "step_message": result["step_message"],
                "partial_message": result["partial_message"],
                "skill_gaps": self.__logic.find_skill_gaps(ranked["jobs"]), "filters": result["filters"],
                "show": result["show"]}

    def __finished_task(self, task_id: str, kind: str) -> dict | None:
        """The result of a finished task of this kind ("search" or "tailor"), or None, so a tailoring run's id can't
        open a results page (or the other way round)."""
        task = self.__tasks.get(task_id)
        if task is None or task.get("kind") != kind or task["status"] != "done":
            return None
        return task["result"]

    def __resume_from(self, values: dict, resume: bytes) -> tuple[str, str]:
        """The resume the form named, as (path, text): the upload, saved and read back, or the saved one chosen.
        An upload identical to a saved resume uses that file instead of saving another copy.
        Raises ValueError(message) if it can't be used; an upload that can't be read is not kept."""
        uploaded_name = values.get("resume_filename", "")
        is_new_file = False
        if uploaded_name:
            problem = self.__validator.check_upload(uploaded_name, len(resume))
            if problem:
                raise ValueError(problem)
            try:
                path = self.__files.find_copy(resume, uploaded_name)
                if path is None:
                    path = self.__files.save_upload(resume, uploaded_name)  # a new file: never replaces a saved one
                    is_new_file = True
            except OSError as error:  # e.g. the disk is full or the folder can't be written
                logging.error(f"Could not save the upload {uploaded_name} ({type(error).__name__}: {error})")
                raise ValueError("Could not save your resume on the server. Please try again, "
                                 "or choose a saved one.") from error
        else:
            chosen_name = values.get("resume_choice", "")
            if chosen_name not in self.__files.list_resumes():
                raise ValueError("Please choose a resume or upload one.")
            path = self.__files.path_of(chosen_name)
        file_name, text = self.__files.load(path)
        problem = self.__validator.check_resume(file_name, text)
        if problem:
            if is_new_file:  # never an earlier saved resume the upload matched
                self.__files.remove_upload(path)  # an upload that can't be used would be offered in the resume list
            raise ValueError(problem)
        return path, text

    def __run_search(self, task_id: str, resume_text: str, resume_name: str, filters: dict,
                     fallback_qualification: str, fallback_skills: list[str], profile: dict | None = None,
                     show: int = TOP_N) -> None:
        """Runs the AI, logic and data steps in a background thread, reporting progress as it goes."""
        try:
            if profile is None:  # "Update matches" passes the earlier search's profile, so the resume isn't read again
                self.__tasks.update(task_id, 5, "Reading your resume with AI...")
                profile = self.__search.read_profile(resume_text, {"qualification": fallback_qualification,
                                                                   "skills": fallback_skills})
                if profile is None:
                    self.__tasks.fail(task_id, self.__problem_message(
                        "ai_not_set_up" if self.__ai.setup_problem else "ai_unavailable"))
                    return

            # on_progress is how the portal, the postings and AIManager report each finished step. Each stage has its
            # own part of the bar, and the bar never goes back (TaskRegistry.update)
            def on_progress(stage: str, done: int, total: int) -> None:
                """Shows the search's progress on its task: stage is "search", "postings" or "analyse", done of
                total finished."""
                # The bar shows how far along it is, so the messages say only what is happening, with no counts
                if stage == "search":
                    percent, text = 25 + 10 * done // total, "Searching MyCareersFuture..."
                elif stage == "postings":
                    percent, text = 35 + 10 * done // total, "Reading the job postings..."
                else:
                    percent, text = 45 + 47 * done // total, "Reading job requirements..."
                self.__tasks.update(task_id, percent, text)

            self.__tasks.update(task_id, 25, "Searching MyCareersFuture...")
            jobs, summary = self.__search.find_jobs(profile, filters, on_progress=on_progress)
            problem = self.__search_problem_message(summary["listings_found"], len(jobs),
                                                  summary.get("searches_errored", 1) > 0)
            if problem:
                self.__tasks.fail(task_id, problem)
                return

            self.__tasks.update(task_id, 94, "Ranking and saving your matches...")
            kept_filters = self.__logic.filters_for_step(filters, summary["search_step"])
            ranked = self.__search.rank_and_save(profile, jobs, kept_filters, resume_name, show)
            # all_jobs lets the tailor page find a job from this search (TaskRegistry.find_recent_job);
            # filters stays as the user typed it, for the results page's "Update matches" form
            result = {"profile": profile, "all_jobs": jobs, "filters": filters, "resume": resume_name,
                      "search_step": summary["search_step"],
                      "partial_message": self.__partial_results_message(summary, len(jobs)),
                      "ranked": ranked, "step_message": self.__search_step_message(summary["search_step"], filters),
                      "show": show}
            self.__tasks.finish(task_id, result)

        except InterruptedError:  # cancelled (TaskRegistry.update)
            return  # the browser has already gone back to the form

        except Exception as error:
            logging.error(f"Search failed ({type(error).__name__}: {error})")
            try:
                # The error's type is in the log line above, not in the message the user reads
                self.__tasks.fail(task_id, "Something went wrong during the search. Please try again.")
            except InterruptedError:  # cancelled meanwhile: nothing left to report
                pass

    # ---------- tailoring a resume ----------

    def tailoring_form(self, job_url: str) -> dict | None:
        """The Tailor page: the job (from the saved jobs or a recent search), the resumes to choose from and the one
        preselected (the resume that found the job, else the first). None if the job can't be found."""
        job = self.__find_job("", job_url)
        if job is None:
            return None
        resumes = self.__files.list_resumes()
        chosen = job.get("resume") if job.get("resume") in resumes else (resumes[0] if resumes else "")
        return {"job": self.__present_job(job), "resumes": resumes, "chosen": chosen,
                "ai_name": self.__ai.provider_name}

    def start_tailoring(self, task_id: str, job_url: str, values: dict | None = None, resume: bytes = b"") -> str:
        """Starts tailoring a resume for one job in the background and returns its task id; progress and cancelling
        use the same methods as a search. task_id: the search the job came from ("" if it came from Saved jobs).
        values/resume: the resume chosen or uploaded, as for start_search; with none, the resume that found the job
        is used. A second click on the same job and resume follows the first run. Raises LookupError if the job
        can't be found and ValueError(message) if the resume can't be used."""
        job = self.__find_job(task_id, job_url)
        if job is None:
            raise LookupError("That job is no longer in your saved jobs or recent searches.")
        path, resume_text = self.__resume_from(values or {"resume_choice": job.get("resume", "")}, resume)
        running = self.__tasks.find_running(kind="tailor", job_url=job["job_url"], resume_path=path)
        if running is None:
            running = self.__tasks.start(kind="tailor", job_url=job["job_url"], resume_path=path)
            self.__spawn(self.__run_tailoring, running, resume_text, path, job)
        return running

    def tailored_resume(self, task_id: str) -> tuple[dict, bytes]:
        """A finished tailored resume as (what the page shows, the Word file's bytes); ({}, b"") if the task is not
        a finished tailoring run. The page shows what changed, what was left out and a preview."""
        result = self.__finished_task(task_id, "tailor")
        if result is None:
            return {}, b""
        page = {key: value for key, value in result.items() if key != "docx_bytes"}
        page["search_id"] = task_id
        page["job"] = self.__present_job(result["job"])
        page["matched"] = [{**match, "same_name": self.__same_skill(match["job_skill"], match["candidate_skill"])}
                           for match in result["matched"]]
        stem = os.path.splitext(result["resume_name"])[0]
        page["download_name"] = secure_filename(f"{stem}_{result['job']['title']}.docx") or "tailored_resume.docx"
        return page, result["docx_bytes"]

    def __find_job(self, task_id: str, job_url: str) -> dict | None:
        """A job by its link: from that search's jobs (task_id), else the saved jobs, else a recent search (also if
        the saved jobs can't be opened)."""
        searched = self.__finished_task(task_id, "search") if task_id else None
        for job in (searched["all_jobs"] if searched else []):
            if job["job_url"] == job_url:
                return {**job, "resume": searched["resume"]}
        try:
            job = self.__data.get_saved_job(job_url)
        except DataStoreError as error:  # e.g. the database is down; a job from a recent search can still be tailored
            logging.error(f"Could not open saved jobs ({type(error).__name__}: {error})")
            job = None
        return job or self.__tasks.find_recent_job(job_url)

    def __run_tailoring(self, task_id: str, resume_text: str, resume_path: str, job: dict) -> None:
        """Tailors a resume for one job in a background thread: a Word file is reworded in place, a PDF rewritten."""
        def on_progress(percent: int, message: str) -> None:
            self.__tasks.update(task_id, percent, message)

        try:
            result = self.__tailoring.tailor(job, resume_path, resume_text, on_progress)
            if result is None:
                self.__tasks.fail(task_id, self.__problem_message("ai_not_set_up") if self.__ai.setup_problem else
                                  "The AI couldn't tailor your resume just now. Please try again in a minute.")
                return
            # The tailoring service returns records; they are worded here
            result["changes"] = self.__describe_changes(result["changes"])
            result["notes"] = self.__describe_changes(result["notes"])
            stem = os.path.splitext(result["resume_name"])[0]
            try:  # a copy in resumes/tailored/, so the file survives the server restarting
                result["saved_as"] = self.__files.save_tailored(result["docx_bytes"], f"{stem}_{job['title']}.docx")
            except OSError as error:
                logging.error(f"Could not save the tailored resume ({type(error).__name__}: {error})")
                result["saved_as"] = ""
            self.__tasks.finish(task_id, result)

        except InterruptedError:  # cancelled (TaskRegistry.update)
            return

        except Exception as error:
            logging.error(f"Tailoring failed ({type(error).__name__}: {error})")
            try:
                self.__tasks.fail(task_id, "Something went wrong while tailoring. Please try again.")
            except InterruptedError:  # cancelled meanwhile: nothing left to report
                pass

    # ---------- what if I learned...? ----------

    def what_if(self, task_id: str, values: dict) -> dict:
        """The What-If page for a finished search, testing skills (no AI involved: it re-ranks the jobs already
        found). values: skills (the ticked ones), extra_skills (typed), top_n; empty values give just the form.
        {} if the task is not a finished search."""
        return self.__whatif_page(task_id, {**values, "mode": "test"} if values else {})

    def roadmap(self, task_id: str, values: dict) -> dict:
        """The What-If page with a roadmap: which skills to learn and in what order. values: mode (a planner's key),
        steps, top_n and optionally skills to choose from; empty values give just the form. {} if the task is not
        a finished search."""
        return self.__whatif_page(task_id, values)

    def __whatif_page(self, task_id: str, values: dict) -> dict:
        """What whatif.html shows: the form's choices, and the report or roadmap the values asked for, or the problem
        with them. The jobs are the ones the results page ranks: only those meeting the filters the search applied."""
        result = self.__finished_task(task_id, "search")
        if result is None:
            return {}
        applied = self.__logic.filters_for_step(result["filters"], result.get("search_step", "all"))
        profile = result["profile"]
        jobs = [job for job in result["all_jobs"] if self.__logic.passes_filters(job, applied)]
        page = {"search_id": task_id, "job_count": len(jobs), "planners": self.__logic.planners,
                "skills_available": self.__logic.learnable_skills(jobs), "max_steps": MAX_STEPS,
                "chosen": [], "extra_skills": "", "mode": "test", "steps": 3, "top_n": result.get("show", TOP_N),
                "report": None, "roadmap": None, "problem": ""}
        # Each listed skill's effect if it were learned on its own, so the page can rank the choices before any
        # click: how many more strong matches it gives, and how many jobs ask for it (the jobs it becomes matched on)
        impact = []
        for order, skill in enumerate(page["skills_available"]):
            try:
                moves = self.__logic.what_if(profile, jobs, [skill], page["top_n"])["movements"]
            except ValueError:  # a skill the simulator can't test is listed with no effect
                moves = []
            impact.append({"skill": skill, "order": order, "jobs": sum(bool(move["skills_gained"]) for move in moves),
                           "strong_gain": sum(move["outcome_after"] == OUTCOME_STRONG for move in moves)
                           - sum(move["outcome_before"] == OUTCOME_STRONG for move in moves)})
        page["skill_impact"] = sorted(impact, key=lambda item: (-item["strong_gain"], item["order"]))
        # What the answer is based on: how many of the jobs each searched title found, and the titles that found
        # none (jobs from searches made before titles were recorded have no found_by, so the line is left out)
        found: dict[str, int] = {}
        for job in jobs:
            if job.get("found_by"):
                found[job["found_by"]] = found.get(job["found_by"], 0) + 1
        searched = list(profile.get("search_keywords", []))
        page["based_on"] = {"titles": [(title, found[title]) for title in searched if title in found]
                            + [(title, count) for title, count in found.items() if title not in searched],
                            "none_for": [title for title in searched if title not in found]} if found else None
        page["you_have"] = list(profile.get("core_skills", []))
        page["comparison"] = None
        if values:
            page.update(self.__run_what_if(profile, jobs, values, page))
        page["verdict"] = self.__describe_what_if(page["report"]) if page["report"] else None
        page["roadmap_steps"] = self.__describe_roadmap(page["roadmap"]) if page["roadmap"] else []
        return page

    def __run_what_if(self, profile: dict, jobs: list[dict], values: dict, page: dict) -> dict:
        """The what-if form's answer: the report or roadmap it asked for, or the problem with what it sent."""
        chosen = values.get("skills", [])
        extra = values.get("extra_skills", "")
        mode = values.get("mode") or page["planners"][0][0]
        answer = {"chosen": chosen, "extra_skills": extra, "mode": mode}
        # A chosen skill is written the way the jobs write it, so the validator is given the skills they ask for
        checked, problem = self.__validator.check_what_if(
            {"mode": mode, "skills": chosen, "extra_skills": extra,
             "steps": values.get("steps", page["steps"]), "top_n": values.get("top_n", page["top_n"])},
            self.__logic.learnable_skills(jobs))
        if problem:
            answer["problem"] = problem
            return answer
        answer.update(steps=checked["steps"], top_n=checked["top_n"])
        try:
            if mode == "test":
                answer["report"] = self.__with_tidy_titles(
                    self.__logic.what_if(profile, jobs, checked["skills"], checked["top_n"]))
            else:
                roadmap = self.__logic.roadmap(profile, jobs, mode, checked["steps"], checked["top_n"],
                                               checked["skills"])
                answer.update(roadmap=roadmap, report=self.__with_tidy_titles(roadmap["report"]))
                # The other goal's plan, in its own measure, so the page can say how the two differ in one line
                other = next((key for key, _ in page["planners"] if key != mode), None)
                if other is not None:
                    alternative = self.__logic.roadmap(profile, jobs, other, checked["steps"], checked["top_n"],
                                                       checked["skills"])
                    if not alternative["is_empty"]:
                        answer["comparison"] = {
                            "key": other, "label": dict(page["planners"])[other],
                            "steps": [step["skill"] for step in alternative["steps"]],
                            "places": alternative["report"]["places_gained"],
                            "pay": self.__describe_what_if(alternative["report"])["pay_after"]}
        except ValueError as error:  # input the simulator can't use; the message is written for the user
            answer["problem"] = str(error)
        return answer

    def __with_tidy_titles(self, report: dict) -> dict:
        """A copy of a what-if report whose job titles are tidied for the page."""
        def tidied(moves: list[dict]) -> list[dict]:
            return [{**move, "title": self.__tidy_title(move["title"])} for move in moves]
        return {**report, "movements": tidied(report["movements"]), "entered_top": tidied(report["entered_top"]),
                "left_top": tidied(report["left_top"])}

    def __problem_message(self, problem: str) -> str:
        """The sentence for a code (a saved-jobs outcome, or a failure the AI side reports), or "" if unknown."""
        sentences = {
            "status": f"Status must be one of {', '.join(STATUSES)}.",
            "note": f"A note can be at most {MAX_NOTE_CHARS} characters.",
            "gone": "That job is no longer saved. It may have been removed in another tab.",
            "updated": "Saved your changes.",
            "removed": "Removed the job.",
            "cleared": "Removed all saved jobs.",
            "ai_unavailable": "The AI couldn't read your resume just now. Please try again in a minute.",
            # Retrying can't help here, so the sentence says what to fix instead
            "ai_not_set_up": (f"The AI isn't set up: {self.__ai.setup_problem}. Put a valid key in the .env file "
                              "(see .env.example), then restart the app."),
        }
        return sentences.get(problem, "")

    def __present_job(self, job: dict) -> dict:
        """A copy of a job with the text the pages show: its tidied title, short location, salary, job types and
        experience, the day it was posted ("12 Nov 2026", or "" if unknown), and for each matched skill whether it
        matched under the same name."""
        try:
            posted = datetime.date.fromisoformat(str(job.get("posted_date") or ""))
            posted_text = f"{posted.day} {posted:%b %Y}"
        except ValueError:  # no date (e.g. a job saved before dates were kept) or one in another format
            posted_text = ""
        return {**job,
                "posted_text": posted_text,
                "title_text": self.__tidy_title(job.get("title")),
                "location_text": self.__short_location(job.get("location") or ""),
                "salary_text": self.__format_salary(job),
                "job_types_text": self.__join_list(job.get("employment_types")),
                "experience_text": self.__experience_text(job.get("min_years_experience")),
                "matched_skills": [{**skill, "same_name": self.__same_skill(str(skill.get("job_skill", "")),
                                                                         str(skill.get("candidate_skill", "")))}
                                   for skill in job.get("matched_skills", []) if isinstance(skill, dict)]}

    # ---------- wording: how jobs, notes and changes are written for the pages ----------

    def __join_list(self, value: list | None) -> str:
        """Join a list into comma-separated text."""
        if not isinstance(value, list) or not value:
            return "None"
        names = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("job_skill", "")
            names.append(str(item))
        return ", ".join(names)

    def __format_salary(self, job: dict) -> str:
        """Return a job's salary range as text."""
        low = job.get("min_salary", 0)
        high = job.get("max_salary", 0)
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)) or (low == 0 and high == 0):
            return "Not stated"
        return f"${low:,.0f} - ${high:,.0f} {job.get('salary_period', 'monthly')}"

    def __tidy_title(self, title: str | None) -> str:
        """Capitalised titles, e.g. "SERVICE CREW" -> "Service Crew"; others stay as they are, and a missing one is N/A."""
        if not title or title == "N/A":  # "N/A" is a placeholder, not a title to capitalise ("N/a")
            return "N/A"
        if not title.isupper():
            return title
        return " ".join(word if word in ACRONYMS else word.capitalize() for word in title.split())

    def __short_location(self, location: str) -> str:
        """First place in a MyCareersFuture district, e.g. "D19 Hougang, Sengkang, ..." -> "Hougang"."""
        place = re.sub(r"^D\d+\s+", "", location or "").split(",")[0].strip()
        return place or location

    def __experience_text(self, years: int) -> str:
        """A job's minimum experience as text, e.g. 0 -> "No experience needed", 2 -> "2+ years"."""
        if not years:
            return "No experience needed"
        return f"{years}+ year{'s' if years != 1 else ''}"

    def __same_skill(self, job_skill: str, candidate_skill: str) -> bool:
        """True if two skill names are the same apart from case and spaces, e.g. "SQL" and "sql",
        so a match under another name ("Stakeholder Management" by "Stakeholder Communication") can be shown."""
        return job_skill.strip().lower() == candidate_skill.strip().lower()

    def __search_step_message(self, step: str, filters: dict) -> str:
        """What to tell the user when the job search had to drop some preferences, or "" if it didn't."""
        if step == "job type":
            return f"No jobs matched all your preferences, so these match your job type ({filters['job_type']}) only."
        if step == "general":
            return "No jobs matched your preferences, so these are general results for your resume."
        return ""

    def __search_problem_message(self, listings_found: int, jobs_analysed: int, portal_failed: bool = True) -> str:
        """Why a job search has nothing to show (web), or "" if it found and analysed some listings.
        portal_failed: False when MyCareersFuture answered but listed nothing, so it isn't a connection problem."""
        if listings_found == 0 and not portal_failed:
            return ("MyCareersFuture has no listings for your resume's job titles and filters right now. "
                    "Try loosening your filters, or another resume.")
        if listings_found == 0:
            return ("Could not get any job listings from MyCareersFuture. Check your internet "
                    "connection, or try again in a few minutes.")
        if jobs_analysed == 0:
            return (f"We found {listings_found} job listing{'s' if listings_found != 1 else ''} but couldn't read "
                    f"{'it' if listings_found == 1 else 'them'} just now. Please wait a minute and try again.")
        return ""

    def __partial_results_message(self, summary: dict, jobs_analysed: int) -> str:
        """What a search that partly failed left out (web), or "" if nothing was left out: listings the
        AI could not analyse, and job title searches MyCareersFuture didn't answer."""
        problems = []
        left_out = summary.get("listings_found", 0) - jobs_analysed
        if left_out > 0:
            problems.append(f"{left_out} of {summary['listings_found']} listings could not be analysed by the AI")
        if summary.get("searches_errored"):
            problems.append(f"{summary['searches_errored']} of {summary.get('searches', summary['searches_errored'])} "
                            "job title searches on MyCareersFuture failed")
        if not problems:
            return ""
        return f"{' and '.join(problems)}, so some matches may be missing. Search again later to fill the gaps."

    def __describe_changes(self, records: list) -> list[str]:
        """What tailoring changed or left out, as sentences for the user. The records come from the tailors (a Word
        resume's changes, e.g. {"kind": "reworded", "start": "Built dashboards for the sales team...", "reason":
        "leads with SQL"}) and from the fact checks' notes (e.g. {"kind": "entry_not_in_resume", "title": "..."});
        a PDF resume's changes are already sentences from the AI and stay as they are."""
        def recombined_reasons(tools: list[str], words: list[str]) -> list[str]:
            """Why a rewording of real facts was undone, e.g. ['put Tableau into work your resume doesn't use it for',
            'made it sound stronger ("complex")']."""
            reasons = []
            if tools:
                them = "it" if len(tools) == 1 else "them"
                reasons.append(f"put {', '.join(tools)} into work your resume doesn't use {them} for")
            if words:
                reasons.append(f"made it sound stronger ({', '.join(f'“{word}”' for word in words)})")
            return reasons

        sentences = []
        for record in records:
            if isinstance(record, str):
                sentences.append(record)
                continue
            kind = record["kind"]
            if kind == "layout_durations":
                sentences.append("Tidied the layout: removed the length of time after the dates, e.g. \"(6 months)\".")
            elif kind == "layout_dated":
                sentences.append(f"Tidied the layout: put the year of {record['count']} certification(s) or award(s) "
                                 f"on the right, like your other entries.")
            elif kind in ("reworded", "removed"):
                what = "Removed" if kind == "removed" else "Reworded"
                sentences.append(f'{what} "{record["start"]}"' + (f": {record['reason']}" if record["reason"] else ""))
            elif kind == "section_number_not_in_resume":
                sentences.append(f'The "{record["heading"]}" section was left out because it had a number '
                                 f"that isn't in your resume.")
            elif kind == "entry_not_in_resume":
                sentences.append(f'Left out "{record["title"]}": it isn\'t in your resume.')
            elif kind == "section_adds_missing_skills":
                sentences.append(f'The "{record["heading"]}" section was left out because it added '
                                 f"{', '.join(record['skills'])}, which isn't in your resume.")
            elif kind == "bullet_adds_missing_skills":
                sentences.append(f'Left out a point under "{record["title"]}" that added '
                                 f"{', '.join(record['skills'])}, which isn't in your resume.")
            elif kind == "bullets_number_not_in_resume":
                sentences.append(f'Left out {record["count"]} point(s) under "{record["title"]}" with numbers that '
                                 f"aren't in your resume.")
            elif kind == "edit_number_not_in_resume":
                sentences.append(f'Kept "{record["start"]}..." as it was: the new wording had a number that '
                                 f"isn't in your resume.")
            elif kind == "edit_adds_missing_skills":
                sentences.append(f'Kept "{record["start"]}..." as it was: the new wording added '
                                 f"{', '.join(record['skills'])}, which isn't in your resume.")
            elif kind == "sentences_checked":
                # e.g. 'In "Professional Summary", kept your own wording for 2 sentences: the new wording added
                # Tuition, which isn't in your resume.'
                unit = "point" if record["is_point"] else "sentence"
                done = []
                if record["kept_original"]:
                    count = record["kept_original"]
                    done.append(f"kept your own wording for {count} {unit}{'s' if count != 1 else ''}")
                if record["removed"]:
                    count = record["removed"]
                    done.append(f"left out {count} {unit}{'s' if count != 1 else ''}")
                why = []
                if record["skills"]:
                    why.append(f"added {', '.join(record['skills'])}, which isn't in your resume")
                if record["qualifications"]:
                    named = ", ".join(f'"{name}"' for name in record["qualifications"])
                    why.append(f"mentioned {named}, a qualification your resume doesn't list")
                if record["number"]:
                    why.append("had a number that isn't in your resume")
                why += recombined_reasons(record.get("tools", []), record.get("words", []))
                sentences.append(f'In "{record["where"]}", {" and ".join(done)}: the new wording {"; ".join(why)}.')
            elif kind == "edit_recombines":
                # e.g. 'Kept "Built Power BI dashboards..." as it was: the new wording put Tableau into work your
                # resume doesn't use it for.'
                why = "; ".join(recombined_reasons(record["tools"], record["words"]))
                sentences.append(f'Kept "{record["start"]}..." as it was: the new wording {why}.')
            elif kind.endswith("_adds_qualification"):
                # e.g. 'mentioned "Degree", a qualification your resume doesn't list'
                named = ", ".join(f'"{name}"' for name in record["qualifications"])
                claim = f"{named}, a qualification your resume doesn't list."
                if kind == "edit_adds_qualification":
                    sentences.append(f'Kept "{record["start"]}..." as it was: the new wording mentioned {claim}')
                elif kind == "section_adds_qualification":
                    sentences.append(f'The "{record["heading"]}" text was left out because it mentioned {claim}')
                else:
                    sentences.append(f'Left out a point under "{record["title"]}" that mentioned {claim}')
            elif kind == "bullet_group_kept":
                sentences.append(f'Kept every point under "{record["heading"]}": removing them all would leave it empty.')
            elif kind == "edit_too_long":
                sentences.append(f'Kept "{record["start"]}..." as it was: the new wording was longer and '
                                 f"could push your resume onto another page.")
        return sentences

    def __describe_what_if(self, report: dict) -> dict:
        """What the what-if page shows for one report, the answer first. The verdict: tier ("high", "some" or "none"),
        its badge, and effect, the words after "Learning <skills>", judged by strong matches (for "Try a skill");
        rank_* and pay_* are the same judged by places gained and by the top list's average pay (for the two plan
        goals). Then the numbers behind it: strong matches and pay before and after, the titles entering or leaving
        the top list, the whole top list best first ("top"), the other jobs that got better ("below"), the rest
        ("others") and one sentence saying what happened to those. Titles are already tidied."""
        moves, top_n = report["movements"], report["top_n"]
        badges = {"high": "Big boost", "some": "Small boost", "none": "No change"}

        def in_top(move: dict, side: str) -> bool:
            return move[f"listed_{side}"] and move[f"rank_{side}"] <= top_n

        def paid(side: str) -> list[float]:
            """The pay of the top-list jobs that state it, before or after."""
            return [move["salary"] for move in moves if in_top(move, side) and move["salary"] > 0]
        pay_before = round(sum(paid("before")) / len(paid("before"))) if paid("before") else 0
        pay_after = round(sum(paid("after")) / len(paid("after"))) if paid("after") else 0
        entered = len(report["entered_top"])
        into_top = f" and puts {entered} new job{'s' if entered != 1 else ''} in your top {top_n}" if entered else ""
        places, moved = report["places_gained"], report["jobs_moved_up"]
        rank_effect = (f"moves {moved} job{'s' if moved != 1 else ''} up {places} place{'s' if places != 1 else ''}"
                       f"{into_top}" if places else "wouldn't move any job up")
        rank_tier = "high" if places >= 10 or entered else "some" if places else "none"
        rise = pay_after - pay_before
        pay_effect = (f"raises the average pay of your top {top_n} from ${pay_before:,} to ${pay_after:,} a month"
                      f"{into_top}" if rise > 0 else
                      f"doesn't raise the average pay of your top {top_n} (${pay_before:,} a month)")
        pay_tier = "high" if rise >= 300 else "some" if rise > 0 or entered else "none"
        strong_before = sum(move["outcome_before"] == OUTCOME_STRONG for move in moves)
        strong_after = sum(move["outcome_after"] == OUTCOME_STRONG for move in moves)
        more, entered, upgrades, moved_up = (strong_after - strong_before, len(report["entered_top"]),
                                             report["outcome_upgrades"], report["jobs_moved_up"])
        gains = []
        if more > 0:
            gains.append(f"turns {more} more job{'s' if more != 1 else ''} into strong matches")
        if entered:
            gains.append(f"puts {entered} new job{'s' if entered != 1 else ''} in your top {report['top_n']}")
        if gains:
            effect = " and ".join(gains)
        elif upgrades:
            effect = f"improves {upgrades} match{'es' if upgrades != 1 else ''}, but adds no strong matches"
        elif moved_up:
            effect = f"moves {moved_up} job{'s' if moved_up != 1 else ''} up, but adds no strong matches"
        else:
            effect = "wouldn't change your matches"
        tier = "high" if more >= 2 or entered else "some" if more or upgrades or moved_up else "none"

        def got_better(move: dict) -> bool:
            return move["moved_up"] or move["outcome_upgraded"]
        by_rank = sorted(moves, key=lambda move: move["rank_after"])
        # The whole top list is always shown (so the new best match is there even if it didn't move); below it,
        # only the jobs that got better; the rest are one sentence
        top = [move for move in by_rank if in_top(move, "after")]
        below = [move for move in by_rank if not in_top(move, "after") and got_better(move)]
        others = [move for move in by_rank if not in_top(move, "after") and not got_better(move)]
        slipped = sum(move["places_gained"] < 0 for move in others)
        parts = ([f"{slipped} other job{'s' if slipped != 1 else ''} slipped down"] if slipped else []) + \
                ([f"{len(others) - slipped} unchanged"] if len(others) > slipped else [])
        line = ", ".join(parts)
        return {"tier": tier, "badge": badges[tier], "effect": effect,
                "rank_tier": rank_tier, "rank_badge": badges[rank_tier], "rank_effect": rank_effect,
                "pay_tier": pay_tier, "pay_badge": badges[pay_tier], "pay_effect": pay_effect,
                "strong_before": strong_before, "strong_after": strong_after, "more_strong": more,
                "pay_before": pay_before, "pay_after": pay_after,
                "best_pay_before": max(paid("before"), default=0), "best_pay_after": max(paid("after"), default=0),
                "entered_top": [move["title"] for move in report["entered_top"]],
                "left_top": [move["title"] for move in report["left_top"]],
                "top": top, "below": below, "others": others,
                "others_line": (line[0].upper() + line[1:] + ".") if line else ""}

    def __describe_roadmap(self, roadmap: dict) -> list[dict]:
        """The roadmap's steps for the page's staircase, in both goals' measures: each step's skill (a pair is
        "A + B", learned together), and after it the places gained in all and the top list's average pay, with
        what the step itself adds to each. [] if no skill would help."""
        if roadmap["is_empty"]:
            return []
        places_before, pay_before = 0, self.__describe_what_if(roadmap["report"])["pay_before"]
        steps = []
        for step in roadmap["steps"]:
            places, pay = step["report"]["places_gained"], self.__describe_what_if(step["report"])["pay_after"]
            steps.append({"skill": step["skill"], "together": len(step.get("skills", [step["skill"]])) > 1,
                          "places": places, "places_added": places - places_before,
                          "pay": pay, "pay_added": pay - pay_before})
            places_before, pay_before = places, pay
        return steps
