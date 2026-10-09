"""Main program: connects the four layers, which run in this order.

    1. Input layer (io_manager)          -> resume text and job filters
    2. AI layer    (ai_manager)          -> candidate profile and job records
    3. Logic layer (logic_manager)       -> filtered and ranked top jobs
    4. Data layer  (database_functions)  -> jobs saved in data/job_listings.json

The other layers only contain functions; this file defines none and only calls them.
Run from the project root:

    python src/main.py
"""

import logging
import sys

import ai_manager
import database_functions
import io_manager
import logic_manager

TOP_N = 5  # jobs kept and saved per search

if __name__ == "__main__":
    # ai_manager turns on INFO logging when imported; show only warnings and errors
    logging.getLogger().setLevel(logging.WARNING)

    try:
        io_manager.display_header("RESUME JOB MATCHER")
        while True:
            choice = io_manager.prompt_choice("Main menu", io_manager.MENU_OPTIONS)
            if choice == io_manager.MENU_EXIT:
                break

            # An unexpected error in one option returns to the menu instead of ending the program
            try:
                if choice == io_manager.MENU_SEARCH:
                    # 1. Input layer: resume text and job filters
                    record = io_manager.collect_input()
                    if record is None:
                        io_manager.display_message("Search cancelled.")
                        continue

                    # 2. AI layer: the profile is completed with prompt_missing_data BEFORE the
                    #    job search, so skills the user types in are used when matching jobs.
                    #    On failure the user can try again.
                    while True:
                        io_manager.display_message("\nAnalysing your resume and searching for jobs. "
                                                   "This can take a minute...")
                        profile, jobs = None, []
                        try:
                            profile = ai_manager.extract_candidate_profile(record["resume_text"])
                            if profile is not None:
                                profile = io_manager.prompt_missing_data(profile)
                                jobs = ai_manager.search_and_extract_jobs(profile)
                            problem = io_manager.check_ai_result(profile, jobs)
                        except Exception as error:
                            problem = f"Something went wrong during the search ({type(error).__name__}: {error})."
                        if problem == "":
                            break
                        io_manager.display_error(problem)
                        if not io_manager.prompt_yes_no("Try again?"):
                            profile = None
                            break
                    if profile is None:
                        continue

                    # 3. Logic layer: filter, rank and keep the top jobs
                    try:
                        top_jobs = logic_manager.filter_and_rank(profile, jobs, record["filters"], TOP_N)
                        removed = logic_manager.count_removed(jobs, record["filters"])
                    except Exception as error:
                        logging.error(f"Filtering and ranking failed ({type(error).__name__}: {error})")
                        io_manager.display_error("Could not filter and rank the jobs. Please try a new search.")
                        continue

                    # 4. Data layer: save the top jobs, skipping ones already saved.
                    #    A failed save still shows the results.
                    if top_jobs:
                        try:
                            database = database_functions.read_database()
                            database = database_functions.insert_no_duplicates(top_jobs, database)
                            database_functions.write_database(database)
                        except Exception as error:
                            logging.error(f"Could not save jobs ({type(error).__name__}: {error})")
                            io_manager.display_error("Your results could not be saved, but they are shown below.")

                    io_manager.display_result(profile, top_jobs)

                    # What the logic layer did, e.g. "Checked 26 jobs: 22 removed by your
                    # filters (14 salary, 8 job type). Showing the best 4."
                    total_removed = sum(removed.values())
                    if total_removed == 0:
                        summary = f"Checked {len(jobs)} jobs: none removed by your filters."
                    else:
                        details = ", ".join(f"{count} {reason}" for reason, count in removed.items())
                        summary = f"Checked {len(jobs)} jobs: {total_removed} removed by your filters ({details})."
                    io_manager.display_message(f"\n{summary} Showing the best {len(top_jobs)}.")

                else:
                    # Show my last results / View one job in detail: both use every saved job (data layer)
                    saved_jobs = database_functions.read_database()
                    if not isinstance(saved_jobs, list):
                        saved_jobs = []
                    saved_jobs = [job for job in saved_jobs if isinstance(job, dict)]
                    if not saved_jobs:
                        io_manager.display_error("No saved jobs yet. Run a search first.")
                        continue

                    io_manager.display_header(f"SAVED JOBS ({len(saved_jobs)})")
                    io_manager.display_list(saved_jobs)
                    if choice == io_manager.MENU_DETAILS:
                        number = io_manager.prompt_number("Job number to view", 1, len(saved_jobs))
                        io_manager.display_record(saved_jobs[number - 1])

            except EOFError:
                raise  # input was closed: let the handler below exit cleanly
            except Exception as error:
                logging.error(f"Unexpected error ({type(error).__name__}: {error})")
                io_manager.display_error("Something went wrong. Returning to the main menu.")

    except (KeyboardInterrupt, EOFError):
        # Ctrl+C, or input closed: exit cleanly instead of printing a traceback
        print()

    io_manager.display_message("Goodbye!")
    sys.exit(0)
