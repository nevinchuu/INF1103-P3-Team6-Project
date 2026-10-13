"""Console app: connects the four layers in order (io_manager, ai_manager, logic_manager, data_manager).

Run from the project root with python src/main.py. Warnings and errors appear in the terminal;
everything, including INFO, goes to logs/job_matcher.log.
"""

import logging
import os

import ai_manager
import data_manager
import io_manager
import logic_manager

TOP_N = 5  # jobs kept and saved per search
JOBS_PER_SEARCH = 10  # listings fetched per job title; more means more AI batches (slower, costs more)
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "job_matcher.log")
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(module)s | %(message)s"


def set_up_logging() -> None:
    """Keeps the terminal to warnings and errors, and writes everything (INFO and up) to a log
    file for finding out what went wrong later."""
    logging.basicConfig(level=logging.INFO)  # the terminal handler, turned down to warnings below
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for terminal_handler in root_logger.handlers:
        terminal_handler.setLevel(logging.WARNING)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        log_file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        log_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root_logger.addHandler(log_file_handler)
    except OSError as error:
        # No log file (e.g. a read-only folder): the program still runs, with terminal logging only
        logging.warning(f"Could not open the log file {LOG_PATH} ({type(error).__name__}); logging to the terminal only")
    logging.info(f"event=start provider={ai_manager.provider_name()}")


def load_saved_jobs_on_startup() -> None:
    """4. Data layer: loads every saved job on startup and says how many; a file that can't be opened is reported."""
    try:
        saved_count = len(data_manager.read_saved_jobs())
        io_manager.display_message(f"{saved_count} saved job{'s' if saved_count != 1 else ''} loaded.")
        logging.info(f"event=load_saved_jobs outcome=ok count={saved_count}")
    except OSError as error:
        logging.error(f"event=load_saved_jobs outcome=failed error={type(error).__name__}: {error}")
        io_manager.display_error("Could not open your saved jobs file. New searches still work, and their "
                                 "results are kept until you exit.")


def read_profile(resume_text: str) -> dict | None:
    """2a. AI layer: the candidate profile (retrying while the user wants), then any details it missed.
    Returns None if the user gives up."""
    profile = None
    while profile is None:
        io_manager.display_message("\nReading your resume with AI...")
        profile = ai_manager.extract_candidate_profile(resume_text)
        if profile is None:
            logging.warning("event=search stage=profile outcome=failed")
            io_manager.display_error(io_manager.ai_unavailable_message())
            if not io_manager.prompt_yes_no("Try again?"):
                return None
    profile = io_manager.prompt_missing_data(profile)
    logging.info(f"event=search stage=profile outcome=ok keywords={profile['search_keywords']}")
    return profile


def find_jobs(profile: dict, preferences: dict) -> tuple[list[dict] | None, dict]:
    """2b. Job listings (input layer), then their requirements (AI layer).
    Returns (jobs, summary); jobs is None if the user gives up."""
    jobs = None
    summary = {}
    while jobs is None:
        io_manager.display_message("Searching MyCareersFuture and analysing the listings. "
                                   "This can take a minute...")
        summary = {}
        listings = io_manager.fetch_listings(
            profile["search_keywords"], logic_manager.search_steps(preferences), preferences,
            limit_per_search=JOBS_PER_SEARCH, summary=summary,
            fits=lambda job, step: logic_manager.passes_filters(
                job, logic_manager.filters_for_step(preferences, step)))
        answers = ai_manager.extract_requirements_for_listings(listings, profile, summary=summary)
        # 3. Logic layer: each listing with its answer, and its matched and missing skills
        jobs = logic_manager.add_requirements(listings, answers, profile)
        logging.info(f"event=search stage=jobs summary={summary}")
        problem = io_manager.search_problem_message(summary["listings_found"], len(jobs))
        if problem:
            logging.warning(f"event=search stage=jobs outcome=failed reason={problem!r}")
            io_manager.display_error(problem)
            jobs = None
            if not io_manager.prompt_yes_no("Try again?"):
                return None, summary
    left_out = summary["listings_found"] - len(jobs)
    if left_out:
        # Some listings (a failed batch, or entries the AI skipped) could not be checked
        io_manager.display_error(f"{left_out} of {summary['listings_found']} listings could not be "
                                 "analysed and were left out. The rest are shown below.")
    return jobs, summary


def save_top_jobs(top_jobs: list[dict], resume_name: str) -> None:
    """4. Data layer: saves the top jobs, skipping ones already saved.
    A failed save is reported; the caller still shows the results and keeps them for this session."""
    if not top_jobs:
        return
    try:
        newly_saved = data_manager.save_new_jobs(top_jobs, resume_name)
        logging.info(f"event=search stage=save outcome=ok new={newly_saved}")
    except Exception as error:
        logging.error(f"event=search stage=save outcome=failed error={type(error).__name__}: {error}")
        io_manager.display_error("Your results could not be saved, but they are shown below and "
                                 "kept until you exit.")


