"""Data layer: the saved jobs, kept in data/job_listings.json between runs.

read_saved_jobs loads every saved job on startup; save_new_jobs adds the jobs that aren't saved yet;
search_jobs and find_by_url are the queries. A missing file is created, and a corrupt one is backed
up and replaced, so a bad file never crashes the program.
"""

import json
import logging
import os
import tempfile
import time

# The database file lives in <project root>/data/, wherever the program is run from
DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "job_listings.json")

# How often to retry swapping in a new file when Windows reports it as locked
# (e.g. an antivirus or OneDrive scan has it open for a moment), and the wait between tries
REPLACE_ATTEMPTS = 5
REPLACE_WAIT_SECONDS = 0.2


def _back_up_database(reason: str) -> None:
    """Keeps a broken database as a dated backup, e.g. job_listings.json.20261009-153000.bak, never overwriting one."""
    timestamp = time.strftime('%Y%m%d-%H%M%S')
    backup_path = f"{DATABASE_PATH}.{timestamp}.bak"
    backup_number = 1
    while os.path.exists(backup_path):  # two broken files in the same second each keep their own backup
        backup_number += 1
        backup_path = f"{DATABASE_PATH}.{timestamp}-{backup_number}.bak"
    os.replace(DATABASE_PATH, backup_path)
    logging.warning(f"Saved jobs file was {reason}; kept it as {backup_path} and started a new one")


def read_database() -> list[object]:
    """Reads the database. A missing file is created empty; a corrupt or non-list file is backed up and read
    as empty."""
    try:
        with open(DATABASE_PATH, 'r', encoding='utf-8') as file:
            contents = json.load(file)

    # if the database doesn't exist yet, create it with an empty list
    except FileNotFoundError:
        write_database([])
        return []

    # if the database is corrupted, back it up and start again with an empty list
    except (json.JSONDecodeError, UnicodeDecodeError):
        _back_up_database("not valid JSON")
        return []

    # valid JSON but not a list (e.g. edited by hand): same as corrupt
    if not isinstance(contents, list):
        _back_up_database(f"a JSON {type(contents).__name__}, not a list of jobs")
        return []
    # Any other error (e.g. no permission to read) is raised, so a save never overwrites a file it couldn't read
    return contents


def write_database(jobs: list[dict]) -> None:
    """Writes the jobs to the database file, numbering their IDs first. Any error is raised to the caller."""
    jobs = _number_ids(jobs)

    # write to a temporary file, then swap it in, so a failed save never leaves a half-written database
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    file_descriptor, temp_path = tempfile.mkstemp(dir=os.path.dirname(DATABASE_PATH), suffix=".tmp")
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(jobs, file, indent=2)
        for attempt in range(1, REPLACE_ATTEMPTS + 1):
            try:
                os.replace(temp_path, DATABASE_PATH)
                break
            except PermissionError:
                # Windows: another program has the file open for a moment; wait and try again
                if attempt == REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_WAIT_SECONDS)
    finally:
        # after a failed save, don't leave the temporary file behind
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _has_link(job: dict) -> bool:
    """True if the job has a link to save it by; "N/A" is the portal layer's placeholder for a missing one."""
    return isinstance(job.get("job_url"), str) and job["job_url"] not in ("", "N/A")


def read_saved_jobs() -> list[dict]:
    """Loads every saved job, skipping entries that aren't job records or have no link. Raises OSError if
    it can't be opened."""
    return [job for job in read_database() if isinstance(job, dict) and _has_link(job)]


def search_jobs(saved_jobs: list[dict], keyword: str) -> list[dict]:
    """Query: saved jobs whose title, company or location contain every word in keyword, ignoring case."""
    words = keyword.lower().split()
    results = []
    for job in saved_jobs:
        text = " ".join(str(job.get(field, "")) for field in ("title", "company", "location")).lower()
        if all(word in text for word in words):
            results.append(job)
    return results


def save_new_jobs(jobs: list[dict], resume_name: str) -> int:
    """Saves the jobs with a link that aren't saved yet, noting which resume found them; returns how many were new."""
    # Only job records are kept, so one stray entry in the file can't stop every save
    saved_jobs = read_saved_jobs()
    count_before = len(saved_jobs)
    # Copies, so the caller's records aren't changed; "resume" is which resume to tailor for this job later.
    # A job without a link is not saved: it can't be found again or checked for duplicates
    new_jobs = [{**job, "resume": resume_name} for job in jobs if _has_link(job)]
    all_jobs = _insert_no_duplicates(new_jobs, saved_jobs)
    if len(all_jobs) > count_before:
        write_database(all_jobs)
    return len(all_jobs) - count_before


def find_by_url(saved_jobs: list[dict], job_url: str) -> dict | None:
    """Query: the saved job with this link, or None."""
    for job in saved_jobs:
        if job.get("job_url") == job_url:
            return job
    return None


def _remove_by_url(saved_jobs: list[dict], job_url: str) -> list[dict]:
    """The saved jobs without the one with this link (IDs change after every removal, so the link is used)."""
    return [job for job in saved_jobs if job.get("job_url") != job_url]


def remove_saved_job(job_url: str) -> None:
    """Removes one saved job from the file, found by its link."""
    write_database(_remove_by_url(read_saved_jobs(), job_url))


def clear_saved_jobs() -> None:
    """Removes every saved job from the file."""
    write_database([])


def _insert_no_duplicates(new_jobs: list[dict], saved_jobs: list[dict]) -> list[dict]:
    """Adds each new job to saved_jobs unless a job with the same link is already there, and returns the list."""
    saved_urls = []

    # Retrieve all job URLs and put them into saved_urls
    for job in saved_jobs:
        saved_urls.append(job["job_url"])

    # loop through the jobs to add, checking if each job URL is in saved_urls
    for job in new_jobs:

        # if the job URL is there, it is a duplicate, so skip it
        if job["job_url"] in saved_urls:
            continue

        # else add the job to the database and its URL to saved_urls
        saved_jobs.append(job)
        saved_urls.append(job["job_url"])

    return saved_jobs


def _number_ids(jobs: list[dict]) -> list[dict]:
    """Numbers the jobs' IDs 0, 1, 2, ... in their current order (after a removal they would have gaps)."""
    for index in range(len(jobs)):
        jobs[index]["ID"] = index

    return jobs
