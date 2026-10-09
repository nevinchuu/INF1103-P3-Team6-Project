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

                    io_manager.display_result(profile, jobs)

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
