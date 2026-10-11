"""Main program: connects the four layers, which run in this order.

    1. Input layer (io_manager)          -> resume text and job filters
    2. AI layer    (ai_manager)          -> candidate profile and job records
    3. Logic layer (logic_manager)       -> filtered and ranked top jobs
    4. Data layer  (database_functions)  -> jobs saved in data/job_listings.json

The other layers only contain functions; this file defines none and only calls them.
Run from the project root:

    python src/main.py
    
Each step of a search recovers on its own, so a failure never loses the work before it:
  - the resume profile is kept if the job search fails, so "Try again?" only repeats the search
  - a failed save still shows the results, and keeps them for "Show my last results" this session
  - Ctrl+C during a search cancels that search and returns to the menu; at the menu it exits
Warnings and errors appear in the terminal; everything, including INFO, goes to logs/job_matcher.log.
"""

import logging
import sys
import os

import ai_manager
import database_functions
import io_manager
import logic_manager

TOP_N = 5  # jobs kept and saved per search
JOBS_PER_SEARCH = 10  # listings fetched per job title; more means more AI batches (slower, costs more)
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "job_matcher.log")
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(module)s | %(message)s"

if __name__ == "__main__":
    # ---------- Logging ----------
    # ai_manager sets up terminal logging when imported. Keep the terminal to warnings and errors,
    # and write everything (INFO and up) to a log file for finding out what went wrong later.
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
    logging.info(f"event=start provider={ai_manager.PROVIDER} model={ai_manager.MODEL}")

    # The latest search's top jobs, kept in memory for "Show my last results" if they could not be
    # saved, or if the saved jobs file can't be opened this session
    session_jobs = []

    try:
        io_manager.display_header("RESUME JOB MATCHER")

        # ---------- 4. Data layer: load every saved job on startup ----------
        # A missing or corrupt file gives an empty list (a corrupt one is backed up first).
        # A file that can't be opened at all (e.g. locked or no permission) is reported, not fatal.
        try:
            saved_count = len(database_functions.read_saved_jobs())
            io_manager.display_message(f"{saved_count} saved job{'s' if saved_count != 1 else ''} loaded.")
            logging.info(f"event=load_saved_jobs outcome=ok count={saved_count}")
        except OSError as error:
            logging.error(f"event=load_saved_jobs outcome=failed error={type(error).__name__}: {error}")
            io_manager.display_error("Could not open your saved jobs file. New searches still work, and their "
                                     "results are kept until you exit.")

        while True:
            choice = io_manager.prompt_choice("Main menu", io_manager.MENU_OPTIONS)
            if choice == io_manager.MENU_EXIT:
                break

            # An unexpected error in one option returns to the menu instead of ending the program
            try:
                if choice == io_manager.MENU_SEARCH:
                    # ---------- 1. Input layer: resume text and job filters ----------
                    record = io_manager.collect_input(ai_manager.provider_name())
                    if record is None:
                        io_manager.display_message("Search cancelled.")
                        continue
                    resume_name = os.path.basename(record["resume_path"])
                    logging.info(f"event=search stage=input outcome=ok resume={resume_name} filters={record['filters']}")

                    # Ctrl+C from here until the results are shown cancels only this search
                    try:
                        # ---------- 2a. AI layer: candidate profile (kept if a later step fails) ----------
                        profile = None
                        while profile is None:
                            io_manager.display_message("\nReading your resume with AI...")
                            profile = ai_manager.extract_candidate_profile(record["resume_text"])
                            if profile is None:
                                logging.warning("event=search stage=profile outcome=failed")
                                io_manager.display_error(io_manager.check_ai_result(None, []))
                                if not io_manager.prompt_yes_no("Try again?"):
                                    break
                        if profile is None:
                            continue
                        # Ask for what the AI couldn't find BEFORE the job search, so typed-in skills are used
                        profile = io_manager.prompt_missing_data(profile)
                        logging.info(f"event=search stage=profile outcome=ok keywords={profile['search_keywords']}")

                        # ---------- 2b. AI layer: job search and requirements ----------
                        # Retrying repeats only this step; the profile above is kept
                        jobs = None
                        while jobs is None:
                            io_manager.display_message("Searching MyCareersFuture and analysing the listings. "
                                                       "This can take a minute...")
                            summary = {}
                            jobs = ai_manager.search_and_extract_jobs(profile, limit_per_search=JOBS_PER_SEARCH,
                                                                      summary=summary)
                            logging.info(f"event=search stage=jobs summary={summary}")
                            problem = ""
                            if summary["listings_found"] == 0:
                                problem = ("Could not get any job listings from MyCareersFuture. Check your internet "
                                           "connection, or try again in a few minutes.")
                            elif not jobs:
                                problem = (f"The AI could not analyse any of the {summary['listings_found']} listings "
                                           "found. Check your API key, or wait a minute if the AI service is busy.")
                            if problem:
                                logging.warning(f"event=search stage=jobs outcome=failed reason={problem!r}")
                                io_manager.display_error(problem)
                                jobs = None
                                if not io_manager.prompt_yes_no("Try again?"):
                                    break
                        if jobs is None:
                            continue
                        left_out = summary["listings_found"] - len(jobs)
                        if left_out:
                            # Some listings (a failed batch, or entries the AI skipped) could not be checked
                            io_manager.display_error(f"{left_out} of {summary['listings_found']} listings could not be "
                                                     "analysed and were left out. The rest are shown below.")

                    except KeyboardInterrupt:
                        logging.info("event=search outcome=cancelled_by_user")
                        io_manager.display_message("\nSearch cancelled.")
                        continue

                    # ---------- 3. Logic layer: filter, rank and keep the top jobs ----------
                    try:
                        top_jobs = logic_manager.filter_and_rank(profile, jobs, record["filters"], TOP_N)
                        removed = logic_manager.count_removed(jobs, record["filters"])
                    except Exception as error:
                        logging.exception(f"event=search stage=logic outcome=failed error={type(error).__name__}")
                        io_manager.display_error("Could not filter and rank the jobs. Please try a new search.")
                        continue

                    session_jobs = top_jobs
                    logging.info(f"event=search stage=logic outcome=ok checked={len(jobs)} shown={len(top_jobs)} "
                                 f"removed={removed}")
                    
                    # ---------- 4. Data layer: save the top jobs, skipping ones already saved ----------
                    # A failed save still shows the results, and session_jobs keeps them for this session
                    if top_jobs:
                        try:
                            newly_saved = database_functions.save_new_jobs(top_jobs, resume_name)
                            logging.info(f"event=search stage=save outcome=ok new={newly_saved}")
                        except Exception as error:
                            logging.error(f"event=search stage=save outcome=failed error={type(error).__name__}: {error}")
                            io_manager.display_error("Your results could not be saved, but they are shown below and "
                                                     "kept until you exit.")

                    io_manager.display_result(profile, top_jobs)

                    # Where the jobs came from and what the logic layer did, e.g. "Checked 26 listings:
                    # up to 10 from each of 3 MyCareersFuture searches (Data Analyst, ...).
                    # 22 removed by your filters (14 salary, 8 job type). Showing the best 4."
                    keywords = profile["search_keywords"]
                    source = (f"Checked {len(jobs)} listings: up to {JOBS_PER_SEARCH} from each of {len(keywords)} "
                              f"MyCareersFuture searches ({', '.join(keywords)}).")
                    total_removed = sum(removed.values())
                    if total_removed == 0:
                        summary_text = "None removed by your filters."
                    else:
                        details = ", ".join(f"{count} {reason}" for reason, count in removed.items())
                        summary_text = f"{total_removed} removed by your filters ({details})."
                    io_manager.display_message(f"\n{source} {summary_text} Showing the best {len(top_jobs)}.")

                else:
                    # ---------- Show my last results / View one job in detail (data layer) ----------
                    # Every saved job; if the file can't be opened, this session's results instead
                    try:
                        saved_jobs = database_functions.read_saved_jobs()
                    except OSError as error:
                        logging.error(f"event=show_saved outcome=failed error={type(error).__name__}: {error}")
                        saved_jobs = session_jobs
                        io_manager.display_error("Could not open your saved jobs file. "
                                                 + ("Showing this session's results instead." if saved_jobs else ""))
                    if not saved_jobs:
                        io_manager.display_error("No saved jobs yet. Run a search first.")
                        continue

                    # Query (data layer): narrow the list by a word in the title, company or location
                    keyword = io_manager.prompt_text("Filter by keyword (press Enter to show all)", allow_blank=True)
                    if keyword:
                        saved_jobs = database_functions.search_jobs(saved_jobs, keyword)
                        if not saved_jobs:
                            io_manager.display_error(f'No saved jobs match "{keyword}".')
                            continue

                    io_manager.display_header(f"SAVED JOBS ({len(saved_jobs)})")
                    io_manager.display_list(saved_jobs)
                    if choice == io_manager.MENU_DETAILS:
                        number = io_manager.prompt_number("Job number to view", 1, len(saved_jobs))
                        io_manager.display_record(saved_jobs[number - 1])

            except EOFError:
                raise  # input was closed: let the handler below exit cleanly
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
    sys.exit(0)