def run_search() -> list[dict] | None:
    """One search through all four layers. Returns its top jobs, or None if it was cancelled
    or failed before results were ready."""
    # ---------- 1. Input layer: resume text and job filters ----------
    record = io_manager.collect_input(ai_manager.provider_name())
    if record is None:
        io_manager.display_message("Search cancelled.")
        return None
    resume_name = os.path.basename(record["resume_path"])
    logging.info(f"event=search stage=input outcome=ok resume={resume_name} filters={record['filters']}")

    # ---------- 2. AI layer: profile, then job search ----------
    # Ctrl+C from here until the results are shown cancels only this search
    try:
        profile = read_profile(record["resume_text"])
        if profile is None:
            return None
        jobs, summary = find_jobs(profile, record["filters"])
        if jobs is None:
            return None
    except KeyboardInterrupt:
        logging.info("event=search outcome=cancelled_by_user")
        io_manager.display_message("\nSearch cancelled.")
        return None

    # ---------- 3. Logic layer: filter, rank and keep the top jobs ----------
    # Only the preferences the search kept: one it had to drop would empty the results again
    filters = logic_manager.filters_for_step(record["filters"], summary["search_step"])
    try:
        top_jobs = logic_manager.filter_and_rank(profile, jobs, filters, TOP_N)
        removed = logic_manager.count_removed(jobs, filters)
    except Exception as error:
        logging.exception(f"event=search stage=logic outcome=failed error={type(error).__name__}")
        io_manager.display_error("Could not filter and rank the jobs. Please try a new search.")
        return None
    logging.info(f"event=search stage=logic outcome=ok checked={len(jobs)} shown={len(top_jobs)} "
                 f"removed={removed}")

    # ---------- 4. Data layer: save, then show the results ----------
    save_top_jobs(top_jobs, resume_name)
    io_manager.display_result(profile, top_jobs)
    io_manager.display_message("\n" + io_manager.search_summary(len(jobs), JOBS_PER_SEARCH, profile["search_keywords"],
                                                                removed, len(top_jobs)))
    # If the search had to drop some preferences to find any listings, say so
    step_message = io_manager.search_step_message(summary["search_step"], record["filters"])
    if step_message:
        io_manager.display_error(step_message)
    return top_jobs


def show_saved_jobs(choice: str, session_jobs: list[dict]) -> None:
    """Show my saved jobs / View one job in detail (data layer).
    Every saved job; if the file can't be opened, this session's results instead."""
    try:
        saved_jobs = data_manager.read_saved_jobs()
    except OSError as error:
        logging.error(f"event=show_saved outcome=failed error={type(error).__name__}: {error}")
        if not session_jobs:
            io_manager.display_error("Could not open your saved jobs file, and there are no results from this "
                                     "session to show yet.")
            return
        saved_jobs = session_jobs
        io_manager.display_error("Could not open your saved jobs file. Showing this session's results instead.")
    if not saved_jobs:
        io_manager.display_error("No saved jobs yet. Run a search first.")
        return

    # Query (data layer): narrow the list by a word in the title, company or location
    keyword = io_manager.prompt_text("Filter by keyword (press Enter to show all)", allow_blank=True)
    if keyword:
        saved_jobs = data_manager.search_jobs(saved_jobs, keyword)
        if not saved_jobs:
            io_manager.display_error(f'No saved jobs match "{keyword}".')
            return

    io_manager.display_header(f"SAVED JOBS ({len(saved_jobs)})")
    io_manager.display_list(saved_jobs)
    if choice == io_manager.MENU_DETAILS:
        number = io_manager.prompt_number("Job number to view", 1, len(saved_jobs))
        io_manager.display_record(saved_jobs[number - 1])


def main() -> None:
    """Start-up, then the main menu until the user exits."""
    set_up_logging()
    # The latest search's top jobs, kept in memory for "Show my saved jobs" if they could not be
    # saved, or if the saved jobs file can't be opened this session
    session_jobs = []

    try:
        io_manager.display_header("RESUME JOB MATCHER")
        load_saved_jobs_on_startup()

        while True:
            choice = io_manager.prompt_choice("Main menu", io_manager.MENU_OPTIONS)
            if choice == io_manager.MENU_EXIT:
                break

            # An unexpected error in one option returns to the menu instead of ending the program
            try:
                if choice == io_manager.MENU_SEARCH:
                    top_jobs = run_search()
                    if top_jobs is not None:
                        session_jobs = top_jobs
                else:
                    show_saved_jobs(choice, session_jobs)
            except EOFError:
                raise  # input was closed: let the handler below exit cleanly
            except KeyboardInterrupt:
                # Ctrl+C inside an option cancels just that option; Ctrl+C at the menu exits (below)
                logging.info(f"event=menu choice={choice!r} outcome=cancelled_by_user")
                io_manager.display_message("\nCancelled.")
            except Exception as error:
                # logging.exception also writes the traceback to the log file, for finding the cause later
                logging.exception(f"event=menu choice={choice!r} outcome=unexpected_error error={type(error).__name__}")
                io_manager.display_error("Something went wrong. Returning to the main menu.")
            finally:
                logging.info(f"event=menu choice={choice!r} outcome=finished")

    except KeyboardInterrupt:
        # Ctrl+C at the menu: exit cleanly instead of printing a traceback
        io_manager.display_message("")

    except EOFError:
        # Input closed, e.g. Docker started without a keyboard attached: say how to get one
        io_manager.display_message("\nNo keyboard input. In Docker, start the console with "
                                   "'docker compose run --rm console' or 'docker run -it ...'.")

    finally:
        # Runs however the program ends, so the log always records it and the file is flushed
        io_manager.display_message("Goodbye!")
        logging.info("event=exit")
        logging.shutdown()


if __name__ == "__main__":
    main()
