"""Test harness for ai_manager.py - run this to try the AI manager on its own.

    python src/ai_manager_test/ai_manager_test.py

ai_manager.py only contains AI manager work (prompts, API calls, parsing,
validation). To test it end to end, this file fills in the parts other managers
will own in the real program, with simple stand-ins:

    read the resume PDF        -> input manager
    rank and keep the top N    -> logic manager
    format the output JSON     -> input manager (display) / main.py
    save results to a file     -> data manager

Once those managers exist, main.py should use them instead of this file.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# This file lives in src/ai_manager_test/, but ai_manager.py is in src/.
# Add src/ to the places Python looks for imports so "import ai_manager" works.
# (append, not insert: this folder must stay first, or "import ai_manager_test" from
# compare_models.py would find this folder instead of this file)
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))

import ai_manager  # noqa: E402 (import after the sys.path change above)
import io_manager  # noqa: E402  (all output goes through the input/output layer)

TOP_N_RESULTS = 10  # job listings in the final output
OUTPUT_DIR = Path(__file__).resolve().parent / "output_results"  # src/ai_manager_test/output_results


# ==========================================
# 1. FILE READING  (stand-in for the input manager)
# Turns the resume file into plain text. Only this text is sent to the AI, not the PDF itself.
# ==========================================

def read_resume_file(file_path: str) -> str:
    """Reads resume text from a .txt or .pdf file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Resume file not found at: {file_path}")

    if file_path.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        # Join the text of every page into one string
        text = "".join([page.extract_text() or "" for page in reader.pages])
        return text
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
def get_data_root() -> str:
    # Get the directory where this python script is located
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Go up two levels (src/ai_manager_test -> src -> project root) to reach the data folder
    project_root = os.path.abspath(os.path.join(base_dir, "..", ".."))

    # Define the path to the resume file
    data_root = os.path.join(project_root, "data")
    return data_root

def get_resume_file_path(data_root: str) -> str:
    "Prompts the user for a resume name and returns the full path to the resume file."
    # Keep asking until we get a valid, existing PDF filename
    while True:
        filename = input("\nEnter the PDF resume filename in Data/")

        if not filename:
            io_manager.display_message("Filename cannot be empty. Please try again.")
            continue

        if not filename.lower().endswith(".pdf"):
            io_manager.display_message("Please provide a valid PDF filename (e.g., 'resume.pdf').")
            continue

        # Connects the path to the data folder and the filename provided by user
        target_path = os.path.join(data_root, filename)

        # Check if the file exists
        try:
            if not os.path.exists(target_path):
                raise FileNotFoundError(f"Resume file not found at: {target_path}")

            io_manager.display_message(f"Resume file found: {target_path}")
            return target_path

        except FileNotFoundError as e:
            io_manager.display_message(f"Exception occurred: {e}")


# ==========================================
# 2. RANKING  (stand-in for the logic manager)
# ==========================================

def select_top_jobs(profile: dict, jobs: list[dict], top_n: int = TOP_N_RESULTS) -> list[dict]:
    """Returns the top_n jobs that best suit the candidate.

    Jobs whose experience or education requirement the candidate misses rank below
    those they meet; within each group, jobs are ordered by share of required skills
    matched, then by number of skills matched.
    """
    # EDUCATION_LEVELS is ordered lowest to highest, so its index works as a level number
    candidate_level = ai_manager.EDUCATION_LEVELS.index(profile["highest_qualification"])

    # sorted() uses this to rank jobs. Python compares the returned tuples item by item,
    # smallest first, so the earlier items matter most:
    #   1. experience_gap   - 0 years short beats 2 years short
    #   2. not education_ok - False (meets it) sorts before True (doesn't)
    #   3. -match_ratio     - negative so a HIGHER ratio sorts FIRST
    #   4. -matched         - tie-breaker: more matched skills first
    def sort_key(job: dict):
        experience_gap = max(0, job["min_years_experience"] - profile["years_of_experience"])
        education_ok = (
            job["min_education"] == "Not specified"
            or ai_manager.EDUCATION_LEVELS.index(job["min_education"]) <= candidate_level
        )
        matched = len(job["matched_skills"])
        match_ratio = matched / len(job["required_skills"]) if job["required_skills"] else 0
        return (experience_gap, not education_ok, -match_ratio, -matched)

    return sorted(jobs, key=sort_key)[:top_n]  # [:top_n] keeps only the first top_n


# ==========================================
# 3. OUTPUT FORMATTING AND SAVING  (stand-ins for display / the data manager)
# ==========================================

def format_output(profile: dict, jobs: list[dict]) -> dict:
    """Converts the candidate record and job records into the required output schema:
    {applicant_name, education, job_listings: [{job_title, company, location,
    employment_type, suitability_reason, pay_range, job_url}]}"""
    return {
        "applicant_name": profile["candidate_name"],
        "education": profile["qualification_detail"],
        "job_listings": [
            {
                "job_title": job["title"],
                "company": job["company"],
                "location": job["location"],
                "employment_type": ", ".join(job["employment_types"]) or "Not specified",
                "suitability_reason": job["suitability_reason"],
                "pay_range": {
                    "min": job["min_salary"],
                    "max": job["max_salary"],
                    "currency": "SGD",
                    "period": job["salary_period"],
                },
                "job_url": job["job_url"],
            }
            for job in jobs  # list comprehension: build one dict per job
        ],
    }


def save_output(output: dict) -> Path:
    """Writes the formatted output to src/ai_manager_test/output_results/ as a timestamped
    JSON file; returns its path."""
    OUTPUT_DIR.mkdir(exist_ok=True)  # create the folder if it doesn't exist yet
    # e.g. output_results/job_matches_20261008_143012.json - a new file every run
    path = OUTPUT_DIR / f"job_matches_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ==========================================
# 4. TEST RUN
# Uses the provider/model from .env (AI_PROVIDER / AI_MODEL).
# ==========================================

if __name__ == "__main__":
    try:
        # Read the resume, then hand the text to the AI manager - the same call main.py will make
        resume_text = read_resume_file(get_resume_file_path(get_data_root()))
        profile, jobs = ai_manager.run_ai_pipeline(resume_text)
        if profile is None:
            raise SystemExit("Could not extract candidate profile; see errors above.")

        # Rank, keep the top 10, convert to the required format, print and save
        output = format_output(profile, select_top_jobs(profile, jobs))
        io_manager.display_message(json.dumps(output, indent=2))
        io_manager.display_message(f"\nSaved to {save_output(output)}")
    except KeyboardInterrupt:  # Ctrl+C
        io_manager.display_message("\nOperation cancelled by user.")
